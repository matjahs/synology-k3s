#!/usr/bin/env bash
# Create lab.mxe11.nl zone in NetBox DNS via API (run after NetBox DNS plugin is active).
set -euo pipefail

NETBOX_URL="${NETBOX_URL:-https://netbox.lab.mxe11.nl}"
NETBOX_URL="${NETBOX_URL%/}"
NETBOX_TOKEN="${NETBOX_TOKEN:?set NETBOX_TOKEN}"

ZONE="${1:-lab.mxe11.nl}"
# Primary NS hostname for SOA MNAME + zone nameservers list (UniFi serves lab.mxe11.nl).
SOA_MNAME="${DNS_SOA_MNAME:-ns1.${ZONE}}"
# RFC 1035 admin contact (dots, not @): hostmaster.lab.mxe11.nl -> hostmaster@lab.mxe11.nl
SOA_RNAME="${DNS_SOA_RNAME:-hostmaster.${ZONE}}"

nb_api() {
  local method=$1
  local path=$2
  shift 2
  local body_file http
  body_file=$(mktemp)
  http=$(curl -sS -w "%{http_code}" -o "${body_file}" -X "${method}" \
    -H "Authorization: Token ${NETBOX_TOKEN}" \
    -H "Accept: application/json" \
    "$@" \
    "${NETBOX_URL}${path}")
  if [[ "${http}" -ge 400 ]]; then
    echo "NetBox API ${method} ${path} failed (HTTP ${http}):" >&2
    cat "${body_file}" >&2
    rm -f "${body_file}"
    return 1
  fi
  cat "${body_file}"
  rm -f "${body_file}"
}

count_for() {
  local path=$1
  nb_api GET "${path}" | python3 -c "import json,sys; print(json.load(sys.stdin)['count'])"
}

ensure_nameserver() {
  local name=$1
  if [[ "$(count_for "/api/plugins/netbox-dns/nameservers/?name=${name}")" != "0" ]]; then
    echo "Nameserver ${name} already exists"
    return 0
  fi
  nb_api POST "/api/plugins/netbox-dns/nameservers/" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${name}\"}" >/dev/null
  echo "Created nameserver ${name}"
}

if [[ "$(count_for "/api/plugins/netbox-dns/zones/?name=${ZONE}")" != "0" ]]; then
  echo "Zone ${ZONE} already exists"
  exit 0
fi

ensure_nameserver "${SOA_MNAME}"

payload=$(SOA_MNAME="${SOA_MNAME}" SOA_RNAME="${SOA_RNAME}" ZONE="${ZONE}" python3 - <<'PY'
import json, os
print(json.dumps({
    "name": os.environ["ZONE"],
    "status": "active",
    "soa_mname": {"name": os.environ["SOA_MNAME"]},
    "soa_rname": os.environ["SOA_RNAME"],
    "nameservers": [{"name": os.environ["SOA_MNAME"]}],
}))
PY
)

nb_api POST "/api/plugins/netbox-dns/zones/" \
  -H "Content-Type: application/json" \
  -d "${payload}" >/dev/null

echo "Created zone ${ZONE} (SOA MNAME=${SOA_MNAME}, RNAME=${SOA_RNAME})"
