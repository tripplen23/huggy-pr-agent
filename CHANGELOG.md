# Changelog

All notable changes to `huggy-pr-agent` are documented in this file.

## [1.8.0] - 2026-11-12

### Added
- **First-class Pi Package (`package.json` + `extensions/huggy.ts` + `pi-prompts/huggy-review.md`)**: installable via `pi install git:github.com/tripplen23/huggy-pr-agent` or `pi install npm:huggy-pr-agent` (`pi-package` gallery metadata included). Adds `/huggy review [base]`, `/huggy review deep [base]`, `/huggy setup`, `/huggy sync`, and the `huggy_prepare_diff` tool inside Pi.
- **Portable Cross-Harness Review Skill (`skills/huggy-review/SKILL.md`)**: brings Engine v3 local branch review to Claude Code, Codex, Cursor, Windsurf, OpenCode, and Pi via `npx skills add tripplen23/huggy-pr-agent`.
- **1-Click Update Sync (`examples/dependabot.yml` + `/huggy sync`)**: supports both automatic zero-click updates via `@v1` and 1-click merge PRs via Dependabot / Renovate when pinning `@v1.x.y`.

## [1.7.2] - 2026-11-12

### Fixed
- **Adaptive Timeout Step-Down (`scripts/run_review.py`)**: steps `thinking` down to `low` after a wall-clock `TimeoutExpired` and degrades the final fallback pass to `--no-tools` diff-only review with a reserved 300s budget so dense multi-endpoint bundles never time out across all attempts.
- **Balanced Bundle Sizing**: tightened default bundle limits to `8 files / 1,100 changed lines` to avoid lopsided bundles on medium-large PRs.

## [1.7.1] - 2026-11-12

### Changed
- **Minimal Dark/Light Wordmark Lockup (`assets/banner-dark.svg`, `assets/banner-light.svg`)**: clean `<picture>` hero adapting automatically to GitHub Dark and Light themes.

## [1.7.0] - 2026-11-12

### Added
- **Engine v3 Precision Judge (`0–100` confidence, `>= 80` inline cutoff)**: verifies findings against ground-truth code and base-branch `AGENTS.md` + path-scoped rules, enforcing 7 Hard Exclusions and demoting `65–79` items to `Low`.
- **Two-Channel `chill` vs `assertive` Output (`review_profile: chill` default)**: posts `High`/`Medium` defects inline and collapses `Low` items inside `<details><summary>🧹 Nitpicks</summary>`.
- **Base-Branch Path-Scoped Rules & `.gitattributes` Gate**: collects ancestor `AGENTS.md`/`CLAUDE.md`/`REVIEW.md` and `.github/instructions/*.instructions.md` (`applyTo` globs with brace expansion), and skips `linguist-generated` / `linguist-vendored` files.
- **Failed CI Check Log Excerpts & Content-Anchored Suggestion Relocation**: extracts `##[error]` lines from failing head-commit GitHub Actions jobs and repairs `1–5` line drift on `suggestion` blocks.
- **Dual-Lens Deep Mode (`review_depth: deep` / `/review deep`)**: runs parallel Logic/Callers and Security/Spec passes merged through the Judge.

## [1.6.3] - 2026-11-12

### Added
- **Rebrand to Huggy PR Agent (`tripplen23/huggy-pr-agent`)**: stoic 16-bit pixel-art reviewing indicator (`assets/huggy-reviewing.svg`, shown only while running) and static `Review by Huggy` icon (`assets/huggy-icon.svg`). Supports both `@huggy`/`/huggy` and `@pi`/`/pi` triggers.
- **Native Google Vertex AI & Cross-Provider Failover**: `gcp_project`, `gcp_location`, and `gcp_credentials_json` inputs with automatic `pi update --models` catalog caching.
- **Agent Installation Playbook (`INSTALL.md` + `skills/setup-huggy-pr-agent/SKILL.md`)**: 1-prompt agent setup for any repository.

## [1.5.1] - 2026-11-11

### Added
- **Smart Parallel Bundling for 10k+ LOC PRs, Fact-Checker Judge Pass & Linked Issues (`#4`, `#5`)**: counterpart file bundling, adaptive timeouts, GraphQL `closingIssuesReferences`, collaborator write-access verification (`/collaborators/{username}/permission`) for concealed org members, and rich inline diff context in `.pi-fix-request.md`.
