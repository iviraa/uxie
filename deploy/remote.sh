#!/usr/bin/env bash
# Run the verification cluster on a remote Ubuntu server (e.g. an Oracle Cloud Always Free
# ARM VM) instead of local k3d. Usage: deploy/remote.sh ubuntu@HOST [ssh-key]
# Only SSH is exposed; the Kubernetes API is reached through an SSH tunnel on localhost:16443.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${1:?usage: deploy/remote.sh ubuntu@HOST [ssh-key]}"
KEY="${2:-$HOME/.ssh/uxie_oracle}"
SSH=(ssh -i "$KEY" -o BatchMode=yes "$HOST")
export KUBECONFIG="$HOME/.kube/uxie-remote.yaml"

# 1. k3s (same settings as deploy/k3d.sh), plus the jobs directory
"${SSH[@]}" 'command -v k3s >/dev/null || curl -sfL https://get.k3s.io | sudo INSTALL_K3S_EXEC="--disable=traefik --kubelet-arg=pod-max-pids=256 --write-kubeconfig-mode=600" sh -
sudo mkdir -p /var/lib/uxie/jobs && sudo chown 10001:10001 /var/lib/uxie/jobs'

# 2. Oracle's Ubuntu images end INPUT/FORWARD with a blanket REJECT, which breaks pod
#    networking. Allow cluster-internal ranges just before it (live and on disk).
"${SSH[@]}" 'set -e
for chain in INPUT FORWARD; do
  pos=$(sudo iptables -L $chain --line-numbers | awk "/REJECT/ && /icmp-host-prohibited/ {print \$1; exit}")
  [ -z "$pos" ] && continue
  for cidr in 10.42.0.0/16 10.43.0.0/16; do
    sudo iptables -C $chain -s $cidr -j ACCEPT 2>/dev/null || sudo iptables -I $chain $pos -s $cidr -j ACCEPT
  done
  if [ -f /etc/iptables/rules.v4 ] && ! sudo grep -q "^-A $chain -s 10.42.0.0/16" /etc/iptables/rules.v4; then
    sudo sed -i "0,/^-A $chain -j REJECT/s##-A $chain -s 10.42.0.0/16 -j ACCEPT\n-A $chain -s 10.43.0.0/16 -j ACCEPT\n-A $chain -j REJECT#" /etc/iptables/rules.v4
  fi
done'

# 3. kubeconfig through an SSH tunnel (the API port is never opened to the internet)
umask 077
"${SSH[@]}" 'sudo cat /etc/rancher/k3s/k3s.yaml' \
  | sed 's#https://127.0.0.1:6443#https://127.0.0.1:16443#; s/: default$/: uxie-remote/' > "$KUBECONFIG"
pgrep -f "16443:127.0.0.1:6443" >/dev/null \
  || ssh -i "$KEY" -o BatchMode=yes -o ExitOnForwardFailure=yes -fN -L 16443:127.0.0.1:6443 "$HOST"

# 4. images: built locally (same arch as an ARM server) and streamed into containerd
docker build -q -t uxie-sandbox:dev sandbox >/dev/null
docker build -q -f sandbox/go.Dockerfile -t uxie-sandbox-go:dev sandbox >/dev/null
docker build -q -f sandbox/ts.Dockerfile -t uxie-sandbox-ts:dev sandbox >/dev/null
docker build -q -f dispatcher/Dockerfile -t uxie-dispatcher:dev . >/dev/null
for img in uxie-sandbox:dev uxie-sandbox-go:dev uxie-sandbox-ts:dev uxie-dispatcher:dev; do
  docker save "$img" | gzip -1 | "${SSH[@]}" 'gunzip | sudo k3s ctr -n k8s.io images import - >/dev/null'
done

# 5. KEDA, the chart, and the dispatcher's AWS key (never written to disk)
REGION="${AWS_REGION:-us-east-2}"
out() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name uxie \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
args=(--set dispatcher.enabled=true --set dispatcher.queueUrl="$(out VerifyQueueUrl)"
      --set dispatcher.reviewsTable="$(out ReviewsTableName)"
      --set dispatcher.appSecretArn="$(jq -r .AppSecretArn infrastructure/parameters.json)"
      --set dispatcher.otelSecretArn="$(jq -r '.OtelSecretArn // ""' infrastructure/parameters.json)"
      --set jobs.nodePath=/var/lib/uxie/jobs)
helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
helm upgrade --install keda kedacore/keda -n keda --create-namespace --wait
helm upgrade --install uxie-sandbox deploy/helm/uxie-sandbox -n uxie-sandbox --create-namespace \
  "${args[@]}" || true  # KEDA only goes ready once the AWS secret below exists
kubectl label namespace uxie-sandbox pod-security.kubernetes.io/enforce=restricted --overwrite
if ! kubectl get secret aws-dispatcher -n uxie-system >/dev/null 2>&1; then
  aws iam create-access-key --user-name uxie-dispatcher --output json \
    | jq -r '"AWS_ACCESS_KEY_ID=\(.AccessKey.AccessKeyId)\nAWS_SECRET_ACCESS_KEY=\(.AccessKey.SecretAccessKey)"' \
    | kubectl create secret generic aws-dispatcher -n uxie-system --from-env-file=/dev/stdin
fi
helm upgrade --install uxie-sandbox deploy/helm/uxie-sandbox -n uxie-sandbox --wait "${args[@]}"
echo "remote verification cluster ready on $HOST"
