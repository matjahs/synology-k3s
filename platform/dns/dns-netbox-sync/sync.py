#!/usr/bin/env python3
"""Sync Gateway API HTTPRoute hostnames into NetBox DNS (netbox-plugin-dns)."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException

LOG = logging.getLogger("dns-netbox-sync")

DNS_DOMAIN = os.environ.get("DNS_DOMAIN", "lab.mxe11.nl").strip().rstrip(".")
NETBOX_URL = os.environ.get("NETBOX_URL", "https://netbox.lab.mxe11.nl").rstrip("/")
NETBOX_TOKEN = os.environ["NETBOX_TOKEN"]
SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "60"))
MANAGED_MARKER = "managed-by=k8s"
ZONE_NAME = DNS_DOMAIN


def netbox_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Token {NETBOX_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    session.verify = os.environ.get("NETBOX_TLS_VERIFY", "true").lower() == "true"
    return session


def api_get(session: requests.Session, path: str, **params: Any) -> dict[str, Any]:
    response = session.get(f"{NETBOX_URL}{path}", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def api_post(session: requests.Session, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = session.post(f"{NETBOX_URL}{path}", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def api_patch(session: requests.Session, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = session.patch(f"{NETBOX_URL}{path}", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def api_delete(session: requests.Session, path: str) -> None:
    response = session.delete(f"{NETBOX_URL}{path}", timeout=30)
    if response.status_code not in (204, 404):
        response.raise_for_status()


def relative_name(fqdn: str) -> str | None:
    fqdn = fqdn.strip().rstrip(".").lower()
    zone = ZONE_NAME.lower()
    if fqdn == zone:
        return "@"
    suffix = f".{zone}"
    if not fqdn.endswith(suffix):
        return None
    label = fqdn[: -len(suffix)]
    if not label or "." in label:
        return None
    return label


def gateway_ip(custom: client.CustomObjectsApi, ref: dict[str, Any]) -> str | None:
    group = ref.get("group") or "gateway.networking.k8s.io"
    kind = ref.get("kind", "Gateway")
    if kind != "Gateway":
        return None
    namespace = ref.get("namespace") or "default"
    name = ref.get("name")
    if not name:
        return None
    version = "v1" if group == "gateway.networking.k8s.io" else ref.get("version", "v1")
    try:
        gateway = custom.get_namespaced_custom_object(
            group=group,
            version=version,
            namespace=namespace,
            plural="gateways",
            name=name,
        )
    except ApiException as exc:
        LOG.warning("gateway %s/%s: %s", namespace, name, exc)
        return None
    for entry in gateway.get("status", {}).get("addresses", []):
        value = entry.get("value")
        if value:
            return value
    return None


def collect_desired(custom: client.CustomObjectsApi) -> dict[str, str]:
    desired: dict[str, str] = {}
    routes = custom.list_cluster_custom_object(
        group="gateway.networking.k8s.io",
        version="v1",
        plural="httproutes",
    )
    for route in routes.get("items", []):
        parents = route.get("spec", {}).get("parentRefs", [])
        hostnames = route.get("spec", {}).get("hostnames", [])
        if not parents or not hostnames:
            continue
        ip = gateway_ip(custom, parents[0])
        if not ip:
            continue
        for host in hostnames:
            name = relative_name(host)
            if name:
                desired[f"{name}.{ZONE_NAME}".lower()] = ip
    return desired


def zone_id(session: requests.Session) -> int:
    data = api_get(session, "/api/plugins/netbox-dns/zones/", name=ZONE_NAME)
    results = data.get("results", [])
    if not results:
        raise RuntimeError(f"NetBox DNS zone {ZONE_NAME!r} not found — create it in the UI first")
    return int(results[0]["id"])


def record_fqdn(record_name: str) -> str:
    if record_name == "@":
        return ZONE_NAME
    return f"{record_name}.{ZONE_NAME}".lower()


def managed_records(session: requests.Session, zone: int) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        data = api_get(
            session,
            "/api/plugins/netbox-dns/records/",
            zone_id=zone,
            type="A",
            limit=200,
            offset=offset,
        )
        for record in data.get("results", []):
            description = record.get("description") or ""
            if MANAGED_MARKER not in description:
                continue
            records[record_fqdn(record.get("name", ""))] = record
        if data.get("next") is None:
            break
        offset += 200
    return records


def upsert_a_record(
    session: requests.Session,
    zone: int,
    relative: str,
    ip: str,
    existing: dict[str, Any] | None,
) -> None:
    payload = {
        "zone": zone,
        "name": relative,
        "type": "A",
        "value": ip,
        "status": "active",
        "ttl": 300,
        "description": MANAGED_MARKER,
    }
    if existing:
        if existing.get("value") == ip and MANAGED_MARKER in (existing.get("description") or ""):
            return
        api_patch(session, f"/api/plugins/netbox-dns/records/{existing['id']}/", payload)
        LOG.info("updated A %s.%s -> %s", relative, ZONE_NAME, ip)
    else:
        api_post(session, "/api/plugins/netbox-dns/records/", payload)
        LOG.info("created A %s.%s -> %s", relative, ZONE_NAME, ip)


def sync_once(custom: client.CustomObjectsApi, session: requests.Session) -> None:
    zone = zone_id(session)
    desired = collect_desired(custom)
    existing = managed_records(session, zone)

    for fqdn, ip in desired.items():
        rel = relative_name(fqdn)
        if not rel:
            continue
        upsert_a_record(session, zone, rel, ip, existing.get(fqdn.lower()))

    for fqdn, record in existing.items():
        if fqdn not in desired:
            api_delete(session, f"/api/plugins/netbox-dns/records/{record['id']}/")
            LOG.info("deleted stale A %s", fqdn)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config.load_incluster_config()
    custom = client.CustomObjectsApi()
    session = netbox_session()
    LOG.info("syncing HTTPRoutes -> NetBox DNS zone %s every %ss", ZONE_NAME, SYNC_INTERVAL)
    while True:
        try:
            sync_once(custom, session)
        except Exception:
            LOG.exception("sync failed")
        time.sleep(SYNC_INTERVAL)


if __name__ == "__main__":
    main()
