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

Optional SOA defaults (override if needed): `DNS_SOA_MNAME` (default `ns1.<zone>`),
`DNS_SOA_RNAME` (default `hostmaster.<zone>`). The script creates the nameserver
object if missing — required by `netbox-plugin-dns` for zone creation.

## Homelab inventory seed

One-time DCIM/IPAM/virtualization seed from git (edit data, then apply):

```bash
# 1. Fill scripts/netbox-homelab-data.yaml (Synology model, host IPs, VM sizes, …)
./scripts/seed-netbox-homelab.sh --check

export NETBOX_TOKEN=$(vault kv get -field=token secret/netbox/api)
./scripts/seed-netbox-homelab.sh --import-community-device-types
```

Creates site, prefixes, physical devices, k3s/ESXi VMs, static DNS A records,
and the external-gateway VIP. With `--import-community-device-types`, exact
matches from `netbox-community/devicetype-library` are imported first (currently
UCG-Fiber and MikroTik CRS310); local fallbacks remain for models missing from
the library (Synology DS723+ and Dell PowerEdge T550). HTTPRoute hostnames stay
owned by `dns-netbox-sync` (`managed-by=k8s`).

Discovery from live systems:

```bash
export VCF_SSH_PASS='…'   # ESXi + MikroTik (vcf91-helper skill)
./scripts/discover-homelab.sh esxi
./scripts/discover-homelab.sh mikrotik

# UniFi Cloud Gateway Fiber — non-interactive password SSH:
export UCG_SSH_PASS='…'
./scripts/discover-homelab.sh ucg
# Do not run without UCG_SSH_PASS unless key auth is enabled on the UCG.
```

Merge discovery output into `scripts/netbox-homelab-data.yaml`, then seed.

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
