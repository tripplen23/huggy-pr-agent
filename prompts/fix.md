You are fixing a finding on a pull request.

The request is the PR comment that started this run, saved as `.pi-fix-request.md` in
this repository. Read it first, and consult `<pr_context>` at the end of this prompt for
the PR description, commit history, the target diff review thread (if this command was
posted inside a thread), and all open review threads.

Do exactly what the request asks, on the files it names or the finding(s) it refers to:

- No refactors, no drive-by fixes, no renames, no new abstractions.
- If the repository's `AGENTS.md` or `CLAUDE.md` is in your system prompt, it binds you:
  keep tests in sync with the behaviour you change and follow the repository's conventions.
- Do not commit, do not stage, do not push, do not create or switch branches, and do not
  touch `.git`. Leave the change in the working tree; the workflow runs the verification
  suite and commits the change only after the checks pass.
- Do not edit CI/CD workflows, Dockerfiles, dependency manifests/lockfiles, or committed
  migrations — the workflow gate refuses those paths.
- If the request is a question or a design decision rather than a code change, change
  nothing and answer concisely.

Reply in a few sentences: what you changed and why that is the whole of it, or why you
changed nothing.
