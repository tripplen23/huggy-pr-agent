# Huggy PR Agent — Agent Installation Playbook (`INSTALL.md`)

> **Audience:** Coding agents (`Pi`, `Claude Code`, `Codex`, `Cursor`, `Windsurf`, `OpenCode`) asked to set up **Huggy PR Agent** (`tripplen23/huggy-pr-agent@v1`) on a GitHub repository.

Follow these steps in order. Do not push directly to the repository's default branch if branch protection is enabled — create a setup branch and open a pull request instead.

---

## Step 1 — Inspect the Target Repository

Inspect the repository root (read at most the top-level manifests and existing CI workflows) to determine four values:

1. **Runtime toolchain steps** needed *only* for the `command` job (when `@huggy` / `@pi` edits code and runs tests):
   - Node.js (`package.json`): check `.nvmrc`, `engines.node`, `devEngines.runtime`, or existing `.github/workflows/*.yml` / `Dockerfile` for the exact Node version (`actions/setup-node@v4`).
   - Go (`go.mod` or `*/go.mod`): check the `go` directive (`actions/setup-go@v5`).
   - Rust (`Cargo.toml`): `dtolnay/rust-toolchain@stable`.
   - Python (`pyproject.toml` / `requirements.txt`): `actions/setup-python@v5`.
   - Swift / macOS Xcode (`Package.swift` / `*.xcodeproj`): set `runs-on: macos-15` on the `command` job while keeping `review` on `ubuntu-latest`.
2. **`setup_cmd`**: command that installs dependencies cleanly without mutating lockfiles (e.g. `corepack enable pnpm && pnpm install --frozen-lockfile`, `npm ci`, `uv sync --frozen`, or `""` for Go/Rust/Swift).
3. **`verify_cmd`**: fast deterministic check suite that must pass before `@huggy` is allowed to commit and push a fix (e.g. `pnpm typecheck && pnpm lint && pnpm test`, `cargo clippy && cargo test`, `go vet ./... && go test ./...`, `pytest`). Copy any required placeholder env vars (such as dummy `DATABASE_URL`) from existing CI workflows.
4. **`guarded_paths`**: space-separated paths `@huggy` must never modify automatically. Always include `.github`, plus lockfiles/manifests/migrations/deploy configs present in the repo (e.g. `".github Dockerfile package.json pnpm-lock.yaml migrations"`).

**Done when:** You have concrete, repository-verified values for toolchain steps, `setup_cmd`, `verify_cmd`, and `guarded_paths`.

---

## Step 2 — Choose Provider & Verify GitHub Actions Secrets

Ask the user which model provider they want **only if** they have not already stated one and `gh secret list` shows none of the secrets below.

| Provider Choice | Workflow `with:` Inputs | Required GitHub Actions Secret(s) |
| :--- | :--- | :--- |
| **OpenCode Go (Default)** | `api_key: ${{ secrets.OPENCODE_GO_KEY }}` *(uses default `opencode-go/deepseek-v4.1-flash` + fallback `opencode-go/glm-5.3-flash`)* | `OPENCODE_GO_KEY` |
| **Google Vertex AI + Cross-Provider Fallback** | `model: google-vertex/gemini-4-carbon`<br>`fallback_model: opencode-go/deepseek-v4.1-flash`<br>`gcp_project: <PROJECT_ID>`<br>`gcp_location: global`<br>`gcp_credentials_json: ${{ secrets.GOOGLE_VERTEX_CREDENTIALS }}`<br>`api_key: ${{ secrets.OPENCODE_GO_KEY }}`<br>`max_parallel_bundles: "2"` | `GOOGLE_VERTEX_CREDENTIALS` *(Service Account JSON with `roles/aiplatform.user`)* + optional `OPENCODE_GO_KEY` |
| **OpenRouter** | `api_key: ${{ secrets.OPENROUTER_API_KEY }}`<br>`api_key_env: OPENROUTER_API_KEY`<br>`model: openrouter/anthropic/claude-sonnet-4` | `OPENROUTER_API_KEY` |
| **Anthropic** | `api_key: ${{ secrets.ANTHROPIC_API_KEY }}`<br>`api_key_env: ANTHROPIC_API_KEY`<br>`model: anthropic/claude-sonnet-4-5` | `ANTHROPIC_API_KEY` |
| **OpenAI** | `api_key: ${{ secrets.OPENAI_API_KEY }}`<br>`api_key_env: OPENAI_API_KEY`<br>`model: openai/gpt-5` | `OPENAI_API_KEY` |

Check existing repository secrets without reading values:

```bash
gh secret list --repo OWNER/REPO
```

- If `OPENCODE_GO_KEY` is missing and Pi is authenticated locally with `opencode-go`, offer or run (with user confirmation):
  ```bash
  pi auth print-api-key --provider opencode-go | tr -d '\r\n' | gh secret set OPENCODE_GO_KEY --repo OWNER/REPO
  ```
- If using Google Vertex AI and `~/.config/gcloud/pi-vertex-sa.json` exists locally, set `GOOGLE_VERTEX_CREDENTIALS` without printing the file:
  ```bash
  gh secret set GOOGLE_VERTEX_CREDENTIALS --repo OWNER/REPO < ~/.config/gcloud/pi-vertex-sa.json
  ```
- Otherwise, give the user the exact `gh secret set <SECRET_NAME>` one-liner to paste their key safely.

**Done when:** `gh secret list` shows the required secret(s), or the user has the exact `gh secret set` command ready.

---

## Step 3 — Write `.github/workflows/huggy.yml`

Create `.github/workflows/huggy.yml` using the canonical template below, substituting the toolchain steps, `setup_cmd`, `verify_cmd`, `guarded_paths`, and provider inputs from Steps 1–2:

```yaml
name: Huggy
on:
  pull_request:
    types: [opened, synchronize, reopened]
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]

jobs:
  review:
    if: >-
      github.event_name == 'pull_request' &&
      github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    timeout-minutes: 60
    permissions:
      contents: write # required by GitHub GraphQL resolveReviewThread
      pull-requests: write
    concurrency:
      group: huggy-review-${{ github.event.pull_request.number }}
      cancel-in-progress: true
    steps:
      - uses: tripplen23/huggy-pr-agent@v1
        with:
          mode: review
          api_key: ${{ secrets.OPENCODE_GO_KEY }}

  command:
    if: >-
      (github.event_name == 'pull_request_review_comment' ||
       (github.event_name == 'issue_comment' && github.event.issue.pull_request)) &&
      github.event.comment.user.type != 'Bot' &&
      (contains(github.event.comment.body, '@huggy') ||
       contains(github.event.comment.body, '/huggy') ||
       contains(github.event.comment.body, '@pi') ||
       contains(github.event.comment.body, '/pi') ||
       contains(github.event.comment.body, '/fix') ||
       contains(github.event.comment.body, '/review') ||
       contains(github.event.comment.body, '/describe') ||
       contains(github.event.comment.body, '/improve') ||
       contains(github.event.comment.body, '/ask'))
    runs-on: ubuntu-latest
    timeout-minutes: 90
    permissions:
      contents: write
      pull-requests: write
      issues: write
    concurrency:
      group: huggy-cmd-${{ github.event.issue.number || github.event.pull_request.number }}
      cancel-in-progress: false
    steps:
      # Insert repo toolchain setup steps from Step 1 here (e.g. actions/setup-node@v4)
      - uses: tripplen23/huggy-pr-agent@v1
        with:
          mode: comment
          api_key: ${{ secrets.OPENCODE_GO_KEY }}
          setup_cmd: "<FROM_STEP_1>"
          verify_cmd: "<FROM_STEP_1>"
          guarded_paths: "<FROM_STEP_1>"
```

### Critical Invariants (Do Not Alter)

1. Keep `review` job `timeout-minutes: 60` and `command` job `timeout-minutes: 90` so large 10k+ LOC multi-bundle PRs have headroom to finish and run the Fact-Check Judge pass.
2. Do **not** add `author_association` checks to the `command` job's `if:` expression — GitHub redacts concealed organization memberships (`MEMBER` → `CONTRIBUTOR`/`NONE`) in webhook payloads. `tripplen23/huggy-pr-agent@v1` verifies true repository `write`/`maintain`/`admin` permission upstream via `/collaborators/{username}/permission`.
3. If the repository has an `AGENTS.md` or `CLAUDE.md` on its default branch, `huggy-pr-agent` loads it automatically as the review and fix rubric (`rubric_file` defaults to `AGENTS.md` with automatic fallback to `CLAUDE.md`).

**Done when:** `.github/workflows/huggy.yml` parses cleanly (`python3 -c "import yaml; yaml.safe_load(open('.github/workflows/huggy.yml'))"` or `actionlint`) and contains zero leftover `<FROM_STEP_1>` placeholders (`! grep -q '<FROM_STEP_1>' .github/workflows/huggy.yml`).

---

## Step 4 — Verify & Report

1. Validate the workflow YAML locally.
2. Summarize the configured provider/models, `verify_cmd`, `guarded_paths`, and how the team can trigger Huggy on any PR:
   - Automatic inline review on every PR push
   - `@huggy <instruction>` (or `@pi <instruction>` / `/fix <instruction>`) on any diff line or PR comment to push a verified fix and auto-resolve the review thread
   - `/describe`, `/improve`, `/ask <question>`, `/review`
