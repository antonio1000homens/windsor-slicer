#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

: "${AWS_REGION:=eu-west-2}"
: "${CLOUDFLARE_ACCOUNT_ID:?CLOUDFLARE_ACCOUNT_ID is required}"
: "${CODESPACE_NAME:?CODESPACE_NAME is required}"
: "${CODESPACE_OWNER:?CODESPACE_OWNER is required}"
: "${ACCESS_ISSUER:?ACCESS_ISSUER is required}"
: "${ACCESS_AUDIENCE:?ACCESS_AUDIENCE is required}"

get_parameter() {
  aws ssm get-parameter \
    --region "$AWS_REGION" \
    --name "/windsor-slicer/cloud-slicer/$1" \
    --with-decryption \
    --query 'Parameter.Value' \
    --output text
}

set_worker_secret() {
  local worker_name="$1"
  local parameter_name="$2"
  local value
  value="$(get_parameter "$parameter_name")"
  if [[ -z "$value" || "$value" == "None" ]]; then
    echo "Required SSM parameter /windsor-slicer/cloud-slicer/$parameter_name is empty" >&2
    return 1
  fi
  printf '::add-mask::%s\n' "$value"
  printf '%s' "$value" | CLOUDFLARE_ACCOUNT_ID="$CLOUDFLARE_ACCOUNT_ID" \
    wrangler secret put "$worker_name" --config cloud/slicer-worker/wrangler.toml
  unset value
}

cloudflare_token="$(get_parameter cloudflare-api-token)"
if [[ -z "$cloudflare_token" || "$cloudflare_token" == "None" ]]; then
  echo "Required SSM parameter /windsor-slicer/cloud-slicer/cloudflare-api-token is empty" >&2
  exit 1
fi
printf '::add-mask::%s\n' "$cloudflare_token"
export CLOUDFLARE_API_TOKEN="$cloudflare_token"
unset cloudflare_token

CLOUDFLARE_ACCOUNT_ID="$CLOUDFLARE_ACCOUNT_ID" wrangler deploy \
  --config cloud/slicer-worker/wrangler.toml \
  --var "CODESPACE_NAME:$CODESPACE_NAME" \
  --var "CODESPACE_OWNER:$CODESPACE_OWNER" \
  --var "CODESPACE_PORT:8000" \
  --var "ACCESS_ISSUER:$ACCESS_ISSUER" \
  --var "ACCESS_AUDIENCE:$ACCESS_AUDIENCE" \
  --var "ACCESS_ADDITIONAL_AUDIENCES:${ACCESS_ADDITIONAL_AUDIENCES:-}"

set_worker_secret GITHUB_CODESPACES_TOKEN github-codespaces-token
set_worker_secret ORIGIN_BEARER_TOKEN origin-bearer-token
