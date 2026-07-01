#!/usr/bin/env bash
# Discover homelab inventory from live systems.
#
# Auth per target (nothing committed to git):
#   esxi, mikrotik — export VCF_SSH_PASS='…'
#   unifi / ucg    — export UCG_SSH_PASS='…'  (required unless key auth works)
#                    optional: UCG_SSH_PASS_FILE=/path/to/file
#                    optional: UCG_SSH_KEY=~/.ssh/id_ecdsa
#
# Usage:
#   export UCG_SSH_PASS='…'
#   ./scripts/discover-homelab.sh ucg
#   ./scripts/discover-homelab.sh esxi
#   ./scripts/discover-homelab.sh mikrotik
set -euo pipefail

SSH_COMMON=(-o StrictHostKeyChecking=no)
SSH_PASSWORD_OPTS=(
  -o PubkeyAuthentication=no
  -o PreferredAuthentications=password
  -o KbdInteractiveAuthentication=no
  -o NumberOfPasswordPrompts=1
)

run_ssh_password() {
  local user=$1 host=$2
  shift 2
  : "${VCF_SSH_PASS:?Set VCF_SSH_PASS for password SSH to ${user}@${host}}"
  export SSHPASS="${VCF_SSH_PASS}"
  sshpass -e ssh "${SSH_COMMON[@]}" "${SSH_PASSWORD_OPTS[@]}" "${user}@${host}" "$@"
}

ucg_password() {
  if [[ -n "${UCG_SSH_PASS:-}" ]]; then
    printf '%s' "${UCG_SSH_PASS}"
    return 0
  fi
  if [[ -n "${UCG_SSH_PASS_FILE:-}" && -f "${UCG_SSH_PASS_FILE}" ]]; then
    tr -d '\r\n' <"${UCG_SSH_PASS_FILE}"
    return 0
  fi
  return 1
}

run_ssh_unifi() {
  local cmd=$1
  local host="172.16.0.1"
  local pass

  if pass="$(ucg_password)"; then
    export SSHPASS="${pass}"
    sshpass -e ssh "${SSH_COMMON[@]}" "${SSH_PASSWORD_OPTS[@]}" "root@${host}" "${cmd}"
    return
  fi

  local key="${UCG_SSH_KEY:-${HOME}/.ssh/id_ecdsa}"
  if [[ -f "${key}" ]]; then
    if ssh "${SSH_COMMON[@]}" -i "${key}" -o BatchMode=yes -o PubkeyAuthentication=yes \
      "root@${host}" true 2>/dev/null; then
      ssh "${SSH_COMMON[@]}" -i "${key}" -o BatchMode=yes -o PubkeyAuthentication=yes \
        "root@${host}" "${cmd}"
      return
    fi
  fi

  cat >&2 <<'EOF'
UCG SSH auth failed.

Password auth is required for most UCG setups. Do not rely on interactive prompts:
  export UCG_SSH_PASS='…'
  ./scripts/discover-homelab.sh ucg

Or store the password in a file (chmod 600):
  export UCG_SSH_PASS_FILE=~/.config/ucg-ssh-pass
EOF
  exit 1
}

discover_esxi() {
  echo "=== ESXi $(run_ssh_password root 172.16.30.11 hostname) @ 172.16.30.11 ==="
  # Remote script runs on ESXi; single quotes prevent local expansion.
  # shellcheck disable=SC2016
  run_ssh_password root 172.16.30.11 '
    echo "--- version ---"
    esxcli system version get
    echo "--- hardware ---"
    esxcli hardware platform get
    echo "--- vmkernel ipv4 ---"
    esxcli network ip interface ipv4 get
    echo "--- portgroups (vlan ids) ---"
    esxcli network vswitch standard portgroup list
    echo "--- vms ---"
    vim-cmd vmsvc/getallvms
    echo "--- vm guest details (powered-on with tools) ---"
    for id in $(vim-cmd vmsvc/getallvms | awk "NR>1 {print \$1}"); do
      sum=$(vim-cmd vmsvc/get.summary "$id" 2>/dev/null) || continue
      name=$(echo "$sum" | sed -n "s/.*name = \"\(.*\)\".*/\1/p" | head -1)
      ip=$(echo "$sum" | sed -n "s/.*ipAddress = \"\(.*\)\".*/\1/p" | head -1)
      host=$(echo "$sum" | sed -n "s/.*hostName = \"\(.*\)\".*/\1/p" | head -1)
      cpus=$(echo "$sum" | sed -n "s/.*numCpu = \(.*\),/\1/p" | head -1)
      mem=$(echo "$sum" | sed -n "s/.*memorySizeMB = \(.*\),/\1/p" | head -1)
      os=$(echo "$sum" | sed -n "s/.*guestFullName = \"\(.*\)\".*/\1/p" | head -1)
      [ -n "$ip" ] && echo "$id|$name|$host|$ip|${cpus}vcpu|${mem}MB|$os"
    done
  '
}

discover_mikrotik() {
  echo "=== MikroTik CRS310 @ 172.16.0.2 ==="
  run_ssh_password admin 172.16.0.2 '
    /system identity print
    /interface vlan print
    /ip address print
    /interface bridge vlan print
  '
}

discover_unifi() {
  echo "=== UniFi Cloud Gateway $(run_ssh_unifi hostname) @ 172.16.0.1 ==="
  run_ssh_unifi '
    echo "--- ipv4 ---"
    ip -4 addr show
    echo "--- proc vlan ---"
    cat /proc/net/vlan/config 2>/dev/null || true
    echo "--- bridge vlan ---"
    bridge vlan show 2>/dev/null || brctl show 2>/dev/null || true
    echo "--- routes ---"
    ip -4 route show
  '
}

TARGETS=(esxi mikrotik unifi)
if [[ $# -gt 0 ]]; then
  TARGETS=("$@")
fi

for t in "${TARGETS[@]}"; do
  case "$t" in
    esxi) discover_esxi ;;
    mikrotik) discover_mikrotik ;;
    unifi|ucg) discover_unifi ;;
    *)
      echo "Unknown target: $t (esxi|mikrotik|unifi|ucg)" >&2
      exit 1
      ;;
  esac
  echo
done
