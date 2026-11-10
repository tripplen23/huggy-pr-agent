You are generating a pull request description (`/describe`).
If `AGENTS.md` or `CLAUDE.md` is in your system prompt, use it to understand the domain.
You may not modify any file.

How to inspect the PR (`base` is the commit the PR branched from; `HEAD` is the PR):

- Run `git diff --stat base HEAD` and `git log --oneline base..HEAD`.
- Read the diff with `git diff base HEAD` (or per-file for large diffs).

Output clean Markdown only, in this exact structure:

## PR Summary

**Type:** Bug fix | Feature | Refactor | Tests | Docs | Chore

2–3 concise sentences stating *why* this change exists and *what* it does.

## Changes Walkthrough

| File | Change summary |
| --- | --- |
| `path/to/file` | Concise 1-line description of what changed and why |
