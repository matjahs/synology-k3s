#!/bin/sh
# ---------------------------------------------------------------------------
# vcf-healthcheck.sh  (runs inside the gatus-vcf CronJob)
# Logs into VCF components, reads their health APIs, and PUSHES pass/fail to
# Gatus external endpoints. Credentials + tokens come from env (gatus-secrets).
# Self-signed lab certs -> curl -k.
# Required env: GATUS_URL, VC_HOST, VC_USER, VC_PASS, SDDC_HOST, SDDC_USER,
#               SDDC_PASS, TOK_VCENTER, TOK_VSAN, TOK_SDDC
# ---------------------------------------------------------------------------
set -u
CURL="curl -ksS --max-time 20"

# push <endpoint-key> <token> <success true|false> <errorMessage> <duration_s>
push() {
  key="$1"; token="$2"; success="$3"; msg="${4:-}"; dur="${5:-0}"
  enc=$(printf '%s' "$msg" | jq -sRr @uri)
  $CURL -X POST \
    "${GATUS_URL}/api/v1/endpoints/${key}/external?success=${success}&error=${enc}&duration=${dur}s" \
    -H "Authorization: Bearer ${token}" >/dev/null \
    && echo "pushed ${key}=${success} ${msg}"
}

# ----------------------------- vCenter -------------------------------------
check_vcenter() {
  key="vcf-deep-health_vcenter-appliance-health"; start=$(date +%s)
  sid=$($CURL -u "${VC_USER}:${VC_PASS}" -X POST "https://${VC_HOST}/api/session" | tr -d '"')
  if [ -z "$sid" ] || [ "$sid" = "null" ]; then
    push "$key" "$TOK_VCENTER" false "vCenter auth failed"; return
  fi
  health=$($CURL -H "vmware-api-session-id: ${sid}" \
           "https://${VC_HOST}/api/appliance/health/system" | tr -d '"')
  dur=$(( $(date +%s) - start ))
  $CURL -H "vmware-api-session-id: ${sid}" -X DELETE "https://${VC_HOST}/api/session" >/dev/null 2>&1
  if [ "$health" = "green" ]; then
    push "$key" "$TOK_VCENTER" true "health=green" "$dur"
  else
    push "$key" "$TOK_VCENTER" false "appliance health=${health:-unknown}" "$dur"
  fi
}

# ----------------------- vSAN / storage (via vCenter) ----------------------
check_vsan() {
  key="vcf-deep-health_vsan-health"; start=$(date +%s)
  sid=$($CURL -u "${VC_USER}:${VC_PASS}" -X POST "https://${VC_HOST}/api/session" | tr -d '"')
  if [ -z "$sid" ] || [ "$sid" = "null" ]; then
    push "$key" "$TOK_VSAN" false "vCenter auth failed"; return
  fi
  sh_health=$($CURL -H "vmware-api-session-id: ${sid}" \
              "https://${VC_HOST}/api/appliance/health/storage" | tr -d '"')
  dur=$(( $(date +%s) - start ))
  $CURL -H "vmware-api-session-id: ${sid}" -X DELETE "https://${VC_HOST}/api/session" >/dev/null 2>&1
  if [ "$sh_health" = "green" ]; then
    push "$key" "$TOK_VSAN" true "storage=green" "$dur"
  else
    push "$key" "$TOK_VSAN" false "storage health=${sh_health:-unknown}" "$dur"
  fi
}

# --------------------------- SDDC Manager ----------------------------------
check_sddc() {
  key="vcf-deep-health_sddc-manager-health"; start=$(date +%s)
  access=$($CURL -X POST "https://${SDDC_HOST}/v1/tokens" \
            -H "Content-Type: application/json" \
            -d "{\"username\":\"${SDDC_USER}\",\"password\":\"${SDDC_PASS}\"}" \
            | jq -r '.accessToken')
  if [ -z "$access" ] || [ "$access" = "null" ]; then
    push "$key" "$TOK_SDDC" false "SDDC token failed"; return
  fi
  bad=$($CURL -H "Authorization: Bearer ${access}" "https://${SDDC_HOST}/v1/domains" \
        | jq -r '[.elements[] | select(.status != "ACTIVE") | .name] | join(",")')
  dur=$(( $(date +%s) - start ))
  if [ -z "$bad" ]; then
    push "$key" "$TOK_SDDC" true "all domains ACTIVE" "$dur"
  else
    push "$key" "$TOK_SDDC" false "non-ACTIVE domains: ${bad}" "$dur"
  fi
}

check_vcenter
check_vsan
check_sddc
