#!/usr/bin/env python3
"""Parallel multi-comment `@huggy` / `@pi` / `/fix` swarm orchestrator, test-gate repair, and publisher."""

from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import fetch_pr_context  # noqa: E402
import post_progress  # noqa: E402
import post_review  # noqa: E402
import prepare_command  # noqa: E402
import run_review  # noqa: E402

BLOCKED_IMG = (
    '<img src="https://raw.githubusercontent.com/tripplen23/huggy-pr-agent/v1/assets/huggy-blocked.svg" '
    'width="160" alt="Huggy stopped on a guarded boundary" />'
)


def group_fix_tasks(tasks: list[dict]) -> tuple[list[tuple[str, list[dict]]], list[tuple[str, list[dict]]]]:
    """Partition `tasks` into `(disjoint_file_groups, general_groups)`.

    Inline comments targeting the same file path are merged into one worker group so two
    parallel workers never edit the same file simultaneously. General PR-wide `/fix` comments
    (`path == ""`) run sequentially after the parallel file-targeted wave.
    """
    by_path: dict[str, list[dict]] = {}
    general: list[tuple[str, list[dict]]] = []
    for t in tasks:
        p = (t.get("path") or "").strip()
        if p:
            by_path.setdefault(p, []).append(t)
        else:
            general.append(("", [t]))
    file_groups = list(by_path.items())
    return file_groups, general


def build_worker_prompt(
    base_fix_prompt: str,
    target_path: str,
    group_tasks: list[dict],
    sibling_locked_paths: list[str],
    pr_context_md: str = "",
    extra_instructions: str = "",
) -> str:
    """Assemble a self-contained prompt for one worker group (1 or more comments on `target_path`)."""
    parts = [base_fix_prompt.strip()]
    parts.append(
        "\nNote: Your full request is embedded directly under `## Assigned Fix Task(s)` below "
        "(you do not need to open `.pi-fix-request.md`)."
    )
    if extra_instructions.strip():
        parts.extend(["", "Additional repository instructions:", extra_instructions.strip()])

    if sibling_locked_paths:
        locked_list = ", ".join(f"`{p}`" for p in sibling_locked_paths)
        parts.extend(
            [
                "",
                "## Parallel Worker File Ownership Rule (CRITICAL)",
                f"- You are assigned to `{target_path}`.",
                f"- Sibling workers are concurrently editing: {locked_list}.",
                "- Do NOT edit or overwrite any file in that sibling list. Keep your edits strictly scoped to your assigned target.",
            ]
        )

    parts.extend(["", f"## Assigned Fix Task(s) ({len(group_tasks)} comment(s))", ""])
    for idx, t in enumerate(group_tasks, start=1):
        if len(group_tasks) > 1:
            parts.append(f"### Task {idx}/{len(group_tasks)} (Comment #{t.get('comment_id')})")
        parts.append((t.get("request_markdown") or t.get("body") or "").strip())
        parts.append("")

    if pr_context_md.strip():
        parts.extend(["", pr_context_md.strip()])
    return "\n".join(parts) + "\n"


def make_gh_api(token: str):
    def api(method: str, url: str, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "huggy-pr-agent",
            },
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None

    return api


def deliver_task_answer(api_fn, repo: str, pr: int, task: dict, message: str) -> None:
    """Replace `task['progress_comment_id']` in-place via PATCH, falling back to POST reply."""
    pid = str(task.get("progress_comment_id") or "").strip()
    pkind = str(task.get("progress_comment_kind") or "issue").strip().lower()
    if pid.isdigit():
        patch_url = (
            f"https://api.github.com/repos/{repo}/pulls/comments/{pid}"
            if pkind == "pull"
            else f"https://api.github.com/repos/{repo}/issues/comments/{pid}"
        )
        try:
            api_fn("PATCH", patch_url, {"body": message})
            return
        except Exception as exc:
            sys.stderr.write(f"warning: PATCH on progress comment {pid} failed ({exc}); falling back to POST\n")

    tcid = task.get("comment_id")
    if task.get("is_thread") and isinstance(tcid, int):
        post_url = f"https://api.github.com/repos/{repo}/pulls/{pr}/comments/{tcid}/replies"
    else:
        post_url = f"https://api.github.com/repos/{repo}/issues/{pr}/comments"
    try:
        api_fn("POST", post_url, {"body": message})
    except Exception as exc:
        sys.stderr.write(f"warning: could not post command answer for comment {tcid} ({exc})\n")


def load_tasks_or_default(temp: str) -> list[dict]:
    tasks_path = os.path.join(temp, "fix-tasks.json")
    if os.path.isfile(tasks_path):
        try:
            loaded = json.loads(open(tasks_path, encoding="utf-8").read())
            if isinstance(loaded, list) and loaded:
                return loaded
        except Exception:
            pass

    cid_raw = (os.environ.get("COMMENT_ID") or "").strip()
    cid = int(cid_raw) if cid_raw.isdigit() else 0
    req_path = os.path.join(temp, "fix-request.md")
    req_md = open(req_path, encoding="utf-8").read() if os.path.isfile(req_path) else (os.environ.get("COMMENT_BODY") or "")
    summary_path = os.path.join(temp, "request.txt")
    summary = open(summary_path, encoding="utf-8").readline().strip() if os.path.isfile(summary_path) else "apply fix"
    return [
        {
            "comment_id": cid,
            "is_thread": bool((os.environ.get("THREAD") or "").strip()),
            "thread_id": "",
            "user": os.environ.get("COMMENT_USER") or "",
            "path": os.environ.get("COMMENT_PATH") or "",
            "summary": summary,
            "body": os.environ.get("COMMENT_BODY") or "",
            "request_markdown": req_md,
            "progress_comment_id": os.environ.get("PROGRESS_COMMENT_ID") or None,
            "progress_comment_kind": os.environ.get("PROGRESS_COMMENT_KIND") or None,
        }
    ]


def run_swarm(temp: str) -> int:
    tasks = load_tasks_or_default(temp)
    tasks_path = os.path.join(temp, "fix-tasks.json")
    rubric_path = os.path.join(temp, "rubric.md")
    pr_ctx_path = os.path.join(temp, "pr-context.md")
    pr_ctx_md = open(pr_ctx_path, encoding="utf-8").read() if os.path.isfile(pr_ctx_path) else ""

    primary_model = (os.environ.get("PI_MODEL") or "opencode-go/deepseek-v4.1-flash").strip()
    wingman_model = (os.environ.get("PI_WINGMAN_MODEL") or "").strip()
    fallback_model = (os.environ.get("PI_FALLBACK_MODEL") or "").strip()
    thinking = (os.environ.get("PI_THINKING") or "high").strip()
    api_key = os.environ.get("PI_API_KEY") or ""
    extra_instructions = os.environ.get("EXTRA_INSTRUCTIONS") or ""
    max_parallel = max(1, min(8, int(os.environ.get("PI_MAX_PARALLEL_BUNDLES") or "4")))
    timeout_min = max(5, int(os.environ.get("PI_FIX_TIMEOUT_MINUTES") or "25"))
    total_sec = timeout_min * 60
    deadline = time.monotonic() + total_sec

    custom_fix_prompt = fetch_pr_context.load_base_git_file(".github/pi-fix-prompt.md")
    if custom_fix_prompt.strip():
        base_fix_prompt = custom_fix_prompt
    else:
        base_fix_prompt = open(os.path.join(temp, "pi-action", "prompts", "fix.md"), encoding="utf-8").read()

    file_groups, general_groups = group_fix_tasks(tasks)
    is_parallel_wave = len(file_groups) > 1
    all_file_paths = [p for p, _ in file_groups]

    print(
        f"[fix] Executing {len(tasks)} @huggy/@pi request(s): "
        f"{len(file_groups)} file group(s) (parallel={min(max_parallel, max(1, len(file_groups)))}) "
        f"+ {len(general_groups)} general group(s)."
    )

    any_succeeded = False

    def _exec_group(idx: int, target_path: str, g_tasks: list[dict], allow_bash: bool) -> bool:
        siblings = [p for p in all_file_paths if p != target_path] if is_parallel_wave and target_path else []
        role_name, model_cands = run_review.select_worker_candidates(
            idx, primary_model, wingman_model, fallback_model
        )
        # Ensure primary gets a retry when no wingman/fallback is set
        if len(model_cands) == 1:
            model_cands = [model_cands[0], model_cands[0]]
        prompt = build_worker_prompt(
            base_fix_prompt=base_fix_prompt,
            target_path=target_path,
            group_tasks=g_tasks,
            sibling_locked_paths=siblings,
            pr_context_md=pr_ctx_md,
            extra_instructions=extra_instructions,
        )
        tools_arg = "read,grep,find,ls,bash,edit,write" if allow_bash else "read,grep,find,ls,edit,write"
        out = run_review.invoke_pi(
            prompt_text=prompt,
            model_candidates=model_cands,
            thinking=thinking,
            tools_arg=tools_arg,
            rubric_path=rubric_path,
            attempt_timeout_sec=min(total_sec, 900),
            deadline_monotonic=deadline,
            label=f"fix-{idx + 1}-{role_name}" + (f"({os.path.basename(target_path)})" if target_path else ""),
        )
        if not out:
            for t in g_tasks:
                t["reply"] = "Worker timed out or failed before producing an edit."
                t["ok"] = False
            return False
        clean_reply = run_review.redact_secret(out.strip(), api_key)
        for t in g_tasks:
            t["reply"] = clean_reply
            t["ok"] = True
        return True

    if len(file_groups) == 1 and not general_groups:
        p0, g0 = file_groups[0]
        # Maintain legacy `.pi-fix-request.md` on single-task runs in case custom repo prompts reference it
        with open(".pi-fix-request.md", "w", encoding="utf-8") as rfh:
            rfh.write(g0[0].get("request_markdown") or g0[0].get("body") or "")
        try:
            any_succeeded = _exec_group(0, p0, g0, allow_bash=True)
        finally:
            if os.path.isfile(".pi-fix-request.md"):
                os.remove(".pi-fix-request.md")
    else:
        if file_groups:
            workers = min(max_parallel, len(file_groups))
            if workers <= 1:
                for i, (p, g_tasks) in enumerate(file_groups):
                    if _exec_group(i, p, g_tasks, allow_bash=not general_groups):
                        any_succeeded = True
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = [
                        pool.submit(_exec_group, i, p, g_tasks, False)
                        for i, (p, g_tasks) in enumerate(file_groups)
                    ]
                    for fut in concurrent.futures.as_completed(futs):
                        if fut.result():
                            any_succeeded = True
        for j, (_, g_tasks) in enumerate(general_groups):
            if _exec_group(len(file_groups) + j, "", g_tasks, allow_bash=True):
                any_succeeded = True

    failed_tasks = [t for t in tasks if t.get("ok") is False]
    if not any_succeeded or failed_tasks:
        sys.stderr.write("At least one @huggy fix worker failed; aborting so partial edits are not published.\n")
        return 1

    with open(tasks_path, "w", encoding="utf-8") as tfh:
        json.dump(tasks, tfh)

    combined_replies = []
    for t in tasks:
        loc_prefix = f"**`{t['path']}`:** " if t.get("path") and len(tasks) > 1 else ""
        combined_replies.append(f"{loc_prefix}{(t.get('reply') or '').strip()}")
    combined_md = "\n\n".join(r for r in combined_replies if r).strip() + "\n"
    with open(os.path.join(temp, "reply.md"), "w", encoding="utf-8") as rfh:
        rfh.write(combined_md)

    status_out = subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
    changed = bool(status_out)
    post_progress.write_gh_output("changed", "true" if changed else "false")
    return 0


def repair_verify_failure(temp: str, log_path: str) -> int:
    """Run one Lead repair pass using the tail of `verify_cmd`'s failure output."""
    if not os.path.isfile(log_path):
        return 1
    raw_log = open(log_path, encoding="utf-8", errors="replace").read()
    clean_log = fetch_pr_context.ANSI_RE.sub("", raw_log)[-4500:]
    diff_now = subprocess.run(["git", "diff"], capture_output=True, text=True, check=False).stdout[-8000:]

    primary_model = (os.environ.get("PI_MODEL") or "opencode-go/deepseek-v4.1-flash").strip()
    fallback_model = (os.environ.get("PI_FALLBACK_MODEL") or "").strip()
    rubric_path = os.path.join(temp, "rubric.md")
    cands = [m for m in (primary_model, fallback_model) if m]
    prompt = "\n".join(
        [
            "The verification suite (`verify_cmd`) failed after applying the requested PR fix(es).",
            "Fix ONLY the lint, formatting, typecheck, or unit-test failure shown below while preserving the requested changes.",
            "Do NOT commit, stage, or push, and do NOT modify CI workflows, lockfiles, Dockerfiles, or migrations.",
            "",
            "## Verification Failure Output",
            "```",
            clean_log,
            "```",
            "",
            "## Current Uncommitted Working Tree Diff",
            "```diff",
            diff_now,
            "```",
        ]
    )
    print("[fix-repair] Verification failed; running 1 Lead repair pass to resolve check errors...")
    out = run_review.invoke_pi(
        prompt_text=prompt,
        model_candidates=cands,
        thinking="low",
        tools_arg="read,grep,find,ls,bash,edit,write",
        rubric_path=rubric_path,
        attempt_timeout_sec=300,
        deadline_monotonic=time.monotonic() + 330,
        label="fix-verify-repair",
    )
    return 0 if out else 1


def build_commit_subject(tasks: list[dict]) -> str:
    summaries = [t.get("summary", "").strip() for t in tasks if (t.get("summary") or "").strip()]
    if len(tasks) == 1 and summaries:
        return f"huggy: {summaries[0][:72]}"
    paths = list(dict.fromkeys(os.path.basename(t["path"]) for t in tasks if t.get("path")))
    if paths:
        scope = ", ".join(paths[:3]) + ("..." if len(paths) > 3 else "")
        return f"huggy: address {len(tasks)} review comments ({scope})"[:78]
    return f"huggy: address {len(tasks)} review comments"


def publish_fix_results(temp: str, changed: bool) -> int:
    repo = os.environ["REPO"]
    pr = int(os.environ["PR"])
    token = os.environ["GH_TOKEN"]
    guarded_raw = (os.environ.get("GUARDED_PATHS") or "").split()
    srv = (os.environ.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    api = make_gh_api(token)
    tasks = load_tasks_or_default(temp)

    # Neutralize untrusted repo git hooks before running git add/commit/push
    hooks_dir = os.path.join(".git", "hooks")
    if os.path.isdir(hooks_dir):
        for entry in os.listdir(hooks_dir):
            fp = os.path.join(hooks_dir, entry)
            if os.path.isfile(fp):
                os.remove(fp)
    subprocess.run(["git", "config", "--local", "--unset-all", "core.fsmonitor"], check=False, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "--local", "--unset-all", "core.hooksPath"], check=False, stderr=subprocess.DEVNULL)

    def _stamp_done(t: dict, text_body: str) -> str:
        tcid = t.get("comment_id")
        if isinstance(tcid, int) and tcid > 0:
            return f"{prepare_command.DONE_MARKER_FMT.format(cid=tcid)}\n{text_body}"
        return text_body

    worktree_dirty = bool(
        subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False).stdout.strip()
    )
    if not changed or not worktree_dirty:
        for t in tasks:
            reply_txt = (t.get("reply") or open(os.path.join(temp, "reply.md"), encoding="utf-8").read()).strip()
            deliver_task_answer(api, repo, pr, t, _stamp_done(t, f"No change made.\n\n{reply_txt}"))
        return 0

    pr_info = api("GET", f"https://api.github.com/repos/{repo}/pulls/{pr}") or {}
    branch = ((pr_info.get("head") or {}).get("ref") or "").strip()
    default_branch = (((pr_info.get("head") or {}).get("repo") or {}).get("default_branch") or "").strip()
    if (
        not branch
        or branch in {"master", "main", "HEAD", default_branch}
        or branch.startswith("refs/")
        or branch.endswith(("/master", "/main"))
    ):
        msg = f"{BLOCKED_IMG}\n\nRefusing to push: that is the default branch. Nothing was pushed."
        for t in tasks:
            deliver_task_answer(api, repo, pr, t, _stamp_done(t, msg))
        return 1

    subprocess.run(["git", "add", "-A"], check=True)
    if guarded_raw:
        touched_res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--", *guarded_raw],
            capture_output=True,
            text=True,
            check=True,
        )
        touched = [ln.strip() for ln in touched_res.stdout.splitlines() if ln.strip()]
        if touched:
            subprocess.run(["git", "reset", "-q", "--", *guarded_raw], check=False)
            msg = (
                f"{BLOCKED_IMG}\n\n"
                f"That fix wanted to change guarded build, dependency or migration files, which I do not touch "
                f"(`{' '.join(touched)}`). Nothing was pushed; do it by hand."
            )
            for t in tasks:
                deliver_task_answer(api, repo, pr, t, _stamp_done(t, msg))
            return 1

    subject = build_commit_subject(tasks)
    subprocess.run(
        [
            "git",
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false",
            "-c", "user.name=Huggy",
            "-c", "user.email=huggy@noreply.github.com",
            "commit", "-q", "-m", subject,
        ],
        check=True,
    )

    git_basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    extra_hdr = f"http.{srv}/.extraheader=AUTHORIZATION: basic {git_basic}"
    remote_url = f"{srv}/{repo}.git"
    push_cmd = ["git", "-c", extra_hdr, "push", "-q", "--no-verify", remote_url, f"HEAD:refs/heads/{branch}"]
    push_res = subprocess.run(push_cmd, check=False)
    if push_res.returncode != 0:
        sys.stderr.write(f"git push to `{branch}` failed (exit {push_res.returncode}); nothing was published.\n")
        rej_msg = (
            f"{BLOCKED_IMG}\n\n"
            f"Push to `{branch}` was rejected (for example, the remote branch advanced or branch protection blocked the push). "
            "Re-run `@huggy` to retry; nothing was pushed."
        )
        for t in tasks:
            deliver_task_answer(api, repo, pr, t, rej_msg)
        return 1

    sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()

    def _reply_one(t: dict) -> None:
        reply_txt = (t.get("reply") or "").strip()
        deliver_task_answer(api, repo, pr, t, _stamp_done(t, f"Pushed `{sha}` to `{branch}`.\n\n{reply_txt}"))

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(tasks))) as pool:
        list(pool.map(_reply_one, tasks))

    # Auto-resolve all addressed inline review threads
    thread_tasks = [t for t in tasks if t.get("is_thread")]
    actual_resolved = 0
    if thread_tasks:
        owner, name = repo.split("/", 1)
        missing_tids = {t["comment_id"] for t in thread_tasks if not t.get("thread_id") and isinstance(t.get("comment_id"), int)}
        cid_to_tid: dict[int, str] = {}
        if missing_tids:
            try:
                q = """
                query($owner: String!, $name: String!, $pr: Int!) {
                  repository(owner: $owner, name: $name) {
                    pullRequest(number: $pr) {
                      reviewThreads(last: 100) {
                        nodes { id isResolved comments(last: 100) { nodes { databaseId } } }
                      }
                    }
                  }
                }
                """
                gql_res = api("POST", "https://api.github.com/graphql", {"query": q, "variables": {"owner": owner, "name": name, "pr": pr}}) or {}
                nodes = ((((gql_res.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads") or {}).get("nodes") or []
                for node in nodes:
                    if not node or node.get("isResolved"):
                        continue
                    for c in ((node.get("comments") or {}).get("nodes") or []):
                        if c and c.get("databaseId") in missing_tids and node.get("id"):
                            cid_to_tid[c["databaseId"]] = node["id"]
            except Exception as exc:
                sys.stderr.write(f"warning: could not query reviewThreads for auto-resolve ({exc})\n")

        resolved_tids: set[str] = set()
        mutation = "mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }"
        for t in thread_tasks:
            tid = t.get("thread_id") or cid_to_tid.get(t.get("comment_id"), "")
            if not tid or tid in resolved_tids:
                continue
            resolved_tids.add(tid)
            try:
                res_mut = api("POST", "https://api.github.com/graphql", {"query": mutation, "variables": {"id": tid}})
                ok_res, res_msg = post_review.is_thread_resolved_response(res_mut)
                if ok_res:
                    actual_resolved += 1
                else:
                    sys.stderr.write(f"warning: resolveReviewThread did not resolve {tid} ({res_msg})\n")
            except Exception as exc:
                sys.stderr.write(f"warning: could not auto-resolve thread {tid} ({exc})\n")

    print(f"Published `{sha}` on {repo}#{pr} ({len(tasks)} comment(s) answered, {actual_resolved}/{len(thread_tasks)} thread(s) resolved).")
    return 0


def report_fix_failure(temp: str, run_url: str) -> int:
    repo = os.environ.get("REPO") or ""
    pr_raw = os.environ.get("PR") or ""
    token = os.environ.get("GH_TOKEN") or ""
    if not (repo and pr_raw.isdigit() and token):
        return 0
    pr = int(pr_raw)
    api = make_gh_api(token)
    tasks = load_tasks_or_default(temp)
    fail_msg = f"Huggy did not finish this one: a step failed, so nothing was pushed past the checks. The log says which: {run_url}"

    for t in tasks:
        pid = str(t.get("progress_comment_id") or "").strip()
        pkind = str(t.get("progress_comment_kind") or "issue").strip().lower()
        if pid.isdigit():
            patch_url = (
                f"https://api.github.com/repos/{repo}/pulls/comments/{pid}"
                if pkind == "pull"
                else f"https://api.github.com/repos/{repo}/issues/comments/{pid}"
            )
            try:
                cur = api("GET", patch_url) or {}
                cur_body = (cur.get("body") or "").strip()
                if cur_body.startswith(fetch_pr_context.CMD_PROGRESS_MARKER):
                    api("PATCH", patch_url, {"body": fail_msg})
                elif cur_body:
                    # Already replaced with a specific refusal (e.g. guarded_paths); preserve it!
                    continue
                else:
                    deliver_task_answer(api, repo, pr, t, fail_msg)
                continue
            except Exception:
                pass
        deliver_task_answer(api, repo, pr, t, fail_msg)
    return 0


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "run").strip().lower()
    temp = os.environ.get("RUNNER_TEMP", "/tmp")
    if mode == "run":
        return run_swarm(temp)
    if mode == "repair":
        log_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(temp, "verify-out.log")
        return repair_verify_failure(temp, log_path)
    if mode == "publish":
        changed = (os.environ.get("FIX_CHANGED") or "").strip().lower() == "true"
        return publish_fix_results(temp, changed=changed)
    if mode == "fail":
        run_url = os.environ.get("RUN") or ""
        return report_fix_failure(temp, run_url=run_url)
    sys.stderr.write(f"Unknown mode: {mode}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
