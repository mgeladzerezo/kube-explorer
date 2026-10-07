#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-node-selector-mismatch patch deployment search --type json \
  -p '[{"op":"remove","path":"/spec/template/spec/nodeSelector"}]'
