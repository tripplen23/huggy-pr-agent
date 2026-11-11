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
                    body = (c.get("body") or "").replace("<!-- pi -->", "").strip()
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
        return 0

    sys.stderr.write(f"Unknown mode: {mode}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
