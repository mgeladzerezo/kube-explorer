#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-unschedulable-resources set resources deployment/analytics \
  --requests=cpu=100m,memory=64Mi
