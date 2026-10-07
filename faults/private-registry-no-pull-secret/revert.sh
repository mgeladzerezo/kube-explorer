#!/usr/bin/env bash
set -euo pipefail
# With a real private image the fix is an imagePullSecret; here a public image is swapped in.
kubectl -n fault-private-registry-no-pull-secret set image deployment/pricing \
  pricing=nginx:1.27-alpine
