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
| Snapshots    | enabled (`external-snapshotter` + `synology-iscsi` VolumeSnapshotClass) |
| Sync wave    | `-1` (before stateful apps)                                            |

Defined in [`democratic-csi-app.yaml`](democratic-csi-app.yaml). DSM connection
credentials are synced from Vault via ESO
([`external-secret-democratic-csi.yaml`](external-secret-democratic-csi.yaml)).

## One-time setup

### DSM + iSCSI initiator

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

### Vault + ESO

Populate `secret/democratic-csi/driver` in Vault (see
[`external-secrets.md`](external-secrets.md)). ESO renders the
`driver-config-file.yaml` key into `democratic-csi/democratic-csi-driver-config`.

The controller pod CrashLoops until the Secret exists — expected on first sync.

[`democratic-csi-secret.example.yaml`](democratic-csi-secret.example.yaml) remains
as a reference for the Secret shape; prefer Vault/ESO over manual `kubectl apply`.

## Verify

```bash
kubectl get pods -n democratic-csi
kubectl get storageclass            # synology-iscsi should be (default)
kubectl get externalsecret -n democratic-csi democratic-csi-driver-config

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

## Enabling snapshots later

Snapshots need cluster-wide CRDs + a controller that this cluster doesn't have
yet. To enable:

1. Install the [external-snapshotter](https://github.com/kubernetes-csi/external-snapshotter)
   CRDs (`VolumeSnapshot*`) and `snapshot-controller` (its own Argo CD app).
2. In `democratic-csi-app.yaml` set `controller.externalSnapshotter.enabled: true`
   and add a `volumeSnapshotClasses` entry.

## Notes

- `reclaimPolicy: Delete` — deleting a PVC deletes the backing LUN. Back up
  before destructive changes (Velero / `pg_dump` CronJob is still open in the TODO).
- `lunTemplate.type: BLUN` is thin-provisioned; switch to `BLUN_THICK` in the
  Secret for thick provisioning.
