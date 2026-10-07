#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-service-selector-mismatch patch service web --type merge \
  -p '{"spec":{"selector":{"app":"web"}}}'
