#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-liveness-probe-failing patch deployment gateway --type json -p '[
  {"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/httpGet/port","value":80}
]'
