---
name: setup-huggy-pr-agent
description: Install and configure Huggy PR Agent (`tripplen23/huggy-pr-agent@v1`) GitHub Actions workflows for automated inline PR code review, 1-click commit suggestions, and test-gated `@huggy` / `@pi` auto-fixes (supporting OpenCode Go, Google Vertex AI, OpenRouter, Anthropic, and OpenAI).
---

# Setup Huggy PR Agent

Follow [`../../INSTALL.md`](../../INSTALL.md) (or fetch `https://raw.githubusercontent.com/tripplen23/huggy-pr-agent/v1/INSTALL.md` if running standalone) step by step:

1. **Inspect the repository** (`package.json`, `go.mod`, `Cargo.toml`, `Package.swift`, `pyproject.toml`, and existing CI workflows) to determine toolchain setup steps, `setup_cmd`, `verify_cmd`, and `guarded_paths`.
2. **Verify GitHub Actions secrets** (`gh secret list`) for the chosen provider (`OPENCODE_GO_KEY`, `GOOGLE_VERTEX_CREDENTIALS`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY`).
3. **Write `.github/workflows/huggy.yml`** using `tripplen23/huggy-pr-agent@v1` with `timeout-minutes: 60` (`review`) and `timeout-minutes: 90` (`command`), keeping write-permission verification delegated to `huggy-pr-agent` so concealed org members are never skipped.
4. **Validate YAML syntax** (`actionlint` or `yaml.safe_load`) before opening a PR.
