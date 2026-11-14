#!/usr/bin/env bash
# GCE startup-script for Option A: Multi-slot GitHub Actions Self-Hosted Runner for Huggy
# Compatible with Debian 13 (Trixie) / Ubuntu 24.04 (100% Google Startup Credit eligible).
set -euo pipefail
exec > >(tee -a /var/log/huggy-provision.log) 2>&1

if [ -f /var/log/huggy-provision.done ]; then
  echo "huggy-runner already provisioned; skipping."
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  git jq curl ca-certificates gnupg tar unzip zstd build-essential \
  python3 libssl3 zlib1g libkrb5-3 libicu-dev

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
  apt-get install -y -qq nodejs
fi
corepack enable

if ! command -v rg >/dev/null 2>&1; then
  curl -fsSL "https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin --strip-components=1 "ripgrep-14.1.1-x86_64-unknown-linux-musl/rg"
  chmod +x /usr/local/bin/rg
fi

if ! id -u runner >/dev/null 2>&1; then
  useradd -m -s /bin/bash runner
fi

su - runner -c '
  set -euo pipefail
  mkdir -p "$HOME/.pi-cli/bin" "$HOME/.pi-cli/agent"
  ln -sf /usr/local/bin/rg "$HOME/.pi-cli/bin/rg"
  npm i --prefix "$HOME/.pi-cli" --no-fund --no-audit "@earendil-works/pi-coding-agent@${HUGGY_PI_VERSION:-latest}"
  export PATH="$HOME/.pi-cli/bin:$HOME/.pi-cli/node_modules/.bin:$PATH"
  export PI_CODING_AGENT_DIR="$HOME/.pi-cli/agent"
  pi update --models || true
'

RUNNER_VER="${HUGGY_RUNNER_VERSION:-2.338.2}"
mkdir -p /opt/actions-runner/_tool
TARBALL="/tmp/actions-runner-linux-x64-${RUNNER_VER}.tar.gz"
curl -fsSL -o "$TARBALL" "https://github.com/actions/runner/releases/download/v${RUNNER_VER}/actions-runner-linux-x64-${RUNNER_VER}.tar.gz"

for slot in 1 2 3; do
  dir="/opt/actions-runner/slot-${slot}"
  mkdir -p "$dir" "$dir/_tmp"
  tar -xzf "$TARBALL" -C "$dir"
done
rm -f "$TARBALL"

cat >/usr/local/bin/huggy-job-cleanup.sh <<'EOF'
#!/usr/bin/env bash
# Clean only this slot's isolated TMPDIR and finished RUNNER_TEMP (never shared /tmp across active slots)
if [ -n "${TMPDIR:-}" ] && [[ "$TMPDIR" == /opt/actions-runner/slot-*/_tmp ]]; then
  find "$TMPDIR" -mindepth 1 -delete 2>/dev/null || true
fi
if [ -n "${RUNNER_TEMP:-}" ] && [[ "$RUNNER_TEMP" == /opt/actions-runner/slot-* ]]; then
  find "$RUNNER_TEMP" -mindepth 1 -delete 2>/dev/null || true
fi
EOF
chmod +x /usr/local/bin/huggy-job-cleanup.sh
chown -R runner:runner /opt/actions-runner

cat >/usr/local/bin/register-huggy-runners <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: sudo register-huggy-runners <https://github.com/ORG_OR_ORG/REPO> <REGISTRATION_TOKEN> [LABELS]" >&2
  exit 1
fi

GH_URL="${1%/}"
REG_TOKEN="$2"
LABELS="${3:-gcp-huggy}"
GCP_PROJ=$(curl -fsSL -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/project/project-id 2>/dev/null || true)

for slot in 1 2 3; do
  dir="/opt/actions-runner/slot-${slot}"
  mkdir -p "$dir/_tmp"
  chown runner:runner "$dir/_tmp"
  name="$(hostname)-${slot}"
  cd "$dir"
  if [ -f .service ]; then
    ./svc.sh stop || true
    ./svc.sh uninstall || true
  fi
  rm -f .runner .credentials .credentials_rsaparams
  cat >"$dir/.env" <<ENVEOF
TMPDIR=${dir}/_tmp
RUNNER_TOOL_CACHE=/opt/actions-runner/_tool
ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/bin/huggy-job-cleanup.sh
GOOGLE_CLOUD_PROJECT=${GCP_PROJ}
GOOGLE_CLOUD_LOCATION=global
ENVEOF
  chown runner:runner "$dir/.env"
  su - runner -c "cd '$dir' && ./config.sh --unattended --replace --url '$GH_URL' --token '$REG_TOKEN' --name '$name' --labels '$LABELS' --work '_work'"
  ./svc.sh install runner
  ./svc.sh start
done
echo "Registered 3 runner slots with labels: [self-hosted, linux, x64, ${LABELS}]"
EOF
chmod +x /usr/local/bin/register-huggy-runners

touch /var/log/huggy-provision.done
echo "PROVISION_COMPLETE"
