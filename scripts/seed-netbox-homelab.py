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
            if key in ("comments", "description", "k8s_services", "k8s_role", "asns", "device"):
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


def seed_slug_named_types(
    nb: NetBoxClient,
    data: dict[str, Any],
    key: str,
    api_path: str,
) -> dict[str, int]:
    ids: dict[str, int] = {}
    for item in data.get(key, []):
        payload: dict[str, Any] = {
            "name": item["name"],
            "slug": item["slug"],
        }
        if item.get("color"):
            payload["color"] = item["color"]
        if item.get("description"):
            payload["description"] = item["description"]
        obj = nb.ensure(api_path, {"slug": item["slug"]}, payload)
        ids[item["slug"]] = int(obj["id"])
    return ids


def seed_rack_types(
    nb: NetBoxClient,
    data: dict[str, Any],
    manufacturer_ids: dict[str, int],
) -> dict[str, int]:
    ids: dict[str, int] = {}
    optional_keys = (
        "description",
        "width",
        "u_height",
        "starting_unit",
        "desc_units",
        "outer_width",
        "outer_height",
        "outer_depth",
        "outer_unit",
        "mounting_depth",
        "weight",
        "max_weight",
        "weight_unit",
    )
    for rt in data.get("rack_types", []):
        payload: dict[str, Any] = {
            "manufacturer": manufacturer_ids[rt["manufacturer"]],
            "model": rt["model"],
            "slug": rt["slug"],
            "form_factor": rt["form_factor"],
        }
        for key in optional_keys:
            if key in rt:
                payload[key] = rt[key]
        obj = nb.ensure("/api/dcim/rack-types/", {"slug": rt["slug"]}, payload)
        ids[rt["slug"]] = int(obj["id"])
    return ids


def seed_module_types(
    nb: NetBoxClient,
    data: dict[str, Any],
    manufacturer_ids: dict[str, int],
) -> dict[str, int]:
    ids: dict[str, int] = {}
    optional_keys = ("part_number", "description", "weight", "weight_unit", "airflow")
    for mt in data.get("module_types", []):
        mfr_id = manufacturer_ids[mt["manufacturer"]]
        payload: dict[str, Any] = {
            "manufacturer": mfr_id,
            "model": mt["model"],
        }
        for key in optional_keys:
            if key in mt:
                payload[key] = mt[key]
        obj = nb.ensure(
            "/api/dcim/module-types/",
            {"manufacturer_id": mfr_id, "model": mt["model"]},
            payload,
            label=f"{mt['manufacturer']} {mt['model']}",
        )
        ids[mt["model"]] = int(obj["id"])
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


def find_device_type(
    nb: NetBoxClient,
    *,
    slug: str | None = None,
    manufacturer_id: int | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    if slug:
        existing = nb.find_one("/api/dcim/device-types/", slug=slug)
        if existing:
            return existing
    if manufacturer_id is not None and model:
        return nb.find_one(
            "/api/dcim/device-types/",
            manufacturer_id=manufacturer_id,
            model=model,
        )
    return None


def import_community_device_types(
    nb: NetBoxClient,
    data: dict[str, Any],
    manufacturer_ids: dict[str, int],
) -> dict[str, int]:
    ids: dict[str, int] = {}
    for entry in data.get("community_device_types", []):
        alias = entry["alias"]
        slug = entry["slug"]
        existing = find_device_type(nb, slug=slug)
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
        model = library_type["model"]
        if manufacturer not in manufacturer_ids:
            slugified = manufacturer.lower().replace(" ", "-")
            obj = nb.ensure(
                "/api/dcim/manufacturers/",
                {"slug": slugified},
                {"name": manufacturer, "slug": slugified},
            )
            manufacturer_ids[manufacturer] = int(obj["id"])
        mfr_id = manufacturer_ids[manufacturer]
        existing = find_device_type(nb, manufacturer_id=mfr_id, model=model)
        if existing:
            device_type_id = int(existing["id"])
            print(f"  exists community:{slug} (manufacturer/model as {existing['slug']})")
            import_component_templates(nb, device_type_id, library_type)
            ids[alias] = device_type_id
            ids[slug] = device_type_id
            continue
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
            "manufacturer": mfr_id,
            **{k: v for k, v in library_type.items() if k in payload_keys and v is not None},
        }
        obj = nb._request("POST", "/api/dcim/device-types/", body=payload)
        print(f"  created: community:{slug}")
        nb._cache.clear()
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
        existing = find_device_type(
            nb,
            slug=dt["slug"],
            manufacturer_id=mfr_id,
            model=dt["model"],
        )
        if existing:
            print(f"  exists: {dt['slug']}")
            ids[dt["slug"]] = int(existing["id"])
            continue
        payload = {
            "manufacturer": mfr_id,
            "model": dt["model"],
            "slug": dt["slug"],
            "u_height": dt.get("u_height", 0),
            "is_full_depth": dt.get("is_full_depth", False),
            "comments": dt.get("comments", ""),
        }
        obj = nb._request("POST", "/api/dcim/device-types/", body=payload)
        print(f"  created: {dt['slug']}")
        nb._cache.clear()
        ids[dt["slug"]] = int(obj["id"])
    return ids


def seed_platforms(nb: NetBoxClient, data: dict[str, Any]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for platform in data.get("platforms", []):
        existing = nb.find_one("/api/dcim/platforms/", slug=platform["slug"])
        if not existing:
            existing = nb.find_one("/api/dcim/platforms/", name=platform["name"])
        if existing:
            print(f"  exists: {platform['name']}")
            ids[platform["slug"]] = int(existing["id"])
            continue
        obj = nb._request(
            "POST",
            "/api/dcim/platforms/",
            body={"name": platform["name"], "slug": platform["slug"]},
        )
        print(f"  created: {platform['name']}")
        nb._cache.clear()
        ids[platform["slug"]] = int(obj["id"])
    return ids


def seed_rir(nb: NetBoxClient, data: dict[str, Any]) -> int | None:
    rir = data.get("rir")
    if not rir:
        return None
    payload: dict[str, Any] = {
        "name": rir["name"],
        "slug": rir["slug"],
        "is_private": rir.get("is_private", True),
    }
    obj = nb.ensure("/api/ipam/rirs/", {"slug": rir["slug"]}, payload)
    return int(obj["id"])


def seed_aggregates(
    nb: NetBoxClient,
    data: dict[str, Any],
    rir_id: int | None,
) -> None:
    if not rir_id:
        return
    for aggregate in data.get("aggregates", []):
        cidr = aggregate["prefix"]
        payload: dict[str, Any] = {
            "prefix": cidr,
            "rir": rir_id,
            "description": aggregate.get("description", ""),
        }
        nb.ensure(
            "/api/ipam/aggregates/",
            {"prefix": cidr},
            payload,
            label=f"aggregate:{cidr}",
        )


def seed_vlan_groups(nb: NetBoxClient, data: dict[str, Any]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for group in data.get("vlan_groups", []):
        payload: dict[str, Any] = {
            "name": group["name"],
            "slug": group["slug"],
            "description": group.get("description", ""),
        }
        obj = nb.ensure(
            "/api/ipam/vlan-groups/",
            {"slug": group["slug"]},
            payload,
            label=f"vlan-group:{group['slug']}",
        )
        ids[group["slug"]] = int(obj["id"])
    return ids


def seed_vrfs(nb: NetBoxClient, data: dict[str, Any]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for vrf in data.get("vrfs", []):
        payload: dict[str, Any] = {
            "name": vrf["name"],
            "slug": vrf["slug"],
            "description": vrf.get("description", ""),
        }
        obj = nb.ensure(
            "/api/ipam/vrfs/",
            {"slug": vrf["slug"]},
            payload,
            label=f"vrf:{vrf['slug']}",
        )
        ids[vrf["slug"]] = int(obj["id"])
    return ids


def seed_asns(
    nb: NetBoxClient,
    data: dict[str, Any],
    rir_id: int | None,
    site_id: int,
) -> dict[int, int]:
    ids: dict[int, int] = {}
    if not rir_id:
        return ids
    for entry in data.get("asns", []):
        asn = int(entry["asn"])
        payload: dict[str, Any] = {
            "asn": asn,
            "rir": rir_id,
            "site": site_id,
            "description": entry.get("description", ""),
        }
        obj = nb.ensure(
            "/api/ipam/asns/",
            {"asn": asn},
            payload,
            label=f"asn:{asn}",
        )
        ids[asn] = int(obj["id"])
    return ids


def find_prefix(nb: NetBoxClient, cidr: str, vrf_id: int | None) -> dict[str, Any] | None:
    params: dict[str, Any] = {"prefix": cidr}
    if vrf_id is not None:
        params["vrf_id"] = vrf_id
    else:
        params["vrf_id"] = "null"
    return nb.find_one("/api/ipam/prefixes/", **params)


def seed_vlans(
    nb: NetBoxClient,
    data: dict[str, Any],
    site_id: int,
    vlan_group_ids: dict[str, int],
) -> dict[str, int]:
    ids: dict[str, int] = {}
    for vlan in data.get("vlans", []):
        if is_missing(vlan.get("vid")):
            print("  skip vlan (no vid configured)")
            continue
        vid = int(vlan["vid"])
        payload: dict[str, Any] = {
            "vid": vid,
            "name": vlan["name"],
            "status": vlan.get("status", "active"),
            "site": site_id,
            "description": vlan.get("description", ""),
        }
        group_slug = vlan.get("vlan_group")
        if group_slug and group_slug in vlan_group_ids:
            payload["group"] = vlan_group_ids[group_slug]
        obj = nb.ensure("/api/ipam/vlans/", {"vid": vid, "site_id": site_id}, payload)
        slug = vlan.get("slug") or f"vlan{vid}"
        ids[slug] = int(obj["id"])
        if not nb.dry_run and int(obj.get("id", 0)):
            updates: dict[str, Any] = {}
            if group_slug and group_slug in vlan_group_ids:
                current_group = obj.get("group")
                current_group_id = current_group.get("id") if isinstance(current_group, dict) else current_group
                if current_group_id != vlan_group_ids[group_slug]:
                    updates["group"] = vlan_group_ids[group_slug]
            if updates:
                nb._request("PATCH", f"/api/ipam/vlans/{obj['id']}/", body=updates)
                print(f"  updated vlan:{slug}")
    return ids


def seed_prefixes(
    nb: NetBoxClient,
    data: dict[str, Any],
    site_id: int,
    vlan_ids: dict[str, int],
    vrf_ids: dict[str, int],
) -> dict[str, int]:
    ids: dict[str, int] = {}
    for prefix in data.get("prefixes", []):
        cidr = prefix["prefix"]
        vrf_slug = prefix.get("vrf")
        vrf_id = vrf_ids.get(vrf_slug) if vrf_slug else None
        payload: dict[str, Any] = {
            "prefix": cidr,
            "status": prefix.get("status", "active"),
            "site": site_id,
            "description": prefix.get("description", ""),
        }
        if vrf_id is not None:
            payload["vrf"] = vrf_id
        vlan_slug = prefix.get("vlan")
        if vlan_slug and vlan_slug in vlan_ids:
            payload["vlan"] = vlan_ids[vlan_slug]
        existing = find_prefix(nb, cidr, vrf_id)
        if not existing and vrf_id is not None:
            existing = find_prefix(nb, cidr, None)
        if existing:
            print(f"  exists: prefix:{cidr}")
            ids[cidr] = int(existing["id"])
            if not nb.dry_run:
                updates: dict[str, Any] = {}
                current_vrf = existing.get("vrf")
                current_vrf_id = current_vrf.get("id") if isinstance(current_vrf, dict) else current_vrf
                if vrf_id != current_vrf_id:
                    updates["vrf"] = vrf_id
                if vlan_slug and vlan_slug in vlan_ids:
                    current_vlan = existing.get("vlan")
                    current_vlan_id = current_vlan.get("id") if isinstance(current_vlan, dict) else current_vlan
                    if vlan_ids[vlan_slug] != current_vlan_id:
                        updates["vlan"] = vlan_ids[vlan_slug]
                elif existing.get("vlan") and not vlan_slug:
                    updates["vlan"] = None
                if existing.get("description") != payload["description"] and payload["description"]:
                    updates["description"] = payload["description"]
                if updates:
                    nb._request("PATCH", f"/api/ipam/prefixes/{existing['id']}/", body=updates)
                    print(f"  updated prefix:{cidr}")
            continue
        if nb.dry_run:
            print(f"DRY-RUN POST /api/ipam/prefixes/: {json.dumps(payload)[:300]}")
            ids[cidr] = 0
            continue
        obj = nb._request("POST", "/api/ipam/prefixes/", body=payload)
        print(f"  created: prefix:{cidr}")
        nb._cache.clear()
        ids[cidr] = int(obj["id"])
    return ids


def seed_ip_ranges(
    nb: NetBoxClient,
    data: dict[str, Any],
    vrf_ids: dict[str, int],
) -> None:
    for ip_range in data.get("ip_ranges", []):
        start = ip_range["start"]
        end = ip_range["end"]
        if "/" not in start:
            start = f"{start}/32"
        if "/" not in end:
            end = f"{end}/32"
        payload: dict[str, Any] = {
            "start_address": start,
            "end_address": end,
            "status": ip_range.get("status", "active"),
            "description": ip_range.get("description", ""),
        }
        vrf_slug = ip_range.get("vrf")
        if vrf_slug and vrf_slug in vrf_ids:
            payload["vrf"] = vrf_ids[vrf_slug]
        nb.ensure(
            "/api/ipam/ip-ranges/",
            {"start_address": start, "end_address": end},
            payload,
            label=f"ip-range:{start}-{end}",
        )


def seed_vcf_ips(
    nb: NetBoxClient,
    data: dict[str, Any],
    vrf_ids: dict[str, int],
) -> None:
    vrf_id = vrf_ids.get("vcf-nested")
    for entry in data.get("vcf_management_ips", []):
        address = entry["address"]
        payload: dict[str, Any] = {
            "address": address,
            "status": "active",
            "description": entry.get("description", ""),
        }
        if vrf_id is not None:
            payload["vrf"] = vrf_id
        if entry.get("dns_name"):
            payload["dns_name"] = entry["dns_name"]
        nb.ensure(
            "/api/ipam/ip-addresses/",
            {"address": address},
            payload,
            label=f"VCF IP {address}",
        )


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
    print("Rack types…")
    seed_rack_types(nb, data, mfr_ids)
    print("Module types…")
    seed_module_types(nb, data, mfr_ids)
    print("Circuit types…")
    seed_slug_named_types(nb, data, "circuit_types", "/api/circuits/circuit-types/")
    print("Virtual circuit types…")
    seed_slug_named_types(
        nb,
        data,
        "virtual_circuit_types",
        "/api/circuits/virtual-circuit-types/",
    )
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
    print("RIR…")
    rir_id = seed_rir(nb, data)
    print("Aggregates…")
    seed_aggregates(nb, data, rir_id)
    print("VLAN groups…")
    vlan_group_ids = seed_vlan_groups(nb, data)
    print("VRFs…")
    vrf_ids = seed_vrfs(nb, data)
    print("ASNs…")
    seed_asns(nb, data, rir_id, site_id)
    print("VLANs…")
    vlan_ids = seed_vlans(nb, data, site_id, vlan_group_ids)
    print("Prefixes…")
    seed_prefixes(nb, data, site_id, vlan_ids, vrf_ids)
    print("IP ranges…")
    seed_ip_ranges(nb, data, vrf_ids)
    print("Clusters…")
    cluster_ids = seed_clusters(nb, data, site_id)
    print("Devices…")
    seed_devices(nb, data, site_id, dt_ids, role_ids)
    print("Virtual machines…")
    seed_vms(nb, data, cluster_ids, role_ids, platform_ids)
    print("Service IPs…")
    seed_service_ips(nb, data)
    print("VCF management IPs…")
    seed_vcf_ips(nb, data, vrf_ids)
    print("DNS records…")
    seed_dns(nb, data)
    print("Done.")


if __name__ == "__main__":
    main()
