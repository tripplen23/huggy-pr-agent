# pi-pr-agent

An open-source, zero-infrastructure GitHub Action inspired by [PR-Agent](https://github.com/The-PR-Agent/pr-agent), powered by the [Pi coding agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent).

Unlike single-call prompt wrappers that only see a diff snippet, `pi-pr-agent` runs an agent with `read` + `rg` over the full repository, loads your `AGENTS.md` / `CLAUDE.md` from the **base** branch as its review rubric, posts multi-line inline diff comments with 1-click **Commit suggestion** blocks, and can **actually edit, test, and push fixes** to the PR branch on command.

---

## 3-Step Setup Guide (Any Repository in 2 Minutes)

### Step 1 — Set the API Key Secret

Using the GitHub CLI (`gh`), set your [OpenCode Go](https://opencode.ai/console/go) key (or OpenRouter/Anthropic key) on the target repository:

```bash
# If you already connected opencode-go in Pi locally:
pi auth print-api-key --provider opencode-go | gh secret set OPENCODE_GO_KEY --repo OWNER/REPO

# Or paste a restricted key interactively:
gh secret set OPENCODE_GO_KEY --repo OWNER/REPO
```

*(Or in the GitHub UI: **Settings → Secrets and variables → Actions → New repository secret** → Name: `OPENCODE_GO_KEY`.)*

### Step 2 — Add `.github/workflows/pi.yml`

Create `.github/workflows/pi.yml` in your repository and adjust `verify_cmd` to your project's test command:

```yaml
name: Pi
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
    timeout-minutes: 20
    permissions:
      contents: read
      pull-requests: write
    concurrency:
      group: pi-review-${{ github.event.pull_request.number }}
      cancel-in-progress: true
    steps:
      - uses: tripplen23/pi-pr-agent@v1
        with:
          mode: review
          api_key: ${{ secrets.OPENCODE_GO_KEY }}

  command:
    if: >-
      (github.event_name == 'pull_request_review_comment' ||
       (github.event_name == 'issue_comment' && github.event.issue.pull_request)) &&
      contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association) &&
      github.event.comment.user.type != 'Bot'
    runs-on: ubuntu-latest
    timeout-minutes: 45
    permissions:
      contents: write
      pull-requests: write
      issues: write
    concurrency:
      group: pi-cmd-${{ github.event.issue.number || github.event.pull_request.number }}
      cancel-in-progress: false
    steps:
      - uses: tripplen23/pi-pr-agent@v1
        with:
          mode: comment
          api_key: ${{ secrets.OPENCODE_GO_KEY }}
          # Replace with your repo's test command (e.g. "cargo test", "go test ./...", "pytest"):
          verify_cmd: "pnpm typecheck && pnpm lint && pnpm test"
```

> **macOS / Swift projects** (e.g. [`HappyVoxel/granny`](https://github.com/HappyVoxel/granny)): keep `review` on `ubuntu-latest` (fast & free) and set `runs-on: macos-15` on `command` so `swift test` can gate `@pi` commits — see [`examples/swift-macos.yml`](examples/swift-macos.yml).

### Step 3 — Merge to Default Branch (`main` / `master`)

- **Auto-review (`pull_request`)** works immediately — even on the PR that adds `.github/workflows/pi.yml`.
- **Slash & `@pi` comment commands (`issue_comment`, `pull_request_review_comment`)** become active as soon as `.github/workflows/pi.yml` is merged to your default branch (`main` / `master`), because GitHub reads comment-triggered workflows from the default branch.

---

## Commands & Capabilities

| Trigger / Command | Mode | What it does |
| --- | --- | --- |
| **Automatic on PR open / sync** *(or comment `/review`)* | Read-only | Reviews `base...HEAD` against `AGENTS.md` / `CLAUDE.md`, posts inline diff comments (`start_line..line`) with colored `High` / `Medium` / `Low` badges, 1-click `suggestion` blocks, auto-resolves addressed threads, and updates one sticky `<!-- pi-review -->` summary. |
| **`/describe`** *(or `@pi /describe`)* | Read-only | Generates a structured PR summary (`Type`, overview, and file-by-file walkthrough table) and updates the PR description. |
| **`/improve`** *(or `@pi /improve`)* | Read-only | Scans the PR diff for self-contained code improvements and posts inline committable ` ```suggestion ` blocks. |
| **`/ask <question>`** *(or `@pi /ask ...`)* | Read-only | Answers questions about the PR or surrounding codebase directly in the conversation or diff review thread, citing exact `path:line` references. |
| **`@pi <instruction>`** *(or `/fix ...`, `/pi ...`)* | Write + Verify | Edits the PR branch to address the request (works in PR conversation, inline diff threads, and **Quote replies**), runs your `verify_cmd` test gate, blocks changes to `guarded_paths`, and commits + pushes to the PR branch. |

---

## Configuration & Inputs

| Input | Default | Description |
| --- | --- | --- |
| `mode` | `auto` | `review`, `comment` (`fix`), or `auto` (`pull_request` → review, comment events → slash/`@pi` commands). |
| `api_key` | *(required)* | API key for the model provider. |
| `api_key_env` | `OPENCODE_API_KEY` | Environment variable Pi reads for the provider key (`OPENCODE_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.). |
| `model` | `opencode-go/deepseek-v4.1-flash` | Model ID passed to `pi --model`. |
| `pi_version` | `1.5.0` | Version of `@earendil-works/pi-coding-agent` to install. |
| `rubric_file` | `AGENTS.md` | File read from the **base** branch as the system-prompt rubric (falls back to `CLAUDE.md` if absent). |
| `extra_instructions` | `""` | Extra repository-specific review/fix rules appended to the prompt. |
| `verify_cmd` | `""` | Shell command that must pass before a `@pi` / `/fix` edit is committed and pushed. |
| `guarded_paths` | `.github .woodpecker Dockerfile ...` | Space-separated paths that `@pi` / `/fix` is forbidden from staging or pushing. |
| `github_token` | `${{ github.token }}` | Token used to post reviews/comments and push fixes. |

---

## Customizing the Rubric per Repo

1. **Zero-config (`AGENTS.md` / `CLAUDE.md`)** — `pi-pr-agent` automatically loads `AGENTS.md` (or `CLAUDE.md`) from the base branch into Pi's system prompt. Whatever architectural invariants you document there become your PR review checklist automatically.
2. **Inline rules (`extra_instructions`)** — pass `extra_instructions:` in `with:` for quick repo-specific checks.
3. **Full prompt override** — add `.github/pi-review-prompt.md` or `.github/pi-fix-prompt.md` on your default branch to completely replace the built-in prompt while keeping the inline diff publisher and thread sync.

---

## Security Model

- **Base-branch rubric isolation** — `AGENTS.md` / `CLAUDE.md` and any custom `.github/pi-review-prompt.md` or `.github/pi-fix-prompt.md` are read from `base` via `git show`, and Pi runs with `--no-context-files --no-approve`. A PR cannot poison its own review checklist or auto-load `.pi/` extensions.
- **Step-scoped credentials** — `github_token` is never present in the step that runs Pi or project tests; `api_key` is only present in the step that invokes `pi` and is literally redacted (`perl \Q...\E`) from all published output.
- **Git hook & path guards** — before committing a `@pi` fix, `.git/hooks`, `core.fsmonitor`, and `core.hooksPath` are cleared, default branches (`main`/`master`) are refused, and any staged change touching `guarded_paths` aborts the push.
