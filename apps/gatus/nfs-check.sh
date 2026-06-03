#!/bin/sh
# ---------------------------------------------------------------------------
# nfs-check.sh  (runs inside the gatus-nfs CronJob)
# The NFS export is mounted at /nfs by the pod (volume type: nfs). This script
# proves the share is *writable and readable*, not just reachable: it writes a
# probe file, reads it back, verifies the content, deletes it, and PUSHES the
# result to a Gatus external endpoint.
# Required env: GATUS_URL, TOK_NFS
# ---------------------------------------------------------------------------
set -u
MOUNT="/nfs"
KEY="storage_nfs-share-readwrite"
CURL="curl -ksS --max-time 15"

push() {  # <success true|false> <msg> <dur_s>
  enc=$(printf '%s' "${2:-}" | sed 's/ /%20/g')
  $CURL -X POST \
    "${GATUS_URL}/api/v1/endpoints/${KEY}/external?success=$1&error=${enc}&duration=${3:-0}s" \
    -H "Authorization: Bearer ${TOK_NFS}" >/dev/null \
    && echo "pushed ${KEY}=$1 ${2:-}"
}

start=$(date +%s)
probe="${MOUNT}/.gatus-probe-$$"
token="gatus-$(date +%s)-$$"

# 1) mount sanity — is the export actually mounted?
if ! mountpoint -q "$MOUNT" 2>/dev/null && [ ! -d "$MOUNT" ]; then
  push false "NFS not mounted at ${MOUNT}"; exit 0
fi

# 2) write
if ! printf '%s\n' "$token" > "$probe" 2>/dev/null; then
  push false "write failed" "$(( $(date +%s) - start ))"; exit 0
fi

# 3) read back + verify
read_back=$(cat "$probe" 2>/dev/null)
rm -f "$probe" 2>/dev/null
dur=$(( $(date +%s) - start ))

if [ "$read_back" = "$token" ]; then
  push true "read/write OK" "$dur"
else
  push false "readback mismatch" "$dur"
fi
