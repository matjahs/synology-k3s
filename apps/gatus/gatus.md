# Gatus — status page & VCF lab health

Self-hosted health monitoring with grouped checks and a status page, replacing
Uptime Kuma. Reached at <https://status.lab.mxe11.nl> — TLS is terminated at the
shared external-gateway via a Let's Encrypt cert (`status-tls`, issued by the
`letsencrypt-prod` ClusterIssuer; the listener lives in
[`platform/argocd-ingress.yaml`](../../platform/argocd-ingress.yaml) and the
`Certificate` in [`platform/external-gateway-certs.yaml`](../../platform/external-gateway-certs.yaml)).

## Layout

| File | Purpose |
|------|---------|
| `gatus-app.yaml` | Argo CD `Application` (discovered by the root app-of-apps). |
| `config.yaml` | Gatus config, rendered to a ConfigMap by kustomize. |
| `deployment.yaml` / `service.yaml` / `httproute.yaml` / `pvc.yaml` | The app, its ClusterIP, the Gateway routes (TLS + http→https redirect), and SQLite storage (`synology-iscsi`). |
| `cronjob.yaml` + `vcf-healthcheck.sh` | Push-based deep VCF checks (run every 5 min). |
| `secret.example.yaml` | Template for `gatus-secrets` (credentials + push tokens). |

## Check tiers

**Tier 1 — native polling** (in `config.yaml`): UI reachability, TLS cert
expiry and response time for SDDC Manager, vCenter, NSX and the ESXi hosts.
The **NSX cluster status** check is a real deep check — NSX accepts Basic auth,
so Gatus polls `/api/v1/cluster/status` and asserts every group is `STABLE`.

**Tier 2 — push-based deep checks** (`external-endpoints` + CronJob): the
token-authenticated APIs can't be polled directly (tokens expire), so the
CronJob logs in and pushes results to Gatus:

- **vCenter** — `POST /api/session` → `GET /api/appliance/health/system` (pass only on `green`).
- **vSAN** — storage subsystem health via the same vCenter session.
- **SDDC Manager** — `POST /v1/tokens` → `GET /v1/domains` (fails if any domain ≠ `ACTIVE`).

Each external endpoint has a `heartbeat`, so a stalled CronJob also alerts.

## Setup

1. **Create the secret** (never committed):

   ```sh
   cp secret.example.yaml gatus.secret      # .secret is gitignored
   # edit values — NSX_AUTH = $(printf 'admin:PASS' | base64)
   kubectl apply -n monitoring -f gatus.secret
   ```

   The `TOK_*` values are arbitrary shared strings but must match the
   `external-endpoints[].token` entries in `config.yaml`.

2. **Hosts** in `config.yaml` are sourced from Infoblox IPAM (zone
   `site-a.vcf.lab`) and mirror the live dashboard. Three entries are inferred
   and worth confirming: `vCenter - Admin` / `VCF Operations - Admin` use the
   VAMI port `:5480`, and `Dell PowerEdge T550` is disabled until you set its
   iDRAC address (no Infoblox record exists). `cronjob.yaml` uses
   `vc-mgmt-a` / `sddcmanager-a`.

3. **Commit & push** — Argo CD syncs the `gatus` Application automatically.

## Notes / TODO

- The CronJob uses `alpine:3.20` and `apk add curl jq` at start, so it runs as
  root (capabilities still dropped). To run rootless, bake a tiny pinned
  `curl + jq` image and set it as the container `image`, then re-add
  `runAsNonRoot: true` / `runAsUser: 1000`.
- vSAN currently uses vCenter's storage-subsystem health as a proxy. For true
  per-cluster vSAN disk-group health, extend `vcf-healthcheck.sh` with the vSAN
  health API.
- SDDC token field names (`accessToken`, `.elements[].status`) are stable across
  VCF 9.x; confirm against the 9.1 build if a check misbehaves.
- Optional: add Gatus to the Homepage dashboard (`apps/homepage/config/services.yaml`).
