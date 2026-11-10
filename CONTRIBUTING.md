# Contributing to `pi-pr-agent`

Thank you for helping improve `pi-pr-agent`! This repository packages a deterministic pre/post-processing pipeline around the [Pi coding agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent) for GitHub Pull Requests.

---

## Repository Layout

```text
pi-pr-agent/
├── action.yml                  # Composite GitHub Action definition (review + comment modes)
├── scripts/
│   ├── fetch_pr_context.py     # GraphQL context fetcher, lockfile filter & L<num> diff annotator
│   └── post_review.py          # Hunk mapper, line relocation, GraphQL thread sync & review publisher
├── prompts/
│   ├── review.md               # Default /review prompt
│   ├── fix.md                  # Default @pi / /fix prompt
│   ├── describe.md             # /describe prompt
│   ├── improve.md              # /improve prompt
│   └── ask.md                  # /ask prompt
├── examples/                   # Copy-paste workflow templates (Node/pnpm, Swift/macOS, minimal)
├── assets/                     # SVG banner, preview card, and architecture diagrams
└── tests/
    └── test_post_review.py     # Unit test suite (stdlib unittest, zero external deps)
```

---

## Running Tests Locally

The Python scripts use only the Python 3.9+ standard library so they run on any GitHub runner (`ubuntu-latest`, `macos-15`) with zero `pip install` overhead.

```bash
# 1. Run the unit test suite
python3 tests/test_post_review.py -v

# 2. Validate YAML syntax of action.yml and workflow files
python3 -c "import yaml; yaml.safe_load(open('action.yml'))"
```

---

## Security Invariants (Must Not Be Broken)

Every pull request to `pi-pr-agent` must preserve these four invariants:

1. **Never load untrusted context files from the PR head** — `AGENTS.md`, `CLAUDE.md`, `.github/pi-review-prompt.md`, and `.github/pi-fix-prompt.md` must always be read from `base:` (`git show base:...`), and `pi` must always be invoked with `--no-context-files --no-approve`.
2. **Never put `GH_TOKEN` (`github_token`) in the same step that runs `pi` or `verify_cmd`** — untrusted PR code or test scripts could otherwise read `process.env.GH_TOKEN` and push to the repository before the verification gate finishes.
3. **Never allow a `@pi` fix to push to the default branch or touch `guarded_paths`** — `.git/hooks`, `core.fsmonitor`, and `core.hooksPath` must be stripped before any `git` command runs with `GH_TOKEN`.
4. **Never leave a `suggestion` fence active when a comment's line range was relocated or collapsed across diff hunks** — disarm ` ```suggestion ` to a plain code fence whenever `eff_start != start` or `relocated == True` so clicking **Commit suggestion** cannot overwrite the wrong lines.

---

## Releasing a New Version

After merging to `main` and verifying CI is green:

```bash
git tag -f v1.x.y
git tag -f v1
git push -f origin v1.x.y v1
gh release create v1.x.y --title "v1.x.y" --generate-notes
```
