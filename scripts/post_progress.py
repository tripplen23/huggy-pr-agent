#!/usr/bin/env python3
"""Post or update animated pixel-art progress indicators across PR review, Judge, and @huggy commands."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import fetch_pr_context  # noqa: E402
import post_review  # noqa: E402
import run_review  # noqa: E402

ASSET_BASE = "https://raw.githubusercontent.com/tripplen23/huggy-pr-agent/v1/assets"
DEFAULT_SVG_URL = f"{ASSET_BASE}/huggy-reviewing.svg"
JUDGING_SVG_URL = f"{ASSET_BASE}/huggy-judging.svg"
FIXING_SVG_URL = f"{ASSET_BASE}/huggy-fixing.svg"
TESTING_SVG_URL = f"{ASSET_BASE}/huggy-testing.svg"
THINKING_SVG_URL = f"{ASSET_BASE}/huggy-thinking.svg"
BLOCKED_SVG_URL = f"{ASSET_BASE}/huggy-blocked.svg"
CMD_PROGRESS_MARKER = fetch_pr_context.CMD_PROGRESS_MARKER


def build_progress_markdown(
    files: list[dict],
    num_bundles: int,
    last_sha: str | None = None,
    svg_url: str = DEFAULT_SVG_URL,
    previous_body: str = "",
) -> str:
    marker = f"<!-- pi-review sha={last_sha} -->" if last_sha else post_review.MARKER
    total_lines = sum(f["changed_lines"] for f in files)
    bundle_note = f" across **{num_bundles} parallel bundles**" if num_bundles > 1 else ""
    scope_items = [
        f"- `{f['path']}` (+{f['additions']} / -{f['deletions']})" for f in files[:30]
    ]
    if len(files) > 30:
        scope_items.append(f"- *(...and {len(files) - 30} more files)*")

    lines = [
        marker,
        "> [!NOTE]",
        f'> <img src="{svg_url}" alt="Huggy is reviewing this PR" width="232" />',
        ">",
        f"> Currently reviewing **{len(files)} file(s)** ({total_lines} changed lines){bundle_note}. This animation disappears automatically once Huggy finishes.",
        "",
        "<details>",
        "<summary>🧭 <b>Review scope</b></summary>",
        "",
        *scope_items,
        "",
        "</details>",
    ]
    prev_clean = fetch_pr_context.clean_body(previous_body, limit=25000)
    if prev_clean and "Currently reviewing " not in prev_clean and "precision Judge pass" not in prev_clean:
        lines.extend(
            [
                "",
                "<details>",
                "<summary>📜 <b>Previous review</b></summary>",
                "",
                prev_clean,
                "",
                "</details>",
            ]
        )
    return "\n".join(lines)


def build_judging_markdown(
    num_candidates: int,
    last_sha: str | None = None,
    svg_url: str = JUDGING_SVG_URL,
    existing_body: str = "",
) -> str:
    marker = f"<!-- pi-review sha={last_sha} -->" if last_sha else post_review.MARKER
    m_existing = re.match(r"^<!--\s*pi-review(?:\s+sha=([0-9a-fA-F]+))?\s*-->", (existing_body or "").strip())
    if m_existing and not last_sha and m_existing.group(1):
        marker = f"<!-- pi-review sha={m_existing.group(1)} -->"

    lines = [
        marker,
        "> [!NOTE]",
        f'> <img src="{svg_url}" alt="Huggy Judge is verifying candidate findings" width="208" />',
        ">",
        f"> Verifying **{num_candidates} candidate finding(s)** against ground-truth code in the precision Judge pass. This animation disappears automatically once Huggy finishes.",
    ]
    idx = existing_body.find("<details>")
    if idx != -1:
        lines.extend(["", existing_body[idx:].strip()])
    return "\n".join(lines)


def build_command_progress_markdown(cmd: str, stage: str = "start") -> str:
    norm_cmd = (cmd or "fix").strip().lower()
    if stage == "testing":
        svg = TESTING_SVG_URL
        alt = "Huggy is running verification tests"
        caption = "Patch applied — running verification test gate (`verify_cmd`). Replaced automatically once checks finish."
    elif norm_cmd == "fix":
        svg = FIXING_SVG_URL
        alt = "Huggy is applying a fix"
        caption = "Applying your requested changes. Replaced automatically once Huggy finishes."
    elif norm_cmd == "ask":
        svg = THINKING_SVG_URL
        alt = "Huggy is investigating the codebase"
        caption = "Tracing callers across the codebase to answer your question..."
    else:
        svg = THINKING_SVG_URL
        alt = "Huggy is analyzing the pull request"
        caption = f"Running `/{norm_cmd}` on this pull request..."

    return "\n".join(
        [
            CMD_PROGRESS_MARKER,
            "> [!NOTE]",
            f'> <img src="{svg}" alt="{alt}" width="208" />',
            ">",
            f"> {caption}",
        ]
    )


def write_gh_output(key: str, val: str) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={val}\n")


def make_api(token: str):
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
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None

    return api


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "review-start").strip().lower()
    repo = os.environ.get("REPO") or ""
    pr_raw = os.environ.get("PR") or ""
    token = os.environ.get("GH_TOKEN") or ""
    temp = os.environ.get("RUNNER_TEMP", "/tmp")

    if not (repo and pr_raw.isdigit() and token):
        return 0
    pr = int(pr_raw)
    api = make_api(token)

    if mode == "judge":
        stage_path = os.path.join(temp, "review-stage.json")
        if not os.path.isfile(stage_path):
            return 0
        try:
            stage = json.loads(open(stage_path, encoding="utf-8").read())
            if not stage.get("judge_pending"):
                return 0
            num_cands = int(stage.get("num_candidates") or 0)
            if num_cands <= 0:
                return 0
            issues_url = f"https://api.github.com/repos/{repo}/issues"
            comments = api("GET", f"{issues_url}/{pr}/comments?per_page=100") or []
            existing = next(
                (c for c in comments if (c.get("body") or "").startswith(post_review.MARKER_PREFIX)),
                None,
            )
            if existing:
                body = build_judging_markdown(num_cands, existing_body=existing.get("body") or "")
                api("PATCH", f"{issues_url}/comments/{existing['id']}", {"body": body})
                print(f"Updated review banner to Judge phase ({num_cands} candidates) on {repo}#{pr}.")
        except Exception as exc:
            sys.stderr.write(f"warning: could not update Judge progress banner ({exc}); continuing\n")
        return 0

    if mode == "command-start":
        cmd = (os.environ.get("CMD") or "fix").strip().lower()
        if cmd == "review":
            return 0
        tasks_path = os.path.join(temp, "fix-tasks.json")
        if cmd == "fix" and os.path.isfile(tasks_path):
            try:
                with open(tasks_path, encoding="utf-8") as tfh_r:
                    tasks = json.load(tfh_r) or []
            except Exception:
                tasks = []
            if tasks:
                body = build_command_progress_markdown("fix", stage="start")

                def _post_one(task: dict) -> None:
                    tcid = task.get("comment_id")
                    if task.get("is_thread") and isinstance(tcid, int):
                        t_url = f"https://api.github.com/repos/{repo}/pulls/{pr}/comments/{tcid}/replies"
                        t_kind = "pull"
                    else:
                        t_url = f"https://api.github.com/repos/{repo}/issues/{pr}/comments"
                        t_kind = "issue"
                    try:
                        created = api("POST", t_url, {"body": body}) or {}
                        pcid = created.get("id")
                        if pcid:
                            task["progress_comment_id"] = str(pcid)
                            task["progress_comment_kind"] = t_kind
                    except Exception as exc:
                        sys.stderr.write(f"warning: could not post progress banner for comment {tcid} ({exc})\n")

                with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(tasks))) as pool:
                    list(pool.map(_post_one, tasks))
                with open(tasks_path, "w", encoding="utf-8") as tfh:
                    json.dump(tasks, tfh)
                first_prog = next((t for t in tasks if t.get("progress_comment_id")), None)
                if first_prog:
                    write_gh_output("progress_comment_id", str(first_prog["progress_comment_id"]))
                    write_gh_output("progress_comment_kind", str(first_prog["progress_comment_kind"]))
                print(f"Posted {sum(1 for t in tasks if t.get('progress_comment_id'))}/{len(tasks)} in-progress `fix` banner(s) on {repo}#{pr}.")
                return 0

        comment_id = (os.environ.get("COMMENT_ID") or "").strip()
        is_thread = bool((os.environ.get("THREAD") or "").strip()) and comment_id.isdigit()
        body = build_command_progress_markdown(cmd, stage="start")
        try:
            if is_thread:
                url = f"https://api.github.com/repos/{repo}/pulls/{pr}/comments/{comment_id}/replies"
                kind = "pull"
            else:
                url = f"https://api.github.com/repos/{repo}/issues/{pr}/comments"
                kind = "issue"
            created = api("POST", url, {"body": body}) or {}
            cid = created.get("id")
            if cid:
                write_gh_output("progress_comment_id", str(cid))
                write_gh_output("progress_comment_kind", kind)
                print(f"Posted in-progress `{cmd}` animation ({kind} comment {cid}) on {repo}#{pr}.")
        except Exception as exc:
            sys.stderr.write(f"warning: could not post command progress banner ({exc}); continuing\n")
        return 0

    if mode == "command-testing":
        body = build_command_progress_markdown("fix", stage="testing")
        tasks_path = os.path.join(temp, "fix-tasks.json")
        targets: list[tuple[str, str]] = []
        if os.path.isfile(tasks_path):
            try:
                with open(tasks_path, encoding="utf-8") as tfh_t:
                    loaded_t = json.load(tfh_t) or []
                for t in loaded_t:
                    pid = str(t.get("progress_comment_id") or "").strip()
                    pkind = str(t.get("progress_comment_kind") or "issue").strip().lower()
                    if pid.isdigit():
                        targets.append((pid, pkind))
            except Exception:
                targets = []
        if not targets:
            prog_id = (os.environ.get("PROGRESS_COMMENT_ID") or "").strip()
            prog_kind = (os.environ.get("PROGRESS_COMMENT_KIND") or "issue").strip().lower()
            if prog_id.isdigit():
                targets.append((prog_id, prog_kind))
        if not targets:
            return 0

        def _patch_testing(pair: tuple[str, str]) -> None:
            pid, pkind = pair
            url = (
                f"https://api.github.com/repos/{repo}/pulls/comments/{pid}"
                if pkind == "pull"
                else f"https://api.github.com/repos/{repo}/issues/comments/{pid}"
            )
            try:
                api("PATCH", url, {"body": body})
            except Exception as exc:
                sys.stderr.write(f"warning: could not update testing banner {pid} ({exc}); continuing\n")

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
            list(pool.map(_patch_testing, targets))
        print(f"Updated {len(targets)} fix animation(s) to test-gate phase on {repo}#{pr}.")
        return 0

    # Default: `review-start`
    svg_url = os.environ.get("PI_PROGRESS_SVG_URL") or DEFAULT_SVG_URL
    diff_path = os.path.join(temp, "diff.patch")
    meta_path = os.path.join(temp, "pr-meta.json")
    diff_text = open(diff_path, encoding="utf-8", errors="replace").read() if os.path.isfile(diff_path) else ""
    files = fetch_pr_context.parse_annotated_files(diff_text)
    if not files:
        return 0

    meta = json.loads(open(meta_path, encoding="utf-8").read()) if os.path.isfile(meta_path) else {}
    last_sha = meta.get("last_reviewed_sha")
    bundles = run_review.bundle_review_files(files)

    try:
        issues_url = f"https://api.github.com/repos/{repo}/issues"
        comments = api("GET", f"{issues_url}/{pr}/comments?per_page=100") or []
        existing = next(
            (c for c in comments if (c.get("body") or "").startswith(post_review.MARKER_PREFIX)),
            None,
        )
        prev_body = (existing.get("body") or "") if existing else ""
        body = build_progress_markdown(files, len(bundles), last_sha=last_sha, svg_url=svg_url, previous_body=prev_body)
        if existing:
            api("PATCH", f"{issues_url}/comments/{existing['id']}", {"body": body})
        else:
            api("POST", f"{issues_url}/{pr}/comments", {"body": body})
        print(f"Posted in-progress review banner on {repo}#{pr}.")
    except Exception as exc:
        sys.stderr.write(f"warning: could not post progress banner ({exc}); continuing\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
