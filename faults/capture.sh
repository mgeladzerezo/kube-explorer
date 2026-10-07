#!/usr/bin/env bash
# Usage: faults/capture.sh <scenario-id>
# Records the scenario's namespace from the current cluster into faults/<id>/captured,
# replacing the synthesized capture with real output. Inject the fault first and give it
# a few minutes to settle (restarts, back-off) before capturing.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
scenario="${1:?usage: capture.sh <scenario-id>}"
namespace="fault-$scenario"
out="$here/$scenario/captured"

rm -rf "$out"
mkdir -p "$out/logs"
touch "$out/logs/.gitkeep"

# One YAML document per object, the layout backend/app/kube/fake.py loads.
resources="deployments,replicasets,statefulsets,daemonsets,pods,services,endpoints,events,persistentvolumeclaims,configmaps,networkpolicies,resourcequotas"
{
  echo "# Captured from a live cluster by faults/capture.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)."
  for item in $(kubectl -n "$namespace" get "$resources" -o name) $(kubectl get nodes -o name); do
    echo "---"
    kubectl -n "$namespace" get "$item" -o yaml
  done
} > "$out/cluster.yaml"

for pod in $(kubectl -n "$namespace" get pods -o jsonpath='{.items[*].metadata.name}'); do
  containers=$(kubectl -n "$namespace" get pod "$pod" \
    -o jsonpath='{.spec.initContainers[*].name} {.spec.containers[*].name}')
  for container in $containers; do
    kubectl -n "$namespace" logs "$pod" -c "$container" --tail=200 \
      > "$out/logs/$pod.$container.log" 2>/dev/null || rm -f "$out/logs/$pod.$container.log"
    kubectl -n "$namespace" logs "$pod" -c "$container" --tail=200 --previous \
      > "$out/logs/$pod.$container.previous.log" 2>/dev/null \
      || rm -f "$out/logs/$pod.$container.previous.log"
  done
done

echo "captured $namespace into $out"
echo "Captured logs and objects are written as-is. Check them for credentials before committing."
