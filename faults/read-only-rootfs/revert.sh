#!/usr/bin/env bash
set -euo pipefail
# Keep the root filesystem read-only and give nginx writable scratch directories instead.
kubectl -n fault-read-only-rootfs patch deployment assets --type strategic -p '
{"spec":{"template":{"spec":{
  "volumes":[{"name":"cache","emptyDir":{}},{"name":"run","emptyDir":{}}],
  "containers":[{"name":"assets","volumeMounts":[
    {"name":"cache","mountPath":"/var/cache/nginx"},{"name":"run","mountPath":"/var/run"}]}]}}}}'
