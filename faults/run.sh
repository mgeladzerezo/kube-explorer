#!/usr/bin/env bash
# Usage: faults/run.sh inject|revert|clean <scenario-id>
#        faults/run.sh list
# Every scenario lives in its own namespace, fault-<scenario-id>.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
action="${1:-}"
scenario="${2:-}"

if [ "$action" = "list" ]; then
  for dir in "$here"/*/; do
    [ -f "$dir/scenario.yaml" ] && basename "$dir"
  done
  exit 0
fi

if [ -z "$scenario" ] || [ ! -f "$here/$scenario/inject.yaml" ]; then
  echo "usage: $0 inject|revert|clean <scenario-id>   (see: $0 list)" >&2
  exit 2
fi

case "$action" in
  inject) kubectl apply -f "$here/$scenario/inject.yaml" ;;
  revert) bash "$here/$scenario/revert.sh" ;;
  clean)  kubectl delete namespace "fault-$scenario" --ignore-not-found ;;
  *)      echo "unknown action: $action" >&2; exit 2 ;;
esac
