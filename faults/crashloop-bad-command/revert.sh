#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-crashloop-bad-command patch deployment notifier --type json -p '[
  {"op":"replace","path":"/spec/template/spec/containers/0/command","value":["httpd"]},
  {"op":"replace","path":"/spec/template/spec/containers/0/args","value":["-f","-p","8080"]}
]'
