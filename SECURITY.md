# Security Policy

## Threat Model

`pi-pr-agent` runs against pull requests whose diffs and files may contain untrusted input (including prompt injection attempts planted in source files, commit messages, or PR descriptions).

### Built-in Defenses

1. **Fork & Write-Access Isolation**
   - Recommended workflows skip review jobs on fork PRs (`github.event.pull_request.head.repo.full_name == github.repository`), and interactive `@pi` / slash commands verify actual repository write access (`write`, `maintain`, or `admin` via `/repos/{owner}/{repo}/collaborators/{username}/permission`) before checking out code or running `setup_cmd`.
2. **Base-Branch Rubric & Action Script Isolation**
   - `AGENTS.md`, `CLAUDE.md`, and custom prompt overrides are read from the `base` branch via `git show base:<path>`.
   - For external callers (`uses: tripplen23/pi-pr-agent@v1`), action scripts and prompts live outside the repository workspace and are snapshotted to `$RUNNER_TEMP/pi-action/` before any `git checkout pr-head` (in this repository's own CI, `uses: ./` intentionally tests the PR's action scripts on `pull_request` while the `command` job pins its checkout to the default branch).
   - Pi is invoked with `--no-extensions --no-mcp --no-skills --no-context-files --no-approve --no-session`, and read-only PR reviews default to shell-free built-in tools (`--tools read,grep,find,ls`) while the Judge pass runs with `--no-tools`.
3. **Step-Scoped Secrets & Non-Persisted Checkout Credentials**
   - `github_token` (`GH_TOKEN`) is only passed to the checkout/fetch and publish steps — never to the `Run Pi` or `Verify fix` steps.
   - **GitHub GraphQL `GITHUB_TOKEN` quirk (`orgs/community#20469`):** GitHub's GraphQL `resolveReviewThread` mutation rejects `GITHUB_TOKEN` with `FORBIDDEN: Resource not accessible by integration` unless **both** `contents: write` and `pull-requests: write` are granted on the workflow job (verified empirically in CI). To keep `Run Pi` completely credential-free even when the job declares `contents: write`, `action.yml` sets `persist-credentials: false` on `actions/checkout@v4`, passes ephemeral `-c http.https://github.com/.extraheader=...` flags to setup `git fetch` calls (so raw tokens never enter `.git/config` or `.git/FETCH_HEAD`), deletes `.git/FETCH_HEAD`, and excludes `GH_TOKEN` from every `Run Pi` step.
   - `api_key` and `gcp_credentials_json` are only passed to the `Run Pi` steps, shredded on exit, and redacted from all published comments.
   - Use a restricted, monthly-capped model API key (e.g. an OpenCode Go key with `Models: Request` permission only).
4. **Git Hook & Guarded Path Enforcement**
   - Before staging/committing in the publish step, `.git/hooks`, `core.fsmonitor`, and `core.hooksPath` are removed, `git commit` runs with `-c core.hooksPath=/dev/null -c core.fsmonitor=false`, and `git push` runs with `--no-verify`.
   - Changes to `.github`, `.woodpecker`, `Dockerfile`, lockfiles, or migrations (`guarded_paths`) are rejected and reset before any push.

## Reporting a Vulnerability

If you discover a security issue (such as a credential leak path, command injection, or guard bypass), please open a private security advisory on GitHub at:

https://github.com/tripplen23/pi-pr-agent/security/advisories/new
