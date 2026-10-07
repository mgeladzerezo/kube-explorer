#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-readiness-probe-failing patch deployment storefront --type json -p '[
  {"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/"}
]'
