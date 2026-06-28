#!/usr/bin/env bash
# Join Raspberry Pi 5 as k3s agent (observability worker).
# Run on the Pi as root (or via sudo). Requires K3S_TOKEN from the server:
#   ssh ubuntu@172.16.30.8 'sudo cat /var/lib/rancher/k3s/server/node-token'
set -euo pipefail

K3S_URL="${K3S_URL:-https://172.16.30.8:6443}"
K3S_TOKEN="${K3S_TOKEN:?Set K3S_TOKEN from the k3s server node-token}"
INSTALL_K3S_VERSION="${INSTALL_K3S_VERSION:-v1.35.5+k3s1}"
NODE_IP="${NODE_IP:-172.16.0.4}"

echo "[1/5] Stop conflicting container runtimes (Vault stays on systemd)"
systemctl stop k3s-agent 2>/dev/null || true
systemctl stop containerd docker 2>/dev/null || true
systemctl disable containerd docker 2>/dev/null || true
systemctl mask containerd docker 2>/dev/null || true
if id "${SUDO_USER:-ubuntu}" >/dev/null 2>&1; then
  sudo -u "${SUDO_USER:-ubuntu}" systemctl --user stop docker containerd 2>/dev/null || true
  sudo -u "${SUDO_USER:-ubuntu}" systemctl --user disable docker containerd 2>/dev/null || true
  sudo -u "${SUDO_USER:-ubuntu}" systemctl --user mask docker containerd 2>/dev/null || true
fi
/usr/local/bin/k3s-killall.sh 2>/dev/null || true
rm -f /run/containerd/containerd.sock /var/run/containerd/containerd.sock 2>/dev/null || true

echo "[2/5] Install open-iscsi + fix microk8s CNI paths for k3s"
# microk8s leaves broken symlinks that break k3s containerd CRI and Cilium.
if [ -L /etc/cni/net.d ] && [ ! -e /etc/cni/net.d ]; then
  rm -f /etc/cni/net.d
fi
mkdir -p /etc/cni/net.d
if [ -L /opt/cni/bin ] && [ ! -e /opt/cni/bin ]; then
  rm -f /opt/cni/bin
fi
mkdir -p /opt/cni/bin
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y open-iscsi
systemctl enable --now iscsid
modprobe iscsi_tcp || true
echo iscsi_tcp > /etc/modules-load.d/iscsi.conf

echo "[3/5] Remove prior agent install (if any)"
if command -v k3s-agent-uninstall.sh >/dev/null 2>&1; then
  k3s-agent-uninstall.sh 2>/dev/null || true
fi

echo "[4/5] Write k3s agent config"
mkdir -p /etc/rancher/k3s
# disable-kube-proxy and flannel-backend are server-only; Cilium on the control
# plane already replaces kube-proxy cluster-wide.
cat > /etc/rancher/k3s/config.yaml <<EOF
node-ip: ${NODE_IP}
EOF

echo "[5/5] Install k3s agent ${INSTALL_K3S_VERSION}"
curl -sfL https://get.k3s.io | \
  INSTALL_K3S_VERSION="${INSTALL_K3S_VERSION}" \
  K3S_URL="${K3S_URL}" \
  K3S_TOKEN="${K3S_TOKEN}" \
  sh -s - agent

echo "[done] Agent status"
systemctl is-active k3s-agent
journalctl -u k3s-agent --no-pager -n 10
