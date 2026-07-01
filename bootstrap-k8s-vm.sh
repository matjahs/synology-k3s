#!/usr/bin/env bash
set -euo pipefail

# --- CONFIGURATION VARIABLES ---
# NODE_IP="172.16.30.8"
GATEWAY_API_VERSION="v1.1.0"
INSTALL_K3S_VERSION="${INSTALL_K3S_VERSION:-v1.35.5+k3s1}"
ARGOCD_CHART_VERSION="${ARGOCD_CHART_VERSION:-10.0.1}"
GITOPS_REPO_URL="${GITOPS_REPO_URL:-https://github.com/matjahs/synology-k3s.git}"
GITOPS_TARGET_REVISION="${GITOPS_TARGET_REVISION:-main}"

echo "=== 1. Creating Declarative K3s Config ==="
sudo mkdir -p /etc/rancher/k3s
sudo tee /etc/rancher/k3s/config.yaml > /dev/null <<EOF
flannel-backend: "none"
disable-kube-proxy: true
disable-network-policy: true
disable:
  - traefik
  - servicelb
write-kubeconfig-mode: "0644"
EOF

echo "=== 2. Installing K3s (Control Plane Only) ==="
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="${INSTALL_K3S_VERSION}" sh -

echo "=== 3. Configuring Local Environment Context ==="
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
if ! grep -q "KUBECONFIG" ~/.bashrc; then
    echo "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml" >> ~/.bashrc
fi

echo "=== 4. Installing Kubernetes Gateway API CRDs ==="
kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"

echo "=== 5. Bootstrapping Argo CD (pinned Helm chart) ==="
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -

if ! command -v helm >/dev/null 2>&1; then
    echo "Installing Helm for bootstrap templating..."
    curl -sfL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

helm repo add argo https://argoproj.github.io/argo-helm 2>/dev/null || true
helm repo update argo
helm template argocd argo/argo-cd \
    --version "${ARGOCD_CHART_VERSION}" \
    --namespace argocd \
    --set configs.params.server.insecure=true \
    | kubectl apply -f -

echo "=== 6. Apply homelab AppProject (before root-app) ==="
kubectl apply -f "${GITOPS_REPO_URL%.git}/raw/${GITOPS_TARGET_REVISION}/platform/appproject/homelab-appproject.yaml"

echo "=== 7. Apply root GitOps Application ==="
kubectl apply -f "${GITOPS_REPO_URL%.git}/raw/${GITOPS_TARGET_REVISION}/root-app.yaml"

echo "===================================================="
echo " Bootstrap Complete! "
echo " Argo CD chart: ${ARGOCD_CHART_VERSION} (server.insecure=true)"
echo " k3s version:   ${INSTALL_K3S_VERSION}"
echo " GitOps root:   ${GITOPS_REPO_URL} @ ${GITOPS_TARGET_REVISION}"
echo "===================================================="

# --- Migration (existing clusters on imperative stable install) ---
# 1. Merge homelab AppProject + argocd-app.yaml to git; sync root-platform.
# 2. homelab AppProject must exist before Applications use project: homelab.
# 3. argocd-app.yaml (sync-wave -2) adopts the live install at the pinned chart.
#    First sync may show diffs — reconcile until Synced/Healthy.
# 4. Drop manual `kubectl patch argocd-cmd-params-cm` once GitOps owns server.insecure.
# 5. Populate Vault paths for new ESO resources (see platform/external-secrets.md).
