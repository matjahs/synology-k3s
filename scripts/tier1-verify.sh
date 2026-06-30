#!/usr/bin/env bash
# Tier 1 verification checklist (run after Vault population + Argo sync).
set -euo pipefail

fail=0
ok() { echo "OK  $*"; }
warn() { echo "WARN $*"; fail=1; }

echo "=== ExternalSecrets ==="
while read -r ns name ready reason; do
  [[ "$ns" == "NS" ]] && continue
  if [[ "$ready" == "True" ]]; then ok "$ns/$name"; else warn "$ns/$name ($reason)"; fi
done < <(kubectl get externalsecret -A -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,REASON:.status.conditions[?(@.type=="Ready")].reason' --no-headers)

echo ""
echo "=== Image Updater ==="
if kubectl -n argocd get deploy argocd-image-updater-controller >/dev/null 2>&1; then
  ok "controller deployment exists"
else
  warn "argocd-image-updater-controller missing"
fi

nexus_url=$(kubectl -n argocd get application argocd-image-updater -o jsonpath='{.spec.source.helm.values}' 2>/dev/null | grep -m1 'api_url:' | awk '{print $2}' || true)
if [[ "$nexus_url" == *"nexus-svc.tools.svc.cluster.local"* ]]; then
  ok "Nexus registry uses in-cluster URL ($nexus_url)"
else
  warn "Nexus registry still external ($nexus_url) — sync root-app from main"
fi

if kubectl -n argocd get secret argocd-image-updater-git-creds >/dev/null 2>&1; then
  ok "git creds secret exists"
else
  warn "argocd-image-updater-git-creds missing — populate secret/argocd/git in Vault"
fi

echo ""
echo "=== CNPG backups ==="
if kubectl -n keycloak get scheduledbackup keycloak-db-daily >/dev/null 2>&1; then
  ok "ScheduledBackup keycloak-db-daily"
else
  warn "ScheduledBackup missing — sync keycloak app from main"
fi

archiving=$(kubectl -n keycloak get cluster keycloak-db -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")].status}' 2>/dev/null || true)
if [[ "$archiving" == "True" ]]; then
  ok "keycloak-db continuous archiving active"
elif [[ -n "$archiving" ]]; then
  warn "keycloak-db continuous archiving: $archiving"
else
  warn "keycloak-db cluster not found or archiving status unknown"
fi

echo ""
echo "=== Volume snapshots ==="
if kubectl get crd volumesnapshotclasses.snapshot.storage.k8s.io >/dev/null 2>&1; then
  ok "VolumeSnapshotClass CRD installed"
  kubectl get volumesnapshotclass 2>/dev/null | tail -n +2 | while read -r line; do ok "  $line"; done
else
  warn "external-snapshotter CRDs not installed — sync platform apps"
fi

echo ""
echo "=== AppProject ==="
if kubectl -n argocd get appproject homelab >/dev/null 2>&1; then
  ok "homelab AppProject"
else
  warn "homelab AppProject missing"
fi

default_apps=$(kubectl -n argocd get applications -o jsonpath='{range .items[?(@.spec.project=="default")]}{.metadata.name}{"\n"}{end}' 2>/dev/null | grep -v '^root-app$' || true)
if [[ -z "$default_apps" ]]; then
  ok "no Applications on project: default (except root-app)"
else
  warn "Applications still on project: default: $default_apps"
fi

exit "$fail"
