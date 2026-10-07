#!/usr/bin/env bash
set -euo pipefail
# storageClassName is immutable, so the claim is recreated with the cluster's default class.
ns=fault-pvc-pending
kubectl -n "$ns" scale deployment media --replicas=0
kubectl -n "$ns" delete pvc uploads --wait
kubectl -n "$ns" apply -f - <<'YAML'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: uploads
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 1Gi
YAML
kubectl -n "$ns" scale deployment media --replicas=1
