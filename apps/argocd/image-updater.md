# Argo CD Image Updater

Automatically bumps container image tags in Git and opens GitHub pull requests
when newer semver tags appear in the configured registries.

## Layout

| File                            | Role                                                            |
| ------------------------------- | --------------------------------------------------------------- |
| `image-updater-app.yaml`        | Helm chart — controller + CRDs in `argocd` namespace            |
| `image-updater-config-app.yaml` | Kustomize — `ImageUpdater` CR and ExternalSecrets (sync-wave 2) |
| `image-updater/`                | Config manifests synced by the config Application               |

## Managed applications

| Argo CD App | Image                                    | Registry      |
| ----------- | ---------------------------------------- | ------------- |
| `backstage` | `nexus.lab.mxe11.nl/backstage/backstage` | Nexus         |
| `netbox`    | `ghcr.io/netbox-community/netbox`        | GHCR (public) |

Write-back uses **GitHub pull requests** against `main`, updating each app's
`kustomization.yaml` `images[].newTag` field.

## Vault setup (before first sync)

### GitHub App for PR write-back

Populate `secret/argocd/git` with a GitHub App installed on
`matjahs/synology-k3s` (**still required** — Vault was unreachable during git
implementation; confirm manually):

```bash
vault kv put secret/argocd/git \
  app_id=<github-app-id> \
  installation_id=<github-app-installation-id> \
  private_key=@/path/to/private-key.pem
```

Required repository permissions:

- **Contents** — Read and write
- **Pull requests** — Read and write

You can reuse the ARC runners GitHub App (`secret/github/runners`) only if that
installation includes `synology-k3s` with write access. Otherwise create a
dedicated app for image-updater commits.

### Nexus registry credentials

Nexus pull credentials are sourced from the existing `secret/backstage/nexus-docker`
path (same creds as the backstage deployment). No additional Vault path is needed.

## Verify

```bash
# Controller running
kubectl -n argocd get pods -l app.kubernetes.io/name=argocd-image-updater

# Secrets synced
kubectl -n argocd get externalsecret,secret | grep image-updater

# ImageUpdater CR reconciled
kubectl -n argocd get imageupdater lab-apps -o yaml

# Controller logs
kubectl -n argocd logs -l app.kubernetes.io/name=argocd-image-updater --tail=50
```

## How updates flow

1. CI pushes a new semver tag (e.g. `nexus.lab.mxe11.nl/backstage/backstage:1.2.2`).
2. Image Updater polls the registry, detects the newer tag.
3. Controller pushes a branch (`image-updater-<app>-<images>`) and opens a PR
   updating `apps/<app>/kustomization.yaml`.
4. Merge the PR → Argo CD syncs `main` → cluster rolls to the new image.

## Troubleshooting

| Symptom                                    | Check                                                                   |
| ------------------------------------------ | ----------------------------------------------------------------------- |
| `argocd-image-updater-git-creds` not ready | Vault path `secret/argocd/git` populated; ESO logs                      |
| Nexus registry ping fails                  | `argocd-image-updater-nexus-creds` secret; Nexus reachable from cluster |
| No PR created                              | GitHub App permissions; controller logs for SCM API errors              |
| PR merged but no deploy                    | Target Application `targetRevision` must be `main`                      |
