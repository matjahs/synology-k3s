#!/usr/bin/env bash
set -euo pipefail

# --- CONFIGURATION VARIABLES ---
NODE_IP="172.16.30.8"
GATEWAY_API_VERSION="v1.1.0"

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
curl -sfL https://get.k3s.io | sh -

echo "=== 3. Configuring Local Environment Context ==="
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
if ! grep -q "KUBECONFIG" ~/.bashrc; then
    echo "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml" >> ~/.bashrc
fi

echo "=== 4. Installing Kubernetes Gateway API CRDs ==="
kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"

echo "=== 5. Bootstrapping Argo CD Engine ==="
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

echo "=== 6. Patching Argo CD for Non-TLS Ingress (GitOps-ready) ==="
kubectl patch configmap argocd-cmd-params-cm -n argocd --type merge -p '{"data":{"server.insecure":"true"}}'
kubectl rollout restart deployment/argocd-server -n argocd

echo "===================================================="
echo " Bootstrap Complete! "
echo " Next step: Apply your root GitOps Application Spec."
echo "===================================================="