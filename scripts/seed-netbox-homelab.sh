#!/usr/bin/env bash
# Seed NetBox with homelab DCIM/IPAM/virtualization/DNS data.
#
# Prerequisites:
#   - NetBox running with API token (write)
#   - DNS zone lab.mxe11.nl (scripts/bootstrap-netbox-dns-zone.sh)
#
# Usage:
#   export NETBOX_TOKEN=$(vault kv get -field=token secret/netbox/api)
#   ./scripts/seed-netbox-homelab.sh              # apply
#   ./scripts/seed-netbox-homelab.sh --import-community-device-types
#   ./scripts/seed-netbox-homelab.sh --check      # validate data file only
#   ./scripts/seed-netbox-homelab.sh --dry-run    # print API actions
#   ./scripts/seed-netbox-homelab.sh --force      # skip objects with null IPs
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_FILE="${NETBOX_DATA_FILE:-${SCRIPT_DIR}/netbox-homelab-data.yaml}"
VENV_DIR="${SCRIPT_DIR}/.netbox-seed-venv"

python_with_yaml() {
  if python3 -c "import yaml" 2>/dev/null; then
    echo python3
    return
  fi
  if [[ ! -d "${VENV_DIR}" ]]; then
    echo "Creating local venv for PyYAML at ${VENV_DIR}…" >&2
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install -q pyyaml
  fi
  echo "${VENV_DIR}/bin/python"
}

PY="$(python_with_yaml)"
exec "${PY}" "${SCRIPT_DIR}/seed-netbox-homelab.py" --data "${DATA_FILE}" "$@"
