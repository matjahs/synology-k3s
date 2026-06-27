# Nexus Repository OSS

Sonatype Nexus Repository OSS for Maven, npm, Docker, and other artifact formats.
Reached at <https://nexus.lab.mxe11.nl> — TLS is terminated at the shared
external-gateway via a Let's Encrypt cert (`nexus-tls`, issued by the
`letsencrypt-prod` ClusterIssuer; the listener lives in
[`platform/argocd-ingress.yaml`](../../platform/argocd-ingress.yaml) and the
`Certificate` in [`platform/external-gateway-certs.yaml`](../../platform/external-gateway-certs.yaml)).

## Layout

| File | Purpose |
|------|---------|
| `nexus-app.yaml` | Argo CD `Application` (discovered by the root app-of-apps). |
| `deployment.yaml` / `service.yaml` / `httproute.yaml` / `pvc.yaml` | The app, ClusterIP, Gateway routes (TLS + http→https redirect), and 50Gi storage (`synology-iscsi`). |

## First login

On first boot Nexus generates a random admin password in `/nexus-data/admin.password`.
Retrieve it once the pod is Ready:

```sh
kubectl exec -n tools deploy/nexus -- cat /nexus-data/admin.password
```

Log in as `admin` with that password. Nexus removes the file after you change the
password.

## Post-deploy configuration

1. **Set Base URL** — Administration → System → General → Base URL:
   `https://nexus.lab.mxe11.nl/`
   Required so Nexus generates correct links behind TLS-terminated Gateway.

2. **Create repositories** as needed (e.g. Maven proxy/hosted, Docker hosted).

## Maven (VCF Build Tools)

Build Tools artifacts (`com.vmware.pscoe.*` @ **4.22.0**) resolve from **Maven Central** via a Nexus proxy. The only manual upload is your **vRO package signing keystore**.

### Repository layout

| Repository key | Type | Remote / purpose |
|----------------|------|------------------|
| `maven-central` | maven2 (proxy) | `https://repo1.maven.org/maven2/` |
| `aria-local` | maven2 (hosted) | vRO signing keystore + optional internal packages |
| `vcf-maven-public` | maven2 (group) | Members: `aria-local`, then `maven-central` (hosted first) |
| `npm-proxy` | npm (proxy) | `https://registry.npmjs.org` |
| `vcf-npm-public` | npm (group) | Members: `npm-proxy` |

Create them under **Administration → Repository → Repositories → Create repository**.

For `vcf-maven-public`, add **aria-local before maven-central** so a hosted keystore wins over anything on Central.

### Upload the vRO signing keystore (one-time)

Signing material lives at `~/.vro-signing/` (`cert.pem`, `private_key.pem`). Package and deploy:

```sh
WORKDIR=$(mktemp -d)
mkdir -p "$WORKDIR/vcf-lab-vro-signing-1.0.0"
cp ~/.vro-signing/cert.pem ~/.vro-signing/private_key.pem \
  "$WORKDIR/vcf-lab-vro-signing-1.0.0/"
(cd "$WORKDIR" && zip -r vcf-lab-vro-signing-1.0.0.zip vcf-lab-vro-signing-1.0.0)

mvn deploy:deploy-file \
  -DgroupId=com.vcf.lab \
  -DartifactId=vcf-lab-vro-signing \
  -Dversion=1.0.0 \
  -Dpackaging=zip \
  -Dfile="$WORKDIR/vcf-lab-vro-signing-1.0.0.zip" \
  -DrepositoryId=aria-local \
  -Durl=https://nexus.lab.mxe11.nl/repository/aria-local/
```

Import `cert.pem` into Orchestrator (Administration → Certificates) so signed packages validate.

### Developer `settings.xml`

See [`maven-settings.example.xml`](./maven-settings.example.xml). Copy to `~/.m2/settings.xml`, set Nexus credentials under `<servers>`, and activate profiles `packaging` + `dev`.

Quick sanity check after configuring Nexus:

```sh
export JAVA_HOME=$(/usr/libexec/java_home -v 21)
mvn -q dependency:get \
  -Dartifact=com.vmware.pscoe.polyglot:polyglot-project:4.22.0:pom
```

### npm (polyglot / Node.js actions)

Point npm at the Nexus group:

```sh
npm config set registry https://nexus.lab.mxe11.nl/repository/vcf-npm-public/
npm login --registry=https://nexus.lab.mxe11.nl/repository/vcf-npm-public/
```

Build Tools also publishes npm bundles as Maven `.tgz` artifacts under `com.vmware.pscoe.iac` and `com.vmware.pscoe.ts.types` — those come through the Maven proxy on first `mvn package`.

## Docker registry

Nexus serves Docker over the same hostname as the UI (`nexus.lab.mxe11.nl`). TLS is
terminated at the external-gateway; the HTTPRoute sets `X-Forwarded-Proto: https`
so Nexus generates correct registry URLs.

### One-time Nexus configuration

1. **Base URL** — Administration → System → General → Base URL:
   `https://nexus.lab.mxe11.nl/`

2. **Docker Bearer Token Realm** — Administration → Security → Realms → move
   **Docker Bearer Token Realm** into **Active** (required for `docker login`).

3. **Docker hosted repository** — e.g. name `backstage`:
   - Type: docker (hosted)
   - HTTP connector: leave blank (reverse-proxy mode; traffic arrives on 8081 via the gateway)
   - Enable **Allow anonymous docker pull** only if you want unauthenticated pulls

4. **User permissions** — the account used for `docker login` needs at least:
   - `nx-repository-view-docker-backstage-*` (pull)
   - `nx-repository-admin-docker-backstage-*` (push)
   - `admin` has these by default

### Login and push

```sh
# Retrieve initial admin password if you have not changed it yet
kubectl exec -n tools deploy/nexus -- cat /nexus-data/admin.password

docker login nexus.lab.mxe11.nl -u admin
# Image path: <host>/<repo-name>/<image>:<tag>
docker tag my-backstage:1.0.0 nexus.lab.mxe11.nl/backstage/backstage:1.0.0
docker push nexus.lab.mxe11.nl/backstage/backstage:1.0.0
```

Use your **Nexus** username and password — not a GitHub token. GitHub tokens belong
in Vault (`secret/backstage/github`) for Backstage catalog integration only.

### Troubleshooting `401 Unauthorized` on `docker login`

| Cause | Fix |
|-------|-----|
| Wrong password | Reset via Nexus UI or re-read `admin.password` from the pod (only works before first password change) |
| Docker Bearer Token Realm inactive | Add it to Active realms (step 2 above) |
| User lacks repo permissions | Grant docker repo roles or use `admin` |
| Base URL not set | Set to `https://nexus.lab.mxe11.nl/` |
| Logging into wrong host | Use `nexus.lab.mxe11.nl`, not `backstage.lab.mxe11.nl` |

Verify the registry responds (401 without credentials is expected):

```sh
curl -sI https://nexus.lab.mxe11.nl/v2/ | grep -i 'docker-distribution\|www-authenticate'
```

After a successful login, `~/.docker/config.json` should contain an entry for
`nexus.lab.mxe11.nl`.

### Troubleshooting `access to the requested resource is not authorized` on push

Login can succeed while push still fails if the account lacks **write** privileges
on the docker hosted repository.

1. **Confirm you are logged in as a push-capable user**
   ```sh
   docker logout nexus.lab.mxe11.nl
   docker login nexus.lab.mxe11.nl -u admin
   ```
   Use `admin` (or a dedicated deploy user) — not a read-only or GitHub token.

2. **Grant push privileges in Nexus** — Administration → Security → Roles
   (or edit the user’s roles). The account needs **admin** on the docker repo,
   not just view/read:
   - `nx-repository-admin-docker-backstage-add`
   - `nx-repository-admin-docker-backstage-edit`
   - `nx-repository-view-docker-backstage-read`

   The built-in `nx-admin` role includes these. A custom role with only
   `nx-repository-view-docker-backstage-*` allows pull but **not** push.

3. **Verify repository type** — `backstage` must be **docker (hosted)**, not proxy.

4. **Re-login after permission changes** — Nexus issues a new bearer token on
   login; old tokens do not pick up new roles.

5. **Check the image path** — repo name is the first path segment:
   ```sh
   docker push nexus.lab.mxe11.nl/backstage/<image-name>:<tag>
   # e.g. nexus.lab.mxe11.nl/backstage/backstage:latest
   ```

## Notes

- Single replica with `Recreate` strategy and a 50Gi RWO PVC on `synology-iscsi`.
- First startup can take 2–3 minutes; probes allow up to ~3 minutes before failing.
- DNS for `nexus.lab.mxe11.nl` is managed automatically by external-dns from the HTTPRoute.
