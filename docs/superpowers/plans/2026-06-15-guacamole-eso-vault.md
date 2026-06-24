# Guacamole + ESO + Vault Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Apache Guacamole with Keycloak OIDC SSO, all secrets managed by External Secrets Operator pulling from Vault, with Guacamole's database co-hosted on the existing CNPG keycloak-db cluster.

**Architecture:** Three tracks in sync-wave order — ESO platform app (wave `-1`), ClusterSecretStore + keycloak secret migration + CNPG extension (wave `0`/`1`), Guacamole app (wave `2`) with DB init Job (wave `3`). The root ArgoCD app auto-discovers all `*-app.yaml` files; `kustomization.yaml` files control which resources each Application deploys. All secrets flow from Vault via ExternalSecrets — no out-of-band `kubectl apply` required after prerequisites are met.

**Tech Stack:** ArgoCD app-of-apps, Kustomize, Cilium Gateway API (HTTPRoute), cert-manager (Let's Encrypt DNS-01), CloudNativePG, Keycloak Operator, External Secrets Operator (ESO) 0.9.x Helm chart, HashiCorp Vault KV v2 with Kubernetes auth, Apache Guacamole 1.5.5 (`guacamole/guacamole` + `guacamole/guacd`)

---

## Status — 2026-06-18 (verified against the live cluster)

**Guacamole is UP.** In-cluster `GET /guacamole/` returns HTTP 200, the pod is `2/2 Running`, ESO is flowing secrets, and the DB schema is correct. Both earlier blockers — Vault auth, then a malformed DB schema — are resolved.

**Done (verified in-cluster):**

- ESO deployed (`external-secrets` app `Synced/Healthy`); all manifests migrated off the now-removed `external-secrets.io/v1beta1` API to `v1` (running ESO image `v2.6.0` serves only `v1`).
- **Vault Kubernetes auth fixed** — `ClusterSecretStore/vault` is `Ready`; all 5 ExternalSecrets `SecretSynced`; secrets present in `tools`/`keycloak`/`cert-manager`.
- Vault KV populated (Prereq 2): `secret/guacamole/db`, `secret/guacamole/oidc`, `secret/keycloak/db`, `secret/cert-manager/cloudflare`.
- Guacamole deployed and serving: `Deployment/guacamole` `2/2`, `Service/guacamole-svc`, `HTTPRoute/guacamole`, `guacamole-db-init` Job `Complete`. Route `guacamole.lab.mxe11.nl` live.
- **DB schema corrected (2026-06-18).** The original `initdb-configmap.yaml` held a malformed schema (enum columns as `varchar`, no `CREATE TYPE`), so auth queries casting to `'USER'::guacamole_entity_type` threw and the UI showed a generic error page. Regenerated the canonical schema from the running `guacamole/guacamole:1.5.5` image, wiped + reloaded the `guacamole` DB, and updated the ConfigMap. All 5 enum types present; `guacamole_entity.type` is the real `guacamole_entity_type`; `guacadmin` seeded.
- Side fix landed: removed `directory: recurse: false` from `keycloak-operator-app.yaml` (zero-value boolean dropped on apply -> permanent phantom diff). `root-platform` and `keycloak-operator` green.

**Resolved blocker — Vault Kubernetes auth (Prereq 1):** login returned `403 permission denied` because Vault runs **outside** the cluster (`vault.mxe11.nl` -> `172.16.0.4`, self-hosted, non-Enterprise) and `auth/kubernetes/config` set no `token_reviewer_jwt`, so Vault used the *client* SA token for the `TokenReview` call — which the `external-secrets` SA wasn't allowed to make. Fixed by granting that SA `system:auth-delegator` (Option A). Keep the External-Vault caveat under Prereq 1 in mind for any rebuild.

**Remaining work:**

- [ ] Commit the corrected `apps/guacamole/initdb-configmap.yaml` so git matches the live DB. (The completed db-init Job won't re-run, so no reload risk.)
- [ ] Change the default `guacadmin` / `guacadmin` password, then verify the Keycloak OIDC login button end-to-end.
- [x] Hardened the `guacamole-db-init` guard to gate on the `guacamole_entity_type` enum + `ON_ERROR_STOP=1` (in git, pending commit) — a future partial/wrong load now fails loudly instead of silently "completing".
- [ ] Make the Vault auth fix durable in GitOps — add the `system:auth-delegator` ClusterRoleBinding for the `external-secrets` SA as a manifest (or set a `token_reviewer_jwt` on Vault) so a cluster rebuild doesn't reintroduce the 403.
- [ ] Run the Post-Sync Checklist (bottom of this doc).
- [ ] Housekeeping: drop the stale `VAULT_NAMESPACE=admin` (and dead HCP `VAULT_ADDR`) from the shell rc — this Vault is self-hosted, non-Enterprise, so `admin/` doesn't exist and silently 403s admin reads.

---

## Prerequisites

These steps are out-of-band and must be done before ArgoCD syncs, or ExternalSecrets will stay `SecretSyncedError` until they do.

**1. Vault Kubernetes auth (one-time setup): RESOLVED 2026-06-18 — login works after granting the `external-secrets` SA `system:auth-delegator`. See the Status section for details; keep the External-Vault caveat below in mind for any rebuild.**

> **External-Vault caveat:** this Vault runs at `172.16.0.4`, outside the cluster. The `config` write below omits `token_reviewer_jwt`, so Vault uses the *client* SA token for TokenReview — which requires the `external-secrets` SA to hold `system:auth-delegator`. Either bind that role to the SA, or add `token_reviewer_jwt=@/tmp/reviewer.jwt` (from an auth-delegator SA) to the `config` command.

**1. Vault Kubernetes auth (one-time setup):**

```bash
### Grab the k3s CA cert from the cluster
kubectl get configmap -n kube-system kube-root-ca.crt -o jsonpath='{.data.ca\.crt}' > /tmp/k3s-ca.crt

vault auth enable kubernetes
vault write auth/kubernetes/config \
  kubernetes_host="https://172.16.30.8:6443" \
  kubernetes_ca_cert=@/tmp/k3s-ca.crt

vault policy write external-secrets - <<EOF
path "secret/data/*" { capabilities = ["read"] }
EOF

vault write auth/kubernetes/role/external-secrets \
  bound_service_account_names=external-secrets \
  bound_service_account_namespaces=external-secrets \
  policies=external-secrets \
  ttl=1h
```

**2. Vault KV paths (populate before or immediately after first sync): DONE — verified `secret/guacamole/db` (`password`), `secret/guacamole/oidc` (`client-secret`), `secret/keycloak/db`, and `secret/cert-manager/cloudflare` are all populated.**

```bash
### Enable KV v2 if not already enabled
vault secrets enable -path=secret kv-v2

vault kv put secret/keycloak/db \
  username=keycloak \
  password=<strong-password>

vault kv put secret/cert-manager/cloudflare \
  api-token=<cloudflare-api-token>

vault kv put secret/guacamole/db \
  password=<strong-password>

### Populated after KeycloakRealmImport creates the client — see Task 12
vault kv put secret/guacamole/oidc \
  client-secret=<retrieved-from-keycloak>
```

---

## File Map

```plain
platform/
  external-secrets-app.yaml          CREATE  ArgoCD Application for ESO Helm chart (wave -1)
  vault-secret-store.yaml            CREATE  ClusterSecretStore pointing at vault.mxe11.nl
  cloudflare-external-secret.yaml    CREATE  ExternalSecret → cloudflare-api-token in cert-manager ns
  external-secrets.md                CREATE  Vault setup documentation
  kustomization.yaml                 MODIFY  add vault-secret-store + cloudflare-external-secret
  argocd-ingress.yaml                MODIFY  add https-guacamole listener
  external-gateway-certs.yaml        MODIFY  add guacamole-tls Certificate
  cert-manager-cloudflare-secret.example.yaml  MODIFY  update comment to point at ExternalSecret

apps/keycloak/
  external-secret-db.yaml            CREATE  ExternalSecret → keycloak-db-app in keycloak ns
  external-secret-guacamole-db.yaml  CREATE  ExternalSecret → guacamole-db-creds in keycloak ns
  keycloak-realm-import-vcf.yaml         CREATE  KeycloakRealmImport for vcf realm + guacamole client
  postgres-cluster.yaml              MODIFY  add spec.managed.databases + spec.managed.roles
  kustomization.yaml                 MODIFY  add three new resources
  keycloak-db-secret.example.yaml    MODIFY  update comment to point at ExternalSecret

apps/guacamole/
  guacamole-app.yaml                 CREATE  ArgoCD Application (wave 2)
  kustomization.yaml                 CREATE  Kustomize config for tools ns
  external-secret.yaml               CREATE  ExternalSecrets for tools ns (db + oidc)
  deployment.yaml                    CREATE  guacamole + guacd sidecar Deployment
  service.yaml                       CREATE  ClusterIP Service guacamole-svc:80→8080
  httproute.yaml                     CREATE  HTTPRoute on external-gateway https-guacamole listener
  initdb-configmap.yaml              CREATE  ConfigMap with Guacamole PostgreSQL init SQL
  db-init-job.yaml                   CREATE  one-shot schema init Job (wave 3)
  guacamole.md                       CREATE  operations documentation

.github/workflows/validate.yaml      MODIFY  add apps/guacamole to kustomize-kubeconform loop
```

---

## Validation Commands

Run these after each task to catch errors early. Requires `kustomize` v5.8.1 and `kubeconform` v0.6.7 (same versions as CI).

```bash
CRD_SCHEMA="https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"

# validate a specific path
kustomize build platform | kubeconform -strict -summary -ignore-missing-schemas \
  -schema-location default -schema-location "$CRD_SCHEMA"

kustomize build apps/keycloak | kubeconform -strict -summary -ignore-missing-schemas \
  -schema-location default -schema-location "$CRD_SCHEMA"

kustomize build apps/guacamole | kubeconform -strict -summary -ignore-missing-schemas \
  -schema-location default -schema-location "$CRD_SCHEMA"

# validate all *-app.yaml files
find . -path ./.git -prune -o -name '*-app.yaml' -print0 \
  | xargs -0 kubeconform -strict -summary -ignore-missing-schemas \
      -schema-location default -schema-location "$CRD_SCHEMA"
```

---

## Task 1: ESO ArgoCD Application

**Files:** Create `platform/external-secrets-app.yaml`

- [ ] Create the file. Pin to a specific chart version — check the latest at <https://github.com/external-secrets/external-secrets/releases> before writing (replace `2.6.0` if newer stable exists):

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: external-secrets
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "-1"
spec:
  project: default
  source:
    repoURL: https://charts.external-secrets.io
    chart: external-secrets
    targetRevision: "2.6.0"
    helm:
      values: |
        installCRDs: true
  destination:
    server: https://kubernetes.default.svc
    namespace: external-secrets
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

- [ ] Validate: `find . -name 'external-secrets-app.yaml' | xargs kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"` — expect `Summary: 1 resource found, 1 valid`

- [ ] Commit: `git add platform/external-secrets-app.yaml && git commit -m "feat(platform): add External Secrets Operator ArgoCD app"`

---

## Task 2: ClusterSecretStore

**Files:** Create `platform/vault-secret-store.yaml`, modify `platform/kustomization.yaml`

- [ ] Create `platform/vault-secret-store.yaml`:

```yaml
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: vault
spec:
  provider:
    vault:
      server: "https://vault.mxe11.nl"
      path: "secret"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "external-secrets"
          serviceAccountRef:
            name: "external-secrets"
            namespace: "external-secrets"
```

- [ ] Add it to `platform/kustomization.yaml`:

```yaml
resources:
  - argocd-ingress.yaml
  - l2-networking.yaml
  - letsencrypt-clusterissuer.yaml
  - external-gateway-certs.yaml
  - vault-secret-store.yaml
```

- [ ] Validate: `kustomize build platform | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"` — the ClusterSecretStore CRD won't be in the catalog yet; `-ignore-missing-schemas` means it passes.

- [ ] Commit: `git add platform/vault-secret-store.yaml platform/kustomization.yaml && git commit -m "feat(platform): add Vault ClusterSecretStore"`

---

## Task 3: Cloudflare ExternalSecret

**Files:** Create `platform/cloudflare-external-secret.yaml`, modify `platform/kustomization.yaml`, modify `platform/cert-manager-cloudflare-secret.example.yaml`

- [ ] Create `platform/cloudflare-external-secret.yaml`. Note the explicit `namespace: cert-manager` — the platform kustomization sets no global namespace, so this lands in cert-manager correctly:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: cloudflare-api-token
  namespace: cert-manager
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault
    kind: ClusterSecretStore
  target:
    name: cloudflare-api-token
    creationPolicy: Owner
  data:
    - secretKey: api-token
      remoteRef:
        key: cert-manager/cloudflare
        property: api-token
```

- [ ] Add to `platform/kustomization.yaml`:

```yaml
resources:
  - argocd-ingress.yaml
  - l2-networking.yaml
  - letsencrypt-clusterissuer.yaml
  - external-gateway-certs.yaml
  - vault-secret-store.yaml
  - cloudflare-external-secret.yaml
```

- [ ] Update the header comment in `platform/cert-manager-cloudflare-secret.example.yaml`. Replace the "apply out-of-band" block with:

```yaml
# EXAMPLE ONLY — kept for reference. This Secret is now managed by ESO.
# See platform/cloudflare-external-secret.yaml — ESO syncs it from
# Vault path secret/data/cert-manager/cloudflare (key: api-token).
# No manual kubectl apply needed.
```

- [ ] Validate: `kustomize build platform | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"`

- [ ] Commit: `git add platform/cloudflare-external-secret.yaml platform/kustomization.yaml platform/cert-manager-cloudflare-secret.example.yaml && git commit -m "feat(platform): add Cloudflare ExternalSecret via ESO"`

---

## Task 4: Keycloak DB ExternalSecret

**Files:** Create `apps/keycloak/external-secret-db.yaml`, modify `apps/keycloak/kustomization.yaml`, modify `apps/keycloak/keycloak-db-secret.example.yaml`

- [ ] Create `apps/keycloak/external-secret-db.yaml`. The target Secret must be `kubernetes.io/basic-auth` with `username` and `password` keys — that's the exact shape both the CNPG Cluster and the Keycloak CR already expect:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: keycloak-db-app
  namespace: keycloak
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault
    kind: ClusterSecretStore
  target:
    name: keycloak-db-app
    creationPolicy: Owner
    template:
      type: kubernetes.io/basic-auth
  data:
    - secretKey: username
      remoteRef:
        key: keycloak/db
        property: username
    - secretKey: password
      remoteRef:
        key: keycloak/db
        property: password
```

- [ ] Add to `apps/keycloak/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: keycloak

resources:
  - certificate.yaml
  - gateway.yaml
  - httproute.yaml
  - postgres-cluster.yaml
  - keycloak-cr.yaml
  - external-secret-db.yaml
```

- [ ] Update the header comment in `apps/keycloak/keycloak-db-secret.example.yaml`. Replace the "apply out-of-band" block with:

```yaml
# EXAMPLE ONLY — kept for reference. This Secret is now managed by ESO.
# See apps/keycloak/external-secret-db.yaml — ESO syncs it from
# Vault path secret/data/keycloak/db (keys: username, password).
# No manual kubectl apply needed.
```

- [ ] Validate: `kustomize build apps/keycloak | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"`

- [ ] Commit: `git add apps/keycloak/external-secret-db.yaml apps/keycloak/kustomization.yaml apps/keycloak/keycloak-db-secret.example.yaml && git commit -m "feat(keycloak): replace out-of-band DB secret with ExternalSecret"`

---

## Task 5: Guacamole DB on CNPG

**Files:** Create `apps/keycloak/external-secret-guacamole-db.yaml`, modify `apps/keycloak/postgres-cluster.yaml`, modify `apps/keycloak/kustomization.yaml`

- [ ] Create `apps/keycloak/external-secret-guacamole-db.yaml`. This Secret lives in the `keycloak` namespace so CNPG can reference it as `passwordSecret`:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: guacamole-db-creds
  namespace: keycloak
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault
    kind: ClusterSecretStore
  target:
    name: guacamole-db-creds
    creationPolicy: Owner
  data:
    - secretKey: password
      remoteRef:
        key: guacamole/db
        property: password
```

- [ ] Add `spec.managed` to `apps/keycloak/postgres-cluster.yaml`. The existing spec block gains:

```yaml
  managed:
    databases:
      - name: guacamole
        ensure: present
        owner: guacamole
    roles:
      - name: guacamole
        ensure: present
        login: true
        passwordSecret:
          name: guacamole-db-creds
```

  The full `postgres-cluster.yaml` `spec:` section after edit should look like (read the current file first to preserve existing fields):
  Add the `managed:` block at the same indent level as `instances:`, `storage:`, etc.

- [ ] Add to `apps/keycloak/kustomization.yaml`:

```yaml
resources:
  - certificate.yaml
  - gateway.yaml
  - httproute.yaml
  - postgres-cluster.yaml
  - keycloak-cr.yaml
  - external-secret-db.yaml
  - external-secret-guacamole-db.yaml
```

- [ ] Validate: `kustomize build apps/keycloak | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"`

- [ ] Commit: `git add apps/keycloak/external-secret-guacamole-db.yaml apps/keycloak/postgres-cluster.yaml apps/keycloak/kustomization.yaml && git commit -m "feat(keycloak): add guacamole database and role to CNPG cluster"`

---

## Task 6: KeycloakRealmImport

**Files:** Create `apps/keycloak/keycloak-realm-import-vcf.yaml`, modify `apps/keycloak/kustomization.yaml`

The `KeycloakRealmImport` CR creates a `vcf` realm and a confidential OIDC client. It does **not** specify the client secret — Keycloak generates one on first import. After sync, retrieve the generated secret and store it in Vault (see Task 12).

> If a `vcf` realm already exists in Keycloak, the operator will log a conflict and skip the import. In that case, create the `guacamole` client manually in the Keycloak admin console with redirect URI `https://guacamole.lab.mxe11.nl/guacamole/*`, copy its secret to Vault, then delete this CR (or leave it — the operator won't re-import). **Important:** enable *Implicit flow* on the `guacamole` client — Guacamole's OpenID extension uses the implicit flow (`response_type=id_token`); without it Keycloak returns `unauthorized_client / Implicit flow is disabled`.

- [ ] Create `apps/keycloak/keycloak-realm-import-vcf.yaml`:

```yaml
apiVersion: k8s.keycloak.org/v2beta1
kind: KeycloakRealmImport
metadata:
  name: vcf
  namespace: keycloak
spec:
  keycloakCRName: keycloak
  realm:
    realm: vcf
    displayName: VCF
    enabled: true
    sslRequired: external
    registrationAllowed: false
    clients:
      - clientId: guacamole
        name: Guacamole
        description: Apache Guacamole remote desktop gateway
        protocol: openid-connect
        enabled: true
        publicClient: false
        standardFlowEnabled: true
        implicitFlowEnabled: true
        directAccessGrantsEnabled: false
        redirectUris:
          - "https://guacamole.lab.mxe11.nl/guacamole/*"
        webOrigins:
          - "https://guacamole.lab.mxe11.nl"
        attributes:
          post.logout.redirect.uris: "https://guacamole.lab.mxe11.nl/guacamole/*"
```

- [ ] Add to `apps/keycloak/kustomization.yaml`:

```yaml
resources:
  - certificate.yaml
  - gateway.yaml
  - httproute.yaml
  - postgres-cluster.yaml
  - keycloak-cr.yaml
  - external-secret-db.yaml
  - external-secret-guacamole-db.yaml
  - keycloak-realm-import-vcf.yaml
```

- [ ] Validate: `kustomize build apps/keycloak | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"`

- [ ] Commit: `git add apps/keycloak/keycloak-realm-import-vcf.yaml apps/keycloak/kustomization.yaml && git commit -m "feat(keycloak): add vcf realm + guacamole OIDC client via KeycloakRealmImport"`

---

## Task 7: Gateway TLS for Guacamole

**Files:** Modify `platform/argocd-ingress.yaml`, modify `platform/external-gateway-certs.yaml`

- [ ] Add the `https-guacamole` listener to the `external-gateway` Gateway in `platform/argocd-ingress.yaml`. The listeners list already has `http` and `https-status`. Add after `https-status`:

```yaml
    - name: https-guacamole
      protocol: HTTPS
      port: 443
      hostname: guacamole.lab.mxe11.nl
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: guacamole-tls
            namespace: kube-system
      allowedRoutes:
        namespaces:
          from: All
```

- [ ] Add the `guacamole-tls` Certificate to `platform/external-gateway-certs.yaml` after the existing `status-tls` Certificate (separate with `---`):

```yaml
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: guacamole-tls
  namespace: kube-system
spec:
  secretName: guacamole-tls
  renewBefore: 720h
  commonName: guacamole.lab.mxe11.nl
  dnsNames:
    - guacamole.lab.mxe11.nl
  issuerRef:
    name: letsencrypt-prod
    kind: ClusterIssuer
```

- [ ] Validate: `kustomize build platform | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"`

- [ ] Commit: `git add platform/argocd-ingress.yaml platform/external-gateway-certs.yaml && git commit -m "feat(platform): add guacamole TLS listener and cert to external-gateway"`

---

## Task 8: Guacamole App Skeleton

**Files:** Create `apps/guacamole/guacamole-app.yaml`, create `apps/guacamole/kustomization.yaml`

- [x] Create `apps/guacamole/guacamole-app.yaml`. Wave `2` ensures Keycloak (wave `1`) and the DB role are ready:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: guacamole
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "2"
spec:
  project: default
  source:
    repoURL: https://github.com/matjahs/synology-k3s.git
    targetRevision: HEAD
    path: apps/guacamole
  destination:
    server: https://kubernetes.default.svc
    namespace: tools
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [x] Create `apps/guacamole/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: tools

resources:
  - external-secret.yaml
  - deployment.yaml
  - service.yaml
  - httproute.yaml
  - initdb-configmap.yaml
  - db-init-job.yaml
```

- [ ] Validate the app manifest: `find . -name 'guacamole-app.yaml' | xargs kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"`

- [ ] Commit: `git add apps/guacamole/guacamole-app.yaml apps/guacamole/kustomization.yaml && git commit -m "feat(guacamole): add ArgoCD Application and Kustomize skeleton"`

---

## Task 9: Guacamole ExternalSecrets

**Files:** Create `apps/guacamole/external-secret.yaml`

The ExternalSecret lands in `tools` namespace (inherited from `kustomization.yaml`) and creates the DB password Secret. Guacamole's OIDC uses the implicit flow (no client secret), so no OIDC ExternalSecret is needed.

- [ ] Create `apps/guacamole/external-secret.yaml`:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: guacamole-db-app
  namespace: tools
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault
    kind: ClusterSecretStore
  target:
    name: guacamole-db-app
    creationPolicy: Owner
  data:
    - secretKey: password
      remoteRef:
        key: guacamole/db
        property: password
```

- [ ] Validate: `kustomize build apps/guacamole | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"`

- [ ] Commit: `git add apps/guacamole/external-secret.yaml && git commit -m "feat(guacamole): add ExternalSecrets for DB and OIDC credentials"`

---

## Task 10: Guacamole Deployment

**Files:** Create `apps/guacamole/deployment.yaml`

- [ ] Before writing, pin the image versions. Check Docker Hub for the latest stable tag:
  - `guacamole/guacamole` — <https://hub.docker.com/r/guacamole/guacamole/tags>
  - `guacamole/guacd` — <https://hub.docker.com/r/guacamole/guacd/tags>

  Both use the same version tag. As of the spec date, `1.5.5` is the latest stable. Replace below if a newer version exists.

- [ ] Create `apps/guacamole/deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: guacamole
  namespace: tools
  labels:
    app.kubernetes.io/name: guacamole
spec:
  replicas: 1
  selector:
    matchLabels:
      app: guacamole
  template:
    metadata:
      labels:
        app: guacamole
    spec:
      containers:
        - name: guacd
          image: guacamole/guacd:1.5.5
          ports:
            - containerPort: 4822
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 500m
              memory: 512Mi
          readinessProbe:
            tcpSocket:
              port: 4822
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            tcpSocket:
              port: 4822
            initialDelaySeconds: 10
            periodSeconds: 20
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
            seccompProfile:
              type: RuntimeDefault
        - name: guacamole
          image: guacamole/guacamole:1.5.5
          ports:
            - name: http
              containerPort: 8080
          env:
            - name: GUACD_HOSTNAME
              value: localhost
            - name: GUACD_PORT
              value: "4822"
            - name: POSTGRESQL_HOSTNAME
              value: keycloak-db-rw.keycloak.svc.cluster.local
            - name: POSTGRESQL_DATABASE
              value: guacamole
            - name: POSTGRESQL_USERNAME
              value: guacamole
            - name: POSTGRESQL_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: guacamole-db-app
                  key: password
            - name: OPENID_AUTHORIZATION_ENDPOINT
              value: https://keycloak.lab.mxe11.nl/realms/vcf/protocol/openid-connect/auth
            - name: OPENID_JWKS_ENDPOINT
              value: https://keycloak.lab.mxe11.nl/realms/vcf/protocol/openid-connect/certs
            - name: OPENID_ISSUER
              value: https://keycloak.lab.mxe11.nl/realms/vcf
            - name: OPENID_CLIENT_ID
              value: guacamole
            - name: OPENID_REDIRECT_URI
              value: https://guacamole.lab.mxe11.nl/guacamole/
            - name: OPENID_USERNAME_CLAIM_TYPE
              value: preferred_username
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          readinessProbe:
            httpGet:
              path: /guacamole/
              port: 8080
            initialDelaySeconds: 15
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /guacamole/
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 20
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
            seccompProfile:
              type: RuntimeDefault
```

- [ ] Validate: `kustomize build apps/guacamole | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"`

- [ ] Commit: `git add apps/guacamole/deployment.yaml && git commit -m "feat(guacamole): add guacamole+guacd sidecar Deployment"`

---

## Task 11: Service and HTTPRoute

**Files:** Create `apps/guacamole/service.yaml`, create `apps/guacamole/httproute.yaml`

- [ ] Create `apps/guacamole/service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: guacamole-svc
  namespace: tools
spec:
  selector:
    app: guacamole
  ports:
    - name: http
      port: 80
      targetPort: 8080
```

- [ ] Create `apps/guacamole/httproute.yaml`. The `sectionName: https-guacamole` must match the listener name added to the Gateway in Task 7:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: guacamole
  namespace: tools
spec:
  parentRefs:
    - name: external-gateway
      namespace: kube-system
      sectionName: https-guacamole
  hostnames:
    - guacamole.lab.mxe11.nl
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: guacamole-svc
          port: 80
```

- [ ] Validate: `kustomize build apps/guacamole | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"`

- [ ] Commit: `git add apps/guacamole/service.yaml apps/guacamole/httproute.yaml && git commit -m "feat(guacamole): add Service and HTTPRoute"`

---

## Task 12: DB Init ConfigMap and Job

**Files:** Create `apps/guacamole/initdb-configmap.yaml`, create `apps/guacamole/db-init-job.yaml`

The init SQL must match the Guacamole version in the Deployment (Task 10). Generate it from the same image tag.

- [ ] Generate the init SQL (requires Docker):

```bash
docker run --rm guacamole/guacamole:1.5.5 /opt/guacamole/bin/initdb.sh --postgresql > /tmp/guacamole-initdb.sql
cat /tmp/guacamole-initdb.sql   # verify it contains CREATE TABLE statements
```

- [ ] Create `apps/guacamole/initdb-configmap.yaml` — paste the SQL output from the previous step as the ConfigMap data value. The key must be `initdb.sql`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: guacamole-initdb
  namespace: tools
data:
  initdb.sql: |
    <paste the full output of initdb.sh --postgresql here>
```

- [ ] Create `apps/guacamole/db-init-job.yaml`. Wave `3` runs after the Deployment (wave `2`). The idempotency check means re-syncing ArgoCD won't re-run the schema. Once the Job reaches `Completed`, ArgoCD leaves it:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: guacamole-db-init
  namespace: tools
  annotations:
    argocd.argoproj.io/sync-wave: "3"
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: init
          image: postgres:16-alpine
          env:
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: guacamole-db-app
                  key: password
          command:
            - /bin/sh
            - -c
            - |
              set -e
              HOST=keycloak-db-rw.keycloak.svc.cluster.local
              if psql -h "$HOST" -U guacamole -d guacamole \
                      -c "\dt guacamole_user" 2>/dev/null | grep -q guacamole_user; then
                echo "Schema already initialized, skipping."
                exit 0
              fi
              echo "Initializing Guacamole schema..."
              psql -h "$HOST" -U guacamole -d guacamole -f /init/initdb.sql
              echo "Done."
          volumeMounts:
            - name: initdb
              mountPath: /init
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
            seccompProfile:
              type: RuntimeDefault
      volumes:
        - name: initdb
          configMap:
            name: guacamole-initdb
```

- [ ] Validate: `kustomize build apps/guacamole | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CRD_SCHEMA"` — expect all resources valid.

- [ ] Commit: `git add apps/guacamole/initdb-configmap.yaml apps/guacamole/db-init-job.yaml && git commit -m "feat(guacamole): add DB init ConfigMap and Job"`

---

## Task 13: OIDC Client Secret — not applicable (implicit flow)

> **Obsolete (2026-06-18).** Guacamole's OpenID extension uses the **implicit flow** (`response_type=id_token`, validated via JWKS) and does **not** use a client secret — there is nothing to retrieve or store. The `guacamole-oidc-secret` ExternalSecret and the `OPENID_CLIENT_SECRET` env were removed (Tasks 9–10); the Vault path `secret/guacamole/oidc` is unused and may be deleted. Just ensure the `guacamole` client in the **vcf** realm has *Implicit flow* enabled and redirect URI `https://guacamole.lab.mxe11.nl/guacamole/*` registered.

---

## Task 14: Update CI Validation

**Files:** Modify `.github/workflows/validate.yaml`

- [ ] Add `apps/guacamole` to the kustomize-kubeconform loop. In `.github/workflows/validate.yaml`, change:

```yaml
          for d in platform apps/cyberchef apps/keycloak; do
```

to:

```yaml
          for d in platform apps/cyberchef apps/keycloak apps/guacamole; do
```

- [ ] Validate locally to confirm the build passes:

```bash
kustomize build apps/guacamole | kubeconform -strict -summary -ignore-missing-schemas \
  -schema-location default \
  -schema-location "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
```

Expected output: `Summary: N resources found, N valid, 0 invalid, 0 errors, 0 skipped`

- [ ] Commit: `git add .github/workflows/validate.yaml && git commit -m "ci: add apps/guacamole to kustomize-kubeconform validation"`

---

## Task 15: Documentation

**Files:** Create `platform/external-secrets.md`, create `apps/guacamole/guacamole.md`

- [ ] Create `platform/external-secrets.md`:

```markdown
# External Secrets Operator

ESO syncs secrets from Vault (https://vault.mxe11.nl) into Kubernetes Secrets via the `vault` ClusterSecretStore.

## Vault Setup (one-time, out-of-band)

```bash
kubectl get configmap -n kube-system kube-root-ca.crt -o jsonpath='{.data.ca\.crt}' > /tmp/k3s-ca.crt

vault auth enable kubernetes
vault write auth/kubernetes/config \
  kubernetes_host="https://172.16.30.8:6443" \
  kubernetes_ca_cert=@/tmp/k3s-ca.crt

vault policy write external-secrets - <<EOF
path "secret/data/*" { capabilities = ["read"] }
EOF

vault write auth/kubernetes/role/external-secrets \
  bound_service_account_names=external-secrets \
  bound_service_account_namespaces=external-secrets \
  policies=external-secrets \
  ttl=1h
```

## Vault KV Paths

| Path | Keys | Produces Secret |
|---|---|---|
| `secret/data/keycloak/db` | `username`, `password` | `keycloak/keycloak-db-app` |
| `secret/data/cert-manager/cloudflare` | `api-token` | `cert-manager/cloudflare-api-token` |
| `secret/data/guacamole/db` | `password` | `keycloak/guacamole-db-creds`, `tools/guacamole-db-app` |
| `secret/data/guacamole/oidc` | `client-secret` | `tools/guacamole-oidc-secret` |

## Verify

```bash
kubectl get clustersecretstore vault
kubectl get externalsecret -A
# All should show READY=True and STATUS=SecretSynced
```

```

- [ ] Create `apps/guacamole/guacamole.md`:

```markdown
# Guacamole

Apache Guacamole provides HTML5 browser-based access to RDP/SSH/VNC targets.

## Components

| Piece | Notes |
|---|---|
| `guacamole` container | Tomcat webapp, port 8080, OIDC via Keycloak `vcf` realm |
| `guacd` sidecar | Remote desktop proxy, `localhost:4822` |
| `guacamole-svc` | ClusterIP, port 80 → 8080 |
| `guacamole` HTTPRoute | TLS-terminated at `external-gateway` https-guacamole listener |
| `guacamole-db-init` Job | One-shot schema init via `postgres:16-alpine` |
| DB | `guacamole` database on `keycloak-db` CNPG cluster, role `guacamole` |

## Access

Browse to https://guacamole.lab.mxe11.nl/guacamole/ — you are redirected to Keycloak for login.

## First-time Setup

The init SQL creates a default DB user `guacadmin` / `guacadmin`. Since both PostgreSQL and OIDC auth are active, the Guacamole login page shows both a username/password form and an OIDC redirect button. Log in as `guacadmin` first, change the password, then set up connections. OIDC users (via Keycloak) can log in via the button and are auto-created on first login, but they start with no permissions — grant them access to connections from the admin account.

1. Settings → Connections → New Connection
2. Protocol: RDP
3. Hostname: `corp-ca01.lab.mxe11.nl`, Port: `3389`
4. Fill in domain credentials

## OIDC Client Secret Rotation

If the Keycloak client secret is rotated:
1. Update Vault: `vault kv put secret/guacamole/oidc client-secret=<new>`
2. ESO syncs within 1h, or: `kubectl annotate externalsecret -n tools guacamole-oidc-secret force-sync=$(date +%s) --overwrite`
3. Restart the pod: `kubectl rollout restart deployment/guacamole -n tools`

## Verify

```bash
kubectl get pods -n tools                          # guacamole-* Running
kubectl get httproute -n tools guacamole           # Accepted
kubectl get externalsecret -n tools                # SecretSynced
kubectl get job -n tools guacamole-db-init         # Complete
kubectl logs job/guacamole-db-init -n tools        # "Done." or "Schema already initialized"
```

```

- [ ] Commit: `git add platform/external-secrets.md apps/guacamole/guacamole.md && git commit -m "docs: add ESO and Guacamole operations documentation"`

---

## Post-Sync Checklist

Run after pushing to `main` and ArgoCD syncs all waves:

```bash
# ESO
kubectl get clustersecretstore vault                         # Ready
kubectl get externalsecret -A                                # All SecretSynced

# Keycloak DB
kubectl get cluster -n keycloak keycloak-db                 # Healthy
kubectl exec -n keycloak keycloak-db-1 -- psql -U postgres -c "\l" | grep guacamole
# should show guacamole database

# Guacamole
kubectl get pods -n tools                                    # guacamole-* Running
kubectl get job -n tools guacamole-db-init                  # Complete
kubectl get httproute -n tools guacamole                    # Accepted, Programmed
curl -I https://guacamole.lab.mxe11.nl/guacamole/           # 200 OK
```
