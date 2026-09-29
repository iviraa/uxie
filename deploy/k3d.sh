#!/usr/bin/env bash
# Local k3s cluster (k3s inside Docker via k3d) for the verification sandbox.
set -euo pipefail

JOBS_DIR="${UXIE_JOBS_DIR:-$HOME/.uxie/jobs}"
mkdir -p "$JOBS_DIR"

k3d cluster delete uxie >/dev/null 2>&1 || true
k3d cluster create uxie \
  --agents 1 \
  --k3s-arg "--disable=traefik@server:0" \
  --k3s-arg "--kubelet-arg=pod-max-pids=256@all" \
  -v "$JOBS_DIR:/jobs@all" \
  --wait

docker build -q -t uxie-sandbox:dev "$(dirname "$0")/../sandbox" >/dev/null
k3d image import uxie-sandbox:dev -c uxie
helm upgrade --install uxie-sandbox "$(dirname "$0")/helm/uxie-sandbox" \
  --namespace uxie-sandbox --create-namespace --wait
# Pod Security Admission: reject any pod that isn't non-root, capability-free, seccomp'd
kubectl label namespace uxie-sandbox pod-security.kubernetes.io/enforce=restricted --overwrite
