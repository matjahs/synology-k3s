# Design: Guacamole + External Secrets Operator + Vault

**Date:** 2026-06-15
**Status:** Approved

## Overview

Add Apache Guacamole to the cluster so users authenticated via Keycloak (`keycloak.lab.mxe11.nl`) can RDP into `corp-ca01.lab.mxe11.nl` through a browser. At the same time, replace all out-of-band secret management with External Secrets Operator (ESO) pulling from a Vault instance at `vault.mxe11.nl`. This fulfils the Tier-1 secrets TODO in `TODO.md`.

---

## Scope

Three parallel tracks, applied in sync-wave order:

| Track                  | What changes                                                                                                                |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **Platform**           | Add ESO Helm app + ClusterSecretStore (Vault, k8s auth); add guacamole TLS listener to external-gateway; add guacamole cert |
| **Keycloak migration** | Replace two out-of-band secrets with ExternalSecrets; extend CNPG cluster with guacamole DB + role                          |
| **Guacamole app**      | New `apps/guacamole/` with deployment, service, httproute, DB init job, KeycloakRealmImport, ExternalSecrets                |

---

## Section 1: Platform — ESO + Vault

**`platform/external-secrets-app.yaml`** (sync-wave `-1`)
ArgoCD Application deploying the `external-secrets` Helm chart into the `external-secrets` namespace. Wave `-1` ensures CRDs exist before platform kustomization resources apply.

**`platform/vault-secret-store.yaml`** (added to `platform/kustomization.yaml`)
`ClusterSecretStore` pointing at `https://vault.mxe11.nl`, KV v2 engine (`secret/`), Kubernetes auth:

```yaml
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

**`platform/external-secrets.md`**
Documents the out-of-band Vault-side setup required once:

```bash
# Enable Kubernetes Auth
vault auth enable kubernetes
vault write auth/kubernetes/config \
  kubernetes_host="https://172.16.30.8:6443" \
  kubernetes_ca_cert=@/tmp/k3s-ca.crt

# Policy: Read All Secrets
vault policy write external-secrets - <<EOF
path "secret/data/*" { capabilities = ["read"] }
EOF

# Bind ESO's ServiceAccount to the Policy
vault write auth/kubernetes/role/external-secrets \
  bound_service_account_names=external-secrets \
  bound_service_account_namespaces=external-secrets \
  policies=external-secrets \
  ttl=1h
```

### Vault KV Paths

| Path                                  | Keys                   | Consumed by                                    |
| ------------------------------------- | ---------------------- | ---------------------------------------------- |
| `secret/data/keycloak/db`             | `username`, `password` | CNPG bootstrap + Keycloak CR                   |
| `secret/data/cert-manager/cloudflare` | `api-token`            | cert-manager DNS-01 issuer                     |
| `secret/data/guacamole/db`            | `password`             | CNPG managed role + guacamole pod              |
| `secret/data/guacamole/oidc`          | `client-secret`        | guacamole OIDC extension + KeycloakRealmImport |

### Kubernetes Auth — How It Works with External Vault

Vault validates ServiceAccount JWTs by calling the k3s TokenReview API (`https://<k3s-api>:6443`). Since Vault and k3s share the same LAN, this works without extra tunnelling. The `kubernetes_ca_cert` tells Vault how to trust the k3s API TLS cert.

### Gateway + TLS Additions

**`platform/argocd-ingress.yaml`** — add listener to `external-gateway`:

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

**`platform/external-gateway-certs.yaml`** — add Certificate:

```yaml
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

---

## Section 2: Keycloak — Secrets Migration

### `apps/keycloak/external-secret-db.yaml`

`ExternalSecret` in `keycloak` namespace. Reads `secret/data/keycloak/db` → produces `keycloak-db-app` Secret with type `kubernetes.io/basic-auth` and keys `username`/`password`. Shape is identical to the existing out-of-band Secret, so `postgres-cluster.yaml` and `keycloak-cr.yaml` need no changes.

### `platform/cloudflare-external-secret.yaml`

`ExternalSecret` with `namespace: cert-manager`. Reads `secret/data/cert-manager/cloudflare` → produces `cloudflare-api-token` Secret. Lives in `platform/` because cert-manager has no `apps/` directory.

### `.example.yaml` Files

Both `keycloak-db-secret.example.yaml` and `cert-manager-cloudflare-secret.example.yaml` remain in git as reference but get their header comments updated: replace the "apply out-of-band" instructions with a pointer to the ExternalSecret that now manages this Secret.

### `apps/keycloak/postgres-cluster.yaml` — CNPG Managed Databases

Add to the existing `Cluster` spec:

```yaml
spec:
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
          name: guacamole-db-creds   # created by ExternalSecret in keycloak ns
```

The `guacamole-db-creds` Secret (key `password`) is created by a separate ExternalSecret in the `keycloak` namespace (see below).

---

## Section 3: Guacamole App

### Directory Structure

Resources that belong to the `keycloak` namespace live in `apps/keycloak/` (where Kustomize sets `namespace: keycloak`). Resources that belong to `tools` live in `apps/guacamole/`.

```plain
apps/keycloak/
  external-secret-guacamole-db.yaml   # ExternalSecret → guacamole-db-creds (keycloak ns, for CNPG role)
  keycloak-realm-import.yaml          # KeycloakRealmImport CR (keycloak ns)

apps/guacamole/
  guacamole-app.yaml          # ArgoCD Application, wave 2
  kustomization.yaml
  deployment.yaml             # guacamole + guacd sidecar
  service.yaml
  httproute.yaml
  external-secret.yaml        # two ExternalSecrets in tools ns: guacamole-db-app + guacamole-oidc-secret
  initdb-configmap.yaml       # Guacamole PostgreSQL init SQL
  db-init-job.yaml            # one-shot schema init Job, wave 3
  guacamole.md
```

### `guacamole-app.yaml`

ArgoCD Application, sync-wave `2` (after Keycloak's wave `1`), destination namespace `tools`, `CreateNamespace=true`.

### `external-secret.yaml`

Three `ExternalSecret` resources:

| Namespace  | Vault path                   | Produces Secret                                | Consumed by                                                |
| ---------- | ---------------------------- | ---------------------------------------------- | ---------------------------------------------------------- |
| `keycloak` | `secret/data/guacamole/db`   | `guacamole-db-creds` (key: `password`)         | CNPG managed role                                          |
| `tools`    | `secret/data/guacamole/db`   | `guacamole-db-app` (key: `password`)           | guacamole pod `POSTGRESQL_PASSWORD`                        |
| `tools`    | `secret/data/guacamole/oidc` | `guacamole-oidc-secret` (key: `client-secret`) | guacamole pod `OPENID_CLIENT_SECRET` + KeycloakRealmImport |

### `keycloak-realm-import.yaml`

`KeycloakRealmImport` CR in the `keycloak` namespace. Declares a `lab` realm with one confidential OIDC client:

- Client ID: `guacamole`
- Protocol: `openid-connect`
- Access type: confidential
- Valid redirect URIs: `https://guacamole.lab.mxe11.nl/guacamole/*`
- Client secret: mounted from `guacamole-oidc-secret`

> **Note:** If a `lab` realm already exists in Keycloak, the operator will skip the import. In that case, create the `guacamole` OIDC client manually in the Keycloak admin console and ensure the client secret matches what's in Vault at `secret/data/guacamole/oidc`.

### `deployment.yaml`

Single Deployment in `tools` namespace, two containers:

| Container   | Image                       | Role                                   |
| ----------- | --------------------------- | -------------------------------------- |
| `guacd`     | `guacamole/guacd:<ver>`     | Remote desktop proxy, `localhost:4822` |
| `guacamole` | `guacamole/guacamole:<ver>` | Web frontend, port `8080`              |

Key env vars on the `guacamole` container:

```shell
GUACD_HOSTNAME=localhost
GUACD_PORT=4822
POSTGRESQL_HOSTNAME=keycloak-db-rw.keycloak.svc.cluster.local
POSTGRESQL_DATABASE=guacamole
POSTGRESQL_USERNAME=guacamole
POSTGRESQL_PASSWORD=<from guacamole-db-app secret>
OPENID_AUTHORIZATION_ENDPOINT=https://keycloak.lab.mxe11.nl/realms/lab/protocol/openid-connect/auth
OPENID_JWKS_ENDPOINT=https://keycloak.lab.mxe11.nl/realms/lab/protocol/openid-connect/certs
OPENID_ISSUER=https://keycloak.lab.mxe11.nl/realms/lab
OPENID_CLIENT_ID=guacamole
OPENID_CLIENT_SECRET=<from guacamole-oidc-secret>
OPENID_REDIRECT_URI=https://guacamole.lab.mxe11.nl/guacamole/
OPENID_USERNAME_CLAIM_TYPE=preferred_username
```

Both containers use `securityContext` matching the existing cluster pattern (drop ALL capabilities, `allowPrivilegeEscalation: false`, `RuntimeDefault` seccomp).

### `initdb-configmap.yaml` + `db-init-job.yaml`

**ConfigMap** holds the Guacamole PostgreSQL init SQL, captured during implementation:

```bash
docker run --rm guacamole/guacamole:<ver> /opt/guacamole/bin/initdb.sh --postgresql
```

**Job** (sync-wave `3`) uses `postgres:16-alpine`. Idempotent check before running:

```bash
psql ... -c "\dt guacamole_user" | grep -q guacamole_user \
  && echo "schema exists, skipping" \
  || psql ... -f /init/initdb.sql
```

Password from `guacamole-db-app` Secret via `PGPASSWORD`. Once the Job completes, ArgoCD leaves it in `Completed` state and does not re-run it.

### `service.yaml`

ClusterIP Service `guacamole-svc`, port `80` → pod port `8080`.

### `httproute.yaml`

Attaches to `external-gateway` in `kube-system`, hostname `guacamole.lab.mxe11.nl`, `sectionName: https-guacamole`, routes to `guacamole-svc:80`.

### RDP Connection Setup

After Guacamole is running, log into `https://guacamole.lab.mxe11.nl/guacamole/` as the Guacamole admin, navigate to **Settings → Connections**, and create an RDP connection:

- Hostname: `corp-ca01.lab.mxe11.nl`
- Port: `3389`
- Authentication: domain credentials as appropriate

---

## Sync-Wave Summary

| Wave | Resource                                                                   |
| ---- | -------------------------------------------------------------------------- |
| `-1` | `external-secrets-app.yaml` (ESO Helm app)                                 |
| `-1` | `keycloak-operator-app.yaml` (existing)                                    |
| `0`  | `platform-app.yaml` (ClusterSecretStore, gateway certs, listeners)         |
| `1`  | `keycloak-app.yaml` (Keycloak CR, ExternalSecrets, CNPG with guacamole DB) |
| `2`  | `guacamole-app.yaml` (Deployment, Service, HTTPRoute, KeycloakRealmImport) |
| `3`  | DB init Job (within guacamole app, annotated wave `3`)                     |

---

## Files Changed / Added

### New Files

- `platform/external-secrets-app.yaml`
- `platform/vault-secret-store.yaml`
- `platform/cloudflare-external-secret.yaml`
- `platform/external-secrets.md`
- `apps/guacamole/guacamole-app.yaml`
- `apps/guacamole/kustomization.yaml`
- `apps/guacamole/deployment.yaml`
- `apps/guacamole/service.yaml`
- `apps/guacamole/httproute.yaml`
- `apps/guacamole/external-secret.yaml`
- `apps/guacamole/initdb-configmap.yaml`
- `apps/guacamole/db-init-job.yaml`
- `apps/guacamole/keycloak-realm-import.yaml`
- `apps/guacamole/guacamole.md`

### Modified Files

- `platform/kustomization.yaml` — add `vault-secret-store.yaml`, `cloudflare-external-secret.yaml`
- `platform/argocd-ingress.yaml` — add `https-guacamole` listener
- `platform/external-gateway-certs.yaml` — add `guacamole-tls` Certificate
- `apps/keycloak/postgres-cluster.yaml` — add `spec.managed.databases` + `spec.managed.roles`
- `apps/keycloak/kustomization.yaml` — add `external-secret-db.yaml`
- `apps/keycloak/external-secret-db.yaml` — new file in keycloak app
- `apps/keycloak/keycloak-db-secret.example.yaml` — update header comment
- `platform/cert-manager-cloudflare-secret.example.yaml` — update header comment

---

## Out-Of-Band Prerequisites (Before First Sync)

1. **Vault KV paths populated** — put the four secret paths in Vault before ArgoCD syncs
2. **Vault kubernetes auth configured** — run the `vault auth enable` / `vault write` commands in `platform/external-secrets.md`
3. **`lab` realm in Keycloak** — if it already exists, create the `guacamole` OIDC client manually and ensure its secret matches `secret/data/guacamole/oidc`
