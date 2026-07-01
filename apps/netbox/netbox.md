# Netbox

IPAM, network documentation, and **DNS source of truth** for `lab.mxe11.nl` at
https://netbox.lab.mxe11.nl.

## Architecture

- **Image:** `nexus.lab.mxe11.nl/netbox/netbox` (custom build from [matjahs/lab-netbox](https://github.com/matjahs/lab-netbox) with `netbox-plugin-dns`)
- **Web + worker:** same image; worker runs `rqworker`
- **PostgreSQL:** CloudNativePG cluster `netbox-db` (1 instance, 4Gi PVC)
- **Redis:** single `redis:7-alpine` pod (db `0` tasks, db `1` cache)
- **Media:** 10Gi RWO PVC on `synology-iscsi`
- **Secrets:** Vault via External Secrets Operator
- **Ingress:** Cilium Gateway `external-gateway` + cert-manager TLS

## DNS source of truth

NetBox DNS holds all `lab.mxe11.nl` records. See [platform/dns.md](../../platform/dns.md).

| Flow | Component |
|------|-----------|
| HTTPRoute → NetBox | `dns-netbox-sync` in `dns` namespace |
| NetBox → UniFi | `octodns-sync` CronJob in `dns` namespace |

**Bootstrap zone (once, after plugin image is live):**

```bash
export NETBOX_TOKEN=$(vault kv get -field=token secret/netbox/api)
../../scripts/bootstrap-netbox-dns-zone.sh lab.mxe11.nl
```

## Vault (required before first sync)

```bash
vault kv put secret/netbox/db \
  username=netbox \
  password=<strong-password>

vault kv put secret/netbox/app \
  secret_key=<django-secret-key> \
  superuser_name=admin \
  superuser_email=admin@lab.mxe11.nl \
  superuser_password=<strong-password>

vault kv put secret/netbox/api \
  token=<netbox-api-token-with-write>
```

Or use `./scripts/populate-dns-vault.sh` for `secret/netbox/api` and `secret/dns/unifi`.

Generate a Django secret key:

```bash
openssl rand -base64 50
```

See also [platform/external-secrets.md](../../platform/external-secrets.md).

## Image upgrades

Routine releases: merge Release PR in `matjahs/lab-netbox` → CI pushes to Nexus and
opens a GitOps PR here. Do not edit `newTag` manually unless bypassing CI.

## First login

After Argo CD sync and pods are healthy, log in at https://netbox.lab.mxe11.nl with the `superuser_name` / `superuser_password` from Vault. The superuser is created on first web pod startup only.

**First boot** runs a large Django migration set and can take 10–15 minutes on a homelab node. The web Deployment uses an extended startup probe (`failureThreshold: 120`) so Kubernetes does not kill the pod mid-migration.

## Verify

```bash
kubectl get externalsecret -n netbox
kubectl get cluster netbox-db -n netbox
kubectl get pods -n netbox
kubectl get certificate netbox-tls -n kube-system
kubectl get httproute -n netbox
curl -I https://netbox.lab.mxe11.nl/login/
kubectl -n dns logs deploy/dns-netbox-sync --tail=30
```

## Optional follow-ups

- Keycloak OIDC SSO
- CNPG scheduled backups to S3/MinIO
- CSV/API import of existing homelab inventory
- IPAM DNSsync for PTR automation
