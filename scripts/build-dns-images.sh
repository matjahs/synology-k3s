#!/usr/bin/env bash
# Build and push DNS automation images to Nexus (run once from a host with docker + nexus login).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${1:-0.1.0}"
REGISTRY="${REGISTRY:-nexus.lab.mxe11.nl}"

build() {
  local name="$1"
  local context="$2"
  docker buildx build --platform linux/amd64 \
    -t "${REGISTRY}/platform/${name}:${TAG}" \
    --push "${context}"
}

build dns-netbox-sync "${ROOT}/platform/dns/dns-netbox-sync"
build octodns-sync "${ROOT}/platform/dns/octodns"

echo "Pushed ${REGISTRY}/platform/dns-netbox-sync:${TAG}"
echo "Pushed ${REGISTRY}/platform/octodns-sync:${TAG}"
