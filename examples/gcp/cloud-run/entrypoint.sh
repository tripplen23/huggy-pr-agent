#!/usr/bin/env bash
# Ephemeral 1-job GitHub Actions Runner entrypoint for Google Cloud Run Jobs.
# Receives a short-lived single-use GH_REG_TOKEN minted by webhook.py (never holds GH_RUNNER_PAT).
set -euo pipefail

: "${GH_REPO_URL:?GH_REPO_URL is required (e.g. https://github.com/OWNER/REPO or https://github.com/ORG)}"
: "${GH_REG_TOKEN:?GH_REG_TOKEN is required (minted per execution by webhook.py)}"

TRIMMED="${GH_REPO_URL%/}"
RUNNER_NAME="huggy-cr-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')"

./config.sh \
  --unattended \
  --ephemeral \
  --replace \
  --url "$TRIMMED" \
  --token "$GH_REG_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "${RUNNER_LABELS:-gcp-huggy}" \
  --work "_work"
unset GH_REG_TOKEN

# Bound idle wait: if the queued job was cancelled before boot, exit after 180s instead of idling 95m
(
  sleep "${IDLE_PICKUP_TIMEOUT_SEC:-180}"
  if ! pgrep -f "Runner.Worker" >/dev/null 2>&1; then
    echo "No job claimed within idle timeout; shutting down ephemeral runner." >&2
    pkill -TERM -f "Runner.Listener" || true
  fi
) &

exec ./run.sh
