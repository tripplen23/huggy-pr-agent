#!/usr/bin/env python3
"""Parse Pi's markdown review, sync stale PR descriptions, and publish inline diff comments."""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

MARKER = "<!-- pi-review -->"
MARKER_PREFIX = "<!-- pi-review"
COMMENT_MARKER_RE = re.compile(r"^<!--\s*pi(?:\s+id=([0-9a-f]{6,16}))?\s*-->")
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


def compute_finding_fingerprint(path: str, rest: str, body_text: str) -> str:
    """Compute a line-shift-resilient 10-hex fingerprint from `(path, title_without_severity, code_identifiers)`."""
    parts = [p.strip() for p in re.split(r"\s*·\s*", rest or "", maxsplit=1) if p.strip()]
    title = parts[1] if (len(parts) == 2 and parts[0].capitalize() in BADGES) else (rest or "")
    norm_title = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    backtick_ids = sorted({m.lower() for m in re.findall(r"`([A-Za-z_][A-Za-z0-9_.]*)`", body_text or "")})[:5]
    key = f"{path}::{norm_title}::{'|'.join(backtick_ids)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def finalize_inline_comment(finding: dict, disarmed: bool = False) -> str:
    """Build final inline comment body, appending a collapsible `Prompt for AI Agents` when no 1-click suggestion exists."""
    comment_body = disarm_suggestion(finding["comment"]) if disarmed else finding["comment"]
    if (
        finding.get("severity") in ("high", "medium")
        and not SUGGESTION_BLOCK_RE.search(comment_body)
        and "Prompt for AI Agents" not in comment_body
    ):
        s_l, e_l = finding["start"], finding["end"]
        loc_desc = f"line {s_l}" if s_l == e_l else f"lines {s_l}-{e_l}"
        plain_issue = disarm_suggestion(finding.get("body_text") or "").strip()
        plain_issue = re.sub(r"```.*?```", "", plain_issue, flags=re.S).strip()
        title = finding.get("title") or finding.get("rest") or "Fix defect"
        agent_block = (
            "\n\n<details>\n"
            "<summary>🤖 <b>Prompt for AI Agents</b></summary>\n\n"
            "```text\n"
            f"In {finding['path']} around {loc_desc} ({title}):\n"
            f"{plain_issue}\n"
            "Verify callers and tests, and apply the smallest safe fix (prefer deleting/reusing existing code over adding new abstractions).\n"
            "```\n"
            "</details>"
        )
        comment_body = comment_body.rstrip() + agent_block
    return comment_body


def should_autoresolve_thread(thread_node: dict, is_partial_coverage: bool = False) -> bool:
    """Return True if an addressed open Huggy review thread is safe to auto-resolve."""
    if is_partial_coverage or not thread_node or thread_node.get("isResolved") or not thread_node.get("id"):
        return False
    last_conn = thread_node.get("lastComments") or thread_node.get("comments") or {}
    c_nodes = last_conn.get("nodes") or []
    if not c_nodes:
        return False
    if "lastComments" not in thread_node:
        total_count = last_conn.get("totalCount") or len(c_nodes)
        if total_count > len(c_nodes):
            return False
    last_c = c_nodes[-1] or {}
    last_author = (((last_c.get("author") or {}).get("login")) or "").lower()
    last_body = (last_c.get("body") or "").strip()
    is_bot = (
        not last_author
        or last_author in ("github-actions", "github-actions[bot]")
        or last_author.endswith("[bot]")
        or bool(COMMENT_MARKER_RE.match(last_body))
    )
    if not is_bot:
        stripped_cmd = re.sub(r"^>.*$", "", last_body, flags=re.M).strip()
        if stripped_cmd.endswith("?"):
            return False
    return True


def classify_review_threads(
    nodes: list[dict],
    findings: list[dict],
    is_partial_coverage: bool = False,
) -> tuple[dict[tuple[str, int, int], list[tuple[int | None, str]]], dict[tuple[str, str], list[tuple[int | None, str]]], list[str]]:
    """Match open Huggy threads by `(path, eff_start, end)` or `(path, fingerprint)`, and list addressed thread IDs to resolve."""
    open_by_loc: dict[tuple[str, int, int], list[tuple[int | None, str]]] = {}
    open_by_fp: dict[tuple[str, str], list[tuple[int | None, str]]] = {}
    stale_thread_ids: list[str] = []

    new_targets = {(f["path"], f["eff_start"], f["end"]) for f in findings if f.get("end_hunk") is not None}
    new_fps = {(f["path"], f["fingerprint"]) for f in findings if f.get("fingerprint")}

    for t in nodes or []:
        if not t or t.get("isResolved"):
            continue
        first_conn = t.get("firstComments") or t.get("comments") or {}
        c_nodes = first_conn.get("nodes") or []
        first_body = (c_nodes[0].get("body") or "") if c_nodes else ""
        first_id = c_nodes[0].get("databaseId") if c_nodes else None
        m = COMMENT_MARKER_RE.match(first_body.strip())
        if not m:
            continue
        fp = m.group(1) or ""
        t_path = t.get("path") or ""
        line_no = t.get("line")
        start_no = t.get("startLine") or line_no
        loc_key = (t_path, start_no, line_no)
        if loc_key in new_targets:
            open_by_loc.setdefault(loc_key, []).append((first_id, first_body))
        elif fp and (t_path, fp) in new_fps:
            open_by_fp.setdefault((t_path, fp), []).append((first_id, first_body))
        elif should_autoresolve_thread(t, is_partial_coverage=is_partial_coverage):
            stale_thread_ids.append(t["id"])

    return open_by_loc, open_by_fp, stale_thread_ids


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


IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
COMMON_KEYWORDS = {"const", "let", "var", "return", "function", "async", "await", "import", "from", "def", "class", "true", "false", "null", "none", "self", "this"}


def ident_tokens(text: str) -> set[str]:
    return {tok for tok in IDENT_RE.findall(text) if tok.lower() not in COMMON_KEYWORDS}


def leading_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def relocate_suggestion_window(
    markdown: str,
    file_lines: list[str],
    path: str,
    start: int,
    end: int,
    hunk_of: dict[tuple[str, int], int],
    max_shift: int = 5,
) -> tuple[int, int] | None:
    """Content-anchored sliding-window line repair (`±max_shift`) for drifted suggestion ranges."""
    m = SUGGESTION_BLOCK_RE.search(markdown)
    if not m or not file_lines:
        return None
    sugg_body = m.group(2)
    sugg_nonempty = [ln for ln in sugg_body.splitlines() if ln.strip()]
    if not sugg_nonempty:
        return None
    sugg_indent = leading_indent(sugg_nonempty[0])
    sugg_idents = ident_tokens(sugg_body)
    if not sugg_idents:
        return None

    # Only relocate when the original range is off-hunk, cross-hunk, or shares zero identifiers with the suggestion
    orig_h_end = hunk_of.get((path, end))
    orig_in_single_hunk = orig_h_end is not None and (start == end or hunk_of.get((path, start)) == orig_h_end)
    if orig_in_single_hunk and 1 <= start <= end <= len(file_lines):
        orig_idents = ident_tokens("\n".join(file_lines[start - 1 : end]))
        if orig_idents & sugg_idents:
            return None

    for dist in range(1, max_shift + 1):
        matches: list[tuple[int, int]] = []
        for delta in (-dist, dist):
            s2, e2 = start + delta, end + delta
            if s2 < 1 or e2 > len(file_lines) or s2 > e2:
                continue
            h_end = hunk_of.get((path, e2))
            if h_end is None or (s2 < e2 and hunk_of.get((path, s2)) != h_end):
                continue
            cand_slice = file_lines[s2 - 1 : e2]
            cand_nonempty = [ln for ln in cand_slice if ln.strip()]
            if not cand_nonempty or leading_indent(cand_nonempty[0]) != sugg_indent:
                continue
            cand_idents = ident_tokens("\n".join(cand_slice))
            if not (cand_idents & sugg_idents):
                continue
            if validate_suggestion_against_file(markdown, file_lines, s2, e2) != markdown:
                continue
            matches.append((s2, e2))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None


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
        parts_sev = [p.strip() for p in re.split(r"\s*·\s*", rest, maxsplit=1) if p.strip()]
        f["severity"] = (
            parts_sev[0].lower()
            if (parts_sev and parts_sev[0].capitalize() in BADGES)
            else "medium"
        )
        snippet = ""
        if not is_safe_rel_path(worktree_root, f["path"]):
            body_text = disarm_suggestion(body_text)
        else:
            full_path = safe_worktree_file(worktree_root, f["path"])
            if full_path:
                with open(full_path, encoding="utf-8", errors="replace") as fh:
                    file_lines = fh.read().splitlines()
                snapped_window = relocate_suggestion_window(
                    body_text, file_lines, f["path"], f["start"], f["end"], hunk_of
                )
                if snapped_window is not None:
                    f["start"], f["end"] = snapped_window
                cited = file_lines[max(0, f["start"] - 1) : min(len(file_lines), f["end"])]
                if cited:
                    snippet = "\n```\n" + "\n".join(cited[:6]) + "\n```"
                body_text = validate_suggestion_against_file(body_text, file_lines, f["start"], f["end"])

        loc = f"`{f['path']}:{f['start']}" + (f"-{f['end']}`" if f["end"] != f["start"] else "`")
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
        f["body_text"] = body_text
        fp = compute_finding_fingerprint(f["path"], rest, body_text)
        f["fingerprint"] = fp
        marker_line = f"<!-- pi id={fp} -->"

        if body_text and rest:
            parts = [p.strip() for p in re.split(r"\s*·\s*", rest, maxsplit=1) if p.strip()]
            if len(parts) == 2 and parts[0].capitalize() in BADGES:
                f["title"] = parts[1]
                header = f"{BADGES[parts[0].capitalize()]} **{parts[1]}**"
            else:
                f["title"] = rest
                header = f"**{rest}**"
            f["comment"] = f"{marker_line}\n{header}\n\n{body_text}"
            f["summary"] = f"- {loc} ({rest}) — {summary_body}{snippet}"
        else:
            f["title"] = rest
            combined = (rest + ("\n" + body_text if body_text else "")).strip()
            summary_combined = (rest + ("\n" + summary_body if summary_body else "")).strip()
            f["comment"] = f"{marker_line}\n{combined}"
            f["summary"] = f"- {loc} — {summary_combined}{snippet}"

    return verdict_line, intro_lines, findings


def partition_findings_by_profile(findings: list[dict], profile: str = "chill") -> tuple[list[dict], list[str]]:
    """Split findings into `(inline_candidates, collapsed_nitpick_summaries)`.

    Under `chill` (default), `Low` / nitpick items stay out of inline PR review threads
    and are rendered inside a collapsible `<details>` section in the sticky summary.
    Under `assertive`, all severities are eligible for inline diff comments.
    """
    norm = (profile or "chill").strip().lower()
    if norm == "assertive":
        return list(findings), []
    inline_cands: list[dict] = []
    nitpicks: list[str] = []
    for f in findings:
        if f.get("severity") == "low":
            nitpicks.append(f["summary"])
        else:
            inline_cands.append(f)
    return inline_cands, nitpicks


def main() -> int:
    repo = os.environ["REPO"]
    owner, name = repo.split("/", 1)
    pr = int(os.environ["PR"])
    token = os.environ["GH_TOKEN"]
    temp = os.environ["RUNNER_TEMP"]
    review_profile = (os.environ.get("PI_REVIEW_PROFILE") or "chill").strip().lower()

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

    inline_candidates, nitpick_summaries = partition_findings_by_profile(findings, review_profile)

    is_partial_coverage = "incomplete (partial coverage)" in verdict_line.lower() or any(
        "partial coverage" in ln.lower() for ln in intro_lines
    )

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
              firstComments: comments(first: 1) {
                nodes { databaseId body }
              }
              lastComments: comments(last: 1) {
                totalCount
                nodes { databaseId author { login } body }
              }
            }
          }
        }
      }
    }
    """
    open_threads: dict[tuple[str, int, int], list[tuple[int | None, str]]] = {}
    open_fp_threads: dict[tuple[str, str], list[tuple[int | None, str]]] = {}
    stale_thread_ids: list[str] = []
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
        open_threads, open_fp_threads, stale_thread_ids = classify_review_threads(
            nodes, inline_candidates, is_partial_coverage=is_partial_coverage
        )
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
    for f in inline_candidates:
        p, s_l, e_l = f["path"], f["start"], f["end"]
        end_hunk = f["end_hunk"]
        same_hunk = f["same_hunk"]
        eff_start = f["eff_start"]
        disarm = not (eff_start == s_l and not f.get("relocated"))
        loc_bucket = open_threads.get((p, eff_start, e_l))
        fp_bucket = None if loc_bucket else open_fp_threads.get((p, f.get("fingerprint") or ""))
        bucket = loc_bucket or fp_bucket
        comment_body = finalize_inline_comment(f, disarmed=(disarm or bool(fp_bucket)))
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
        if nitpick_summaries:
            notes.append(f"{len(nitpick_summaries)} minor nitpick(s) collapsed below.")
        if skipped_existing:
            notes.append(f"{skipped_existing} open finding(s) on the diff from an earlier push.")
        if resolved_count:
            notes.append(f"{resolved_count} addressed thread(s) resolved.")
        if updated_desc:
            notes.append("Updated the PR description to match the current diff.")
        if notes:
            body_parts += ["", " ".join(notes)]
    if nitpick_summaries:
        body_parts += [
            "",
            "<details>",
            f"<summary>🧹 <b>Nitpicks ({len(nitpick_summaries)})</b></summary>",
            "",
            "\n\n".join(nitpick_summaries),
            "",
            "</details>",
        ]
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
