# Changelog

All notable changes to `pi-pr-agent` are documented in this file.

## [v1.2.0] - 2026-11-10

### Added
- **Pre-numbered `L<num>` diffs & lockfile filtering (`scripts/fetch_pr_context.py`)**: filters out lockfiles/generated files (`pnpm-lock.yaml`, `Cargo.lock`, `go.sum`, `*.min.js`, `*.snap`, `*.tsbuildinfo`), generates an explicit `<review_files>` checklist, and prefixes every new-side hunk line with `L<num>` to eliminate line-number drift.
- **Full commit message bodies (`messageBody`)** in `<pr_context>` so design rationale recorded in commits is visible during review and PR description sync.
- **Nearest-hunk line relocation (`scripts/post_review.py`)**: snaps findings that drift 1–5 lines outside a hunk boundary back to the nearest valid hunk line while disarming `suggestion` fences.
- **`thinking` input (default `high`)**: configurable reasoning depth for review runs while pre-numbered diffs eliminate wasted tool turns.

## [v1.1.0] - 2026-11-10

### Added
- **PR Context & Review Thread History (`<pr_context>`)**: feeds PR title, description, commit list, open & resolved review threads (with author replies), and conversation comments into both review and `@pi` command runs.
- **Anti-bias & Stale PR Description Sync (`<pr_description>`)**: automatically updates empty or stale PR descriptions when later commits on the branch have drifted.
- **Bounded tool-turn rules**: prevents the review agent from running slow build/test commands that CI already runs in parallel.

## [v1.0.3] - 2026-11-10

### Added
- Initial public & GitHub Marketplace release:
  - Automatic inline PR diff reviews (`start_line..line`) with colored `High` / `Medium` / `Low` SVG badges and 1-click `Commit suggestion` blocks.
  - GraphQL review thread deduplication and automatic resolution of addressed threads.
  - Interactive PR comment commands: `/review`, `/describe`, `/improve`, `/ask`, and test-gated `@pi` / `/fix` auto-commits (including inside inline diff threads and **Quote replies**).
  - Portable `perl` alarm timeout supporting both `ubuntu-latest` and `macos-15` runners.
