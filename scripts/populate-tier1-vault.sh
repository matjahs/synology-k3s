#!/usr/bin/env bash
# One-time Vault population for Tier 1 GitOps hardening.
# Requires: vault CLI authenticated to https://vault.mxe11.nl
set -euo pipefail

: "${VAULT_ADDR:=https://vault.mxe11.nl}"

echo "Using VAULT_ADDR=${VAULT_ADDR}"
vault status >/dev/null

if [[ "${1:-}" == "--check" ]]; then
  for path in argocd/git democratic-csi/driver cnpg/backup-s3; do
    if vault kv get -mount=secret "$path" >/dev/null 2>&1; then
      echo "OK  secret/${path}"
    else
      echo "MISSING secret/${path}"
    fi
  done
  exit 0
fi

cat <<'EOF'
Populate the three Tier 1 Vault paths below, then re-run with --check:

1. secret/argocd/git — GitHub App for Image Updater PR write-back on matjahs/synology-k3s
   Permissions: Contents (read/write), Pull requests (read/write)

   vault kv put secret/argocd/git \
     app_id=<github-app-id> \
     installation_id=<installation-id> \
     private_key=@/path/to/private-key.pem

2. secret/democratic-csi/driver — Synology DSM iSCSI credentials for democratic-csi

   vault kv put secret/democratic-csi/driver \
     host=<synology-ip> \
     port=5000 \
     username=<dsm-user> \
     password=<dsm-password> \
     volume=/volume1 \
     target_portal=<synology-ip>

3. secret/cnpg/backup-s3 — Garage S3 credentials for CNPG backups

   vault kv put secret/cnpg/backup-s3 \
     endpoint_url=https://garage.lab.mxe11.nl:3900 \
     access_key_id=<garage-key> \
     secret_access_key=<garage-secret> \
     bucket=cnpg-backups \
     region=garage

After populating, verify ESO sync:

   kubectl get externalsecret -A | grep -E 'argocd-image-updater-git|democratic-csi|cnpg-backup'

Force Argo CD to pick up main (includes in-cluster Nexus URL for Image Updater):

   kubectl -n argocd annotate application root-app argocd.argoproj.io/refresh=hard --overwrite
EOF
