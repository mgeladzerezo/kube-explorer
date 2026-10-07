#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-bad-image-tag set image deployment/catalog catalog=nginx:1.27-alpine
