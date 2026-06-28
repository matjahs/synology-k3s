# Netbox

IPAM and network documentation at https://netbox.lab.mxe11.nl.

## Architecture

- **Web:** `ghcr.io/netbox-community/netbox` (pinned in `kustomization.yaml`)
- **Worker:** same image, `rqworker` for background jobs and webhooks
- **PostgreSQL:** CloudNativePG cluster `netbox-db` (1 instance, 4Gi PVC)
- **Redis:** single `redis:7-alpine` pod (db `0` tasks, db `1` cache)
- **Media:** 10Gi RWO PVC on `synology-iscsi`
- **Secrets:** Vault via External Secrets Operator
- **Ingress:** Cilium Gateway `external-gateway` + cert-manager TLS

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
```

Generate a Django secret key:

```bash
openssl rand -base64 50
```

See also [platform/external-secrets.md](../../platform/external-secrets.md).

## First login

After Argo CD sync and pods are healthy, log in at https://netbox.lab.mxe11.nl with the `superuser_name` / `superuser_password` from Vault. The superuser is created on first web pod startup only.

**First boot** runs a large Django migration set and can take 10–15 minutes on a homelab node. The web Deployment uses an extended startup probe (`failureThreshold: 120`) so Kubernetes does not kill the pod mid-migration. If migrations are interrupted (e.g. probe too short), reset the CNPG cluster and its PVC before retrying.

## Upgrade image tag

Bump the tag in `kustomization.yaml` under `images:` and commit. Argo CD will roll the web and worker Deployments; the netbox-docker entrypoint runs DB migrations on startup.

## Verify

```bash
kubectl get externalsecret -n netbox
kubectl get cluster netbox-db -n netbox
kubectl get pods -n netbox
kubectl get certificate netbox-tls -n kube-system
kubectl get httproute -n netbox
curl -I https://netbox.lab.mxe11.nl/login/
```

## Optional follow-ups

- Keycloak OIDC SSO
- CNPG scheduled backups to S3/MinIO
- Netbox plugins (custom image)
- CSV/API import of existing homelab inventory
