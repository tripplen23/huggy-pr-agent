# Huggy PR Agent — Repository Instructions (`AGENTS.md`)

`huggy-pr-agent` is a zero-dependency Python 3 stdlib + GitHub Composite Action + Pi Package (`extensions/huggy.ts`) + Agent Skills bundle (`skills/`).

## Core Invariants

1. **Zero Python third-party runtime dependencies:** All orchestrator scripts under `scripts/` (`fetch_pr_context.py`, `run_review.py`, `post_review.py`, `post_progress.py`, `prepare_command.py`) must use Python 3 standard library only so GitHub Actions runners need zero `pip install` steps.
2. **Strict Secret & Token Isolation (`SECURITY.md`):**
   - `GH_TOKEN` must **never** be passed into `env:` of any step that runs `pi` or `verify_cmd`, and must **never** be persisted into `.git/config` or `.git/FETCH_HEAD` (`persist-credentials: false` + ephemeral `git -c http.https://github.com/.extraheader=...` + `rm -f .git/FETCH_HEAD`).
   - **GitHub GraphQL `resolveReviewThread` quirk (`orgs/community#20469`):** GitHub's GraphQL `resolveReviewThread` mutation rejects `GITHUB_TOKEN` with `FORBIDDEN: Resource not accessible by integration` unless **both** `contents: write` and `pull-requests: write` are granted on the `review` job. Do not flag `contents: write` on `review` jobs when `persist-credentials: false` and `.git/config` / `FETCH_HEAD` scrubbing are in place.
   - `AGENTS.md`, `CLAUDE.md`, `REVIEW.md`, `.instructions.md`, and `.gitattributes` are always read from the `base` branch (`git show base:...`), never from untrusted PR head files.
3. **Tests & Validation:**
   - Every behavior change in `scripts/` must be covered in `tests/test_post_review.py` (`python3 tests/test_post_review.py -v`).
   - Every pixel-art SVG in `assets/` must parse as valid XML with `shape-rendering="crispEdges"`.
   - Never merge a PR while `review` is pending or while any review thread remains unresolved (`required_conversation_resolution: true`).
