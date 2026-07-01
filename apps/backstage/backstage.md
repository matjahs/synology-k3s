# Backstage

Developer portal reached at <https://backstage.lab.mxe11.nl> — TLS is
terminated at the shared external-gateway via a Let's Encrypt cert
(`backstage-tls`, issued by the `letsencrypt-prod` ClusterIssuer; the listener
lives in [`platform/argocd-ingress.yaml`](../../platform/argocd-ingress.yaml)
and the `Certificate` in
[`platform/external-gateway-certs.yaml`](../../platform/external-gateway-certs.yaml)).

The Backstage **application** (source, Dockerfile, CI) lives in a separate
repository. This repo only deploys the Kubernetes stack and references the image
published to Nexus.

## Layout

| File | Purpose |
|------|---------|
| `backstage-app.yaml` | Argo CD `Application` (discovered by the root app-of-apps). |
| `kustomization.yaml` | Namespace, resources, and pinned Nexus image tag. |
| `backstage.yaml` / `backstage-service.yaml` | Backstage Deployment + ClusterIP. |
| `postgres.yaml` / `postgres-service.yaml` / `postgres-storage.yaml` | Dedicated Postgres 13 + 2Gi PVC on `synology-iscsi`. |
| `httproute.yaml` | Gateway routes (TLS + http→https redirect). |
| `external-secret.yaml` | ESO → Vault for DB, GitHub token, and Nexus pull creds. |

## External Backstage repo contract

Your Backstage app repo must produce an image at
`nexus.lab.mxe11.nl/backstage/backstage:<tag>` and bake production config into
the image.

### `app-config.production.yaml`

```yaml
app:
  baseUrl: https://backstage.lab.mxe11.nl

backend:
  baseUrl: https://backstage.lab.mxe11.nl
  listen:
    port: 7007
  cors:
    origin: https://backstage.lab.mxe11.nl
  database:
    client: pg
    connection:
      host: ${POSTGRES_HOST}
      port: ${POSTGRES_PORT}
      user: ${POSTGRES_USER}
      password: ${POSTGRES_PASSWORD}

integrations:
  github:
    - host: github.com
      token: ${GITHUB_TOKEN}
```

Auth provider (guest, Keycloak OIDC, etc.) is your choice in the external repo.

### CI in external repo

Releases are automated via **release-please** and GitHub Actions in
[matjahs/backstage](https://github.com/matjahs/backstage):

1. Merge the Release PR in the backstage repo (version bump + changelog).
2. CI builds for `linux/amd64`, pushes to Nexus, and opens a PR here bumping `newTag`.
3. Merge that PR — Argo CD rolls the Deployment.

Do **not** edit `newTag` manually for routine releases; the deploy tag is updated by the automated GitOps PR.

Required secrets in the backstage repo: `NEXUS_USERNAME`, `NEXUS_PASSWORD`, `SYNOLOGY_K3S_PAT`.

Manual local build (emergency only):

```sh
docker buildx build --platform linux/amd64 -t nexus.lab.mxe11.nl/backstage/backstage:<tag> --push .
```

Use GitHub Actions secrets `NEXUS_USERNAME` / `NEXUS_PASSWORD` for
`docker login nexus.lab.mxe11.nl` — these must be **Nexus** credentials
(e.g. `admin`), not a GitHub token.

See [`apps/nexus/nexus.md`](../nexus/nexus.md) for Nexus Docker setup and
`401 Unauthorized` troubleshooting.

### One-time Nexus setup

1. Follow the Docker registry section in [`apps/nexus/nexus.md`](../nexus/nexus.md)
   (Base URL, Docker Bearer Token Realm, hosted repo `backstage`).
2. Run the external-repo CI once to publish the first image **before** Argo CD
   syncs this app.

## Vault secrets (before first sync)

Populate Vault paths (see also [`platform/external-secrets.md`](../../platform/external-secrets.md)):

```bash
vault kv put secret/backstage/db \
  username=backstage \
  password=<strong-password>

vault kv put secret/backstage/github \
  token=<github-pat>

vault kv put secret/backstage/nexus-docker \
  username=<nexus-user> \
  password=<nexus-password>

vault kv put secret/backstage/argocd \
  token=<argocd-api-token>

vault kv put secret/backstage/vault \
  secret=<vault-token-for-backstage-plugin>
```

Vault listens on **`:8200`** (native TLS on the Pi). The Backstage image
bakes `https://vault.mxe11.nl:8200` into `app-config.production.yaml`; do not
use port 443 (HAProxy was removed).

If a GitHub PAT was ever committed to git, **rotate it** in GitHub before
storing the new value in Vault.

ESO produces these Kubernetes Secrets in the `backstage` namespace:

| Vault path | K8s Secret | Keys |
|------------|------------|------|
| `secret/backstage/db` | `postgres-secrets` | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB=backstage` |
| `secret/backstage/github` | `backstage-secrets` | `GITHUB_TOKEN` |
| `secret/backstage/keycloak` | `backstage-secrets` | `AUTH_KEYCLOAK_CLIENT_SECRET` |
| `secret/backstage/session` | `backstage-secrets` | `AUTH_SESSION_SECRET` |
| `secret/backstage/argocd` | `backstage-secrets` | `ARGOCD_AUTH_TOKEN` |
| `secret/backstage/vault` | `backstage-secrets` | `VAULT_STATIC_SECRET` |
| `secret/backstage/nexus-docker` | `nexus-docker-creds` | docker-registry pull secret for Nexus |

The Backstage Deployment also sets `POSTGRES_HOST=postgres` and
`POSTGRES_PORT=5432` as plain env vars.

## Image tag

The deploy tag is set in `kustomization.yaml` (`images[].newTag`). Kustomize overrides the placeholder tag in `backstage.yaml`.

Routine bumps arrive via automated PRs from the backstage release workflow. Argo CD rolls the Deployment on sync.

## Verify

```bash
# ESO ready
kubectl get externalsecret -n backstage
kubectl get secret -n backstage postgres-secrets backstage-secrets nexus-docker-creds

# Workloads
kubectl get pods -n backstage -w
kubectl logs -n backstage deploy/backstage

# Routing + cert
kubectl get httproute -n backstage
kubectl get certificate -n kube-system backstage-tls
curl -I https://backstage.lab.mxe11.nl/healthcheck
```

DNS for `backstage.lab.mxe11.nl` is managed by **dns-netbox-sync** → NetBox DNS →
OctoDNS → UniFi. See `platform/dns.md`.

## Notes

- Postgres runs as a single-replica Deployment with `Recreate` strategy (RWO PVC).
- First Backstage startup may take a minute while migrations run against Postgres.
- Keycloak OIDC auth can replace guest auth later using the existing Keycloak
  deployment — see `apps/keycloak/keycloak.md`.
