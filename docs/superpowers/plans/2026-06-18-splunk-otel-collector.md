# Splunk OTel Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the Splunk Distribution of the OpenTelemetry Collector on the synology-k3s cluster, shipping infrastructure metrics, pod logs, and application traces to Splunk Observability Cloud.

**Architecture:** A new `apps/observability/` Kustomize directory holds two Argo CD Applications discovered by the root app-of-apps: `observability-app.yaml` (Kustomize, deploys the ExternalSecret) and `splunk-otel-collector-app.yaml` (Helm, deploys the chart). The ExternalSecret pulls the ingest token from Vault and creates the k8s Secret the Helm chart expects (`secret.create: false`).

**Tech Stack:** Argo CD app-of-apps, Helm chart `splunk-otel-collector` from `signalfx.github.io/splunk-otel-collector-chart`, External Secrets Operator, HashiCorp Vault (ClusterSecretStore `vault`), Kustomize, yamllint, kubeconform.

## Global Constraints

- Realm: `eu1`
- Cluster name: `synology-k3s`
- Vault secret path: `splunk-otel-collector/observability-cloud`, property `access-token`
- k8s Secret name: `splunk-otel-collector`, key `splunk_observability_access_token`
- Destination namespace: `observability`
- Sync-wave: `1` for both Applications
- `gateway.enabled: false` — no gateway tier
- All YAML must pass `yamllint -c .yamllint.yaml .` (2-space indent, max 160 chars)

---

### Task 1: Look up the latest stable chart version

**Files:**
- No file changes — discovery only

**Interfaces:**
- Produces: `CHART_VERSION` string used in Tasks 2 and 3

- [ ] **Step 1: Add the Helm repo and search for the latest version**

```bash
helm repo add splunk-otel-collector https://signalfx.github.io/splunk-otel-collector-chart
helm repo update
helm search repo splunk-otel-collector/splunk-otel-collector --versions | head -5
```

Expected output: a table of versions, e.g.:
```
NAME                                              CHART VERSION   APP VERSION
splunk-otel-collector/splunk-otel-collector       0.123.0         ...
```

Pick the highest non-prerelease `CHART VERSION` from the first row. Record it — you will use it in Tasks 2 and 3.

---

### Task 2: Create `apps/observability/` directory with all manifests

**Files:**
- Create: `apps/observability/kustomization.yaml`
- Create: `apps/observability/splunk-otel-collector-external-secret.yaml`
- Create: `apps/observability/observability-app.yaml`
- Create: `apps/observability/splunk-otel-collector-app.yaml`

**Interfaces:**
- Consumes: `CHART_VERSION` from Task 1
- Produces: `apps/observability/` directory that kustomize can build and root-app-of-apps discovers

- [ ] **Step 1: Create `apps/observability/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: observability

resources:
  - splunk-otel-collector-external-secret.yaml
```

- [ ] **Step 2: Create `apps/observability/splunk-otel-collector-external-secret.yaml`**

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: splunk-otel-collector
  namespace: observability
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault
    kind: ClusterSecretStore
  target:
    name: splunk-otel-collector
    creationPolicy: Owner
  data:
    - secretKey: splunk_observability_access_token
      remoteRef:
        key: splunk-otel-collector/observability-cloud
        property: access-token
```

- [ ] **Step 3: Create `apps/observability/observability-app.yaml`**

This Application deploys the ExternalSecret via Kustomize.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: observability
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  project: default
  source:
    repoURL: https://github.com/matjahs/synology-k3s.git
    targetRevision: HEAD
    path: apps/observability
  destination:
    server: https://kubernetes.default.svc
    namespace: observability
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step 4: Create `apps/observability/splunk-otel-collector-app.yaml`**

Replace `<CHART_VERSION>` with the version found in Task 1.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: splunk-otel-collector
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  project: default
  source:
    repoURL: https://signalfx.github.io/splunk-otel-collector-chart
    chart: splunk-otel-collector
    targetRevision: <CHART_VERSION>
    helm:
      values: |
        clusterName: synology-k3s

        splunkObservability:
          realm: eu1
          ingestUrl: https://ingest.eu1.signalfx.com
          apiUrl: https://api.eu1.signalfx.com

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
  destination:
    server: https://kubernetes.default.svc
    namespace: observability
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step 5: Validate locally with yamllint and kustomize build**

```bash
yamllint -c .yamllint.yaml apps/observability/
kustomize build apps/observability/
```

Expected: no lint errors; kustomize outputs the ExternalSecret manifest with `namespace: observability`.

- [ ] **Step 6: Commit**

```bash
git add apps/observability/
git commit -m "feat(observability): add Splunk OTel Collector Helm app and ExternalSecret"
```

---

### Task 3: Update CI pipeline to cover the new directory and chart

**Files:**
- Modify: `.github/workflows/validate.yaml`

**Interfaces:**
- Consumes: `CHART_VERSION` from Task 1
- Produces: CI validates `apps/observability/` kustomize build and Helm render on every PR

- [ ] **Step 1: Add `apps/observability` to the kustomize-kubeconform loop**

In `.github/workflows/validate.yaml`, find the `kustomize-kubeconform` job's `Validate Kustomize builds` step. The current loop is:

```yaml
          for d in platform apps/cyberchef apps/keycloak apps/guacamole; do
```

Change it to:

```yaml
          for d in platform apps/cyberchef apps/keycloak apps/guacamole apps/observability; do
```

- [ ] **Step 2: Add a Helm render step for the Splunk OTel chart**

In the `helm-template` job, the current `run:` block is:

```yaml
      - name: Render CloudNativePG operator chart
        # Keycloak no longer uses a Helm chart (Keycloak Operator + CNPG via
        # CRs). Smoke-test the one chart we still pin: cloudnative-pg.
        run: |
          helm template cnpg cloudnative-pg \
            --repo https://cloudnative-pg.github.io/charts \
            --version 0.28.2 > /dev/null
```

Replace it with (substituting the actual `CHART_VERSION` from Task 1):

```yaml
      - name: Render CloudNativePG operator chart
        # Keycloak no longer uses a Helm chart (Keycloak Operator + CNPG via
        # CRs). Smoke-test the one chart we still pin: cloudnative-pg.
        run: |
          helm template cnpg cloudnative-pg \
            --repo https://cloudnative-pg.github.io/charts \
            --version 0.28.2 > /dev/null
      - name: Render Splunk OTel Collector chart
        run: |
          helm template splunk-otel-collector splunk-otel-collector \
            --repo https://signalfx.github.io/splunk-otel-collector-chart \
            --version <CHART_VERSION> \
            --set secret.create=false \
            --set clusterName=synology-k3s \
            --set splunkObservability.realm=eu1 \
            --set logsCollection.enabled=true \
            --set gateway.enabled=false > /dev/null
```

- [ ] **Step 3: Validate the workflow YAML**

```bash
yamllint -c .yamllint.yaml .github/workflows/validate.yaml
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/validate.yaml
git commit -m "ci: validate apps/observability kustomize build and Splunk OTel Helm render"
```

---

### Task 4: Verify Argo CD sync and Splunk Observability Cloud

**Files:**
- No file changes — verification only

- [ ] **Step 1: Push to main and confirm CI passes**

```bash
git push
```

Open the GitHub Actions run and confirm all jobs (`yamllint`, `kustomize-kubeconform`, `helm-template`) pass.

- [ ] **Step 2: Verify Argo CD picks up the new Applications**

```bash
kubectl get applications -n argocd | grep -E "observability|splunk"
```

Expected: two entries (`observability` and `splunk-otel-collector`), both progressing toward `Synced`.

- [ ] **Step 3: Verify the ExternalSecret synced the token**

```bash
kubectl get externalsecret splunk-otel-collector -n observability
kubectl get secret splunk-otel-collector -n observability
```

Expected: ExternalSecret shows `SecretSynced`; Secret exists with key `splunk_observability_access_token`.

- [ ] **Step 4: Verify collector pods are running**

```bash
kubectl get pods -n observability
```

Expected: one `splunk-otel-collector-agent-*` pod per node (DaemonSet) and one `splunk-otel-collector-k8s-cluster-receiver-*` pod (Deployment), all in `Running` state.

- [ ] **Step 5: Verify data arrives in Splunk Observability Cloud**

Open Splunk Observability Cloud → Infrastructure → Kubernetes. Within 2-3 minutes of the pods starting you should see the `synology-k3s` cluster appear with node and pod metrics. Check Logs → Live Tail to confirm pod logs are flowing.
