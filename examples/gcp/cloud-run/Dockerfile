FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    git jq curl ca-certificates gnupg tar unzip zstd build-essential \
    python3 libssl3 zlib1g libkrb5-3 libicu-dev \
  && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
  && apt-get install -y -qq --no-install-recommends nodejs \
  && corepack enable \
  && curl -fsSL "https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-unknown-linux-musl.tar.gz" \
     | tar -xz -C /usr/local/bin --strip-components=1 "ripgrep-14.1.1-x86_64-unknown-linux-musl/rg" \
  && chmod +x /usr/local/bin/rg \
  && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash runner && mkdir -p /runner /app && chown -R runner:runner /runner

ARG RUNNER_VERSION=2.322.0
RUN curl -fsSL "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
    | tar -xz -C /runner \
  && chown -R runner:runner /runner

COPY entrypoint.sh /app/entrypoint.sh
COPY webhook.py /app/webhook.py
RUN chmod +x /app/entrypoint.sh /app/webhook.py

USER runner
RUN mkdir -p "$HOME/.pi-cli/bin" "$HOME/.pi-cli/agent" \
  && ln -sf /usr/local/bin/rg "$HOME/.pi-cli/bin/rg" \
  && npm i --prefix "$HOME/.pi-cli" --no-fund --no-audit @earendil-works/pi-coding-agent@latest

WORKDIR /runner
ENTRYPOINT ["/app/entrypoint.sh"]
