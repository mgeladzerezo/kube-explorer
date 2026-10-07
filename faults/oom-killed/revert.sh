#!/usr/bin/env bash
set -euo pipefail
# The loop allocates without bound, so the fix caps the allocation as well as raising the limit.
kubectl -n fault-oom-killed patch deployment report-worker --type json -p '[
  {"op":"replace","path":"/spec/template/spec/containers/0/command","value":
    ["python","-u","-c","import time\nrows=[bytearray(8*1024*1024) for _ in range(4)]\nprint(\"loaded 32 MiB\", flush=True)\ntime.sleep(86400)"]},
  {"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/memory","value":"128Mi"}
]'
