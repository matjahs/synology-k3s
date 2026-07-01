#!/usr/bin/env bash
# One-time Vault paths for NetBox DNS source of truth.
# Requires: vault CLI authenticated to https://vault.mxe11.nl:8200
set -euo pipefail

: "${VAULT_ADDR:=https://vault.mxe11.nl:8200}"

echo "Using VAULT_ADDR=${VAULT_ADDR}"
vault status >/dev/null

echo "Populate NetBox API token (create in NetBox UI: Admin → API tokens, write enabled):"
read -r -s -p "NetBox API token: " NETBOX_TOKEN
echo
vault kv put secret/netbox/api token="${NETBOX_TOKEN}"

echo "Populate UniFi Integration API key (same key as external-dns used):"
read -r -s -p "UniFi API key: " UNIFI_KEY
echo
vault kv put secret/dns/unifi api_key="${UNIFI_KEY}"

echo "Done. ESO will sync to netbox/netbox-api and dns/{netbox-api,unifi-dns}."
