#!/usr/bin/env bash
# Ensure `pi`, its remote model catalog (`models-store.json`), `rg`, and
# provider credentials (API key and/or Google Vertex AI) are ready inside
# `$HOME/.pi-cli` without relying on `apt-get` or `brew`.
set -euo pipefail

mkdir -p "$HOME/.pi-cli/bin" "$HOME/.pi-cli/agent"
export PATH="$HOME/.pi-cli/bin:$HOME/.pi-cli/node_modules/.bin:$PATH"
export PI_CODING_AGENT_DIR="$HOME/.pi-cli/agent"

if ! command -v pi > /dev/null; then
  for attempt in 1 2 3; do
    npm i --prefix "$HOME/.pi-cli" --no-fund --no-audit "@earendil-works/pi-coding-agent@${PI_VERSION:-latest}" && break
    echo "npm install failed (attempt $attempt)" >&2
    sleep 15
  done
  command -v pi > /dev/null || { echo "Failed to install pi CLI after 3 attempts" >&2; exit 1; }
fi

# Refresh remote model catalog (`models-store.json`) when missing or >12h old so
# newly released models (e.g. `google-vertex/gemini-4-carbon`, `opencode-go/deepseek-v4.1-flash`)
# resolve in headless `pi --print` runs.
if ! find "$PI_CODING_AGENT_DIR" -maxdepth 1 -name "models-store.json" -mmin -720 2>/dev/null | grep -q .; then
  for attempt in 1 2; do
    pi update --models > /dev/null 2>&1 && break
    sleep 3
  done
  [ -s "$PI_CODING_AGENT_DIR/models-store.json" ] \
    || echo "warning: could not refresh remote model catalog; falling back to built-in catalog" >&2
fi

if ! command -v rg > /dev/null; then
  os_name=$(uname -s)
  arch_name=$(uname -m)
  rg_target=""
  case "${os_name}:${arch_name}" in
    Linux:x86_64)                rg_target="x86_64-unknown-linux-musl" ;;
    Linux:aarch64|Linux:arm64)   rg_target="aarch64-unknown-linux-gnu" ;;
    Darwin:arm64|Darwin:aarch64) rg_target="aarch64-apple-darwin" ;;
    Darwin:x86_64)               rg_target="x86_64-apple-darwin" ;;
  esac
  if [ -n "$rg_target" ]; then
    rg_ver="14.1.1"
    rg_url="https://github.com/BurntSushi/ripgrep/releases/download/${rg_ver}/ripgrep-${rg_ver}-${rg_target}.tar.gz"
    if curl -fsSL --retry 3 --max-time 30 "$rg_url" | tar -xz -C "$HOME/.pi-cli/bin" --strip-components=1 "ripgrep-${rg_ver}-${rg_target}/rg" 2>/dev/null; then
      chmod +x "$HOME/.pi-cli/bin/rg"
    else
      echo "warning: could not fetch ripgrep release binary; falling back to built-in grep" >&2
    fi
  fi
fi

# Configure API key provider (primary or cross-provider fallback)
if [ -n "${PI_API_KEY:-}" ]; then
  export "${PI_API_KEY_ENV:-OPENCODE_API_KEY}=$PI_API_KEY"
fi

# Configure Google Vertex AI (Service Account JSON or pre-configured WIF ADC)
if [ -n "${PI_GCP_CREDENTIALS_JSON:-}" ]; then
  gcp_key_file="${RUNNER_TEMP:-/tmp}/pi-gcp-sa-$$.json"
  (umask 077 && printf '%s' "$PI_GCP_CREDENTIALS_JSON" > "$gcp_key_file")
  trap 'rm -f "${gcp_key_file:-}"' EXIT
  export GOOGLE_APPLICATION_CREDENTIALS="$gcp_key_file"
  if [ -z "${PI_GCP_PROJECT:-}" ]; then
    PI_GCP_PROJECT=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("project_id",""))' "$gcp_key_file" 2>/dev/null || true)
  fi
fi

if [ -n "${PI_GCP_PROJECT:-}" ]; then
  export GOOGLE_CLOUD_PROJECT="$PI_GCP_PROJECT"
fi
if [ -n "${GOOGLE_CLOUD_PROJECT:-}" ] || [ -n "${GCLOUD_PROJECT:-}" ]; then
  export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-${PI_GCP_LOCATION:-global}}"
fi

key_env_name="${PI_API_KEY_ENV:-OPENCODE_API_KEY}"
ambient_key="${!key_env_name:-}"
if [ -z "${PI_API_KEY:-}" ] && [ -z "$ambient_key" ] && [ -z "${GOOGLE_CLOUD_PROJECT:-}" ] && [ -z "${GCLOUD_PROJECT:-}" ] && [ -z "${GOOGLE_CLOUD_API_KEY:-}" ]; then
  echo "No model credentials configured: provide api_key and/or gcp_project (+ gcp_credentials_json or Workload Identity Federation)." >&2
  exit 1
fi
