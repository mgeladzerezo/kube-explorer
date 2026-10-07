#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-missing-secret create secret generic billing-db-credentials \
  --from-literal=password="$(head -c 18 /dev/urandom | base64)"
