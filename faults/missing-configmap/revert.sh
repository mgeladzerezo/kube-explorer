#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-missing-configmap create configmap edge-proxy-conf \
  --from-literal=default.conf='server { listen 80; location / { return 200 "ok\n"; } }'
