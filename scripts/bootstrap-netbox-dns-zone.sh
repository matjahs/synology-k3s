#!/usr/bin/env bash
# Create lab.mxe11.nl zone in NetBox DNS via API (run after NetBox DNS plugin is active).
set -euo pipefail

NETBOX_URL="${NETBOX_URL:-https://netbox.lab.mxe11.nl}"
NETBOX_TOKEN="${NETBOX_TOKEN:?set NETBOX_TOKEN}"

ZONE="${1:-lab.mxe11.nl}"

existing="$(curl -fsS -H "Authorization: Token ${NETBOX_TOKEN}" \
  -H "Accept: application/json" \
  "${NETBOX_URL}/api/plugins/netbox-dns/zones/?name=${ZONE}")"

if echo "${existing}" | grep -q '"count": [1-9]'; then
  echo "Zone ${ZONE} already exists"
  exit 0
fi

curl -fsS -X POST -H "Authorization: Token ${NETBOX_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  "${NETBOX_URL}/api/plugins/netbox-dns/zones/" \
  -d "{\"name\":\"${ZONE}\",\"status\":\"active\",\"type\":\"authoritative\"}"

echo "Created zone ${ZONE}"
