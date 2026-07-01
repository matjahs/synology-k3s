# External Secrets Operator

ESO syncs secrets from Vault (`https://vault.mxe11.nl:8200`) into Kubernetes Secrets via the `vault` ClusterSecretStore.

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

| Path                                  | Keys                   | Produces Secret                                         |
| ------------------------------------- | ---------------------- | ------------------------------------------------------- |
| `secret/data/keycloak/db`             | `username`, `password` | `keycloak/keycloak-db-app`                              |
| `secret/data/cert-manager/cloudflare` | `api-token`            | `cert-manager/cloudflare-api-token`                     |
| `secret/data/guacamole/db`            | `password`             | `keycloak/guacamole-db-creds`, `tools/guacamole-db-app` |
| `secret/data/backstage/db`            | `username`, `password` | `backstage/postgres-secrets`                            |
| `secret/data/backstage/github`        | `token`                | `backstage/backstage-secrets`                           |
| `secret/data/backstage/keycloak`      | `client_secret`        | `backstage/backstage-secrets`                           |
| `secret/data/backstage/session`       | `secret`               | `backstage/backstage-secrets`                           |
| `secret/data/backstage/argocd`        | `token`                | `backstage/backstage-secrets`                           |
| `secret/data/backstage/vault`         | `secret`               | `backstage/backstage-secrets` (`VAULT_STATIC_SECRET`)     |
| `secret/data/backstage/nexus-docker`  | `username`, `password` | `backstage/nexus-docker-creds`, `argocd/argocd-image-updater-nexus-creds` |
| `secret/data/grafana/admin`           | `user`, `password`     | `observability/grafana-admin`                           |
| `secret/data/netbox/db`               | `username`, `password` | `netbox/netbox-db-app`                                  |
| `secret/data/netbox/app`              | `secret_key`, `superuser_name`, `superuser_email`, `superuser_password` | `netbox/netbox-secrets` |
| `secret/data/netbox/api`              | `token`                | `netbox/netbox-api`, `dns/netbox-api`                   |
| `secret/data/dns/unifi`               | `api_key`              | `dns/unifi-api`                                         |
| `secret/data/github/runners`          | `app_id`, `installation_id`, `private_key` | `ci/arc-github-app` |
| `secret/data/argocd/git`              | `app_id`, `installation_id`, `private_key` | `argocd/argocd-image-updater-git-creds` |
| `secret/data/democratic-csi/driver`   | `host`, `port`, `username`, `password`, `volume`, `target_portal` | `democratic-csi/democratic-csi-driver-config` |
| `secret/data/cnpg/backup-s3`          | `endpoint_url`, `access_key_id`, `secret_access_key`, `bucket`, `region` | `keycloak/cnpg-backup-s3` |

## Populate Secrets (before first sync)

```bash
# Enable KV v2 if not already enabled
vault secrets enable -path=secret kv-v2

vault kv put secret/keycloak/db \
  username=keycloak \
  password=<strong-password>

vault kv put secret/cert-manager/cloudflare \
  api-token=<cloudflare-api-token>

vault kv put secret/guacamole/db \
  password=<strong-password>

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

vault kv put secret/netbox/db \
  username=netbox \
  password=<strong-password>

vault kv put secret/netbox/app \
  secret_key=<django-secret-key> \
  superuser_name=admin \
  superuser_email=admin@lab.mxe11.nl \
  superuser_password=<strong-password>

vault kv put secret/netbox/api \
  token=<netbox-api-token>

vault kv put secret/dns/unifi \
  api_key=<unifi-integration-api-key>

vault kv put secret/github/runners \
  app_id=<github-app-id> \
  installation_id=<github-app-installation-id> \
  private_key=@/path/to/private-key.pem

vault kv put secret/argocd/git \
  app_id=<github-app-id> \
  installation_id=<github-app-installation-id> \
  private_key=@/path/to/private-key.pem

vault kv put secret/democratic-csi/driver \
  host=172.16.30.X \
  port=5000 \
  username=csi-user \
  password=<dsm-password> \
  volume=/volume1 \
  target_portal=172.16.30.X

vault kv put secret/cnpg/backup-s3 \
  endpoint_url=https://garage.lab.mxe11.nl:3900 \
  access_key_id=<garage-key> \
  secret_access_key=<garage-secret> \
  bucket=cnpg-backups \
  region=garage
```

```bash
kubectl get clustersecretstore vault
kubectl get externalsecret -A
# All should show READY=True and STATUS=SecretSynced
```
