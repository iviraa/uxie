#!/usr/bin/env bash
# Enable the verify dispatcher on the local cluster (after deploy/k3d.sh and `make deploy`).
set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${AWS_REGION:-us-east-2}"
out() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name uxie \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
QUEUE="$(out VerifyQueueUrl)"
TABLE="$(out ReviewsTableName)"
SECRET="$(jq -r .AppSecretArn infrastructure/parameters.json)"
OTEL="$(jq -r '.OtelSecretArn // ""' infrastructure/parameters.json)"

helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
helm upgrade --install keda kedacore/keda -n keda --create-namespace --wait

docker build -q -f dispatcher/Dockerfile -t uxie-dispatcher:dev . >/dev/null
k3d image import uxie-dispatcher:dev -c uxie

args=(--set dispatcher.enabled=true --set dispatcher.queueUrl="$QUEUE"
      --set dispatcher.reviewsTable="$TABLE" --set dispatcher.appSecretArn="$SECRET"
      --set dispatcher.otelSecretArn="$OTEL")
# first install creates the namespace; KEDA only goes ready once the AWS secret exists
helm upgrade --install uxie-sandbox deploy/helm/uxie-sandbox -n uxie-sandbox "${args[@]}" || true

if ! kubectl get secret aws-dispatcher -n uxie-system >/dev/null 2>&1; then
  # key goes straight into the cluster secret; it is never written to disk
  aws iam create-access-key --user-name uxie-dispatcher --output json \
    | jq -r '"AWS_ACCESS_KEY_ID=\(.AccessKey.AccessKeyId)\nAWS_SECRET_ACCESS_KEY=\(.AccessKey.SecretAccessKey)"' \
    | kubectl create secret generic aws-dispatcher -n uxie-system --from-env-file=/dev/stdin
fi
helm upgrade --install uxie-sandbox deploy/helm/uxie-sandbox -n uxie-sandbox --wait "${args[@]}"
