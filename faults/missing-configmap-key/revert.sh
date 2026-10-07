#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-missing-configmap-key patch configmap app-config --type merge \
  -p '{"data":{"APP_MODE":"production"}}'
