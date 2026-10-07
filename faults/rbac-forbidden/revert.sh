#!/usr/bin/env bash
set -euo pipefail
ns=fault-rbac-forbidden
kubectl -n "$ns" create role pod-reader --verb=get,list,watch --resource=pods
kubectl -n "$ns" create rolebinding pod-lister --role=pod-reader --serviceaccount="$ns:pod-lister"
