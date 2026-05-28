# TODO — synology-k3s GitOps hardening

Backlog of improvements to bring this repo to a production-grade 2026 standard.
Ordered by priority. See commit history for the bug-fix / restructure work already done.

## Tier 1 — absolute must-haves

- [ ] **Declarative secrets management.** No secrets are in git today (Keycloak admin
      password is auto-generated in-cluster; PostgreSQL creds undeclared). Adopt one of:
      SOPS + age (Argo-native), Sealed Secrets, or External Secrets Operator. _Use SOPS + age_
- [ ] **Argo CD manages itself, at pinned versions.** Currently installed imperatively
      from the `stable` branch in `bootstrap-k8s-vm.sh` and patched with `kubectl`. Replace
      with an `argocd-app.yaml` pinned to a tag; make the `server.insecure` patch declarative.
      Also pin the k3s version (`INSTALL_K3S_VERSION`). _OK_
- [ ] **Dedicated AppProject** instead of `project: default`. Allow-list the repoURL,
      destination cluster/namespaces, and `clusterResourceWhitelist`.
- [ ] **CI validation on PRs** (GitHub Actions): `kustomize build` every path,
      `kubeconform` (with Gateway API / Cilium / cert-manager CRD schemas), `yamllint`,
      and `helm template` dry-run. _OK_
- [ ] **Renovate** (or Dependabot for Helm/images) to auto-PR chart and image bumps. _OK_
- [ ] **Storage + stateful backup.** Add a CSI driver for Synology (synology-csi,
      iSCSI/NFS), size Keycloak's PostgreSQL PVC, and back up the DB (Velero or pg_dump
      CronJob to the NAS). _Yes on the synology-csi_

## Tier 2 — strongly expected

- [ ] **Real PKI**, not per-service self-signed. Add a ClusterIssuer (Let's Encrypt
      DNS-01 or internal CA) and put TLS on the external Gateway (Argo CD + CyberChef
      are plain HTTP today). _Later_
- [ ] **Observability.** Enable Cilium Hubble + relay (nearly free, Cilium already
      installed); add kube-prometheus-stack (Prometheus/Grafana/Alertmanager). _Later_
- [ ] **NetworkPolicies.** Network policy is delegated to Cilium but zero
      CiliumNetworkPolicies exist — add default-deny per namespace with explicit allows. _Later_
- [ ] **Backup/DR + notifications.** Velero for cluster state; argocd-notifications or
      Alertmanager routes for failed syncs. _Later_
- [ ] **Pod Security + policy engine.** Add `pod-security.kubernetes.io/enforce` labels
      on namespaces; consider Kyverno/Gatekeeper. _Later_
- [ ] **external-dns** to manage `*.mxe11.nl` records pointing at LB IPs. _Later_

## Tier 3 — repo hygiene

- [ ] **README.md** — architecture, bootstrap → root-app flow, recovery steps.
- [ ] LICENSE, CODEOWNERS, .editorconfig, pre-commit (mirror CI checks locally).

## Verify / pin (left as TODO comments in manifests)

- [X] Cilium `1.19.4` — confirm chart version exists / is current. _1.19.4 is the latest_
- [X] cert-manager — bumped to `v1.20.2`. _latest is v1.20.2_
- [X] cyberchef — pinned to `gchq/cyberchef:2.0.0`. _latest is 2.0.0_
- [X] Keycloak chart — switched to OCI `registry-1.docker.io/bitnamicharts` (chart `keycloak`, 24.4.1).
      _oci://registry-1.docker.io/bitnamicharts/keycloak_ — `helm pull` confirmed working.
- [ ] kubeconfig mode `0644` in bootstrap — left as-is (tightening breaks the script's own
      unprivileged kubectl calls); revisit if those run as root.
