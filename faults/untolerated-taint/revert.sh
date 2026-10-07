#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-untolerated-taint patch deployment nightly-batch --type merge -p '
{"spec":{"template":{"spec":{"tolerations":[
  {"key":"dedicated","operator":"Equal","value":"batch","effect":"NoSchedule"}]}}}}'
