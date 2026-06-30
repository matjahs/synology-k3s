# Persistent storage — democratic-csi (Synology iSCSI)

Cluster storage is provided by [democratic-csi](https://github.com/democratic-csi/democratic-csi)
talking to the Synology NAS over the DSM API. It provisions an **iSCSI LUN**
per PersistentVolume, mounted `ReadWriteOnce`, with online expansion support.

|              |                                                                        |
| ------------ | ---------------------------------------------------------------------- |
| Driver       | `org.democratic-csi.synology-iscsi`                                    |
| Chart        | `democratic-csi` `0.15.1` (`https://democratic-csi.github.io/charts/`) |
| Namespace    | `democratic-csi`                                                       |
| StorageClass | `synology-iscsi` (**cluster default**)                                 |
| Access mode  | `ReadWriteOnce` (block)                                                |
| Expansion    | enabled                                                                |
| Snapshots    | disabled (see below)                                                   |
| Sync wave    | `-1` (before stateful apps)                                            |

Defined in [`democratic-csi-app.yaml`](democratic-csi-app.yaml). All values are
inlined there; the only secret material is the DSM connection, kept out of git.

## One-time setup

The driver credentials live in a Secret applied **out-of-band** — the root
Argo CD app only discovers `*-app.yaml`, so the Secret is never read from git.

1. **DSM** — create a dedicated user in the `administrators` group (LUN/target
   management needs admin on DSM 7), enable iSCSI, and note the volume
   (e.g. `/volume1`). Avoid 2FA on that account.

2. **k3s VM host** — install the iSCSI initiator (democratic-csi shells out to
   the host's `iscsiadm`):

   ```bash
   sudo apt-get install -y open-iscsi
   sudo systemctl enable --now iscsid
   sudo modprobe iscsi_tcp
   echo iscsi_tcp | sudo tee /etc/modules-load.d/iscsi.conf
   ```

3. **Secret** — fill in [`democratic-csi-secret.example.yaml`](democratic-csi-secret.example.yaml)
   and apply it (the data key must stay `driver-config-file.yaml`):

   ```bash
   cp democratic-csi-secret.example.yaml /tmp/dcsi-secret.yaml
   $EDITOR /tmp/dcsi-secret.yaml
   kubectl create namespace democratic-csi --dry-run=client -o yaml | kubectl apply -f -
   kubectl apply -f /tmp/dcsi-secret.yaml
   shred -u /tmp/dcsi-secret.yaml
   ```

   The controller pod CrashLoops until this Secret exists — that's expected;
   Argo CD self-heal recovers it once applied.

> When the Tier-1 SOPS + age work lands, replace this manual step with a SOPS-
> encrypted Secret in git and drop the `.example` file.

## Verify

```bash
kubectl get pods -n democratic-csi
kubectl get storageclass            # synology-iscsi should be (default)

kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: test-claim, namespace: default}
spec:
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 1Gi}}
EOF
kubectl get pvc test-claim          # should reach Bound; a LUN appears in DSM
kubectl delete pvc test-claim
```

## Volume snapshots

[`external-snapshotter-app.yaml`](external-snapshotter-app.yaml) installs the
`snapshot-controller` chart (`0.3.0`) at sync-wave `-2`. democratic-csi enables
`externalSnapshotter` and a `synology-iscsi` VolumeSnapshotClass.

Smoke-test on a disposable PVC:

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: snap-test, namespace: default}
spec:
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 1Gi}}
---
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata: {name: snap-test-1, namespace: default}
spec:
  volumeSnapshotClassName: synology-iscsi
  source:
    persistentVolumeClaimName: snap-test
EOF
kubectl get volumesnapshot snap-test-1
kubectl delete volumesnapshot snap-test-1
kubectl delete pvc snap-test
```

## Notes

- `reclaimPolicy: Delete` — deleting a PVC deletes the backing LUN. Back up
  before destructive changes (CNPG backups to Garage; see `apps/keycloak/keycloak.md`).
- `lunTemplate.type: BLUN` is thin-provisioned; switch to `BLUN_THICK` in Vault
  (or the example Secret) for thick provisioning.
