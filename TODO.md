# TODO — synology-k3s GitOps hardening

Backlog of improvements to bring this repo to a production-grade 2026 standard.
Ordered by priority. See commit history for the bug-fix / restructure work already done.

## Tier 1 — absolute must-haves

- [X] **Declarative secrets management.** External Secrets Operator syncs app
      secrets from Vault; paths in `platform/external-secrets.md`. democratic-csi
      DSM config is ESO-backed (`platform/external-secret-democratic-csi.yaml`).
      Keycloak DB via ESO. One-time Vault population still required for new paths.
- [X] **Argo CD manages itself, at pinned versions.** `apps/argocd/argocd-app.yaml`
      (chart `10.0.1`, `server.insecure: true`); bootstrap pins
      `INSTALL_K3S_VERSION` and uses Helm template at the same chart version.
- [X] **Dedicated AppProject** instead of `project: default`. `platform/homelab-appproject.yaml`
      allow-lists repos and namespaces; all `*-app.yaml` use `project: homelab`.
- [X] **CI validation on PRs** (GitHub Actions): `.github/workflows/validate.yml` runs
      `kustomize build` + `kubeconform` per app path, `yamllint`, `shellcheck`, and a
      Helm template smoke-test (CNPG). Extend helm-template coverage as more charts are
      added.
- [X] **Renovate** for Helm chart bumps in `*-app.yaml` (`renovate.json`, argocd manager).
      Also tracks `INSTALL_K3S_VERSION` in `bootstrap-k8s-vm.sh`.
- [~] **Argo CD Image Updater** for container image tags (`apps/argocd/image-updater-app.yaml`,
      chart `1.1.1`). Nexus registry URL fixed (in-cluster Service; synced on cluster).
      **Still open:** populate Vault `secret/argocd/git` (see `scripts/populate-tier1-vault.sh`),
      then verify first PR cycle with `./scripts/tier1-verify.sh`. See
      `apps/argocd/image-updater.md`.
- [~] **Storage + stateful backup.** democratic-csi on `synology-iscsi`; ESO for DSM
      creds; external-snapshotter + VolumeSnapshotClass enabled. CNPG backup manifests
      in git (PR #15). **Still open:** merge PR #15, populate Vault
      `secret/cnpg/backup-s3` and `secret/democratic-csi/driver`, confirm backups in
      Garage bucket. See `platform/democratic-csi.md`, `apps/keycloak/keycloak.md`.

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
- [X] **NetBox DNS source of truth** for `lab.mxe11.nl` — `dns-netbox-sync` (HTTPRoute
      → NetBox), `octodns-sync` (NetBox → UniFi). Argo app `platform/dns-app.yaml`.
      Custom NetBox image from `matjahs/lab-netbox`. Vault: `secret/netbox/api`,
      `secret/dns/unifi`. See `platform/dns.md`, `apps/netbox/netbox.md`.
      Retired `platform/external-dns-app.yaml`.

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
