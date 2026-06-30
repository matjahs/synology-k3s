# GitHub Actions runners (ARC)

Self-hosted GitHub Actions runners on k3s using [Actions Runner Controller](https://github.com/actions/actions-runner-controller) (ARC) runner scale sets. Workloads run in the `ci` namespace.

## Architecture

| Component | Argo CD app | Purpose |
| --------- | ----------- | ------- |
| Vault → ESO | `ci` | GitHub App credentials (`arc-github-app` secret) |
| `gha-runner-scale-set-controller` | `arc-controller` | Watches `ci`, scales runner pods |
| `gha-runner-scale-set` | `arc-runners-backstage` | Runners for `matjahs/backstage` |
| `gha-runner-scale-set` | `arc-runners-lab-netbox` | Runners for `matjahs/lab-netbox` |

Because `matjahs` is a **personal GitHub account** (not an organization), runners are registered at **repository** scope. Each repo gets its own scale set; both share one GitHub App installed on the selected repositories.

Runners are **ephemeral** (`minRunners: 0`, `maxRunners: 2`): pods scale to zero when idle and cap at two concurrent jobs cluster-wide per repo.

## One-time: GitHub App

1. Open [GitHub App settings](https://github.com/settings/apps) → **New GitHub App**.
2. Suggested settings:
   - **Homepage URL:** `https://github.com/actions/actions-runner-controller`
   - **Webhook:** inactive (uncheck “Active”)
   - **Repository permissions:**
     - **Administration:** Read and write (required for repo-level runner registration)
     - **Metadata:** Read-only
3. **Create** the app, then **Generate a private key** (`.pem`).
4. Note the **App ID** from the app settings page.
5. **Install App** on your account → **Only select repositories** → choose `backstage` and `lab-netbox`.
6. On the installation page, note the **Installation ID** from the URL:
   `https://github.com/settings/installations/<INSTALLATION_ID>`.

## One-time: Vault

```bash
vault kv put secret/github/runners \
  app_id="<APP_ID>" \
  installation_id="<INSTALLATION_ID>" \
  private_key=@/path/to/private-key.pem
```

ESO syncs this to `ci/arc-github-app` with keys `github_app_id`, `github_app_installation_id`, and `github_app_private_key`.

## Workflow usage

Set `runs-on` to the runner scale set name (not the Argo app name):

```yaml
jobs:
  build:
    runs-on: k3s-backstage   # matjahs/backstage
```

```yaml
jobs:
  test:
    runs-on: k3s-lab-netbox  # matjahs/lab-netbox
```

Hosted runners (`ubuntu-latest`) and self-hosted runners can coexist in the same workflow on different jobs.

## Container image builds (Kaniko vs DinD)

Many workflows use `docker/build-push-action` or `docker build`, which expect a Docker daemon. On Kubernetes you have three main options:

### Kaniko (recommended for this homelab)

[Kaniko](https://github.com/GoogleContainerTools/kaniko) builds container images **inside a container** without privileged mode or a Docker socket. A workflow step runs the `gcr.io/kaniko-project/executor` image, which reads a Dockerfile from the workspace and pushes directly to a registry.

**Pros:** No privileged pods; fits your security posture (no `hostPath`, no `docker.sock`). Works well with Nexus (`nexus.lab.mxe11.nl`).

**Cons:** Not a drop-in for every `docker build` feature (e.g. some BuildKit-only options). Workflows must be written for Kaniko (or use a Kaniko-based action).

**Example pattern:**

```yaml
- uses: actions/checkout@v4
- name: Build and push
  run: |
    /kaniko/executor \
      --context=. \
      --dockerfile=Dockerfile \
      --destination=nexus.lab.mxe11.nl/backstage/backstage:${{ github.sha }}
  env:
    # Mount registry creds via workflow secret or k8s secret sync
```

### Docker-in-Docker (DinD)

ARC `containerMode.type: dind` adds a privileged `docker:dind` sidecar. The runner talks to it via `/var/run/docker.sock`, so standard `docker build` and `docker/build-push-action` work unchanged.

**Pros:** Maximum compatibility with existing Docker-based workflows.

**Cons:** **Privileged** containers on your cluster; weaker isolation; higher resource use per job. Not enabled in the current manifests.

### Build on hosted runners, deploy from k3s

Keep `runs-on: ubuntu-latest` for image builds (as `backstage` does today → Nexus), and use k3s runners only for jobs that need cluster/network access (integration tests, kustomize deploy smoke tests, etc.). Lowest change, but no local-registry speed benefit for builds.

**Current default:** Runners use the stock ARC template (no DinD). Use Kaniko or hosted runners for image builds until you explicitly enable DinD.

## Verify

```bash
kubectl get pods -n ci
kubectl get autoscalingrunnersets -n ci
kubectl get externalsecret -n ci arc-github-app
```

In each GitHub repo: **Settings → Actions → Runners** — you should see `k3s-backstage` or `k3s-lab-netbox` online when a job is queued.

## Troubleshooting

| Symptom | Check |
| ------- | ----- |
| `arc-github-app` not ready | Vault path `secret/github/runners` and ESO logs |
| Listener pod crash loop | App ID / installation ID must be **strings**; private key PEM intact |
| Job stuck “Waiting for a runner” | `runs-on` label must match `runnerScaleSetName`; repo must be in the app installation |
| Argo can’t pull OCI chart | Cluster egress to `ghcr.io`; Argo CD 2.4+ with OCI support |
