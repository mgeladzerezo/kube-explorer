#!/usr/bin/env bash
set -euo pipefail
kubectl -n fault-netpol-blocks-dns patch networkpolicy checkout-egress --type json -p '[
  {"op":"add","path":"/spec/egress/-","value":{
    "to":[{"namespaceSelector":{"matchLabels":{"kubernetes.io/metadata.name":"kube-system"}},
           "podSelector":{"matchLabels":{"k8s-app":"kube-dns"}}}],
    "ports":[{"protocol":"UDP","port":53},{"protocol":"TCP","port":53}]}}
]'
