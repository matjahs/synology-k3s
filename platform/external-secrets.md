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

| Path                                  | Keys                   | Produces Secret                                         |
| ------------------------------------- | ---------------------- | ------------------------------------------------------- |
| `secret/data/keycloak/db`             | `username`, `password` | `keycloak/keycloak-db-app`                              |
| `secret/data/cert-manager/cloudflare` | `api-token`            | `cert-manager/cloudflare-api-token`                     |
| `secret/data/guacamole/db`            | `password`             | `keycloak/guacamole-db-creds`, `tools/guacamole-db-app` |
| `secret/data/guacamole/oidc`          | `client-secret`        | `tools/guacamole-oidc-secret`                           |

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

# Populated after KeycloakRealmImport creates the guacamole client — see apps/guacamole/guacamole.md
vault kv put secret/guacamole/oidc \
  client-secret=<retrieved-from-keycloak>
```

## Verify

```bash
kubectl get clustersecretstore vault
kubectl get externalsecret -A
# All should show READY=True and STATUS=SecretSynced
```
