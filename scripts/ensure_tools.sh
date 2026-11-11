#!/usr/bin/env bash
# Ensure `pi` and `rg` are installed in `$HOME/.pi-cli` (cached across runs)
# without relying on Ubuntu `apt-get` mirrors or `brew`.
set -euo pipefail

mkdir -p "$HOME/.pi-cli/bin"
export PATH="$HOME/.pi-cli/bin:$HOME/.pi-cli/node_modules/.bin:$PATH"

if ! command -v pi > /dev/null; then
  for attempt in 1 2 3; do
    npm i --prefix "$HOME/.pi-cli" --no-fund --no-audit "@earendil-works/pi-coding-agent@${PI_VERSION:-latest}" && break
    echo "npm install failed (attempt $attempt)" >&2
    sleep 15
  done
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
