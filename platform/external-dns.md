# external-dns → UniFi

Automatically publishes DNS records for `lab.mxe11.nl` to the UniFi gateway at
`172.16.0.1`, derived from the Gateway API. Defined in
[`external-dns-app.yaml`](external-dns-app.yaml).

|           |                                                                                           |
| --------- | ----------------------------------------------------------------------------------------- |
| Chart     | `external-dns` `1.21.1` (app `0.21.0`, `https://kubernetes-sigs.github.io/external-dns/`) |
| Provider  | webhook sidecar `ghcr.io/kashalls/external-dns-unifi-webhook:v0.8.2`                      |
| Namespace | `external-dns`                                                                            |
| Source    | `gateway-httproute`                                                                       |
| Zone      | `lab.mxe11.nl`                                                                            |
| Policy    | `sync` (create + delete, TXT-owned)                                                       |
| Owner ID  | `lab-k3s` (TXT registry, prefix `k8s.`)                                                   |

## How it works

external-dns watches `HTTPRoute` objects, takes the `hostnames`, resolves the
target IP from each route's **parent Gateway** `.status.addresses` (the Cilium
LB IP), and the webhook writes A + TXT records into UniFi's local DNS.

Records it will manage today:

| Host                     | Source HTTPRoute                                            | Target                 |
| ------------------------ | ----------------------------------------------------------- | ---------------------- |
| `keycloak.lab.mxe11.nl`  | `apps/keycloak` (Gateway `keycloak`)                        | that Gateway's LB IP   |
| `cyberchef.lab.mxe11.nl` | `apps/cyberchef` (Gateway `external-gateway`)               | external-gateway LB IP |
| `argocd.lab.mxe11.nl`    | `platform/argocd-ingress.yaml` (Gateway `external-gateway`) | external-gateway LB IP |

## One-time setup

Apply the API key Secret **out-of-band** (see
[`external-dns-unifi-secret.example.yaml`](external-dns-unifi-secret.example.yaml)).
Create the key in UniFi → Settings → Control Plane → Integrations → Create API
Key, then:

```bash
kubectl create namespace external-dns --dry-run=client -o yaml | kubectl apply -f -
# edit a copy of the example with the key, then:
kubectl apply -f /tmp/edns.yaml
```

## Verify

```bash
kubectl -n external-dns logs deploy/external-dns -c external-dns --tail=50
kubectl -n external-dns logs deploy/external-dns -c webhook --tail=50
# then check the record exists in UniFi (Settings -> Routing & DNS -> DNS),
# or: dig @172.16.0.1 keycloak.lab.mxe11.nl +short
```

## Notes

- **`policy: sync`** lets external-dns delete records, but only ones it created
  (tracked via the `k8s.`-prefixed TXT records owned by `lab-k3s`). Switch to
  `upsert-only` in the Application values to disable deletion.
- Targets UniFi Network **10.4.57**, which fully supports local DNS A + TXT
  records — hence `registry: txt` (with the `k8s.` TXT prefix) rather than a
  registry-less setup. Clients must resolve through the gateway (`172.16.0.1`)
  for these names to take effect.
- `UNIFI_EXTERNAL_CONTROLLER=false` because `172.16.0.1` is UniFi gateway
  hardware; set it `true` only for a self-hosted controller (Cloud Key software
  off-box).
