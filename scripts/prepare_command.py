#!/usr/bin/env python3
"""Parse PR slash/@pi commands, verify repo write access, and build `.pi-fix-request.md`.

Why check `/collaborators/{username}/permission` instead of `comment.author_association`?
In GitHub Organizations, members whose organization membership visibility is Private
(GitHub's default) have `comment.author_association` redacted to `CONTRIBUTOR` or `NONE`
inside webhook payloads, causing workflow-level `author_association` gates to skip
legitimate maintainers. Querying `/repos/{owner}/{repo}/collaborators/{username}/permission`
with `GITHUB_TOKEN` checks actual repository write/maintain/admin access regardless of
public/private org membership visibility while still blocking untrusted outsiders.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

WRITE_PERMISSIONS = {"admin", "maintain", "write"}
FALLBACK_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

COMMAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("review", re.compile(r"^(?:@(?:huggy|pi):?\s+)?/review(?:\b|\s|$)(.*)", re.IGNORECASE | re.DOTALL)),
    ("describe", re.compile(r"^(?:@(?:huggy|pi):?\s+)?/describe(?:\b|\s|$)(.*)", re.IGNORECASE | re.DOTALL)),
    ("improve", re.compile(r"^(?:@(?:huggy|pi):?\s+)?/improve(?:\b|\s|$)(.*)", re.IGNORECASE | re.DOTALL)),
    ("ask", re.compile(r"^(?:@(?:huggy|pi):?\s+)?/ask(?:\s+)(.+)", re.IGNORECASE | re.DOTALL)),
    ("fix", re.compile(r"^(?:@(?:huggy|pi)(?::|\b)|/(?:huggy|pi)\b|/fix\b)[\s:]*(.*)", re.IGNORECASE | re.DOTALL)),
)


def parse_command(body: str) -> tuple[str | None, str]:
    """Return `(cmd, summary_line)` or `(None, '')` when `body` is not a Pi command."""
    if not body:
        return None, ""
    non_quoted = [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith(">")
    ]
    if not non_quoted:
        return None, ""
    cleaned = "\n".join(non_quoted).strip()

    for cmd, pattern in COMMAND_PATTERNS:
        m = pattern.match(cleaned)
        if not m:
            continue
        rest = (m.group(1) or "").strip()
        if cmd in {"ask", "fix"} and not rest:
            return None, ""
        first_sentence = next((ln.strip() for ln in rest.splitlines() if ln.strip()), "")
        summary = re.sub(r"\s+", " ", first_sentence)[:72] or f"run {cmd}"
        return cmd, summary

    return None, ""


def has_write_permission(
    repo: str,
    username: str,
    user_type: str = "User",
    author_association: str = "",
    token: str = "",
    opener=urllib.request.urlopen,
) -> tuple[bool, str]:
    """Verify `username` holds write/maintain/admin access on `repo`."""
    if not username:
        return False, "empty username"
    if user_type.lower() == "bot" or username.lower().endswith("[bot]"):
        return False, "bot account"

    url = (
        f"https://api.github.com/repos/{repo}/collaborators/"
        f"{urllib.parse.quote(username, safe='')}/permission"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "pi-pr-agent",
        },
    )
    try:
        with opener(req, timeout=15) as resp:
            raw_resp = json.loads(resp.read().decode("utf-8"))
        data = raw_resp if isinstance(raw_resp, dict) else {}
        perm = str(data.get("permission") or "").lower()
        role = str(data.get("role_name") or "").lower()
        if perm in WRITE_PERMISSIONS or role in WRITE_PERMISSIONS:
            return True, f"permission={perm or role}"
        return False, f"permission={perm or 'none'}"
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, "not a collaborator (404)"
        sys.stderr.write(f"warning: collaborator permission check returned HTTP {exc.code}\n")
        if exc.code in {403, 500, 502, 503, 504} and author_association in FALLBACK_ASSOCIATIONS:
            return True, f"author_association={author_association} (API HTTP {exc.code})"
        return False, f"collaborator check HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        sys.stderr.write(f"warning: collaborator permission check failed ({exc})\n")
        if author_association in FALLBACK_ASSOCIATIONS:
            return True, f"author_association={author_association} ({exc})"
        return False, f"collaborator check failed ({exc})"


def build_fix_request_markdown(
    comment_body: str,
    comment_user: str = "",
    comment_id: int | None = None,
    comment_path: str = "",
    comment_start_line: str = "",
    comment_line: str = "",
    comment_diff_hunk: str = "",
    graphql_data: dict | None = None,
) -> str:
    """Build `.pi-fix-request.md` with exact file/line/diff_hunk and parent thread context."""
    parts: list[str] = ["# Pull Request Command Request", ""]
    if comment_user:
        parts.append(f"- **Author:** @{comment_user}")

    if comment_path:
        if comment_start_line and comment_line and comment_start_line != comment_line:
            loc = f"`{comment_path}` (lines {comment_start_line}–{comment_line})"
        elif comment_line:
            loc = f"`{comment_path}` (line {comment_line})"
        else:
            loc = f"`{comment_path}`"
        parts.append(f"- **Target file:** {loc}")

    parts.append("")

    if comment_diff_hunk.strip():
        hunk_lines = comment_diff_hunk.strip("\r\n").splitlines()
        if len(hunk_lines) > 30:
            hunk_lines = [hunk_lines[0], *hunk_lines[-29:]]
        parts.extend(
            [
                "## Diff Hunk at Commented Line",
                "The final line(s) of this diff hunk show the exact code line where the comment was placed:",
                "",
                "```diff",
                *hunk_lines,
                "```",
                "",
            ]
        )

    if comment_id is not None and graphql_data:
        threads = (
            (((graphql_data.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
            .get("reviewThreads", {})
            .get("nodes")
            or []
        )
        for t in threads:
            if not t:
                continue
            nodes = ((t.get("comments") or {}).get("nodes")) or []
            ids = {c.get("databaseId") for c in nodes if c}
            if comment_id not in ids:
                continue
            earlier = [c for c in nodes if c and c.get("databaseId") != comment_id]
            if earlier:
                parts.append("## Earlier Comments in This Thread")
                parts.append("")
                for c in earlier[-5:]:
                    author = ((c.get("author") or {}).get("login")) or "reviewer"
                    body = re.sub(r"^<!--\s*pi(?:\s+id=[0-9a-fA-F]+)?\s*-->\s*", "", c.get("body") or "").strip()
                    parts.append(f"**@{author}:**")
                    parts.append(body)
                    parts.append("")
            break

    parts.extend(
        [
            "## Requested Change / Instruction",
            "",
            comment_body.strip(),
            "",
        ]
    )
    return "\n".join(parts)


DONE_MARKER_FMT = "<!-- huggy-done cid={cid} -->"


def is_bot_comment_node(node: dict) -> bool:
    author = (node or {}).get("author") or {}
    login = (author.get("login") or "").lower()
    typename = (author.get("__typename") or "").lower()
    return bool(typename == "bot" or login.endswith("[bot]") or login in {"github-actions", "huggy-bot"})


def is_completed_fix_reply(node: dict, target_cid: int | None = None, in_thread: bool = True) -> bool:
    """True only for terminal bot completion replies (never in-flight progress banners or failure notices)."""
    if not is_bot_comment_node(node):
        return False
    body = ((node or {}).get("body") or "").strip()
    if body.startswith("<!-- huggy-done "):
        return bool(target_cid is not None and DONE_MARKER_FMT.format(cid=target_cid) in body)
    stripped = re.sub(r"^<!--[^>]+-->\s*", "", body).strip()
    return stripped.startswith(("Pushed `", "No change made."))


def collect_pending_fix_tasks(
    graphql_data: dict | None,
    trigger_comment_id: int | None,
    trigger_body: str = "",
    trigger_user: str = "",
    trigger_is_thread: bool = False,
    trigger_path: str = "",
    trigger_start_line: str = "",
    trigger_line: str = "",
    trigger_diff_hunk: str = "",
    permission_checker=None,
) -> tuple[list[dict], bool]:
    """Coalesce all unanswered `@huggy` / `@pi` / `/fix` requests across open PR review threads and comments.

    Returns `(tasks, already_handled)`. If `trigger_comment_id` was already answered or resolved by an
    earlier coalesced run and 0 unanswered fix commands remain, returns `([], True)` so the queued
    duplicate workflow run exits immediately in 0 tokens.
    """
    pr_node = ((((graphql_data or {}).get("data") or {}).get("repository") or {}).get("pullRequest")) or {}
    threads = ((pr_node.get("reviewThreads") or {}).get("nodes")) or []
    issue_comments = ((pr_node.get("comments") or {}).get("nodes")) or []

    perm_cache: dict[str, bool] = {}
    if trigger_user:
        perm_cache[trigger_user.lower()] = True

    def _allowed(login: str, user_type: str, assoc: str) -> bool:
        if not login:
            return False
        key = login.lower()
        if key in perm_cache:
            return perm_cache[key]
        if permission_checker is None:
            perm_cache[key] = True
            return True
        ok, _ = permission_checker(login, user_type, assoc)
        perm_cache[key] = bool(ok)
        return bool(ok)

    tasks_by_cid: dict[int, dict] = {}
    trigger_seen_in_gql = False
    trigger_already_answered = False

    # 1. Scan all review threads on the PR
    for t in threads:
        if not t:
            continue
        c_nodes = [c for c in (((t.get("comments") or {}).get("nodes")) or []) if c]
        if not c_nodes:
            continue
        c_ids = [c.get("databaseId") for c in c_nodes]
        if trigger_comment_id is not None and trigger_comment_id in c_ids:
            trigger_seen_in_gql = True
            trig_idx = c_ids.index(trigger_comment_id)
            if any(
                is_completed_fix_reply(later, target_cid=trigger_comment_id, in_thread=True)
                for later in c_nodes[trig_idx + 1 :]
            ):
                trigger_already_answered = True

        if t.get("isResolved") and trigger_comment_id not in c_ids:
            continue

        # Find the latest human `fix` command in this open thread
        last_fix_idx = -1
        last_fix_node: dict | None = None
        last_fix_summary = ""
        for idx, c in enumerate(c_nodes):
            if is_bot_comment_node(c):
                continue
            cmd, summary = parse_command(c.get("body") or "")
            if cmd == "fix":
                last_fix_idx = idx
                last_fix_node = c
                last_fix_summary = summary

        if last_fix_node is None:
            continue
        cid = last_fix_node.get("databaseId")
        if not isinstance(cid, int):
            continue
        # Only skip if a terminal completion reply exists after `last_fix_idx` (in-flight/failed stays retryable!)
        if any(is_completed_fix_reply(later, target_cid=cid, in_thread=True) for later in c_nodes[last_fix_idx + 1 :]):
            continue
        author_obj = last_fix_node.get("author") or {}
        c_login = author_obj.get("login") or ""
        c_type = author_obj.get("__typename") or "User"
        c_assoc = last_fix_node.get("authorAssociation") or ""
        if not _allowed(c_login, c_type, c_assoc):
            continue

        t_path = t.get("path") or ""
        end_l = str(t.get("line") or t.get("originalLine") or "")
        start_l = str(t.get("startLine") or t.get("originalStartLine") or "")
        hunk = last_fix_node.get("diffHunk") or (c_nodes[0].get("diffHunk") or "")
        if cid == trigger_comment_id:
            t_path = t_path or trigger_path
            start_l = start_l or trigger_start_line
            end_l = end_l or trigger_line
            hunk = hunk or trigger_diff_hunk

        req_md = build_fix_request_markdown(
            comment_body=last_fix_node.get("body") or "",
            comment_user=c_login,
            comment_id=cid,
            comment_path=t_path,
            comment_start_line=start_l,
            comment_line=end_l,
            comment_diff_hunk=hunk,
            graphql_data=graphql_data,
        )
        tasks_by_cid[cid] = {
            "comment_id": cid,
            "is_thread": True,
            "thread_id": t.get("id") or "",
            "user": c_login,
            "path": t_path,
            "start_line": start_l,
            "line": end_l,
            "summary": last_fix_summary,
            "body": (last_fix_node.get("body") or "").strip(),
            "request_markdown": req_md,
            "progress_comment_id": None,
            "progress_comment_kind": None,
        }

    # 2. Scan top-level PR Conversation comments using per-comment `<!-- huggy-done cid=... -->` completion markers
    ic_nodes = [ic for ic in issue_comments if ic]
    if trigger_comment_id is not None:
        ic_ids = [ic.get("databaseId") for ic in ic_nodes]
        if trigger_comment_id in ic_ids:
            trigger_seen_in_gql = True
            trig_idx = ic_ids.index(trigger_comment_id)
            if any(
                is_completed_fix_reply(later, target_cid=trigger_comment_id, in_thread=False)
                for later in ic_nodes[trig_idx + 1 :]
            ):
                trigger_already_answered = True

    for idx, ic in enumerate(ic_nodes):
        if is_bot_comment_node(ic):
            continue
        cmd, summary = parse_command(ic.get("body") or "")
        if cmd != "fix":
            continue
        cid = ic.get("databaseId")
        if not isinstance(cid, int) or cid in tasks_by_cid:
            continue
        if any(is_completed_fix_reply(later, target_cid=cid, in_thread=False) for later in ic_nodes[idx + 1 :]):
            continue
        author_obj = ic.get("author") or {}
        c_login = author_obj.get("login") or ""
        c_type = author_obj.get("__typename") or "User"
        c_assoc = ic.get("authorAssociation") or ""
        if not _allowed(c_login, c_type, c_assoc):
            continue
        req_md = build_fix_request_markdown(
            comment_body=ic.get("body") or "",
            comment_user=c_login,
            comment_id=cid,
            graphql_data=graphql_data,
        )
        tasks_by_cid[cid] = {
            "comment_id": cid,
            "is_thread": False,
            "thread_id": "",
            "user": c_login,
            "path": "",
            "start_line": "",
            "line": "",
            "summary": summary,
            "body": (ic.get("body") or "").strip(),
            "request_markdown": req_md,
            "progress_comment_id": None,
            "progress_comment_kind": None,
        }

    # 3. Fallback when GraphQL lagged or omitted trigger_comment_id (and it wasn't marked already answered)
    if trigger_comment_id is not None and trigger_comment_id not in tasks_by_cid:
        if trigger_seen_in_gql and trigger_already_answered:
            if not tasks_by_cid:
                return [], True
        elif trigger_body.strip():
            cmd, summary = parse_command(trigger_body)
            if cmd == "fix":
                req_md = build_fix_request_markdown(
                    comment_body=trigger_body,
                    comment_user=trigger_user,
                    comment_id=trigger_comment_id,
                    comment_path=trigger_path,
                    comment_start_line=trigger_start_line,
                    comment_line=trigger_line,
                    comment_diff_hunk=trigger_diff_hunk,
                    graphql_data=graphql_data,
                )
                tasks_by_cid[trigger_comment_id] = {
                    "comment_id": trigger_comment_id,
                    "is_thread": trigger_is_thread,
                    "thread_id": "",
                    "user": trigger_user,
                    "path": trigger_path,
                    "start_line": trigger_start_line,
                    "line": trigger_line,
                    "summary": summary,
                    "body": trigger_body.strip(),
                    "request_markdown": req_md,
                    "progress_comment_id": None,
                    "progress_comment_kind": None,
                }

    # Order with trigger_comment_id first (if still pending), followed by remaining tasks in chronological order
    ordered: list[dict] = []
    if trigger_comment_id is not None and trigger_comment_id in tasks_by_cid:
        ordered.append(tasks_by_cid.pop(trigger_comment_id))
    for _, task in sorted(tasks_by_cid.items(), key=lambda kv: kv[0]):
        ordered.append(task)
    return ordered, False


def write_output(key: str, value: str) -> None:
    out_file = os.environ.get("GITHUB_OUTPUT")
    if out_file:
        with open(out_file, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "authorize"
    temp = os.environ.get("RUNNER_TEMP", "/tmp")
    body = os.environ.get("COMMENT_BODY") or ""
    user = os.environ.get("COMMENT_USER") or ""
    user_type = os.environ.get("COMMENT_USER_TYPE") or "User"
    assoc = os.environ.get("COMMENT_ASSOC") or ""
    repo = os.environ.get("REPO") or ""
    token = os.environ.get("GH_TOKEN") or ""

    if mode == "authorize":
        cmd, summary = parse_command(body)
        if not cmd:
            print("Not a Pi command; doing nothing.")
            write_output("go", "false")
            return 0

        allowed, reason = has_write_permission(
            repo=repo,
            username=user,
            user_type=user_type,
            author_association=assoc,
            token=token,
        )
        if not allowed:
            print(f"Ignoring command from @{user} ({reason}); write access is required.")
            write_output("go", "false")
            return 0

        print(f"Authorized `{cmd}` command from @{user} ({reason}).")
        with open(os.path.join(temp, "request.txt"), "w", encoding="utf-8") as fh:
            fh.write(summary + "\n")
        write_output("go", "true")
        write_output("cmd", cmd)
        if cmd == "review" and re.match(r"^deep\b", summary, re.IGNORECASE):
            write_output("review_depth", "deep")
        return 0

    if mode == "build-request":
        out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(temp, "fix-request.md")
        cid_raw = os.environ.get("COMMENT_ID") or ""
        cid = int(cid_raw) if cid_raw.isdigit() else None
        meta_path = os.path.join(temp, "pr-meta.json")
        gql_data = {}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as fh:
                    gql_data = (json.load(fh) or {}).get("graphql") or {}
            except (OSError, ValueError):
                gql_data = {}
        md = build_fix_request_markdown(
            comment_body=body,
            comment_user=user,
            comment_id=cid,
            comment_path=os.environ.get("COMMENT_PATH") or "",
            comment_start_line=os.environ.get("COMMENT_START_LINE") or "",
            comment_line=os.environ.get("COMMENT_LINE") or "",
            comment_diff_hunk=os.environ.get("COMMENT_DIFF_HUNK") or "",
            graphql_data=gql_data,
        )
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(md)

        cmd_type, _ = parse_command(body)
        if cmd_type == "fix":
            tasks, already_done = collect_pending_fix_tasks(
                graphql_data=gql_data,
                trigger_comment_id=cid,
                trigger_body=body,
                trigger_user=user,
                trigger_is_thread=bool((os.environ.get("THREAD") or "").strip() or (os.environ.get("COMMENT_PATH") or "").strip()),
                trigger_path=os.environ.get("COMMENT_PATH") or "",
                trigger_start_line=os.environ.get("COMMENT_START_LINE") or "",
                trigger_line=os.environ.get("COMMENT_LINE") or "",
                trigger_diff_hunk=os.environ.get("COMMENT_DIFF_HUNK") or "",
                permission_checker=(
                    lambda login, utype, uassoc: has_write_permission(
                        repo=repo, username=login, user_type=utype, author_association=uassoc, token=token
                    )
                )
                if (repo and token)
                else None,
            )
            tasks_path = os.path.join(temp, "fix-tasks.json")
            with open(tasks_path, "w", encoding="utf-8") as tfh:
                json.dump(tasks, tfh)
            if already_done and not tasks:
                print(f"Comment #{cid} was already handled by an earlier coalesced batch; skipping duplicate run.")
                write_output("already_handled", "true")
            elif len(tasks) > 1:
                print(f"Coalesced {len(tasks)} pending @huggy/@pi fix comments into 1 parallel batch!")
        return 0

    sys.stderr.write(f"Unknown mode: {mode}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
