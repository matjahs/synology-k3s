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

## OIDC (Keycloak)

Guacamole's OpenID extension uses the **implicit flow** (`response_type=id_token`, validated against the JWKS endpoint) — there is **no client secret**. Ensure the `guacamole` client in the **vcf** realm has *Implicit flow* enabled and redirect URI `https://guacamole.lab.mxe11.nl/guacamole/*` registered. OIDC users are auto-created on first login with no permissions; grant access from the `guacadmin` account.

## Verify

```bash
kubectl get pods -n tools                          # guacamole-* Running
kubectl get httproute -n tools guacamole           # Accepted
kubectl get externalsecret -n tools                # SecretSynced
kubectl get job -n tools guacamole-db-init         # Complete
kubectl logs job/guacamole-db-init -n tools        # "Done." or "Schema already initialized"
```
