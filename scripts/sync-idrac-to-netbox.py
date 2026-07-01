#!/usr/bin/env python3
"""Sync Dell iDRAC Redfish hardware inventory into NetBox DCIM components.

Creates/updates on the target device:
  - Physical interfaces + MAC address objects (NetBox 4.4+)
  - Power ports PS1/PS2 with PSU inventory items
  - CPU and DIMM inventory items (per socket/slot)
  - Storage controllers and disks (per bay/slot)
  - BIOS firmware inventory item

Idempotent. Dry-run by default; pass --apply to write.

Environment:
  NETBOX_TOKEN (required unless --dry-run)
  NETBOX_URL   (default https://netbox.lab.mxe11.nl)
  IDRAC_URL    (default https://172.16.30.10)
  IDRAC_USER   (default root)
  IDRAC_PASS   (required for live iDRAC fetch)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

NETBOX_URL = os.environ.get("NETBOX_URL", "https://netbox.lab.mxe11.nl").rstrip("/")
NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN", "")
IDRAC_URL = os.environ.get("IDRAC_URL", "https://172.16.30.10").rstrip("/")
IDRAC_USER = os.environ.get("IDRAC_USER", "root")
IDRAC_PASS = os.environ.get("IDRAC_PASS", "")

INVENTORY_ROLES: list[tuple[str, str]] = [
    ("cpu", "CPU"),
    ("memory", "Memory"),
    ("psu", "Power Supply"),
    ("firmware", "Firmware"),
    ("storage-controller", "Storage Controller"),
    ("disk", "Disk"),
]

# Physical NICs: name, interface type, MAC, link up
NICS: list[tuple[str, str, str, bool]] = [
    ("NIC.Integrated.1-1-1", "1000base-t", "B4:83:51:06:AF:A8", True),
    ("NIC.Integrated.1-2-1", "1000base-t", "B4:83:51:06:AF:A9", False),
    ("NIC.Slot.1-1-1", "10gbase-x-sfpp", "F8:F2:1E:2B:C2:58", False),
    ("NIC.Slot.1-2-1", "10gbase-x-sfpp", "F8:F2:1E:2B:C2:59", True),
    ("NIC.Embedded.1-1-1", "1000base-t", "CC:96:E5:F6:2C:48", False),
    ("NIC.Embedded.2-1-1", "1000base-t", "CC:96:E5:F6:2C:49", False),
]
IDRAC_MAC = "CC:96:E5:F6:2C:42"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def controller_display_name(ctrl_id: str, name: str, description: str) -> str:
    if name == "PCIe Extender":
        match = re.search(r"Slot (\d+)", description)
        if match:
            return f"PCIe SSD Slot {match.group(1)}"
    if ctrl_id.startswith("PCIeSSD.Slot."):
        match = re.search(r"Slot\.(\d+)", ctrl_id)
        if match:
            return f"PCIe SSD Slot {match.group(1)}"
    return name


def drive_inventory_name(controller_name: str, redfish_name: str, location: dict[str, Any]) -> str:
    slot = location.get("ServiceLabel") or location.get("LocationOrdinalValue")
    if controller_name == "Dell HBA355i Fnt" and slot is not None:
        return f"HBA355i Bay {slot}"
    if controller_name == "BOSS-S2":
        return "BOSS-S2 SSD"
    if controller_name.startswith("PCIe SSD Slot"):
        disk = redfish_name.replace("PCIe SSD in ", "")
        disk = re.sub(r"^Slot \d+ ", "", disk)
        return f"{controller_name} {disk}"
    return redfish_name


def normalize_drive_manufacturer(manufacturer: str, model: str) -> str:
    raw = (manufacturer or "").strip()
    aliases = {
        "ATA": "",
        "SKhynix": "SK Hynix",
        "MICRON": "Micron Technology",
        "Samsung Electronics Co Ltd": "Samsung",
        "Kingston Technology Company  Inc.": "Kingston",
        "Kingston Technology Company  Inc. ": "Kingston",
        "MAXIO Technology (Hangzhou) Ltd.": "Lexar",
        "MAXIO Technology (Hangzhou) Ltd. ": "Lexar",
    }
    if raw in aliases:
        mapped = aliases[raw]
        if mapped:
            return mapped
        first = (model or "").split()
        return first[0] if first else "Unknown"
    return raw or "Unknown"


class NetBoxClient:
    def __init__(self, dry_run: bool) -> None:
        if not NETBOX_TOKEN and not dry_run:
            raise SystemExit("NETBOX_TOKEN is required (unless --dry-run)")
        self.dry_run = dry_run
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{NETBOX_URL}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Token {NETBOX_TOKEN}", "Accept": "application/json"}
        if data:
            headers["Content-Type"] = "application/json"
        if self.dry_run and method != "GET":
            label = body.get("name") if body else path
            print(f"  DRY-RUN {method} {path}: {label}")
            return {"id": 0, **(body or {})}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=CTX, timeout=60) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            raise RuntimeError(f"{method} {path} failed ({exc.code}): {detail}") from exc

    def list_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        key = f"{path}?{params}"
        if key in self._cache:
            return self._cache[key]
        results: list[dict[str, Any]] = []
        offset = 0
        base = dict(params or {})
        while True:
            page = {**base, "limit": 200, "offset": offset}
            payload = self.request("GET", f"{path}?{urllib.parse.urlencode(page)}")
            results.extend(payload.get("results", []))
            if not payload.get("next"):
                break
            offset += 200
        self._cache[key] = results
        return results

    def find_one(self, path: str, **lookup: Any) -> dict[str, Any] | None:
        items = self.list_all(path, lookup)
        return items[0] if items else None

    def ensure(
        self,
        path: str,
        lookup: dict[str, Any],
        payload: dict[str, Any],
        *,
        label: str | None = None,
    ) -> dict[str, Any]:
        existing = self.find_one(path, **lookup)
        name = label or payload.get("name") or payload.get("slug") or str(lookup)
        if existing:
            print(f"  exists: {name}")
            return existing
        created = self.request("POST", path, payload)
        print(f"  created: {name}")
        self._cache.clear()
        return created


class IdracClient:
    def __init__(self) -> None:
        if not IDRAC_PASS:
            raise SystemExit("IDRAC_PASS is required for live iDRAC fetch")

    def get(self, path: str) -> dict[str, Any]:
        url = f"{IDRAC_URL}/redfish/v1{path}"
        auth = f"{IDRAC_USER}:{IDRAC_PASS}"
        result = subprocess.run(
            ["curl", "-sS", "--insecure", "-u", auth, "--connect-timeout", "15", url],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"iDRAC GET {path} failed: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def collect(self) -> dict[str, Any]:
        system = self.get("/Systems/System.Embedded.1")
        power = self.get("/Chassis/System.Embedded.1/Power")
        cpus = [
            self.get(member["@odata.id"].split("/redfish/v1", 1)[1])
            for member in self.get("/Systems/System.Embedded.1/Processors").get("Members", [])
        ]
        dimms = []
        for member in self.get("/Systems/System.Embedded.1/Memory").get("Members", []):
            dimm = self.get(member["@odata.id"].split("/redfish/v1", 1)[1])
            if dimm.get("CapacityMiB"):
                dimms.append(dimm)
        storage = self.collect_storage()
        return {"system": system, "power": power, "cpus": cpus, "dimms": dimms, "storage": storage}

    def collect_storage(self) -> dict[str, Any]:
        controllers: list[dict[str, Any]] = []
        drives: list[dict[str, Any]] = []
        for member in self.get("/Systems/System.Embedded.1/Storage").get("Members", []):
            path = member["@odata.id"].split("/redfish/v1", 1)[1]
            ctrl = self.get(path)
            ctrl_id = ctrl.get("Id") or path.rsplit("/", 1)[-1]
            ctrl_name = controller_display_name(ctrl_id, ctrl.get("Name") or "", ctrl.get("Description") or "")
            controllers.append(
                {
                    "id": ctrl_id,
                    "name": ctrl_name,
                    "description": ctrl.get("Description") or "",
                }
            )
            for drive_ref in ctrl.get("Drives", []) or []:
                if not isinstance(drive_ref, dict) or "@odata.id" not in drive_ref:
                    continue
                dr = self.get(drive_ref["@odata.id"].split("/redfish/v1", 1)[1])
                cap = dr.get("CapacityBytes")
                loc = dr.get("PhysicalLocation", {}).get("PartLocation", {})
                model = (dr.get("Model") or "").strip()
                drives.append(
                    {
                        "controller_id": ctrl_id,
                        "controller": ctrl_name,
                        "name": drive_inventory_name(ctrl_name, dr.get("Name") or dr.get("Id") or "Disk", loc),
                        "model": model,
                        "manufacturer": normalize_drive_manufacturer(dr.get("Manufacturer") or "", model),
                        "media": dr.get("MediaType") or "SSD",
                        "protocol": dr.get("Protocol") or "",
                        "cap_gb": round(cap / 1e9, 1) if cap else None,
                        "serial": (dr.get("SerialNumber") or "").strip(),
                        "part": (dr.get("PartNumber") or "").strip(),
                        "slot": loc.get("ServiceLabel") or loc.get("LocationOrdinalValue"),
                        "health": (dr.get("Status") or {}).get("Health"),
                    }
                )
        return {"controllers": controllers, "drives": drives}


def ensure_inventory_roles(nb: NetBoxClient) -> dict[str, int]:
    ids: dict[str, int] = {}
    for slug, name in INVENTORY_ROLES:
        obj = nb.ensure(
            "/api/dcim/inventory-item-roles/",
            {"slug": slug},
            {"name": name, "slug": slug},
        )
        ids[slug] = int(obj["id"])
    return ids


def ensure_manufacturer(nb: NetBoxClient, name: str) -> int:
    slug = slugify(name)
    obj = nb.ensure(
        "/api/dcim/manufacturers/",
        {"slug": slug},
        {"name": name, "slug": slug},
    )
    return int(obj["id"])


def find_device(nb: NetBoxClient, name: str) -> dict[str, Any]:
    device = nb.find_one("/api/dcim/devices/", name=name)
    if not device:
        raise SystemExit(f"NetBox device {name!r} not found")
    return device


def ensure_inventory_item(
    nb: NetBoxClient,
    *,
    device_id: int,
    name: str,
    role_id: int | None = None,
    manufacturer_id: int | None = None,
    part_id: str = "",
    serial: str = "",
    description: str = "",
    label: str = "",
    parent_id: int | None = None,
    component_type: str | None = None,
    component_id: int | None = None,
) -> dict[str, Any]:
    existing = nb.find_one("/api/dcim/inventory-items/", device_id=device_id, name=name)
    payload: dict[str, Any] = {
        "device": device_id,
        "name": name,
        "status": "active",
    }
    if parent_id is not None:
        payload["parent"] = parent_id
    if role_id is not None:
        payload["role"] = role_id
    if manufacturer_id is not None:
        payload["manufacturer"] = manufacturer_id
    if part_id:
        payload["part_id"] = part_id
    if serial:
        payload["serial"] = serial
    if description:
        payload["description"] = description
    if label:
        payload["label"] = label
    if component_type and component_id:
        payload["component_type"] = component_type
        payload["component_id"] = component_id
    if existing:
        print(f"  exists: inventory {name}")
        if not nb.dry_run:
            nb.request("PATCH", f"/api/dcim/inventory-items/{existing['id']}/", payload)
        return existing
    return nb.ensure("/api/dcim/inventory-items/", {"device_id": device_id, "name": name}, payload)


def sync_interfaces(nb: NetBoxClient, device_id: int) -> dict[str, dict[str, Any]]:
    ifaces = {
        i["name"]: i
        for i in nb.list_all("/api/dcim/interfaces/", {"device_id": device_id})
    }
    if "vmk2" in ifaces:
        print("[iface] delete placeholder vmk2")
        if not nb.dry_run:
            nb.request("DELETE", f"/api/dcim/interfaces/{ifaces['vmk2']['id']}/")
        del ifaces["vmk2"]

    first_name, first_type, first_mac, first_up = NICS[0]
    if "mgmt" in ifaces and first_name not in ifaces:
        print(f"[iface] rename mgt -> {first_name}")
        if not nb.dry_run:
            nb.request(
                "PATCH",
                f"/api/dcim/interfaces/{ifaces['mgmt']['id']}/",
                {"name": first_name, "type": first_type, "enabled": first_up},
            )
        ifaces[first_name] = ifaces.pop("mgmt")

    if "idrac" in ifaces:
        print("[iface] patch idrac (mgmt_only)")
        if not nb.dry_run:
            nb.request(
                "PATCH",
                f"/api/dcim/interfaces/{ifaces['idrac']['id']}/",
                {"mgmt_only": True, "type": "1000base-t", "enabled": True},
            )

    for name, typ, _mac, up in NICS:
        if name in ifaces:
            if not nb.dry_run:
                nb.request(
                    "PATCH",
                    f"/api/dcim/interfaces/{ifaces[name]['id']}/",
                    {"type": typ, "enabled": up},
                )
        else:
            obj = nb.ensure(
                "/api/dcim/interfaces/",
                {"device_id": device_id, "name": name},
                {"device": device_id, "name": name, "type": typ, "enabled": up},
            )
            ifaces[name] = obj

    nb._cache.clear()
    return {
        i["name"]: i
        for i in nb.list_all("/api/dcim/interfaces/", {"device_id": device_id})
    }


def sync_mac_addresses(nb: NetBoxClient, ifaces: dict[str, dict[str, Any]]) -> None:
    mac_map = {name: mac for name, _typ, mac, _up in NICS}
    mac_map["idrac"] = IDRAC_MAC
    for name, mac in mac_map.items():
        if name not in ifaces:
            continue
        iface_id = int(ifaces[name]["id"])
        existing = None
        for item in nb.list_all("/api/dcim/mac-addresses/", {"mac_address": mac}):
            if item.get("mac_address", "").upper() == mac.upper():
                existing = item
                break
        if existing:
            mac_id = int(existing["id"])
            print(f"  exists: MAC {mac} on {name}")
        else:
            obj = nb.ensure(
                "/api/dcim/mac-addresses/",
                {"mac_address": mac},
                {
                    "mac_address": mac,
                    "assigned_object_type": "dcim.interface",
                    "assigned_object_id": iface_id,
                },
                label=f"MAC {mac} -> {name}",
            )
            mac_id = int(obj["id"])
        if not nb.dry_run:
            nb.request("PATCH", f"/api/dcim/interfaces/{iface_id}/", {"primary_mac_address": mac_id})


def sync_power_and_psus(
    nb: NetBoxClient,
    device_id: int,
    power: dict[str, Any],
    role_ids: dict[str, int],
) -> None:
    dell_id = ensure_manufacturer(nb, "Dell")
    for psu in power.get("PowerSupplies", []):
        bay = "PS1" if "1" in (psu.get("Name") or "") else "PS2"
        port = nb.ensure(
            "/api/dcim/power-ports/",
            {"device_id": device_id, "name": bay},
            {
                "device": device_id,
                "name": bay,
                "type": "iec-60320-c14",
                "maximum_draw": psu.get("PowerCapacityWatts") or 1400,
                "description": psu.get("Model") or "",
            },
        )
        port_id = int(port["id"])
        ensure_inventory_item(
            nb,
            device_id=device_id,
            name=f"{bay} PSU",
            role_id=role_ids["psu"],
            manufacturer_id=dell_id,
            part_id=(psu.get("Model") or "").strip(),
            serial=(psu.get("SerialNumber") or "").strip(),
            description=f"{psu.get('PowerCapacityWatts')}W redundant PSU",
            component_type="dcim.powerport",
            component_id=port_id,
        )


def sync_cpus(nb: NetBoxClient, device_id: int, cpus: list[dict[str, Any]], role_ids: dict[str, int]) -> None:
    intel_id = ensure_manufacturer(nb, "Intel")
    for cpu in cpus:
        socket = (cpu.get("Socket") or cpu.get("Id") or "CPU").replace("CPU.", "")
        name = socket if socket.startswith("CPU") else f"CPU {socket}"
        model = cpu.get("Model") or "CPU"
        cores = cpu.get("TotalCores")
        threads = cpu.get("TotalThreads")
        speed = cpu.get("MaxSpeedMHz")
        desc = f"{cores} cores / {threads} threads @ {speed} MHz" if cores else model
        ensure_inventory_item(
            nb,
            device_id=device_id,
            name=name,
            role_id=role_ids["cpu"],
            manufacturer_id=intel_id,
            part_id=model,
            serial=(cpu.get("SerialNumber") or "").strip(),
            description=desc,
            label=socket,
        )


def sync_dimms(nb: NetBoxClient, device_id: int, dimms: list[dict[str, Any]], role_ids: dict[str, int]) -> None:
    mfr_cache: dict[str, int] = {}
    for dimm in sorted(dimms, key=lambda d: d.get("DeviceLocator", "")):
        locator = dimm.get("DeviceLocator") or dimm.get("Id") or "DIMM"
        mfr_name = (dimm.get("Manufacturer") or "Unknown").strip()
        if mfr_name not in mfr_cache:
            mfr_cache[mfr_name] = ensure_manufacturer(nb, mfr_name)
        cap_gib = (dimm.get("CapacityMiB") or 0) // 1024
        speed = dimm.get("OperatingSpeedMhz")
        mem_type = dimm.get("MemoryDeviceType") or "DDR4"
        desc = f"{cap_gib} GiB {mem_type}"
        if speed:
            desc += f" @ {speed} MHz"
        ensure_inventory_item(
            nb,
            device_id=device_id,
            name=locator,
            role_id=role_ids["memory"],
            manufacturer_id=mfr_cache[mfr_name],
            part_id=(dimm.get("PartNumber") or "").strip(),
            serial=(dimm.get("SerialNumber") or "").strip(),
            description=desc,
            label=locator,
        )


def sync_storage(
    nb: NetBoxClient,
    device_id: int,
    storage: dict[str, Any],
    role_ids: dict[str, int],
) -> None:
    dell_id = ensure_manufacturer(nb, "Dell")
    controller_ids: dict[str, int] = {}
    for ctrl in storage.get("controllers", []):
        desc = ctrl.get("description") or ""
        obj = ensure_inventory_item(
            nb,
            device_id=device_id,
            name=ctrl["name"],
            role_id=role_ids["storage-controller"],
            manufacturer_id=dell_id,
            part_id=ctrl.get("id") or "",
            description=desc,
            label=ctrl.get("id") or "",
        )
        controller_ids[ctrl["id"]] = int(obj["id"])

    mfr_cache: dict[str, int] = {"Dell": dell_id}
    for drive in sorted(storage.get("drives", []), key=lambda d: d.get("name", "")):
        mfr_name = drive.get("manufacturer") or "Unknown"
        if mfr_name not in mfr_cache:
            mfr_cache[mfr_name] = ensure_manufacturer(nb, mfr_name)
        cap = drive.get("cap_gb")
        media = drive.get("media") or "SSD"
        protocol = drive.get("protocol") or ""
        desc_parts = [media]
        if cap:
            desc_parts.append(f"{cap} GB")
        if protocol:
            desc_parts.append(protocol)
        health = drive.get("health")
        if health:
            desc_parts.append(f"health {health}")
        parent_id = controller_ids.get(drive.get("controller_id", ""))
        ensure_inventory_item(
            nb,
            device_id=device_id,
            name=drive["name"],
            role_id=role_ids["disk"],
            manufacturer_id=mfr_cache[mfr_name],
            part_id=drive.get("model") or "",
            serial=drive.get("serial") or "",
            description=", ".join(desc_parts),
            label=str(drive.get("slot") or ""),
            parent_id=parent_id,
        )


def sync_firmware(nb: NetBoxClient, device_id: int, bios_version: str, role_ids: dict[str, int]) -> None:
    dell_id = ensure_manufacturer(nb, "Dell")
    ensure_inventory_item(
        nb,
        device_id=device_id,
        name=f"BIOS {bios_version}",
        role_id=role_ids["firmware"],
        manufacturer_id=dell_id,
        part_id="BIOS",
        description="System firmware reported by iDRAC Redfish",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync iDRAC hardware inventory into NetBox")
    parser.add_argument("--device", default="esx", help="NetBox device name (default: esx)")
    parser.add_argument("--apply", action="store_true", help="Write to NetBox (default: dry-run)")
    args = parser.parse_args()

    nb = NetBoxClient(dry_run=not args.apply)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== {mode}: iDRAC -> NetBox device {args.device!r} ===")

    print("Collecting iDRAC inventory…")
    inventory = IdracClient().collect()
    system = inventory["system"]

    device = find_device(nb, args.device)
    device_id = int(device["id"])
    service_tag = (system.get("SKU") or system.get("SerialNumber") or "").strip()
    bios = (system.get("BiosVersion") or "").strip()

    print("[device] update serial/asset_tag; clear comments blob")
    nb.request(
        "PATCH",
        f"/api/dcim/devices/{device_id}/",
        {
            "serial": service_tag,
            "asset_tag": service_tag,
            "comments": f"Hardware components synced from iDRAC ({IDRAC_URL}).",
        },
    )

    print("Inventory item roles…")
    role_ids = ensure_inventory_roles(nb)

    print("Interfaces…")
    ifaces = sync_interfaces(nb, device_id)

    print("MAC addresses…")
    sync_mac_addresses(nb, ifaces)

    print("Power ports + PSUs…")
    sync_power_and_psus(nb, device_id, inventory["power"], role_ids)

    print("CPUs…")
    sync_cpus(nb, device_id, inventory["cpus"], role_ids)

    print("Memory modules…")
    sync_dimms(nb, device_id, inventory["dimms"], role_ids)

    print("Storage controllers + disks…")
    sync_storage(nb, device_id, inventory["storage"], role_ids)

    if bios:
        print("Firmware…")
        sync_firmware(nb, device_id, bios, role_ids)

    print(f"=== {mode} complete ===")


if __name__ == "__main__":
    main()
