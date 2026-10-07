#!/usr/bin/env bash
# Creates the kind cluster, builds and loads both images, and installs the chart.
# Requires docker, kind, kubectl and helm. Optional: ANTHROPIC_API_KEY to use the real model.
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
cluster=kube-explorer
namespace=kube-explorer

if ! kind get clusters | grep -qx "$cluster"; then
  kind create cluster --config "$root/deploy/kind/kind-config.yaml"
fi

docker build -t kube-explorer-backend:dev "$root/backend"
docker build -t kube-explorer-frontend:dev "$root/frontend"
kind load docker-image --name "$cluster" kube-explorer-backend:dev kube-explorer-frontend:dev

kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$namespace" apply -f "$root/deploy/kind/postgres.yaml"
kubectl -n "$namespace" rollout status deployment/postgres --timeout=120s

kubectl -n "$namespace" create secret generic kube-explorer \
  --from-literal=DATABASE_URL="postgresql+psycopg://kube_explorer:kube_explorer@postgres:5432/kube_explorer" \
  --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
  --dry-run=client -o yaml | kubectl apply -f -

llm_mode=fake
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  llm_mode=anthropic
fi

helm upgrade --install kube-explorer "$root/deploy/helm/kube-explorer" \
  --namespace "$namespace" \
  -f "$root/deploy/helm/kube-explorer/values-kind.yaml" \
  --set backend.env.LLM_MODE="$llm_mode" \
  --wait --timeout 5m

echo "Installed with LLM_MODE=$llm_mode. Open the UI with:"
echo "  kubectl -n $namespace port-forward svc/kube-explorer-frontend 5175:80"
