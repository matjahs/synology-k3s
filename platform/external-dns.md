# external-dns (retired)

**Replaced by NetBox DNS + OctoDNS.** See [`dns.md`](dns.md).

The `external-dns` Argo CD application (`platform/external-dns-app.yaml`) was removed.
DNS for `lab.mxe11.nl` is now:

1. **dns-netbox-sync** — HTTPRoutes → NetBox DNS
2. **octodns-sync** — NetBox → UniFi gateway (`172.16.0.1`)

Legacy UniFi API key example (for Vault `secret/dns/unifi`):
[`external-dns-unifi-secret.example.yaml`](external-dns-unifi-secret.example.yaml).
