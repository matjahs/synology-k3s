#!/usr/bin/env bash
# Compare HTTPRoute hostnames (lab.mxe11.nl) with UniFi DNS answers.
set -euo pipefail

DNS_SERVER="${DNS_SERVER:-172.16.0.1}"
ZONE="${ZONE:-lab.mxe11.nl}"
GATEWAY_NS="${GATEWAY_NS:-kube-system}"
GATEWAY_NAME="${GATEWAY_NAME:-external-gateway}"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl required" >&2
  exit 1
fi
if ! command -v dig >/dev/null 2>&1; then
  echo "dig required" >&2
  exit 1
fi

gateway_ip="$(kubectl get gateway -n "${GATEWAY_NS}" "${GATEWAY_NAME}" \
  -o jsonpath='{.status.addresses[0].value}' 2>/dev/null || true)"
if [[ -z "${gateway_ip}" ]]; then
  echo "Could not read Gateway ${GATEWAY_NS}/${GATEWAY_NAME} address" >&2
  exit 1
fi

hosts="$(kubectl get httproute -A -o json \
  | jq -r --arg z "${ZONE}" '
      .items[]
      | .spec.hostnames[]?
      | select(endswith("." + $z) or . == $z)
      | rtrimstr(".")
    ' | sort -u)"

fail=0
while IFS= read -r host; do
  [[ -z "${host}" ]] && continue
  answer="$(dig +short "${host}" @"${DNS_SERVER}" A | head -1)"
  if [[ "${answer}" != "${gateway_ip}" ]]; then
    echo "MISMATCH ${host}: dig=${answer:-<empty>} expected=${gateway_ip}"
    fail=1
  else
    echo "OK       ${host} -> ${answer}"
  fi
done <<< "${hosts}"

exit "${fail}"
