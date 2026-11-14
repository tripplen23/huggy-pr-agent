# Changelog

All notable changes to `huggy-pr-agent` are documented in this file.

## [1.12.0] - 2026-11-14

### Added
- **Google Cloud Self-Hosting Guides & Turnkey Templates (`docs/self-hosting-gcp.md`, `examples/gcp/`)**: documents and ships ready-to-run templates for both **Option A (Dedicated GCE VM Multi-Slot Runner, `examples/gcp/gce-setup.sh`)** and **Option B (Serverless Scale-to-Zero Ephemeral Runners on Cloud Run Jobs, `examples/gcp/cloud-run/`)** — keeping GitHub-hosted `runs-on: ubuntu-latest` as the zero-config default while letting high-volume private orgs eliminate GitHub Actions minute usage on GCP Startup Credits with a 1-line `runs-on:` switch.

## [1.11.1] - 2026-11-13

### Fixed
- **Verified GraphQL `resolveReviewThread` & Ephemeral Fetch Auth (`scripts/post_review.py`, `scripts/fetch_pr_context.py`, `action.yml`)**: inspects GraphQL `errors` and `thread.isResolved` before counting threads as resolved, uses `persist-credentials: false` + ephemeral `-c http.<server>/.extraheader` + `FETCH_HEAD` cleanup so `GH_TOKEN` never persists on disk during `Run Pi`, and reads `.gitattributes` from `base` via `GIT_ATTR_SOURCE=base`.
- **Provisional `review.md` on `--phase find` (`scripts/run_review.py`)**: writes a provisional `review.md` before handing off to `--phase judge` so `post_review.py` never fails if the Judge step is skipped or interrupted, and preserves `Security: Needs attention` on `nits` verdicts.
- **Ambiguous Fingerprint & Security Badge Handling (`scripts/post_review.py`)**: avoids cross-line overwrites or premature thread resolution when two findings in the same file share a fingerprint, and displays a distinct `nits · security` alert badge when security attention is flagged without High-severity findings.

## [1.11.0] - 2026-11-13

### Added
- **Multi-Act Review Animation with Coffee-Sip Loop (`assets/huggy-reviewing.svg`)**: `huggy-reviewing.svg` now runs a 24-beat (`6.72s`) discrete pixel-art loop — brisk typing on the laptop with a porcelain mug resting on the desk, followed by an occasional stoic coffee sip before returning to the keyboard.
- **Expressive Verdict Header Icons & Colored Verdict Badges (`assets/huggy-good.svg`, `assets/huggy-serious.svg`, `scripts/post_review.py`)**: the finished `Review by Huggy` header now adapts to the outcome at a glance — cheerful smile + emerald badge on `looks good` / `nits`, stoic neutral + amber badge on `Medium` `needs changes`, and furrowed V-brows with crimson lens glint + red alert badge whenever `High` or security findings are present.

## [1.10.0] - 2026-11-13

### Added
- **Full-Lifecycle 16-Bit Pixel-Art Live Animations (`assets/huggy-{judging,fixing,testing,thinking,blocked}.svg` + `scripts/post_progress.py`)**: live animated status cards now cover every stage of Huggy's workflow — transitioning from `huggy-reviewing.svg` to `huggy-judging.svg` during the precision Judge pass, from `huggy-fixing.svg` to `huggy-testing.svg` when `@huggy` runs `verify_cmd`, showing `huggy-thinking.svg` on `/ask` & `/describe`, and displaying `huggy-blocked.svg` on guarded-boundary refusals — before replacing themselves cleanly in-place once finished.
- **Pi Marketplace Hero Card (`assets/pi-marketplace-card.png`)**: custom `1280x720` pixel-art gallery thumbnail for `pi.dev/packages/huggy-pr-agent`, plus animated pixel-art walkthroughs across `README.md`.
- **Two-Phase Split Execution (`--phase find` / `--phase judge`)**: updates the PR review banner to the Judge pass between bundle discovery and verification while keeping `GH_TOKEN` strictly isolated from every Pi invocation step.

## [1.9.0] - 2026-11-12

### Added
- **Lead + Wingmen Parallel Worker Pool & Blast-Radius Risk Router (`wingman_model` + `scripts/run_review.py`)**: computes best-effort reverse-import blast radius (`compute_blast_radius`) and domain criticality (`migrations/`, `auth`, `billing`, `security`, `contracts`, `api/v1`) before dispatching bundles so the highest-risk bundle and Judge pass run Lead-first on `model` (e.g. `google-vertex/gemini-4-carbon`) while parallel sibling bundles run concurrently on `wingman_model` (e.g. `google-vertex/gemini-4-argon`), falling back to `fallback_model` only on failure/timeout.
- **Line-Shift-Resilient Thread Fingerprints & Multi-Comment Auto-Resolve (`scripts/post_review.py`)**: embeds `<!-- pi id=<hash> -->` markers so open findings survive line shifts across pushes, auto-resolves fixed threads even after developer/agent replies (while preserving open human questions), and respects `👎` downvoted suggestions across re-runs.
- **`🤖 Prompt for AI Agents` Handoff Block**: automatically attaches a collapsible agent-ready fix prompt on `High`/`Medium` inline findings that span multiple lines/files and cannot be committed as a 1-click `suggestion`.
- **Direct Target Issue Extraction & `huggy:deep` Label Trigger (`scripts/fetch_pr_context.py`)**: extracts direct target issues from branch names (`feat/123-...`), PR titles, and `Closes/Fixes` lines, and automatically enables `deep` dual-lens review when a PR carries the `huggy:deep` or `review:deep` label.
- **Ponytail Senior-On-Call Discipline & Anti-Hedging Gate (`prompts/review.md`, `prompts/judge.md`)**: calibrates scale findings against expected load, prioritizes smallest fixes (preferring deletion/reusing existing repo helpers over new abstractions), respects documented `shortcut:` / `ponytail:` limits, and filters out speculative hedging and self-contradictory rambling.

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
