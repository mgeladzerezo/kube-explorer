#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-wrong-container-port patch service docs --type json \
  -p '[{"op":"replace","path":"/spec/ports/0/targetPort","value":"http"}]'
