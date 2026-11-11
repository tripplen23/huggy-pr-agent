#!/usr/bin/env python3
"""Parse Pi's markdown review, sync stale PR descriptions, and publish inline diff comments."""

from __future__ import annotations

import codecs
import json
import os
import re
import sys
import urllib.error
import urllib.request

MARKER = "<!-- pi-review -->"
MARKER_PREFIX = "<!-- pi-review"
HUGGY_ICON_URL = "https://raw.githubusercontent.com/tripplen23/huggy-pr-agent/v1/assets/huggy-icon.svg"
REVIEW_HEADING = (
    f'## <img src="{HUGGY_ICON_URL}" width="24" height="24" align="absmiddle" alt="Huggy" /> Review by Huggy'
)
DESC_START = "<!-- pi-description-start -->"
DESC_END = "<!-- pi-description-end -->"
BADGES = {
    "High": "![High](https://img.shields.io/badge/High-dc2626)",
    "Medium": "![Medium](https://img.shields.io/badge/Medium-facc15)",
    "Low": "![Low](https://img.shields.io/badge/Low-2563eb)",
}
HEADER_RE = re.compile(
    r"^(?:###\s+|(?:\s*(?:[-*]|\d+\.)\s+))"
    r"\*{0,2}`([^`:\n]+):(\d+)(?:-(\d+))?`\*{0,2}"
    r"(?:\s*(?:[·—–-]|\s)\s*(.*))?$"
)
PR_DESC_RE = re.compile(
    r"^\s*<pr_description>\s*\r?\n(.*?)\r?\n\s*</pr_description>\s*$",
    re.M | re.S,
)


def extract_pr_description(text: str) -> tuple[str, str | None]:
    """Strip standalone `<pr_description>...</pr_description>` block and return (cleaned_text, new_desc)."""
    m = PR_DESC_RE.search(text)
    if not m:
        return text, None
    new_desc = m.group(1).strip()
    cleaned = (text[: m.start()] + text[m.end() :]).strip()
    return cleaned, (new_desc or None)


def merge_pr_description(existing_body: str, generated_desc: str) -> str:
    """Insert or update the managed `<!-- pi-description-start -->...<!-- pi-description-end -->` block."""
    pattern = re.compile(re.escape(DESC_START) + r".*?" + re.escape(DESC_END), re.S)
    cleaned_gen = re.sub(r"<!--\s*pi-description-(?:start|end)\s*-->", "", generated_desc or "").strip()
    block = f"{DESC_START}\n{cleaned_gen}\n{DESC_END}"
    existing = (existing_body or "").strip()
    if not existing:
        return block
    if pattern.search(existing):
        return pattern.sub(lambda _: block, existing, count=1)
    return block


def parse_diff_hunks(diff_text: str) -> dict[tuple[str, int], int]:
    """Return {(path, new_line): hunk_id} from `git -c core.quotePath=false diff`."""
    hunk_of: dict[tuple[str, int], int] = {}
    cur_path = None
    hunk_id = 0
    new_line = 0
    for raw in diff_text.splitlines():
        line = raw.rstrip("\r\n")
        if line.startswith("diff --git "):
            cur_path = None
        elif line.startswith("--- "):
            continue
        elif line.startswith("+++ "):
            p = line[4:].split("\t", 1)[0]
            if p.startswith('"') and p.endswith('"'):
                p = codecs.escape_decode(p[1:-1].encode("utf-8"))[0].decode("utf-8", errors="replace")
            cur_path = p[2:] if p.startswith("b/") else None
        elif line.startswith("@@ "):
            m = re.search(r"\+(\d+)", line)
            if m:
                new_line = int(m.group(1))
                hunk_id += 1
        elif cur_path is not None and line[:1] in ("+", " "):
            hunk_of[(cur_path, new_line)] = hunk_id
            new_line += 1
    return hunk_of


SUGGESTION_BLOCK_RE = re.compile(r"^(\s*```)suggestion[ \t]*\r?\n(.*?)\r?\n(\s*```)[ \t]*$", re.M | re.S)
PLACEHOLDER_LINE_RE = re.compile(r"^\s*(?://|#|--|/\*)\s*\.\.\.(?:\s*(?:rest|unchanged|existing).*)?(?:\*/)?\s*$", re.I)


def is_safe_rel_path(worktree_root: str, rel_path: str) -> bool:
    """Return True iff `rel_path` stays strictly inside `worktree_root` (no abs paths, `..`, `.git`, or escaping symlinks)."""
    if not rel_path or os.path.isabs(rel_path):
        return False
    norm_parts = rel_path.replace("\\", "/").split("/")
    if any(part in ("", ".", "..", ".git") for part in norm_parts):
        return False
    root_real = os.path.realpath(worktree_root)
    candidate = os.path.realpath(os.path.join(root_real, *norm_parts))
    return candidate.startswith(root_real + os.sep)


def safe_worktree_file(worktree_root: str, rel_path: str) -> str | None:
    """Resolve `rel_path` to an existing regular file strictly inside `worktree_root`."""
    if not is_safe_rel_path(worktree_root, rel_path):
        return None
    root_real = os.path.realpath(worktree_root)
    norm_parts = rel_path.replace("\\", "/").split("/")
    candidate = os.path.realpath(os.path.join(root_real, *norm_parts))
    return candidate if os.path.isfile(candidate) else None


def disarm_suggestion(markdown: str) -> str:
    """Turn ```suggestion into a plain code fence when a range cannot be anchored."""
    return re.sub(r"^(\s*```)suggestion\b", r"\1", markdown, flags=re.M)


def validate_suggestion_against_file(
    markdown: str,
    file_lines: list[str],
    start: int,
    end: int,
) -> str:
    """Validate every `suggestion` block in `markdown`, disarming no-ops, placeholders, or truncated blocks."""
    if not SUGGESTION_BLOCK_RE.search(markdown):
        return markdown
    if not file_lines or start < 1 or end < start or end > len(file_lines):
        return disarm_suggestion(markdown)
    orig_slice = file_lines[start - 1 : end]
    orig_set = {ln.strip() for ln in orig_slice}
    orig_text = "\n".join(orig_slice)
    orig_delta = orig_text.count("{") - orig_text.count("}")

    def check_block(m: re.Match) -> str:
        open_fence, sugg_body, close_fence = m.group(1), m.group(2), m.group(3)
        sugg_lines = sugg_body.splitlines()
        disarmed = f"{open_fence}\n{sugg_body}\n{close_fence}"

        if sugg_lines == orig_slice:
            return disarmed
        if any(PLACEHOLDER_LINE_RE.match(ln) and ln.strip() not in orig_set for ln in sugg_lines):
            return disarmed
        if end - start >= 1:
            sugg_delta = sugg_body.count("{") - sugg_body.count("}")
            if orig_delta == 0 and sugg_delta != 0:
                return disarmed
        return m.group(0)

    return SUGGESTION_BLOCK_RE.sub(check_block, markdown)


def nearest_hunk_line(
    path: str,
    target_line: int,
    hunk_of: dict[tuple[str, int], int],
    max_distance: int = 5,
) -> tuple[int | None, int | None]:
    """If (path, target_line) is within max_distance of a hunk line in path, return (line, hunk_id)."""
    if (path, target_line) in hunk_of:
        return target_line, hunk_of[(path, target_line)]
    best_line = None
    best_dist = max_distance + 1
    for (p, ln), hid in hunk_of.items():
        if p != path:
            continue
        dist = abs(ln - target_line)
        if dist < best_dist:
            best_dist = dist
            best_line = ln
    if best_line is not None:
        return best_line, hunk_of[(path, best_line)]
    return None, None


def parse_review_markdown(
    text: str,
    hunk_of: dict[tuple[str, int], int],
    worktree_root: str = ".",
) -> tuple[str, list[str], list[dict]]:
    """Return (verdict_line, intro_lines, findings). Raises ValueError if Verdict is missing."""
    text = text.strip()
    verdict_match = re.search(r"^\*\*Verdict:\*\*\s*(.+)$", text, re.M)
    if not verdict_match or not verdict_match.group(1).strip():
        raise ValueError("review.md has no **Verdict:** line; refusing to publish an empty review")
    verdict_line = f"**Verdict:** {verdict_match.group(1).strip()}"

    findings: list[dict] = []
    intro_lines: list[str] = []
    after_verdict = False
    cur = None
    in_fence = False

    for ln in text.splitlines():
        if not after_verdict:
            if re.match(r"^\*\*Verdict:\*\*", ln):
                after_verdict = True
            continue
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else HEADER_RE.match(ln)
        if m:
            if cur:
                findings.append(cur)
            path, start_s, end_s, rest = (
                m.group(1),
                m.group(2),
                m.group(3),
                (m.group(4) or "").strip(),
            )
            start_l = int(start_s)
            end_l = int(end_s) if end_s else start_l
            if end_l < start_l:
                start_l, end_l = end_l, start_l
            cur = {"path": path, "start": start_l, "end": end_l, "rest": rest, "body_lines": []}
        else:
            if cur is not None:
                cur["body_lines"].append(ln)
            elif ln.strip():
                intro_lines.append(ln)
    if cur:
        findings.append(cur)

    for f in findings:
        body_text = "\n".join(f["body_lines"]).strip()
        rest = f["rest"]
        loc = f"`{f['path']}:{f['start']}" + (f"-{f['end']}`" if f["end"] != f["start"] else "`")
        snippet = ""
        if not is_safe_rel_path(worktree_root, f["path"]):
            body_text = disarm_suggestion(body_text)
        else:
            full_path = safe_worktree_file(worktree_root, f["path"])
            if full_path:
                file_lines = open(full_path, encoding="utf-8", errors="replace").read().splitlines()
                cited = file_lines[max(0, f["start"] - 1) : min(len(file_lines), f["end"])]
                if cited:
                    snippet = "\n```\n" + "\n".join(cited[:6]) + "\n```"
                body_text = validate_suggestion_against_file(body_text, file_lines, f["start"], f["end"])

        summary_body = disarm_suggestion(body_text)
        end_hunk = hunk_of.get((f["path"], f["end"]))
        relocated = False
        if end_hunk is None:
            snapped_line, snapped_hunk = nearest_hunk_line(f["path"], f["end"], hunk_of)
            if snapped_line is not None:
                f["end"] = snapped_line
                f["start"] = snapped_line
                end_hunk = snapped_hunk
                relocated = True
        same_hunk = (not relocated) and f["start"] < f["end"] and hunk_of.get((f["path"], f["start"])) == end_hunk
        f["end_hunk"] = end_hunk
        f["same_hunk"] = same_hunk
        f["relocated"] = relocated
        f["eff_start"] = f["start"] if (not relocated and (f["start"] == f["end"] or same_hunk)) else f["end"]

        if body_text and rest:
            parts = [p.strip() for p in re.split(r"\s*·\s*", rest, maxsplit=1) if p.strip()]
            if len(parts) == 2 and parts[0].capitalize() in BADGES:
                header = f"{BADGES[parts[0].capitalize()]} **{parts[1]}**"
            else:
                header = f"**{rest}**"
            f["comment"] = f"<!-- pi -->\n{header}\n\n{body_text}"
            f["summary"] = f"- {loc} ({rest}) — {summary_body}{snippet}"
        else:
            combined = (rest + ("\n" + body_text if body_text else "")).strip()
            summary_combined = (rest + ("\n" + summary_body if summary_body else "")).strip()
            f["comment"] = f"<!-- pi -->\n{combined}"
            f["summary"] = f"- {loc} — {summary_combined}{snippet}"

    return verdict_line, intro_lines, findings


def main() -> int:
    repo = os.environ["REPO"]
    owner, name = repo.split("/", 1)
    pr = int(os.environ["PR"])
    token = os.environ["GH_TOKEN"]
    temp = os.environ["RUNNER_TEMP"]

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
                "User-Agent": "pi-pr-agent",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else None

    raw_text = open("review.md", encoding="utf-8").read()
    cleaned_text, new_pr_desc = extract_pr_description(raw_text)
    diff_text = open(f"{temp}/diff.patch", encoding="utf-8", errors="replace").read()
    hunk_of = parse_diff_hunks(diff_text)
    try:
        verdict_line, intro_lines, findings = parse_review_markdown(cleaned_text, hunk_of)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    updated_desc = False
    if new_pr_desc:
        try:
            pr_obj = api("GET", f"https://api.github.com/repos/{repo}/pulls/{pr}") or {}
            merged_desc = merge_pr_description(pr_obj.get("body") or "", new_pr_desc)
            api("PATCH", f"https://api.github.com/repos/{repo}/pulls/{pr}", {"body": merged_desc})
            updated_desc = True
        except Exception as exc:
            sys.stderr.write(f"warning: could not update PR description ({exc})\n")

    gql_query = """
    query($owner: String!, $name: String!, $pr: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $pr) {
          reviewThreads(last: 100) {
            nodes {
              id
              isResolved
              path
              startLine
              line
              comments(first: 2) {
                totalCount
                nodes { databaseId body }
              }
            }
          }
        }
      }
    }
    """
    open_threads: dict[tuple[str, int, int], list[tuple[int | None, str]]] = {}
    stale_thread_ids: list[str] = []
    new_targets = {(f["path"], f["eff_start"], f["end"]) for f in findings if f["end_hunk"] is not None}
    try:
        gql = (
            api(
                "POST",
                "https://api.github.com/graphql",
                {"query": gql_query, "variables": {"owner": owner, "name": name, "pr": pr}},
            )
            or {}
        )
        nodes = (
            (((gql.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
            .get("reviewThreads", {})
            .get("nodes")
            or []
        )
        for t in nodes:
            if not t or t.get("isResolved"):
                continue
            c_nodes = ((t.get("comments") or {}).get("nodes")) or []
            first_body = (c_nodes[0].get("body") or "") if c_nodes else ""
            first_id = c_nodes[0].get("databaseId") if c_nodes else None
            if not first_body.startswith("<!-- pi -->"):
                continue
            line_no = t.get("line")
            start_no = t.get("startLine") or line_no
            key = (t.get("path"), start_no, line_no)
            if key in new_targets:
                open_threads.setdefault(key, []).append((first_id, first_body))
            elif (t.get("comments") or {}).get("totalCount", 0) == 1 and t.get("id"):
                stale_thread_ids.append(t["id"])
    except Exception as exc:
        sys.stderr.write(f"warning: could not query reviewThreads ({exc}); continuing without dedup\n")

    resolved_count = 0
    for tid in stale_thread_ids:
        try:
            api(
                "POST",
                "https://api.github.com/graphql",
                {
                    "query": "mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }",
                    "variables": {"id": tid},
                },
            )
            resolved_count += 1
        except Exception as exc:
            sys.stderr.write(f"warning: could not resolve stale thread {tid} ({exc})\n")

    inline = []
    fallback = []
    skipped_existing = 0
    for f in findings:
        p, s_l, e_l = f["path"], f["start"], f["end"]
        end_hunk = f["end_hunk"]
        same_hunk = f["same_hunk"]
        eff_start = f["eff_start"]
        comment_body = (
            f["comment"]
            if (eff_start == s_l and not f.get("relocated"))
            else disarm_suggestion(f["comment"])
        )
        bucket = open_threads.get((p, eff_start, e_l))
        if bucket:
            cid, old_body = bucket.pop(0)
            if cid and old_body.strip() != comment_body.strip():
                try:
                    api(
                        "PATCH",
                        f"https://api.github.com/repos/{repo}/pulls/comments/{cid}",
                        {"body": comment_body},
                    )
                except Exception as exc:
                    sys.stderr.write(f"warning: could not update comment {cid} ({exc}); demoting to sticky\n")
                    fallback.append(f["summary"])
                    continue
            skipped_existing += 1
            continue
        if end_hunk is None:
            fallback.append(f["summary"])
            continue
        item = {"path": p, "line": e_l, "side": "RIGHT", "body": comment_body}
        if same_hunk:
            item["start_line"] = s_l
            item["start_side"] = "RIGHT"
        inline.append((item, f["summary"]))

    posted_inline = 0
    if inline:
        try:
            api(
                "POST",
                f"https://api.github.com/repos/{repo}/pulls/{pr}/reviews",
                {"event": "COMMENT", "comments": [item for item, _ in inline]},
            )
            posted_inline = len(inline)
        except Exception as exc:
            err_detail = (
                exc.read().decode("utf-8", errors="replace")
                if isinstance(exc, urllib.error.HTTPError)
                else str(exc)
            )
            sys.stderr.write(f"warning: inline review POST failed ({err_detail}); demoting to sticky\n")
            fallback.extend(summary for _, summary in inline)

    head_sha = (os.environ.get("HEAD_SHA") or "").strip()[:12]
    sticky_marker = f"<!-- pi-review sha={head_sha} -->" if head_sha else MARKER
    body_parts = [sticky_marker, REVIEW_HEADING, "", verdict_line]
    if intro_lines:
        body_parts += ["", "\n".join(intro_lines)]
    if not findings and not intro_lines and "looks good" not in verdict_line.lower():
        body_parts += ["", cleaned_text.strip()]
    else:
        notes = []
        if posted_inline:
            notes.append(f"{posted_inline} finding(s) commented on the diff.")
        if skipped_existing:
            notes.append(f"{skipped_existing} open finding(s) on the diff from an earlier push.")
        if resolved_count:
            notes.append(f"{resolved_count} addressed thread(s) resolved.")
        if updated_desc:
            notes.append("Updated the PR description to match the current diff.")
        if notes:
            body_parts += ["", " ".join(notes)]
    if fallback:
        body_parts += ["", "### Findings outside the diff hunks", "", "\n\n".join(fallback)]

    sticky_body = "\n".join(body_parts)[:60000]
    issues_url = f"https://api.github.com/repos/{repo}/issues"
    comments = api("GET", f"{issues_url}/{pr}/comments?per_page=100") or []
    existing_id = next((c["id"] for c in comments if (c.get("body") or "").startswith(MARKER_PREFIX)), None)
    if existing_id:
        api("PATCH", f"{issues_url}/comments/{existing_id}", {"body": sticky_body})
    else:
        api("POST", f"{issues_url}/{pr}/comments", {"body": sticky_body})

    print(
        f"Review published on {repo}#{pr} ({posted_inline} inline, {len(fallback)} fallback, {resolved_count} resolved, desc_updated={updated_desc})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
