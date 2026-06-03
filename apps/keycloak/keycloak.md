# Keycloak (Operator + CloudNativePG)

Keycloak runs via the **upstream Keycloak Operator** backed by a **CloudNativePG**
Postgres cluster. This replaced the Bitnami Helm chart, which carries a
pull-availability risk after Bitnami moved its catalog to "Secure Images" /
`bitnamilegacy` in 2025.

## Components

| Piece | Where | Notes |
|---|---|---|
| Keycloak Operator + CRDs | [`platform/keycloak-operator-app.yaml`](../../platform/keycloak-operator-app.yaml) | Upstream `keycloak-k8s-resources` pinned to `26.6.2`, into the `keycloak` ns (operator watches its own ns) |
| CloudNativePG operator | [`platform/cloudnative-pg-app.yaml`](../../platform/cloudnative-pg-app.yaml) | Helm chart `0.28.2`, ns `cnpg-system`, cluster-wide |
| Postgres `Cluster` | [`postgres-cluster.yaml`](postgres-cluster.yaml) | `keycloak-db`, 1 instance, 8Gi on `synology-iscsi` |
| `Keycloak` CR | [`keycloak-cr.yaml`](keycloak-cr.yaml) | DB → `keycloak-db-rw:5432`, edge proxy (`xforwarded`), ingress disabled |
| Gateway + HTTPRoutes | [`gateway.yaml`](gateway.yaml), [`httproute.yaml`](httproute.yaml) | TLS terminated at the Cilium Gateway; https → `keycloak-service:8080`, http → 301 https |
| TLS cert (Let's Encrypt) | [`certificate.yaml`](certificate.yaml) | Issued by the cluster-wide `letsencrypt-prod` ClusterIssuer ([`platform/letsencrypt-clusterissuer.yaml`](../../platform/letsencrypt-clusterissuer.yaml)) via Cloudflare DNS-01; auto-renews |

Sync order: both operators are sync-wave `-1`; the `keycloak` app (the CRs) is
wave `1`. Argo CD self-heal converges if a CR is applied before its CRD exists.

## One-time setup

Apply the DB credentials Secret **out-of-band** (not in git — see the Tier-1
SOPS TODO). Fill in [`keycloak-db-secret.example.yaml`](keycloak-db-secret.example.yaml):

```bash
kubectl create namespace keycloak --dry-run=client -o yaml | kubectl apply -f -
cp keycloak-db-secret.example.yaml /tmp/kc-db.yaml
$EDITOR /tmp/kc-db.yaml            # set a strong password
kubectl apply -f /tmp/kc-db.yaml
shred -u /tmp/kc-db.yaml
```

The same `keycloak-db-app` Secret is used by CNPG (to create the `keycloak`
role) and by the Keycloak CR (to connect). CNPG's bootstrap and Keycloak both
fail-and-retry until it exists — expected.

### TLS / Let's Encrypt

The `keycloak-tls` cert is issued by the cluster-wide `letsencrypt-prod`
ClusterIssuer using **Cloudflare DNS-01** (HTTP-01 can't work — the cluster
isn't publicly reachable, and `lab.mxe11.nl` is UniFi *local* DNS). That issuer
needs a Cloudflare API token Secret in the `cert-manager` namespace, applied
**out-of-band** — see
[`platform/cert-manager-cloudflare-secret.example.yaml`](../../platform/cert-manager-cloudflare-secret.example.yaml).
Until it exists, the `Certificate` stays `False`/`Pending` and the Gateway
serves no usable cert. Check issuance with:

```bash
kubectl get certificate -n keycloak keycloak-tls
kubectl describe certificate -n keycloak keycloak-tls   # events show DNS-01 progress
kubectl get clusterissuer letsencrypt-prod
```

## Access

```bash
# Admin console credentials (operator-generated):
kubectl get secret keycloak-initial-admin -n keycloak \
  -o jsonpath='{.data.username}' | base64 -d; echo
kubectl get secret keycloak-initial-admin -n keycloak \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

Then browse to <https://keycloak.lab.mxe11.nl> (trusted Let's Encrypt cert).

## Verify

```bash
kubectl get pods -n cnpg-system                    # operator running
kubectl get pods -n keycloak                        # operator + keycloak-0 + keycloak-db-1
kubectl get cluster -n keycloak keycloak-db         # Cluster in healthy state
kubectl get keycloak -n keycloak keycloak           # Ready
kubectl get httproute -n keycloak                   # both routes Accepted
```

## Notes / follow-ups

- **Backups:** CNPG supports scheduled base-backups + WAL archiving, but needs an
  S3-compatible target (e.g. MinIO on the NAS). Add `spec.backup` to the Cluster
  plus a `ScheduledBackup` once that exists. Tracked in `TODO.md`.
- **HA:** `instances: 1` for both Keycloak and Postgres — this is a single-node
  k3s cluster. Raise once more nodes join.
- **Realms:** manage declaratively later with `KeycloakRealmImport` CRs.
