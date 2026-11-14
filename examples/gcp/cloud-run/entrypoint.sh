#!/usr/bin/env bash
# Ephemeral 1-job GitHub Actions Runner entrypoint for Google Cloud Run Jobs.
set -euo pipefail

: "${GH_REPO_URL:?GH_REPO_URL is required (e.g. https://github.com/OWNER/REPO or https://github.com/ORG)}"
: "${GH_RUNNER_PAT:?GH_RUNNER_PAT is required (fine-grained PAT with Administration: Read & Write)}"

TRIMMED="${GH_REPO_URL%/}"
PATH_PART="${TRIMMED#https://github.com/}"
if [[ "$PATH_PART" == */* ]]; then
  TOKEN_API="https://api.github.com/repos/${PATH_PART}/actions/runners/registration-token"
else
  TOKEN_API="https://api.github.com/orgs/${PATH_PART}/actions/runners/registration-token"
fi

REG_TOKEN=$(curl -fsSL -X POST \
  -H "Authorization: Bearer ${GH_RUNNER_PAT}" \
  -H "Accept: application/vnd.github+json" \
  "$TOKEN_API" | jq -r '.token')

[ -n "$REG_TOKEN" ] && [ "$REG_TOKEN" != "null" ] || { echo "Failed to mint runner registration token" >&2; exit 1; }
unset GH_RUNNER_PAT

RUNNER_NAME="huggy-cr-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
cleanup() {
  ./config.sh remove --token "$REG_TOKEN" >/dev/null 2>&1 || true
}
trap cleanup EXIT

./config.sh \
  --unattended \
  --ephemeral \
  --replace \
  --url "$TRIMMED" \
  --token "$REG_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "${RUNNER_LABELS:-gcp-huggy}" \
  --work "_work"

exec ./run.sh
