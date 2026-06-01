# Homepage — VCF 9.1 Lab Portal

[gethomepage/homepage](https://gethomepage.dev) dashboard, deployed GitOps-style
via Argo CD. Serves the VCF 9.1 service portal at `home.lab.mxe11.nl`.

## Layout

| File | Purpose |
|------|---------|
| `homepage-app.yaml` | Argo CD `Application` (sync-wave 1, namespace `tools`) |
| `kustomization.yaml` | Resources + `configMapGenerator` for the dashboard config |
| `serviceaccount.yaml` / `rbac.yaml` | SA + cluster-scoped read access for the Kubernetes widget |
| `deployment.yaml` | Homepage pinned to `v1.13.1` |
| `service.yaml` | ClusterIP `homepage-svc` (80 → 3000) |
| `httproute.yaml` | Gateway API route on the shared `external-gateway` |
| `config/*.yaml` | Dashboard config — settings, services, widgets, bookmarks |

## Config is declarative

The original bundle mounted `/app/config` from a Synology `hostPath` editable via
File Station. In this repo the config files live under `config/` and are rendered
into a `homepage-config` ConfigMap by Kustomize's `configMapGenerator`. **Edit the
files in git** and let Argo CD sync — the generator's name-suffix hash changes on
every edit, which rolls the pod so the new config is picked up automatically.

Each config file is mounted as an individual **`subPath`** rather than mounting the
whole directory. Homepage writes to `/app/config` at runtime (a log file under
`logs/`, and it scaffolds missing defaults like `custom.css`/`custom.js`), so the
directory must stay writable — a whole-dir ConfigMap mount would make it read-only
and break startup. `/app/config/logs` is backed by an `emptyDir`. Homepage only
*reads* the service/settings YAMLs and never writes them back, so keeping them in a
ConfigMap loses nothing.

The VCF service links (`*.vcf.lab`) resolve on the internal lab network and are
reached directly by the browser, independent of the cluster Gateway.

## Access

- **URL:** `https://home.lab.mxe11.nl` (external-dns can publish this from the
  HTTPRoute via `gateway-httproute`/`sync`, or set the record manually)
- Routing goes through the shared `external-gateway` in `kube-system`, same as
  cyberchef and Argo CD.

> Homepage v1 enforces `HOMEPAGE_ALLOWED_HOSTS`. The deployment allow-lists the
> pod IP (required for the kubelet probes) plus the external FQDN. If the hostname
> changes, update both the `HTTPRoute` and that env var.

## Kubernetes widget

`config/kubernetes.yaml` uses `mode: cluster`, reading cluster state through the
in-pod ServiceAccount token. The `ClusterRole` in `rbac.yaml` grants read access
to nodes/pods/deployments/etc. Live CPU/RAM requires metrics-server:

```bash
kubectl get deployment metrics-server -n kube-system
```
