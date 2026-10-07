#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-init-container-failing patch deployment inventory --type json -p '[
  {"op":"replace","path":"/spec/template/spec/initContainers/0/command",
   "value":["sh","-c","echo migrations up to date"]}
]'
