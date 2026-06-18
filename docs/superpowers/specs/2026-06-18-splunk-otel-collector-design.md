# Splunk OTel Collector — Design

**Date:** 2026-06-18
**Scope:** Deploy the Splunk Distribution of the OpenTelemetry Collector on the synology-k3s cluster via Argo CD + Helm.

## Goal

Ship infrastructure metrics, pod logs, and application traces to Splunk Observability Cloud (realm `eu1`, cluster `synology-k3s`).

## Repo placement

New directory `apps/observability/` following the existing per-app Kustomize pattern. The root app-of-apps discovers `*-app.yaml` recursively, so no changes to `root-app.yaml` are needed.

```
apps/observability/
├── kustomization.yaml
├── splunk-otel-collector-app.yaml
└── splunk-otel-collector-external-secret.yaml
```

## Argo CD Application

- **Chart:** `splunk-otel-collector` from `https://signalfx.github.io/splunk-otel-collector-chart`
- **Version:** pinned, Renovate-updateable
- **Destination namespace:** `observability` (`CreateNamespace=true`)
- **Sync-wave:** `1`
- **Sync policy:** automated, prune + selfHeal

## Helm values

```yaml
clusterName: synology-k3s

splunkObservability:
  realm: eu1
  ingestUrl: https://ingest.eu1.signalfx.com
  apiUrl:    https://api.eu1.signalfx.com

secret:
  create: false
  name: splunk-otel-collector

agent:
  enabled: true

clusterReceiver:
  enabled: true

logsCollection:
  enabled: true

gateway:
  enabled: false
```

`accessToken` is left empty in values — the chart reads from the pre-existing k8s Secret (see below).

## Secret management

**Vault path:** `splunk-otel-collector/observability-cloud`, property `access-token`

ExternalSecret creates k8s Secret `splunk-otel-collector` in namespace `observability` with key `splunk_observability_access_token` (the key name the chart expects when `secret.create: false`).

## Telemetry collected

- **Metrics:** node/pod/container metrics via `agent` DaemonSet + cluster-level metrics via `clusterReceiver` Deployment
- **Logs:** stdout/stderr from all pods via `logsCollection`
- **Traces:** `agent` DaemonSet exposes OTLP gRPC (4317) and HTTP (4318) — applications send traces to the agent on those ports via the node IP or the collector's ClusterIP Service

## Sync ordering

No CRD dependency — sync-wave `1` is correct. ExternalSecret CRD is already installed by the external-secrets platform app (wave `-1`), so the ExternalSecret in this app will reconcile cleanly.
