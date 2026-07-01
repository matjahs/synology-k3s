#!/usr/bin/env python3
"""Seed NetBox with homelab inventory (DCIM, IPAM, virtualization, DNS).

Idempotent: safe to re-run; existing objects are matched by slug/name and skipped
or updated when descriptions differ.

Requires: Python 3.10+, PyYAML (`pip install pyyaml`), NETBOX_TOKEN env var.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

PLACEHOLDER_RE = re.compile(r"^CHANGEME", re.I)
NETBOX_URL = os.environ.get("NETBOX_URL", "https://netbox.lab.mxe11.nl").rstrip("/")
NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN", "")
TLS_VERIFY = os.environ.get("NETBOX_TLS_VERIFY", "true").lower() == "true"


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return not stripped or bool(PLACEHOLDER_RE.match(stripped))
    return False


def collect_missing(data: dict[str, Any], path: str = "") -> list[str]:
    missing: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            child = f"{path}.{key}" if path else key
            if key in ("comments", "description", "k8s_services", "k8s_role"):
                continue
            if key == "primary_ip4" and value is None:
                missing.append(child)
            elif key in ("vcpus", "memory_mb", "disk_gb", "serial", "dns_name") and value is None:
                continue
            elif key == "vid" and value is None:
                continue
            elif isinstance(value, (dict, list)):
                missing.extend(collect_missing(value, child))
            elif is_missing(value):
                missing.append(child)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            child = f"{path}[{index}]"
            if isinstance(item, (dict, list)):
                missing.extend(collect_missing(item, child))
            elif is_missing(item):
                missing.append(child)
    return missing


class NetBoxClient:
    def __init__(self, dry_run: bool = False) -> None:
        if not NETBOX_TOKEN and not dry_run:
            raise SystemExit("NETBOX_TOKEN is required (unless --dry-run)")
        self.dry_run = dry_run
        self._cache: dict[str, dict[str, Any]] = {}

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
        data = None
        headers = {
            "Authorization": f"Token {NETBOX_TOKEN}",
            "Accept": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        if self.dry_run and method != "GET":
            print(f"DRY-RUN {method} {path}: {json.dumps(body, indent=2)[:500]}")
            return {"id": 0, "url": path}
        ctx = None
        if not TLS_VERIFY:
            import ssl

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                raw = resp.read().decode()
                if not raw:
                    return None
                return json.loads(raw)
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

    def ensure(
        self,
        path: str,
        lookup: dict[str, Any],
        payload: dict[str, Any],
        label: str | None = None,
    ) -> dict[str, Any]:
        existing = self.find_one(path, **lookup)
        name = label or payload.get("name") or payload.get("slug") or str(lookup)
        if existing:
            print(f"  exists: {name}")
            return existing
        created = self._request("POST", path, body=payload)
        print(f"  created: {name}")
        self._cache.clear()
        return created

    def ensure_tag(self, name: str) -> dict[str, Any]:
        slug = name.lower().replace(" ", "-")
        return self.ensure(
            "/api/extras/tags/",
            {"slug": slug},
            {"name": name, "slug": slug, "color": "9e9e9e"},
            label=f"tag:{name}",
        )


def load_data(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return yaml.safe_load(fh)


def seed_site(nb: NetBoxClient, data: dict[str, Any]) -> int:
    site = data["site"]
    payload: dict[str, Any] = {
        "name": site["name"],
        "slug": site["slug"],
        "status": site.get("status", "active"),
        "description": site.get("description", ""),
    }
    if site.get("physical_address"):
        payload["physical_address"] = site["physical_address"].strip()
    obj = nb.ensure("/api/dcim/sites/", {"slug": site["slug"]}, payload)
    return int(obj["id"])


def seed_tags(nb: NetBoxClient, data: dict[str, Any]) -> list[dict[str, Any]]:
    return [nb.ensure_tag(name) for name in data.get("tags", [])]


def seed_manufacturers(nb: NetBoxClient, data: dict[str, Any]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for name in data.get("manufacturers", []):
        slug = name.lower().replace(" ", "-")
        obj = nb.ensure(
            "/api/dcim/manufacturers/",
            {"slug": slug},
            {"name": name, "slug": slug},
        )
        ids[name] = int(obj["id"])
    return ids


def seed_device_roles(nb: NetBoxClient, data: dict[str, Any]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for role in data.get("device_roles", []):
        obj = nb.ensure(
            "/api/dcim/device-roles/",
            {"slug": role["slug"]},
            {
                "name": role["name"],
                "slug": role["slug"],
                "color": role.get("color", "9e9e9e"),
            },
        )
        ids[role["slug"]] = int(obj["id"])
    return ids


def import_component_templates(
    nb: NetBoxClient,
    device_type_id: int,
    library_type: dict[str, Any],
) -> None:
    component_sets = {
        "interfaces": ("/api/dcim/interface-templates/", "type"),
        "power-ports": ("/api/dcim/power-port-templates/", "type"),
        "console-ports": ("/api/dcim/console-port-templates/", "type"),
        "console-server-ports": ("/api/dcim/console-server-port-templates/", "type"),
        "power-outlets": ("/api/dcim/power-outlet-templates/", "type"),
        "module-bays": ("/api/dcim/module-bay-templates/", None),
    }
    allowed_fields = {
        "name",
        "label",
        "type",
        "mgmt_only",
        "poe_mode",
        "poe_type",
        "maximum_draw",
        "allocated_draw",
        "power_port",
        "feed_leg",
        "position",
        "description",
    }
    for source_key, (path, required_field) in component_sets.items():
        for item in library_type.get(source_key, []) or []:
            if required_field and not item.get(required_field):
                continue
            name = item["name"]
            existing = nb.find_one(path, device_type_id=device_type_id, name=name)
            if existing:
                continue
            payload = {
                "device_type": device_type_id,
                **{k: v for k, v in item.items() if k in allowed_fields and v is not None},
            }
            try:
                nb._request("POST", path, body=payload)
                print(f"  created template:{source_key}:{name}")
            except RuntimeError as exc:
                print(f"  skip template:{source_key}:{name} ({exc})")
    nb._cache.clear()


def import_community_device_types(
    nb: NetBoxClient,
    data: dict[str, Any],
    manufacturer_ids: dict[str, int],
) -> dict[str, int]:
    ids: dict[str, int] = {}
    for entry in data.get("community_device_types", []):
        alias = entry["alias"]
        slug = entry["slug"]
        existing = nb.find_one("/api/dcim/device-types/", slug=slug)
        if existing:
            print(f"  exists community:{slug}")
            ids[alias] = int(existing["id"])
            ids[slug] = int(existing["id"])
            continue
        if nb.dry_run:
            print(f"DRY-RUN import community device type {slug} from {entry['url']}")
            ids[alias] = 0
            ids[slug] = 0
            continue
        with urllib.request.urlopen(entry["url"], timeout=60) as response:
            library_type = yaml.safe_load(response.read().decode())
        manufacturer = library_type["manufacturer"]
        if manufacturer not in manufacturer_ids:
            slugified = manufacturer.lower().replace(" ", "-")
            obj = nb.ensure(
                "/api/dcim/manufacturers/",
                {"slug": slugified},
                {"name": manufacturer, "slug": slugified},
            )
            manufacturer_ids[manufacturer] = int(obj["id"])
        payload_keys = {
            "model",
            "slug",
            "part_number",
            "u_height",
            "is_full_depth",
            "airflow",
            "weight",
            "weight_unit",
            "comments",
        }
        payload = {
            "manufacturer": manufacturer_ids[manufacturer],
            **{k: v for k, v in library_type.items() if k in payload_keys and v is not None},
        }
        obj = nb.ensure(
            "/api/dcim/device-types/",
            {"slug": slug},
            payload,
            label=f"community:{slug}",
        )
        device_type_id = int(obj["id"])
        import_component_templates(nb, device_type_id, library_type)
        ids[alias] = device_type_id
        ids[slug] = device_type_id
    return ids


def seed_device_types(
    nb: NetBoxClient,
    data: dict[str, Any],
    manufacturer_ids: dict[str, int],
    community_type_ids: dict[str, int] | None = None,
) -> dict[str, int]:
    ids: dict[str, int] = {}
    community_type_ids = community_type_ids or {}
    for dt in data.get("device_types", []):
        library_slug = dt.get("library_slug")
        if library_slug and library_slug in community_type_ids:
            ids[dt["slug"]] = community_type_ids[library_slug]
            print(f"  using community:{library_slug} as {dt['slug']}")
            continue
        mfr_id = manufacturer_ids[dt["manufacturer"]]
        payload = {
            "manufacturer": mfr_id,
            "model": dt["model"],
            "slug": dt["slug"],
            "u_height": dt.get("u_height", 0),
            "is_full_depth": dt.get("is_full_depth", False),
            "comments": dt.get("comments", ""),
        }
        obj = nb.ensure("/api/dcim/device-types/", {"slug": dt["slug"]}, payload)
        ids[dt["slug"]] = int(obj["id"])
    return ids


def seed_platforms(nb: NetBoxClient, data: dict[str, Any]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for platform in data.get("platforms", []):
        obj = nb.ensure(
            "/api/dcim/platforms/",
            {"slug": platform["slug"]},
            {"name": platform["name"], "slug": platform["slug"]},
        )
        ids[platform["slug"]] = int(obj["id"])
    return ids


def seed_vlans(nb: NetBoxClient, data: dict[str, Any], site_id: int) -> dict[str, int]:
    ids: dict[str, int] = {}
    for vlan in data.get("vlans", []):
        if is_missing(vlan.get("vid")):
            print("  skip vlan (no vid configured)")
            continue
        vid = int(vlan["vid"])
        payload = {
            "vid": vid,
            "name": vlan["name"],
            "status": vlan.get("status", "active"),
            "site": site_id,
            "description": vlan.get("description", ""),
        }
        obj = nb.ensure("/api/ipam/vlans/", {"vid": vid, "site_id": site_id}, payload)
        slug = vlan.get("slug") or f"vlan{vid}"
        ids[slug] = int(obj["id"])
    return ids


def seed_prefixes(
    nb: NetBoxClient,
    data: dict[str, Any],
    site_id: int,
    vlan_ids: dict[str, int],
) -> dict[str, int]:
    ids: dict[str, int] = {}
    for prefix in data.get("prefixes", []):
        cidr = prefix["prefix"]
        payload: dict[str, Any] = {
            "prefix": cidr,
            "status": prefix.get("status", "active"),
            "site": site_id,
            "description": prefix.get("description", ""),
        }
        vlan_slug = prefix.get("vlan")
        if vlan_slug and vlan_slug in vlan_ids:
            payload["vlan"] = vlan_ids[vlan_slug]
        obj = nb.ensure("/api/ipam/prefixes/", {"prefix": cidr}, payload, label=f"prefix:{cidr}")
        ids[cidr] = int(obj["id"])
    return ids


DCIM_INTERFACE = "dcim.interface"
VM_INTERFACE = "virtualization.vminterface"


def ensure_device_interface(
    nb: NetBoxClient,
    device_id: int,
    name: str = "mgmt",
    iface_type: str = "1000base-t",
) -> int:
    existing = nb.find_one("/api/dcim/interfaces/", device_id=device_id, name=name)
    if existing:
        return int(existing["id"])
    obj = nb._request(
        "POST",
        "/api/dcim/interfaces/",
        body={
            "device": device_id,
            "name": name,
            "type": iface_type,
            "enabled": True,
        },
    )
    print(f"  created interface:{name} on device {device_id}")
    nb._cache.clear()
    return int(obj["id"])


def ensure_vm_interface(nb: NetBoxClient, vm_id: int, name: str = "eth0") -> int:
    existing = nb.find_one("/api/virtualization/interfaces/", virtual_machine_id=vm_id, name=name)
    if existing:
        return int(existing["id"])
    obj = nb._request(
        "POST",
        "/api/virtualization/interfaces/",
        body={
            "virtual_machine": vm_id,
            "name": name,
            "enabled": True,
        },
    )
    print(f"  created vminterface:{name} on vm {vm_id}")
    nb._cache.clear()
    return int(obj["id"])


def ensure_ip_assigned(
    nb: NetBoxClient,
    address: str,
    *,
    assigned_type: str,
    assigned_id: int,
    dns_name: str | None = None,
    description: str = "",
) -> int:
    payload: dict[str, Any] = {
        "address": address,
        "status": "active",
        "description": description,
        "assigned_object_type": assigned_type,
        "assigned_object_id": assigned_id,
    }
    if dns_name:
        payload["dns_name"] = dns_name
    existing = nb.find_one("/api/ipam/ip-addresses/", address=address)
    if existing:
        needs_assign = (
            existing.get("assigned_object_type") != assigned_type
            or existing.get("assigned_object_id") != assigned_id
        )
        if needs_assign and not nb.dry_run:
            nb._request(
                "PATCH",
                f"/api/ipam/ip-addresses/{existing['id']}/",
                body={
                    "assigned_object_type": assigned_type,
                    "assigned_object_id": assigned_id,
                },
            )
            print(f"  assigned IP {address} -> {assigned_type} {assigned_id}")
        else:
            print(f"  exists IP {address}")
        return int(existing["id"])
    obj = nb._request("POST", "/api/ipam/ip-addresses/", body=payload)
    print(f"  created IP {address}")
    nb._cache.clear()
    return int(obj["id"])


def ensure_ip(
    nb: NetBoxClient,
    address: str,
    *,
    dns_name: str | None = None,
    description: str = "",
) -> int:
    """Create an unassigned IP (service VIPs, etc.)."""
    payload: dict[str, Any] = {
        "address": address,
        "status": "active",
        "description": description,
    }
    if dns_name:
        payload["dns_name"] = dns_name
    obj = nb.ensure("/api/ipam/ip-addresses/", {"address": address}, payload, label=f"IP {address}")
    return int(obj["id"])


def seed_devices(
    nb: NetBoxClient,
    data: dict[str, Any],
    site_id: int,
    device_type_ids: dict[str, int],
    role_ids: dict[str, int],
) -> None:
    for device in data.get("devices", []):
        if is_missing(device.get("primary_ip4")):
            print(f"  skip device {device['name']} (no primary_ip4)")
            continue
        payload: dict[str, Any] = {
            "name": device["name"],
            "device_type": device_type_ids[device["device_type"]],
            "role": role_ids[device["role"]],
            "site": site_id,
            "status": device.get("status", "active"),
            "description": device.get("description", ""),
        }
        if not is_missing(device.get("serial")):
            payload["serial"] = device["serial"]
        obj = nb.ensure(
            "/api/dcim/devices/",
            {"name": device["name"]},
            payload,
            label=f"device:{device['name']}",
        )
        if nb.dry_run or not int(obj.get("id", 0)):
            continue
        device_id = int(obj["id"])
        current_type = obj.get("device_type")
        current_type_id = current_type.get("id") if isinstance(current_type, dict) else current_type
        desired_type_id = device_type_ids[device["device_type"]]
        if current_type_id != desired_type_id:
            nb._request(
                "PATCH",
                f"/api/dcim/devices/{device_id}/",
                body={"device_type": desired_type_id},
            )
            print(f"  updated device_type on {device['name']}")
        iface_name = device.get("interface", "mgmt")
        iface_id = ensure_device_interface(nb, device_id, iface_name)
        ip_id = ensure_ip_assigned(
            nb,
            device["primary_ip4"],
            assigned_type=DCIM_INTERFACE,
            assigned_id=iface_id,
            dns_name=device.get("dns_name"),
            description=device.get("description", ""),
        )
        for extra in device.get("extra_ips", []):
            extra_iface = extra.get("interface", iface_name)
            extra_iface_id = (
                iface_id
                if extra_iface == iface_name
                else ensure_device_interface(nb, device_id, extra_iface)
            )
            ensure_ip_assigned(
                nb,
                extra["address"],
                assigned_type=DCIM_INTERFACE,
                assigned_id=extra_iface_id,
                description=extra.get("description", device.get("description", "")),
            )
        nb._request(
            "PATCH",
            f"/api/dcim/devices/{device_id}/",
            body={"primary_ip4": ip_id},
        )
        print(f"  set primary_ip4 on {device['name']}")


def seed_clusters(nb: NetBoxClient, data: dict[str, Any], site_id: int) -> dict[str, int]:
    type_ids: dict[str, int] = {}
    for ct in data.get("cluster_types", []):
        obj = nb.ensure(
            "/api/virtualization/cluster-types/",
            {"slug": ct["slug"]},
            {"name": ct["name"], "slug": ct["slug"]},
        )
        type_ids[ct["slug"]] = int(obj["id"])
    if not type_ids and data.get("cluster_type"):
        ct = data["cluster_type"]
        obj = nb.ensure(
            "/api/virtualization/cluster-types/",
            {"slug": ct["slug"]},
            {"name": ct["name"], "slug": ct["slug"]},
        )
        type_ids[ct["slug"]] = int(obj["id"])

    cluster_ids: dict[str, int] = {}
    clusters = data.get("clusters")
    if clusters:
        for cluster in clusters:
            type_slug = cluster["type"]
            obj = nb.ensure(
                "/api/virtualization/clusters/",
                {"slug": cluster["slug"]},
                {
                    "name": cluster["name"],
                    "slug": cluster["slug"],
                    "type": type_ids[type_slug],
                    "site": site_id,
                    "status": cluster.get("status", "active"),
                    "description": cluster.get("description", ""),
                },
            )
            cluster_ids[cluster["slug"]] = int(obj["id"])
        return cluster_ids

    cluster = data["cluster"]
    ct_slug = data["cluster_type"]["slug"]
    obj = nb.ensure(
        "/api/virtualization/clusters/",
        {"slug": cluster["slug"]},
        {
            "name": cluster["name"],
            "slug": cluster["slug"],
            "type": type_ids[ct_slug],
            "site": site_id,
            "status": cluster.get("status", "active"),
            "description": cluster.get("description", ""),
        },
    )
    cluster_ids[cluster["slug"]] = int(obj["id"])
    return cluster_ids


def seed_cluster(nb: NetBoxClient, data: dict[str, Any], site_id: int) -> int:
    return next(iter(seed_clusters(nb, data, site_id).values()))


def seed_vms(
    nb: NetBoxClient,
    data: dict[str, Any],
    cluster_ids: dict[str, int],
    role_ids: dict[str, int],
    platform_ids: dict[str, int],
) -> None:
    default_cluster = data.get("cluster", {}).get("slug")
    for vm in data.get("virtual_machines", []):
        cluster_slug = vm.get("cluster", default_cluster)
        if not cluster_slug or cluster_slug not in cluster_ids:
            print(f"  skip vm {vm.get('name')} (unknown cluster {cluster_slug!r})")
            continue
        payload: dict[str, Any] = {
            "name": vm["name"],
            "cluster": cluster_ids[cluster_slug],
            "status": vm.get("status", "active"),
            "description": vm.get("description", ""),
        }
        if vm.get("role") and vm["role"] in role_ids:
            payload["role"] = role_ids[vm["role"]]
        if vm.get("platform") and vm["platform"] in platform_ids:
            payload["platform"] = platform_ids[vm["platform"]]
        for field in ("vcpus", "memory", "disk"):
            yaml_key = "memory_mb" if field == "memory" else f"{field}{'_gb' if field == 'disk' else 's'}"
            value = vm.get(yaml_key)
            if not is_missing(value):
                payload[field] = value
        obj = nb.ensure(
            "/api/virtualization/virtual-machines/",
            {"name": vm["name"]},
            payload,
            label=f"vm:{vm['name']}",
        )
        if is_missing(vm.get("primary_ip4")) or nb.dry_run or not int(obj.get("id", 0)):
            continue
        vm_id = int(obj["id"])
        iface_name = vm.get("interface", "eth0")
        iface_id = ensure_vm_interface(nb, vm_id, iface_name)
        ip_id = ensure_ip_assigned(
            nb,
            vm["primary_ip4"],
            assigned_type=VM_INTERFACE,
            assigned_id=iface_id,
            dns_name=vm["name"],
            description=vm.get("description", ""),
        )
        nb._request(
            "PATCH",
            f"/api/virtualization/virtual-machines/{vm_id}/",
            body={"primary_ip4": ip_id},
        )
        print(f"  set primary_ip4 on {vm['name']}")


def seed_service_ips(nb: NetBoxClient, data: dict[str, Any]) -> None:
    for svc in data.get("service_ips", []):
        ensure_ip(
            nb,
            svc["address"],
            dns_name=svc.get("dns_name"),
            description=svc.get("description", ""),
        )


def seed_dns(nb: NetBoxClient, data: dict[str, Any]) -> None:
    zone_name = data.get("dns_zone")
    if not zone_name:
        return
    zone = nb.find_one("/api/plugins/netbox-dns/zones/", name=zone_name)
    if not zone:
        print(f"  DNS zone {zone_name} not found — run bootstrap-netbox-dns-zone.sh first")
        return
    zone_id = int(zone["id"])
    for record in data.get("dns_records", []):
        if is_missing(record.get("value")):
            print(f"  skip DNS {record['name']} (no value)")
            continue
        rel_name = record["name"]
        existing = nb.find_one(
            "/api/plugins/netbox-dns/records/",
            zone_id=zone_id,
            name=rel_name,
            type=record.get("type", "A"),
        )
        payload = {
            "zone": zone_id,
            "name": rel_name,
            "type": record.get("type", "A"),
            "value": str(record["value"]),
            "status": "active",
            "ttl": record.get("ttl", 300),
            "description": record.get("description", ""),
        }
        if existing:
            if existing.get("value") != payload["value"]:
                nb._request(
                    "PATCH",
                    f"/api/plugins/netbox-dns/records/{existing['id']}/",
                    body=payload,
                )
                print(f"  updated DNS A {rel_name}.{zone_name}")
            else:
                print(f"  exists DNS A {rel_name}.{zone_name}")
        else:
            nb._request("POST", "/api/plugins/netbox-dns/records/", body=payload)
            print(f"  created DNS A {rel_name}.{zone_name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed NetBox homelab inventory")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(__file__).with_name("netbox-homelab-data.yaml"),
        help="YAML data file (default: scripts/netbox-homelab-data.yaml)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without API writes")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only report missing/placeholder fields and exit",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even when optional placeholders remain (skips incomplete objects)",
    )
    parser.add_argument(
        "--import-community-device-types",
        action="store_true",
        help="Fetch selected DeviceTypes from netbox-community/devicetype-library before seeding",
    )
    args = parser.parse_args()

    data = load_data(args.data)
    required_missing = [
        m
        for m in collect_missing(data)
        if any(
            m.endswith(suffix)
            for suffix in (
                "device_types[0].model",
                "dns_records",
                "devices[",
                "virtual_machines[",
            )
        )
        and "primary_ip4" not in m
        and "value" not in m
    ]
    # Report all placeholders
    all_missing = collect_missing(data)
    if all_missing:
        print("Placeholder / null fields in data file:")
        for item in all_missing:
            print(f"  - {item}")
    if args.check:
        sys.exit(0 if not all_missing else 1)
    if all_missing and not args.force and not args.dry_run:
        critical = [m for m in all_missing if m.endswith("device_types[0].model")]
        if critical:
            print("\nFix CHANGEME values (at minimum Synology model) or use --force to skip incomplete objects.")
            sys.exit(2)

    nb = NetBoxClient(dry_run=args.dry_run)
    print(f"NetBox: {NETBOX_URL}")
    print("Site…")
    site_id = seed_site(nb, data)
    print("Tags…")
    seed_tags(nb, data)
    print("Manufacturers…")
    mfr_ids = seed_manufacturers(nb, data)
    print("Device roles…")
    role_ids = seed_device_roles(nb, data)
    community_type_ids: dict[str, int] = {}
    if args.import_community_device_types:
        print("Community device types…")
        community_type_ids = import_community_device_types(nb, data, mfr_ids)
    print("Device types…")
    dt_ids = seed_device_types(nb, data, mfr_ids, community_type_ids)
    print("Platforms…")
    platform_ids = seed_platforms(nb, data)
    print("VLANs…")
    vlan_ids = seed_vlans(nb, data, site_id)
    print("Prefixes…")
    seed_prefixes(nb, data, site_id, vlan_ids)
    print("Clusters…")
    cluster_ids = seed_clusters(nb, data, site_id)
    print("Devices…")
    seed_devices(nb, data, site_id, dt_ids, role_ids)
    print("Virtual machines…")
    seed_vms(nb, data, cluster_ids, role_ids, platform_ids)
    print("Service IPs…")
    seed_service_ips(nb, data)
    print("DNS records…")
    seed_dns(nb, data)
    print("Done.")


if __name__ == "__main__":
    main()
