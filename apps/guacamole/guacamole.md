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

Browse to https://guacamole.lab.mxe11.nl/guacamole/ — you see the **local login form** (username/password) plus a link to sign in via Keycloak. `EXTENSION_PRIORITY=*,openid` prevents the immediate Keycloak redirect.

## Connections

Connections are declared in `connections-configmap.yaml` and applied by the `guacamole-connections-seed` PostSync Job (idempotent — safe to re-run on every Argo CD sync). Credentials are **not** stored in git; Guacamole prompts for username/password when you open a session.

| Connection | Protocol | Target |
|---|---|---|
| `corp-ca01` | RDP | `corp-ca01.lab.mxe11.nl:3389` |
| `esx` | SSH | `esx.lab.mxe11.nl:22` |

Every Guacamole user in the Keycloak `homelab` group is granted READ on all connections (via the `groups` id_token claim → Guacamole user group `homelab`). Add users to that group in Keycloak. Local `guacadmin` also has direct READ.

To add a connection, extend `connections.sql` in `connections-configmap.yaml` and sync.

## First-time Setup

The init SQL creates a default DB user `guacadmin` / `guacadmin`. Since both PostgreSQL and OIDC auth are active, the Guacamole login page shows both a username/password form and an OIDC redirect button. Change the `guacadmin` password after first login if you use local auth. OIDC users log in via Keycloak and land on the home screen with the seeded connections above.

## OIDC (Keycloak)

Guacamole's OpenID extension uses the **implicit flow** (`response_type=id_token`, validated against the JWKS endpoint) — there is **no client secret**. Ensure the `guacamole` client in the **vcf** realm has *Implicit flow* enabled and redirect URI `https://guacamole.lab.mxe11.nl/guacamole/*` registered.

Authorization is group-based: users must be in the Keycloak **`homelab`** group. The `guacamole` client needs a **Group Membership** protocol mapper (`claim.name=groups`, *Add to ID token* on). Guacamole maps that claim to its own user group `homelab` via `OPENID_GROUPS_CLAIM_TYPE=groups`.

**Live cluster (vcf realm already exists — KeycloakRealmImport is create-once):**

1. Keycloak admin → **vcf** realm → **Groups** → create `homelab`
2. Add your user(s) to the `homelab` group
3. **Clients** → `guacamole` → **Client scopes** → `guacamole-dedicated` → **Add mapper** → *Group Membership*
   - Name: `groups`, Token Claim Name: `groups`, Full group path: **Off**, *Add to ID token*: **On**
4. Log out of Guacamole and sign in again via Keycloak

Local admin: `guacadmin` / `guacadmin` (change after first use) — has direct READ on all connections.

## Verify

```bash
kubectl get pods -n tools                          # guacamole-* Running
kubectl get httproute -n tools guacamole           # Accepted
kubectl get externalsecret -n tools                # SecretSynced
kubectl get job -n tools guacamole-db-init         # Complete
kubectl logs job/guacamole-db-init -n tools        # "Done." or "Schema already initialized"
```
