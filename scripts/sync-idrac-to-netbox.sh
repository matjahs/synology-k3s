#!/usr/bin/env bash
# Sync Dell iDRAC Redfish inventory into NetBox DCIM components.
#
# Usage:
#   export NETBOX_TOKEN=$(vault kv get -field=token secret/netbox/api)
#   export IDRAC_PASS='…'
#   ./scripts/sync-idrac-to-netbox.sh --device esx --apply
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/sync-idrac-to-netbox.py" "$@"
