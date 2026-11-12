---
name: huggy-review
description: Run Huggy Engine v3 high-precision code review (`L<num>` diff annotation, Review-Shaped caller lookup, `AGENTS.md` compliance, and `>= 80` confidence filtering with separated Nitpicks) on local git branches or pull requests across any coding agent (Pi, Claude Code, Codex, Cursor, OpenCode). Use when asked to review local changes, review a branch, or pre-check a PR before pushing.
---

# Huggy Engine v3 Local Code Review

Portable across **Pi**, **Claude Code**, **Codex**, **Cursor**, and **OpenCode**.

## Workflow

1. **Prepare `L<num>` Diff & Scoped Rules**
   - If the `huggy_prepare_diff` tool is available (Pi extension), call it.
   - Otherwise, run the bundled zero-dependency Python helper relative to this skill directory (`../../scripts/fetch_pr_context.py`):
     ```bash
     python3 -c '
     import subprocess, sys
     sys.path.insert(0, "'"$(dirname "$0")"'" )
     '
     ```
     Or run `git -c core.quotePath=false diff $(git merge-base origin/HEAD HEAD 2>/dev/null || echo HEAD~1)` directly, skipping lockfiles and `.gitattributes` `linguist-generated` paths, and read root + ancestor `AGENTS.md` / `CLAUDE.md` / `REVIEW.md`.

2. **Review-Shaped Tool Loop (Max 3–4 Turns)**
   - **Turn 0 (Hypothesize):** Read the diff and form specific questions about callers, schemas, auth checks, state transitions, and `AGENTS.md` rules. Make zero exploratory calls.
   - **Turn 1 (Batch Discovery):** Batch all `grep` / `find` lookups for modified functions, types, routes, and config keys in a single parallel wave.
   - **Turn 2 (Batch Narrow Reads):** Batch narrow file reads (`offset` + `limit`) around the discovered call sites.

3. **Precision Gate (`0–100` Confidence) & Two-Channel Output**
   - Drop (`< 65`): pre-existing untouched code, formatter/linter style opinions, ungrounded "what-ifs", or rules not actually written in `AGENTS.md` / `CLAUDE.md`.
   - **Actionable Findings (`confidence >= 80`, `High` / `Medium`):** List first with exact `path:start-end`, concrete failure scenario, and drop-in fix.
   - **Nitpicks (`confidence 65–79`, `Low`):** Group separately in a brief **Nitpicks** section at the end so main findings stay noise-free.
