# TODO — synology-k3s GitOps hardening

Backlog of improvements to bring this repo to a production-grade 2026 standard.
Ordered by priority. See commit history for the bug-fix / restructure work already done.

## Tier 1 — absolute must-haves

- [~] **Declarative secrets management.** External Secrets Operator is deployed
      (`platform/external-secrets-app.yaml`) and syncs most app secrets from Vault;
      paths are documented in `platform/external-secrets.md`. Remaining manual steps:
      democratic-csi DSM secret (`democratic-csi-secret.example.yaml`), Keycloak
      Postgres role password (`apps/keycloak/keycloak-db-secret.example.yaml`), and
      one-time Vault population for new paths (e.g. `secret/argocd/git` for Image
      Updater). Original note preferred SOPS + age; ESO is the current approach.
- [ ] **Argo CD manages itself, at pinned versions.** Currently installed imperatively
      from the `stable` branch in `bootstrap-k8s-vm.sh` and patched with `kubectl`. Replace
      with an `argocd-app.yaml` pinned to a tag; make the `server.insecure` patch declarative.
      Also pin the k3s version (`INSTALL_K3S_VERSION`). _OK_
- [ ] **Dedicated AppProject** instead of `project: default`. Allow-list the repoURL,
      destination cluster/namespaces, and `clusterResourceWhitelist`.
- [X] **CI validation on PRs** (GitHub Actions): `.github/workflows/validate.yml` runs
      `kustomize build` + `kubeconform` per app path, `yamllint`, `shellcheck`, and a
      Helm template smoke-test (CNPG). Extend helm-template coverage as more charts are
      added.
- [X] **Renovate** for Helm chart bumps in `*-app.yaml` (`renovate.json`, argocd manager).
- [~] **Argo CD Image Updater** for container image tags (`apps/argocd/image-updater-app.yaml`,
      chart `1.1.1`). `ImageUpdater` CR targets `backstage` (Nexus, semver) and `netbox`
      (GHCR, semver); git write-back opens PRs against `main` updating `kustomization.yaml`.
      **Still open:** populate Vault `secret/argocd/git` (GitHub App with Contents + PR
      write on `matjahs/synology-k3s`); verify first PR cycle after deploy. See
      `apps/argocd/image-updater.md`.
- [ ] **Storage + stateful backup.** _(CSI done; backup pending.)_ CSI driver added: `democratic-csi` (Synology
      iSCSI) in `platform/democratic-csi-app.yaml`, exposing the cluster-default
      `synology-iscsi` StorageClass. Keycloak's Postgres now runs on CloudNativePG
      (`apps/keycloak/postgres-cluster.yaml`, 8Gi on synology-iscsi). DSM creds are
      applied out-of-band (`democratic-csi-secret.example.yaml`) pending ESO coverage.
      **Still open:** DB backup — CNPG can do scheduled base-backups + WAL archiving but
      needs an S3 target (e.g. MinIO on the NAS); add `spec.backup` + a `ScheduledBackup`
      once that exists. Also volume snapshots (needs external-snapshotter CRDs).
      See `platform/democratic-csi.md`.

## Tier 2 — strongly expected

- [~] **Real PKI**, not per-service self-signed. `letsencrypt-prod`/`-staging`
      ClusterIssuers now exist (Let's Encrypt DNS-01 via Cloudflare,
      `platform/letsencrypt-clusterissuer.yaml`) and Keycloak's cert uses them.
      Still TODO: put TLS on the **external** Gateway (Argo CD is
      plain HTTP today). _Later_
- [~] **Observability.** `kube-prometheus-stack` is deployed (`apps/observability/`,
      chart `87.3.0`, Grafana admin creds via ESO). **Still open:** Cilium Hubble +
      relay; Alertmanager routing for failed Argo syncs. _Later for Hubble_
- [ ] **NetworkPolicies.** Network policy is delegated to Cilium but zero
      CiliumNetworkPolicies exist — add default-deny per namespace with explicit allows. _Later_
- [ ] **Backup/DR + notifications.** Velero for cluster state; argocd-notifications or
      Alertmanager routes for failed syncs. _Later_
- [ ] **Pod Security + policy engine.** Add `pod-security.kubernetes.io/enforce` labels
      on namespaces; consider Kyverno/Gatekeeper. _Later_
- [X] **external-dns** managing `lab.mxe11.nl` records on the UniFi gateway
      (`platform/external-dns-app.yaml`, chart `1.21.1` + kashalls UniFi webhook
      `v0.8.2`). Source `gateway-httproute`, policy `sync`. API key applied
      out-of-band (`external-dns-unifi-secret.example.yaml`). See `platform/external-dns.md`.
      Publishes `keycloak` and `argocd` under `lab.mxe11.nl`.

## Tier 3 — repo hygiene

- [ ] **README.md** — architecture, bootstrap → root-app flow, recovery steps.
- [ ] LICENSE, CODEOWNERS, .editorconfig, pre-commit (mirror CI checks locally).

## Verify / pin (left as TODO comments in manifests)

- [X] Cilium `1.19.4` — confirm chart version exists / is current. _1.19.4 is the latest_
- [X] cert-manager — bumped to `v1.20.2`. _latest is v1.20.2_
- [X] Keycloak — **migrated off the Bitnami chart** to the upstream Keycloak Operator
      (`platform/keycloak-operator-app.yaml`, pinned `26.6.2`) + a `Keycloak` CR, backed by
      CloudNativePG (`platform/cloudnative-pg-app.yaml`, chart `0.28.2`). Avoids the Bitnami
      "Secure Images"/bitnamilegacy pull-availability risk. See `apps/keycloak/keycloak.md`.
- [ ] kubeconfig mode `0644` in bootstrap — left as-is (tightening breaks the script's own
      unprivileged kubectl calls); revisit if those run as root.
