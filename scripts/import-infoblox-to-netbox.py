#!/usr/bin/env python3
"""Import Infoblox WAPI data (DNS zones/records, networks) into NetBox.

Idempotent: records and prefixes tagged with `imported-from=infoblox` in the
description are updated on re-run. Records managed by k8s (`managed-by=k8s`) are
never overwritten.

Environment:
  INFOBLOX_URL      default https://infoblox.lab.mxe11.nl
  INFOBLOX_USER     default apiuser
  INFOBLOX_PASSWORD required unless --dry-run
  NETBOX_URL        default https://netbox.lab.mxe11.nl
  NETBOX_TOKEN      required unless --dry-run
  NETBOX_TLS_VERIFY default true
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

INFOBLOX_URL = os.environ.get("INFOBLOX_URL", "https://infoblox.lab.mxe11.nl").rstrip("/")
INFOBLOX_USER = os.environ.get("INFOBLOX_USER", "apiuser")
INFOBLOX_PASSWORD = os.environ.get("INFOBLOX_PASSWORD", "")
WAPI_VERSION = os.environ.get("INFOBLOX_WAPI_VERSION", "2.13")
INFOBLOX_TLS_VERIFY = os.environ.get("INFOBLOX_TLS_VERIFY", "false").lower() == "true"

NETBOX_URL = os.environ.get("NETBOX_URL", "https://netbox.lab.mxe11.nl").rstrip("/")
NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN", "")
TLS_VERIFY = os.environ.get("NETBOX_TLS_VERIFY", "true").lower() == "true"

IMPORT_MARKER = "imported-from=infoblox"
K8S_MARKER = "managed-by=k8s"
DEFAULT_SITE_SLUG = os.environ.get("NETBOX_SITE_SLUG", "lab")

RECORD_FIELDS = {
    "a": "name,ipv4addr,ttl,comment",
    "aaaa": "name,ipv6addr,ttl,comment",
    "cname": "name,dns_name,ttl,comment",
    "ptr": "name,ptrdname,ipv4addr,ipv6addr,ttl,comment",
    "txt": "name,text,ttl,comment",
    "mx": "name,mail_exchanger,preference,ttl,comment",
    "srv": "name,priority,weight,port,target,ttl,comment",
}


def ssl_context(verify: bool = True) -> ssl.SSLContext | None:
    if verify:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def infoblox_fetch_all(path: str, **params: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    next_page: str | None = None
    auth = "Basic " + base64.b64encode(f"{INFOBLOX_USER}:{INFOBLOX_PASSWORD}".encode()).decode()
    while True:
        query: dict[str, Any] = {
            **params,
            "_max_results": 1000,
            "_paging": 1,
            "_return_as_object": 1,
        }
        if next_page:
            query["_page_id"] = next_page
        url = f"{INFOBLOX_URL}/wapi/v{WAPI_VERSION}/{path}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", auth)
        with urllib.request.urlopen(req, context=ssl_context(INFOBLOX_TLS_VERIFY)) as resp:
            payload = json.loads(resp.read())
        if isinstance(payload, list):
            results.extend(payload)
            break
        results.extend(payload.get("result", []))
        next_page = payload.get("next_page_id")
        if not next_page:
            break
    return results


class NetBoxClient:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{NETBOX_URL}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        headers = {
            "Authorization": f"Token {NETBOX_TOKEN}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.dry_run and method != "GET":
            print(f"DRY-RUN {method} {path}: {json.dumps(body)[:300]}")
            return {"id": 0}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=ssl_context(TLS_VERIFY), timeout=60) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            raise RuntimeError(f"{method} {path} failed ({exc.code}): {detail}") from exc

    def list_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        cache_key = f"{path}?{params}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        results: list[dict[str, Any]] = []
        offset = 0
        base_params = dict(params or {})
        while True:
            page_params = {**base_params, "limit": 200, "offset": offset}
            payload = self._request("GET", path, params=page_params)
            results.extend(payload.get("results", []))
            if payload.get("next") is None:
                break
            offset += 200
        self._cache[cache_key] = results
        return results

    def find_one(self, path: str, **lookup: Any) -> dict[str, Any] | None:
        items = self.list_all(path, lookup)
        return items[0] if items else None


def is_forward_zone(fqdn: str) -> bool:
    return "/" not in fqdn and not fqdn.endswith("in-addr.arpa")


def relative_name(fqdn: str, zone: str) -> str:
    fqdn = fqdn.rstrip(".").lower()
    zone = zone.rstrip(".").lower()
    if fqdn == zone:
        return "@"
    suffix = f".{zone}"
    if not fqdn.endswith(suffix):
        raise ValueError(f"{fqdn!r} is not in zone {zone!r}")
    return fqdn[: -len(suffix)]


def record_value(rtype: str, record: dict[str, Any]) -> str | None:
    if rtype == "a":
        return record.get("ipv4addr")
    if rtype == "aaaa":
        return record.get("ipv6addr")
    if rtype in ("cname", "ns"):
        return record.get("dns_name")
    if rtype == "ptr":
        return record.get("ptrdname")
    if rtype == "txt":
        text = record.get("text")
        if isinstance(text, list):
            return " ".join(f'"{part}"' for part in text)
        return text
    if rtype == "mx":
        pref = record.get("preference", 10)
        host = record.get("mail_exchanger")
        return f"{pref} {host}" if host else None
    if rtype == "srv":
        parts = [
            record.get("priority", 0),
            record.get("weight", 0),
            record.get("port", 0),
            record.get("target"),
        ]
        if not parts[3]:
            return None
        return " ".join(str(p) for p in parts)
    return None


def description_for(record: dict[str, Any]) -> str:
    comment = (record.get("comment") or "").strip()
    if comment:
        return f"{IMPORT_MARKER}; {comment}"
    return IMPORT_MARKER


def ensure_nameserver(nb: NetBoxClient, name: str) -> None:
    if nb.find_one("/api/plugins/netbox-dns/nameservers/", name=name):
        return
    nb._request("POST", "/api/plugins/netbox-dns/nameservers/", {"name": name})
    nb._cache.clear()
    print(f"  created nameserver {name}")


def ensure_zone(nb: NetBoxClient, zone_name: str, soa_mname: str) -> int:
    existing = nb.find_one("/api/plugins/netbox-dns/zones/", name=zone_name)
    if existing:
        return int(existing["id"])
    ensure_nameserver(nb, soa_mname)
    soa_rname = f"hostmaster.{zone_name}"
    payload = {
        "name": zone_name,
        "status": "active",
        "soa_mname": {"name": soa_mname},
        "soa_rname": soa_rname,
        "nameservers": [{"name": soa_mname}],
    }
    created = nb._request("POST", "/api/plugins/netbox-dns/zones/", payload)
    nb._cache.clear()
    print(f"  created zone {zone_name}")
    return int(created["id"])


def import_dns_zone(nb: NetBoxClient, zone_name: str, soa_mname: str) -> tuple[int, int, int]:
    zone_id = ensure_zone(nb, zone_name, soa_mname)
    created = updated = skipped = 0

    for rtype, fields in RECORD_FIELDS.items():
        try:
            records = infoblox_fetch_all(f"record:{rtype}", zone=zone_name, _return_fields=fields)
        except urllib.error.HTTPError:
            continue

        for record in records:
            value = record_value(rtype, record)
            if not value:
                continue
            rel = relative_name(record["name"], zone_name)
            rtype_upper = rtype.upper()
            existing = nb.find_one(
                "/api/plugins/netbox-dns/records/",
                zone_id=zone_id,
                name=rel,
                type=rtype_upper,
            )
            desc = description_for(record)
            ttl = record.get("ttl")
            payload: dict[str, Any] = {
                "zone": zone_id,
                "name": rel,
                "type": rtype_upper,
                "value": str(value),
                "status": "active",
                "description": desc,
            }
            if ttl is not None:
                payload["ttl"] = int(ttl)

            if existing:
                existing_desc = existing.get("description") or ""
                if K8S_MARKER in existing_desc:
                    skipped += 1
                    continue
                if (
                    existing.get("value") == payload["value"]
                    and existing_desc == desc
                    and existing.get("ttl") == payload.get("ttl")
                ):
                    skipped += 1
                    continue
                nb._request("PATCH", f"/api/plugins/netbox-dns/records/{existing['id']}/", payload)
                nb._cache.clear()
                updated += 1
            else:
                nb._request("POST", "/api/plugins/netbox-dns/records/", payload)
                nb._cache.clear()
                created += 1

    return created, updated, skipped


def import_prefixes(nb: NetBoxClient, site_id: int | None) -> tuple[int, int, int]:
    created = updated = skipped = 0
    networks = infoblox_fetch_all("network", _return_fields="network,comment")
    for network in networks:
        cidr = network["network"]
        comment = (network.get("comment") or "").strip()
        desc = f"{IMPORT_MARKER}; {comment}" if comment else IMPORT_MARKER
        existing = nb.find_one("/api/ipam/prefixes/", prefix=cidr)
        payload: dict[str, Any] = {
            "prefix": cidr,
            "status": "active",
            "description": desc,
        }
        if site_id is not None:
            payload["site"] = site_id
        if existing:
            if existing.get("description") == desc:
                skipped += 1
                continue
            nb._request("PATCH", f"/api/ipam/prefixes/{existing['id']}/", {"description": desc})
            nb._cache.clear()
            updated += 1
        else:
            nb._request("POST", "/api/ipam/prefixes/", payload)
            nb._cache.clear()
            created += 1
    return created, updated, skipped


def default_soa_mname(zone_name: str) -> str:
    if zone_name == "lab.mxe11.nl":
        return "ns1.lab.mxe11.nl"
    return "infoblox.lab.mxe11.nl"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Infoblox WAPI data into NetBox")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--zones",
        nargs="*",
        help="Forward zones to import (default: all forward zones in Infoblox)",
    )
    parser.add_argument("--skip-prefixes", action="store_true", help="Skip IPAM prefix import")
    parser.add_argument("--skip-dns", action="store_true", help="Skip DNS zone/record import")
    args = parser.parse_args()

    if not args.dry_run and not INFOBLOX_PASSWORD:
        raise SystemExit("INFOBLOX_PASSWORD is required (unless --dry-run)")
    if not args.dry_run and not NETBOX_TOKEN:
        raise SystemExit("NETBOX_TOKEN is required (unless --dry-run)")

    nb = NetBoxClient(dry_run=args.dry_run)
    print(f"Infoblox: {INFOBLOX_URL}")
    print(f"NetBox:   {NETBOX_URL}")

    site = nb.find_one("/api/dcim/sites/", slug=DEFAULT_SITE_SLUG)
    site_id = int(site["id"]) if site else None
    if site_id is None:
        print(f"Warning: site slug {DEFAULT_SITE_SLUG!r} not found; prefixes will have no site")

    if not args.skip_dns:
        if args.zones:
            zones = args.zones
        else:
            zone_objs = infoblox_fetch_all("zone_auth", _return_fields="fqdn")
            zones = sorted({z["fqdn"] for z in zone_objs if is_forward_zone(z["fqdn"])})
        print(f"DNS zones: {', '.join(zones)}")
        total = [0, 0, 0]
        for zone_name in zones:
            print(f"Importing DNS zone {zone_name}…")
            counts = import_dns_zone(nb, zone_name, default_soa_mname(zone_name))
            print(f"  records: +{counts[0]} ~{counts[1]} ={counts[2]}")
            for i, c in enumerate(counts):
                total[i] += c
        print(f"DNS totals: created {total[0]}, updated {total[1]}, skipped {total[2]}")

    if not args.skip_prefixes:
        print("Importing Infoblox networks as IPAM prefixes…")
        counts = import_prefixes(nb, site_id)
        print(f"Prefixes: +{counts[0]} ~{counts[1]} ={counts[2]}")

    print("Done.")


if __name__ == "__main__":
    main()
