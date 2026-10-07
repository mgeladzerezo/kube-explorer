#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-quota-exceeded patch resourcequota compute-quota --type merge \
  -p '{"spec":{"hard":{"requests.cpu":"2"}}}'
