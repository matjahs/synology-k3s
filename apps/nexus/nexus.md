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

## Notes

- Single replica with `Recreate` strategy and a 50Gi RWO PVC on `synology-iscsi`.
- First startup can take 2–3 minutes; probes allow up to ~3 minutes before failing.
- DNS for `nexus.lab.mxe11.nl` is managed automatically by external-dns from the HTTPRoute.
