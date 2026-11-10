# pi-pr-agent

An open-source, zero-infrastructure GitHub Action inspired by [PR-Agent](https://github.com/The-PR-Agent/pr-agent), powered by the [Pi coding agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent).

Unlike single-call prompt wrappers that only see a diff snippet, `pi-pr-agent` runs an agent with `read` + `rg` over the full repository, loads your `AGENTS.md` / `CLAUDE.md` from the **base** branch as its review rubric, posts multi-line inline diff comments with 1-click **Commit suggestion** blocks, and can **actually edit, test, and push fixes** to the PR branch on command.

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

## Quick Start (1 workflow file + 1 secret)

1. Add your model API key (e.g. an [OpenCode Go](https://opencode.ai/console/go) key with **Models: Request** permission) to your repository under **Settings → Secrets and variables → Actions** as **`OPENCODE_GO_KEY`**.
2. Create **`.github/workflows/pi.yml`**:

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
          verify_cmd: "pnpm typecheck && pnpm lint && pnpm test"
```

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

## Security Model

- **Base-branch rubric isolation** — `AGENTS.md` / `CLAUDE.md` and any custom `.github/pi-review-prompt.md` or `.github/pi-fix-prompt.md` are read from `base` via `git show`, and Pi runs with `--no-context-files --no-approve`. A PR cannot poison its own review checklist or auto-load `.pi/` extensions.
- **Step-scoped credentials** — `github_token` is never present in the step that runs Pi or project tests; `api_key` is only present in the step that invokes `pi` and is literally redacted (`perl \Q...\E`) from all published output.
- **Git hook & path guards** — before committing a `@pi` fix, `.git/hooks`, `core.fsmonitor`, and `core.hooksPath` are cleared, default branches (`main`/`master`) are refused, and any staged change touching `guarded_paths` aborts the push.
