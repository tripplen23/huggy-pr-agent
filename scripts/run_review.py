#!/usr/bin/env python3
"""Orchestrate single- or multi-bundle parallel Pi PR reviews + Judge verification pass."""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import random
import re
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import fetch_pr_context  # noqa: E402
import post_review  # noqa: E402

GENERIC_FILENAMES = {
    "index",
    "main",
    "mod",
    "lib",
    "route",
    "page",
    "layout",
    "actions",
    "types",
    "schemas",
    "schema",
    "utils",
    "helpers",
    "config",
    "constants",
    "client",
    "server",
}
COUNTERPART_PREFIX_RE = re.compile(r"^(?:tests?|specs?)[._-]", re.I)
COUNTERPART_SUFFIX_RE = re.compile(
    r"(?:[._-](?:test|tests|spec|specs|mock|mocks|view|schema|schemas|types|contract|contracts|dto)|Tests|Test|Spec)$"
)
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def stem_family_key(path: str) -> str:
    """Group implementation, contract, route, and test counterparts under one normalized stem."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    base = parts[-1] if parts else path
    stem = re.sub(r"\.[a-zA-Z0-9]+$", "", base)
    stem = re.sub(r"\.(?:d|test|spec|stories|gen)$", "", stem, flags=re.I)
    cleaned = COUNTERPART_PREFIX_RE.sub("", stem)
    cleaned = COUNTERPART_SUFFIX_RE.sub("", cleaned).lower()
    if cleaned in GENERIC_FILENAMES or len(cleaned) <= 2:
        for parent in reversed(parts[:-1]):
            p_clean = re.sub(r"^[\[(]|[\])]$", "", parent).lower()
            if p_clean and p_clean not in (
                "src",
                "app",
                "lib",
                "pkg",
                "internal",
                "cmd",
                "api",
                "v1",
                "v2",
                "tests",
                "test",
                "scripts",
            ):
                return p_clean
    return cleaned or base.lower()


def top_dir_key(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    return "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "")


def split_oversized_file(file_entry: dict, max_lines: int = 1800) -> list[dict]:
    """Split a single file whose diff exceeds `max_lines` across `@@ ` hunk boundaries."""
    if file_entry["changed_lines"] <= max_lines:
        return [file_entry]

    lines = file_entry["annotated"].splitlines()
    header: list[str] = []
    hunks: list[tuple[int, int, list[str]]] = []
    cur_hunk: list[str] = []
    cur_adds = 0
    cur_dels = 0

    for ln in lines:
        if ln.startswith("@@ "):
            if cur_hunk:
                hunks.append((cur_adds, cur_dels, cur_hunk))
            cur_hunk = [ln]
            cur_adds = 0
            cur_dels = 0
        elif not hunks and not cur_hunk:
            header.append(ln)
        else:
            cur_hunk.append(ln)
            if re.match(r"^L\d+\s+\+", ln):
                cur_adds += 1
            elif ln.startswith("       -"):
                cur_dels += 1
    if cur_hunk:
        hunks.append((cur_adds, cur_dels, cur_hunk))

    if len(hunks) <= 1:
        # Single giant hunk: slice body lines into chunks of max_lines
        body = hunks[0][2] if hunks else lines[len(header) :]
        hunk_hdr = [body[0]] if body and body[0].startswith("@@ ") else []
        rest_lines = body[len(hunk_hdr) :]
        slices: list[dict] = []
        step = max(200, max_lines)
        for idx in range(0, len(rest_lines), step):
            seg = rest_lines[idx : idx + step]
            s_adds = sum(1 for x in seg if re.match(r"^L\d+\s+\+", x))
            s_dels = sum(1 for x in seg if x.startswith("       -"))
            slices.append(
                {
                    "path": file_entry["path"],
                    "additions": s_adds,
                    "deletions": s_dels,
                    "changed_lines": max(1, s_adds + s_dels),
                    "annotated": "\n".join(header + hunk_hdr + seg),
                }
            )
        return slices or [file_entry]

    parts_out: list[dict] = []
    acc_hunks: list[str] = []
    acc_adds = 0
    acc_dels = 0
    for h_adds, h_dels, h_lines in hunks:
        h_changed = h_adds + h_dels
        if acc_hunks and (acc_adds + acc_dels + h_changed > max_lines):
            parts_out.append(
                {
                    "path": file_entry["path"],
                    "additions": acc_adds,
                    "deletions": acc_dels,
                    "changed_lines": max(1, acc_adds + acc_dels),
                    "annotated": "\n".join(header + acc_hunks),
                }
            )
            acc_hunks = []
            acc_adds = 0
            acc_dels = 0
        acc_hunks.extend(h_lines)
        acc_adds += h_adds
        acc_dels += h_dels

    if acc_hunks:
        parts_out.append(
            {
                "path": file_entry["path"],
                "additions": acc_adds,
                "deletions": acc_dels,
                "changed_lines": max(1, acc_adds + acc_dels),
                "annotated": "\n".join(header + acc_hunks),
            }
        )
    return parts_out


def bundle_review_files(
    files: list[dict],
    max_files: int = 10,
    max_lines: int = 1800,
) -> list[list[dict]]:
    """Deterministically pack reviewable files (and split oversized single files) into semantic bundles."""
    if not files:
        return []

    expanded: list[dict] = []
    for f in files:
        expanded.extend(split_oversized_file(f, max_lines=max_lines))

    total_lines = sum(f["changed_lines"] for f in expanded)
    if len(expanded) <= max_files and total_lines <= max_lines:
        return [expanded]

    families: dict[str, list[dict]] = {}
    for f in expanded:
        families.setdefault(stem_family_key(f["path"]), []).append(f)

    units: list[tuple[str, str, list[dict]]] = []
    for fkey, members in families.items():
        members_sorted = sorted(members, key=lambda x: x["path"])
        for idx in range(0, len(members_sorted), max_files):
            chunk = members_sorted[idx : idx + max_files]
            units.append((top_dir_key(chunk[0]["path"]), fkey, chunk))

    units.sort(key=lambda u: (u[0], u[1]))

    bundles: list[list[dict]] = []
    cur_bundle: list[dict] = []
    cur_lines = 0

    for _, _, chunk in units:
        chunk_lines = sum(x["changed_lines"] for x in chunk)
        if len(chunk) > 1 and chunk_lines > max_lines:
            for single in chunk:
                s_lines = single["changed_lines"]
                if cur_bundle and (len(cur_bundle) + 1 > max_files or cur_lines + s_lines > max_lines):
                    bundles.append(cur_bundle)
                    cur_bundle = []
                    cur_lines = 0
                cur_bundle.append(single)
                cur_lines += s_lines
            continue

        if cur_bundle and (len(cur_bundle) + len(chunk) > max_files or cur_lines + chunk_lines > max_lines):
            bundles.append(cur_bundle)
            cur_bundle = []
            cur_lines = 0
        cur_bundle.extend(chunk)
        cur_lines += chunk_lines

    if cur_bundle:
        bundles.append(cur_bundle)
    return bundles


def compute_timeout_budget(
    timeout_input: str,
    num_bundles: int,
    total_lines: int,
    max_parallel: int,
) -> tuple[int, int]:
    """Return (total_wall_seconds, per_bundle_attempt_seconds).

    `auto` caps at 50 minutes so a 60-minute GitHub Actions job always has a 10-minute
    reserve for the Judge pass and graceful partial-coverage publishing.
    """
    parallel = max(1, min(max_parallel, max(1, num_bundles)))
    waves = max(1, math.ceil(max(1, num_bundles) / parallel))
    raw = (timeout_input or "auto").strip().lower()
    if raw in ("", "auto"):
        total_min = min(50, max(24, 18 + 5 * waves + total_lines // 800))
    else:
        try:
            total_min = max(5, int(raw))
        except ValueError:
            total_min = 30
    total_sec = total_min * 60
    # Cap each single attempt at <= 14m / 45% of the wave budget so a stalled
    # primary socket can never starve retries and `fallback_model`.
    per_bundle_sec = min(14 * 60, max(4 * 60, int((total_sec / waves) * 0.45)))
    return total_sec, per_bundle_sec


def invoke_pi(
    prompt_text: str,
    model_candidates: list[str],
    thinking: str,
    tools_arg: str,
    rubric_path: str | None,
    attempt_timeout_sec: int,
    deadline_monotonic: float,
    label: str = "review",
    require_verdict: bool = False,
) -> str | None:
    """Run `pi --print` with prompt piped via stdin (immune to Linux MAX_ARG_STRLEN)."""
    active_candidates = [c for c in model_candidates if c]
    for idx, candidate in enumerate(active_candidates):
        remaining = deadline_monotonic - time.monotonic()
        if remaining < 25:
            sys.stderr.write(f"[{label}] Timeout budget exhausted before attempt {idx + 1}\n")
            break
        has_distinct_fallback = any(c != candidate for c in active_candidates[idx + 1 :])
        reserve_for_fallback = (
            min(210, attempt_timeout_sec + 20)
            if (has_distinct_fallback and remaining > 260)
            else 0
        )
        this_timeout = min(attempt_timeout_sec, max(25, int(remaining - reserve_for_fallback)))

        cmd = [
            "pi",
            "--print",
            "--model",
            candidate,
            "--thinking",
            thinking,
        ]
        if tools_arg == "none":
            cmd.append("--no-tools")
        else:
            cmd.extend(["--tools", tools_arg])
        cmd.extend(
            [
                "--no-extensions",
                "--no-mcp",
                "--no-skills",
                "--no-context-files",
                "--no-approve",
                "--no-session",
            ]
        )
        if rubric_path and os.path.isfile(rubric_path) and os.path.getsize(rubric_path) > 0:
            cmd.extend(["--append-system-prompt", rubric_path])

        sys.stdout.write(f"[{label}] Running model={candidate} thinking={thinking} timeout={this_timeout}s\n")
        sys.stdout.flush()
        try:
            proc = subprocess.run(
                cmd,
                input=prompt_text,
                text=True,
                capture_output=True,
                timeout=this_timeout,
            )
            out_str = proc.stdout or ""
            if proc.returncode == 0 and out_str.strip():
                if require_verdict and not re.search(r"^\*\*Verdict:\*\*\s*\S+", out_str, re.M):
                    sys.stderr.write(f"[{label}] Attempt on {candidate} omitted **Verdict:** header; retrying\n")
                else:
                    return out_str
            else:
                err_preview = (proc.stderr or "").strip()[-400:]
                sys.stderr.write(f"[{label}] Attempt on {candidate} exited {proc.returncode}: {err_preview}\n")
        except subprocess.TimeoutExpired:
            sys.stderr.write(f"[{label}] Attempt on {candidate} timed out after {this_timeout}s\n")
        except Exception as exc:
            sys.stderr.write(f"[{label}] Attempt on {candidate} errored: {exc}\n")

        if idx + 1 < len(active_candidates) and (deadline_monotonic - time.monotonic()) > 40:
            backoff = 12.0 + random.uniform(0.0, 8.0)
            time.sleep(backoff)
    return None


def parse_raw_candidates(raw_md: str, worktree_root: str = ".") -> dict:
    """Extract verdict, effort/security metadata, intro lines, raw findings, and <pr_description>."""
    cleaned, pr_desc = post_review.extract_pr_description(raw_md)
    verdict_m = re.search(r"^\*\*Verdict:\*\*\s*(.+)$", cleaned, re.M)
    verdict = verdict_m.group(1).strip().lower() if verdict_m else "looks good"

    effort_num = 2
    effort_m = re.search(r"\*\*Effort:\*\*\s*(\d)", cleaned)
    if effort_m:
        effort_num = int(effort_m.group(1))
    sec_attn = bool(re.search(r"\*\*Security:\*\*\s*Needs attention", cleaned, re.I))

    candidates: list[dict] = []
    intro_lines: list[str] = []
    after_verdict = False
    cur = None
    in_fence = False

    for ln in cleaned.splitlines():
        if not after_verdict:
            if re.match(r"^\*\*Verdict:\*\*", ln):
                after_verdict = True
            continue
        if re.match(r"^\*\*Effort:\*\*", ln.strip()):
            continue
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else post_review.HEADER_RE.match(ln)
        if m:
            if cur:
                candidates.append(cur)
            path, start_s, end_s, rest = (
                m.group(1),
                m.group(2),
                m.group(3),
                (m.group(4) or "").strip(),
            )
            s_l = int(start_s)
            e_l = int(end_s) if end_s else s_l
            if e_l < s_l:
                s_l, e_l = e_l, s_l
            cur = {"path": path, "start": s_l, "end": e_l, "rest": rest, "body_lines": []}
        else:
            if cur is not None:
                cur["body_lines"].append(ln)
            elif ln.strip():
                intro_lines.append(ln)
    if cur:
        candidates.append(cur)

    safe_candidates: list[dict] = []
    for c in candidates:
        body_text = "\n".join(c["body_lines"]).strip()
        if not post_review.is_safe_rel_path(worktree_root, c["path"]):
            continue
        c["body_text"] = body_text

        sev = "medium"
        parts = [p.strip() for p in re.split(r"\s*·\s*", c["rest"], maxsplit=1) if p.strip()]
        if parts and parts[0].lower() in SEVERITY_RANK:
            sev = parts[0].lower()
        c["severity"] = sev
        safe_candidates.append(c)

    return {
        "verdict": verdict,
        "effort": effort_num,
        "security_attention": sec_attn,
        "intro_lines": intro_lines,
        "candidates": safe_candidates,
        "pr_description": pr_desc,
    }


def extract_json_object(text: str) -> dict | None:
    """Extract the first JSON object from model output (with or without code fences)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = fenced.group(1) if fenced else text
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except Exception:
        return None


DEEP_LOGIC_LENS = (
    "\n\n### Specialist Lens: Functional Correctness, Callers & State Integrity\n"
    "Focus this pass strictly on broken callers/contracts across the repo, concrete boundary/null/empty inputs, "
    "concurrency/race conditions, partial state writes, resource leaks, and missing test updates."
)
DEEP_SECURITY_LENS = (
    "\n\n### Specialist Lens: Security, Trust Boundaries & Repository Rules\n"
    "Focus this pass strictly on missing auth/tenant/permission checks, injection/path-traversal/SSRF, "
    "secret/PII leaks, schema-vs-guard drift, swallowed errors, and explicit `AGENTS.md`/`CLAUDE.md` rules."
)


def demote_rest_to_low(rest: str) -> str:
    parts = [p.strip() for p in re.split(r"\s*·\s*", rest or "", maxsplit=1) if p.strip()]
    if len(parts) == 2 and parts[0].lower() in ("high", "medium", "low"):
        return f"Low · {parts[1]}"
    return f"Low · {rest}" if rest else "Low · Minor issue"


def apply_judge_decisions(candidates: list[dict], decisions_obj: dict | None) -> list[dict]:
    """Filter, confidence-gate (`>=80` inline, `65..79` demoted to Low), and disarm candidates."""
    if not decisions_obj or not isinstance(decisions_obj.get("decisions"), list):
        return candidates

    decision_by_idx: dict[int, tuple[str, int]] = {}
    for item in decisions_obj["decisions"]:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        act = (item.get("action") or "keep").strip().lower()
        raw_conf = item.get("confidence")
        try:
            conf = 0 if isinstance(raw_conf, bool) else int(float(raw_conf))
        except (TypeError, ValueError):
            conf = 65
        if isinstance(idx, int) and act in ("keep", "drop", "disarm_suggestion"):
            decision_by_idx[idx] = (act, conf)

    kept: list[dict] = []
    for i, c in enumerate(candidates):
        if i not in decision_by_idx:
            # Missing decision from partial Judge output: demote to Low so unverified items never land inline under chill
            updated = dict(c)
            updated["severity"] = "low"
            updated["rest"] = demote_rest_to_low(updated.get("rest") or "")
            kept.append(updated)
            continue
        act, conf = decision_by_idx[i]
        sev = (c.get("severity") or "medium").lower()
        if act == "drop" or conf < 65:
            sys.stdout.write(f"[judge] Dropped candidate [{i}] {c['path']}:{c['start']}-{c['end']} (conf={conf})\n")
            continue
        updated = dict(c)
        if conf < 80 and sev in ("high", "medium"):
            updated["severity"] = "low"
            updated["rest"] = demote_rest_to_low(updated.get("rest") or "")
            sys.stdout.write(
                f"[judge] Demoted candidate [{i}] {c['path']}:{c['start']}-{c['end']} to Low (conf={conf})\n"
            )
        if act == "disarm_suggestion":
            updated["body_text"] = post_review.disarm_suggestion(updated["body_text"])
        kept.append(updated)
    return kept


def extract_resolved_threads_summary(gql_data: dict) -> list[str]:
    pr = (((gql_data.get("data") or {}).get("repository") or {}).get("pullRequest")) or {}
    threads = ((pr.get("reviewThreads") or {}).get("nodes")) or []
    out: list[str] = []
    for t in threads:
        if not t or not t.get("isResolved"):
            continue
        c_nodes = ((t.get("comments") or {}).get("nodes")) or []
        if not c_nodes:
            continue
        path = t.get("path") or "unknown"
        ln = t.get("line") or t.get("originalLine") or "?"
        summary = fetch_pr_context.clean_body(c_nodes[0].get("body") or "", limit=300)
        out.append(f"- `{path}:{ln}` [RESOLVED]: {summary}")
    return out[-15:]


def build_judge_input(
    candidates: list[dict],
    judge_prompt: str,
    gql_data: dict | None = None,
    scoped_rules_md: str = "",
    worktree_root: str = ".",
) -> str:
    blocks = [judge_prompt.strip()]
    if scoped_rules_md.strip():
        blocks.extend(["", "## Path-Scoped Repository Rules (from `base` branch)", "", scoped_rules_md.strip()])
    resolved_summary = extract_resolved_threads_summary(gql_data or {})
    if resolved_summary:
        blocks.extend(["", "## Previously Resolved Threads on This PR", "", *resolved_summary])
    blocks.extend(["", "## Candidate Findings to Verify", ""])
    for i, c in enumerate(candidates):
        loc = f"{c['path']}:{c['start']}-{c['end']}" if c["start"] != c["end"] else f"{c['path']}:{c['start']}"
        full_path = post_review.safe_worktree_file(worktree_root, c["path"])
        snippet_lines = []
        if full_path:
            flines = open(full_path, encoding="utf-8", errors="replace").read().splitlines()
            lo = max(1, c["start"] - 10)
            hi = min(len(flines), c["end"] + 10)
            for ln_no in range(lo, hi + 1):
                marker = ">>" if c["start"] <= ln_no <= c["end"] else "  "
                snippet_lines.append(f"{marker} L{ln_no:<5d} {flines[ln_no - 1]}")
        else:
            snippet_lines.append("(path not present inside repository worktree)")

        blocks.extend(
            [
                f"### Candidate [{i}] — `{loc}` · {c['rest']}",
                c["body_text"],
                "",
                f"Ground-truth file excerpt (`{c['path']}`):",
                "```",
                "\n".join(snippet_lines[:60]),
                "```",
                "",
            ]
        )
    return "\n".join(blocks)


def assemble_review_markdown(
    candidates: list[dict],
    effort: int,
    security_attention: bool,
    extra_notes: list[str],
    pr_description: str | None,
    max_findings: int = 8,
    has_partial_failure: bool = False,
) -> str:
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (SEVERITY_RANK.get(pair[1].get("severity", "medium"), 1), pair[0]),
    )
    selected = [c for _, c in ranked[:max_findings]]

    if not selected:
        verdict = "incomplete (partial coverage)" if has_partial_failure else "looks good"
        sec_str = "No issues"
    elif any(c.get("severity") in ("high", "medium") for c in selected):
        verdict = "needs changes"
        sec_str = "Needs attention" if security_attention else "No issues"
    else:
        verdict = "nits"
        sec_str = "Needs attention" if security_attention else "No issues"

    out = [
        "## Review by Pi",
        "",
        f"**Verdict:** {verdict}",
        f"**Effort:** {max(1, min(5, effort))} · **Security:** {sec_str}",
    ]
    if extra_notes:
        out.extend(["", "\n".join(extra_notes)])
    elif not selected:
        out.extend(["", "No actionable defects found in this change."])

    for c in selected:
        loc = f"{c['path']}:{c['start']}-{c['end']}" if c["start"] != c["end"] else f"{c['path']}:{c['start']}"
        heading = f"### `{loc}`" + (f" · {c['rest']}" if c["rest"] else "")
        out.extend(["", heading, "", c["body_text"]])

    if pr_description:
        out.extend(["", "<pr_description>", pr_description.strip(), "</pr_description>"])

    return "\n".join(out).strip() + "\n"


def redact_secret(text: str, secret: str) -> str:
    out = text
    api_env = os.environ.get("PI_API_KEY_ENV") or "OPENCODE_API_KEY"
    for candidate in (
        secret,
        os.environ.get(api_env) or "",
        os.environ.get("GOOGLE_CLOUD_API_KEY") or "",
    ):
        if candidate:
            out = out.replace(candidate, "[redacted]")
    gcp_json = os.environ.get("PI_GCP_CREDENTIALS_JSON") or ""
    if gcp_json:
        out = out.replace(gcp_json, "[redacted]")
        try:
            pk = (json.loads(gcp_json) or {}).get("private_key") or ""
            if pk:
                out = out.replace(pk, "[redacted]").replace(pk.replace("\n", "\\n"), "[redacted]")
        except ValueError:
            pass
    return out


def strip_pr_description_section(template_text: str) -> str:
    """Remove only the `<pr_description>` instruction block while preserving trailing extra_instructions."""
    return re.sub(
        r"\n*Finally, check the `Current PR Description`.*?</pr_description>\s*",
        "\n\n",
        template_text,
        flags=re.S,
    ).strip()


def main() -> int:
    temp = os.environ.get("RUNNER_TEMP", "/tmp")
    api_key = os.environ.get("PI_API_KEY") or ""
    api_key_env = os.environ.get("PI_API_KEY_ENV") or "OPENCODE_API_KEY"
    ambient_key = os.environ.get(api_key_env) or ""
    gcp_project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
        or os.environ.get("PI_GCP_PROJECT")
        or ""
    )
    gcp_api_key = os.environ.get("GOOGLE_CLOUD_API_KEY") or ""
    if api_key:
        os.environ[api_key_env] = api_key
    elif not ambient_key and not gcp_project and not gcp_api_key:
        sys.stderr.write("No model credentials configured (PI_API_KEY and GOOGLE_CLOUD_PROJECT are both empty)\n")
        return 1

    primary_model = os.environ.get("PI_MODEL") or "opencode-go/deepseek-v4.1-flash"
    fallback_model = os.environ.get("PI_FALLBACK_MODEL") or ""
    model_candidates = [m for m in (primary_model, primary_model, fallback_model) if m]
    thinking = os.environ.get("PI_THINKING") or "high"
    review_tools = os.environ.get("PI_REVIEW_TOOLS") or "read,grep,find,ls"
    timeout_input = os.environ.get("PI_REVIEW_TIMEOUT_MINUTES") or "auto"
    max_parallel = max(1, int(os.environ.get("PI_MAX_PARALLEL_BUNDLES") or "3"))
    judge_enabled = (os.environ.get("PI_JUDGE_PASS") or "true").strip().lower() == "true"
    review_depth = (os.environ.get("PI_REVIEW_DEPTH") or "standard").strip().lower()
    comment_id_raw = os.environ.get("COMMENT_ID") or ""
    comment_id = int(comment_id_raw) if comment_id_raw.isdigit() else None

    brief_template_path = os.path.join(temp, "brief-template.md")
    rubric_path = os.path.join(temp, "rubric.md")
    scoped_rules_path = os.path.join(temp, "scoped-rules.md")
    diff_path = os.path.join(temp, "diff.patch")
    meta_path = os.path.join(temp, "pr-meta.json")
    judge_prompt_path = os.path.join(temp, "pi-action", "prompts", "judge.md")

    brief_template = open(brief_template_path, encoding="utf-8").read()
    diff_text = open(diff_path, encoding="utf-8", errors="replace").read() if os.path.isfile(diff_path) else ""
    meta = json.loads(open(meta_path, encoding="utf-8").read()) if os.path.isfile(meta_path) else {}
    scoped_rules_md = open(scoped_rules_path, encoding="utf-8").read() if os.path.isfile(scoped_rules_path) else ""
    gitattributes_globs = meta.get("gitattributes_globs") or []
    failed_ci_logs = meta.get("failed_ci_logs") or ""

    parsed_files = fetch_pr_context.parse_annotated_files(diff_text, gitattributes_globs)
    if not parsed_files:
        empty_md = (
            "## Review by Huggy\n\n"
            "**Verdict:** looks good\n"
            "**Effort:** 1 · **Security:** No issues\n\n"
            "No reviewable source files changed in this diff (only lockfiles, generated files, or deletions).\n"
        )
        open("review.md", "w", encoding="utf-8").write(empty_md)
        print("Skipped LLM call: 0 reviewable files after filtering.")
        return 0

    bundles = bundle_review_files(parsed_files)
    lenses: list[tuple[str, str]] = (
        [("logic", DEEP_LOGIC_LENS), ("security", DEEP_SECURITY_LENS)]
        if review_depth == "deep"
        else [("standard", "")]
    )
    total_passes = len(bundles) * len(lenses)
    total_lines = sum(f["changed_lines"] for f in parsed_files)
    total_budget_sec, per_bundle_sec = compute_timeout_budget(
        timeout_input, total_passes, total_lines, max_parallel
    )
    deadline = time.monotonic() + total_budget_sec
    print(
        f"Review plan: {len(parsed_files)} files ({total_lines} changed lines) -> "
        f"{len(bundles)} bundle(s) x {len(lenses)} lens(es) ({review_depth}), "
        f"parallel={min(max_parallel, total_passes)}, budget={total_budget_sec // 60}m."
    )

    def run_pass(idx: int, b_files: list[dict], lens_name: str, lens_suffix: str) -> tuple[int, str, str | None]:
        b_paths = {f["path"] for f in b_files}
        siblings = [f for f in parsed_files if f["path"] not in b_paths]
        b_diff_path = os.path.join(temp, f"diff-numbered-b{idx}-{lens_name}.patch")
        label = f"Bundle {idx + 1}/{len(bundles)}" if len(bundles) > 1 else ""
        b_scoped_rules = fetch_pr_context.collect_scoped_rules(list(b_paths)) if len(bundles) > 1 else scoped_rules_md
        ctx_md = fetch_pr_context.format_pr_context(
            meta.get("graphql") or {},
            comment_id=comment_id,
            diff_text=diff_text,
            incremental_diff_text=meta.get("incremental_diff") or "",
            last_reviewed_sha=meta.get("last_reviewed_sha"),
            annotated_diff_path=b_diff_path,
            bundle_files=b_files,
            sibling_files=siblings if len(bundles) > 1 else None,
            bundle_label=label,
            extra_generated_globs=gitattributes_globs,
            scoped_rules_md=b_scoped_rules,
            failed_ci_logs=failed_ci_logs,
        )
        include_desc = len(bundles) == 1 and lens_name in ("standard", "logic")
        t_template = brief_template if include_desc else strip_pr_description_section(brief_template)
        if lens_suffix:
            t_template = t_template + lens_suffix
        full_prompt = f"{t_template}\n\n{ctx_md}" if ctx_md else t_template
        out = invoke_pi(
            prompt_text=full_prompt,
            model_candidates=model_candidates,
            thinking=thinking,
            tools_arg=review_tools,
            rubric_path=rubric_path,
            attempt_timeout_sec=per_bundle_sec,
            deadline_monotonic=deadline,
            label=f"bundle-{idx + 1}/{len(bundles)}-{lens_name}",
            require_verdict=True,
        )
        return idx, lens_name, out

    pass_tasks = [(i, b, l_name, l_sfx) for i, b in enumerate(bundles) for l_name, l_sfx in lenses]
    outputs_by_bundle: dict[int, list[str]] = {i: [] for i in range(len(bundles))}
    workers = min(max_parallel, len(pass_tasks))
    if workers <= 1:
        for i, b, l_name, l_sfx in pass_tasks:
            _, _, out = run_pass(i, b, l_name, l_sfx)
            if out:
                outputs_by_bundle[i].append(out)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(run_pass, i, b, l_name, l_sfx) for i, b, l_name, l_sfx in pass_tasks]
            for fut in concurrent.futures.as_completed(futs):
                i, _, out = fut.result()
                if out:
                    outputs_by_bundle[i].append(out)

    succeeded = [i for i in range(len(bundles)) if outputs_by_bundle[i]]
    failed = [i for i in range(len(bundles)) if not outputs_by_bundle[i]]
    incomplete_lenses = [
        i for i in succeeded if len(outputs_by_bundle[i]) < len(lenses)
    ]
    if not succeeded:
        sys.stderr.write("All review bundles failed.\n")
        return 1

    all_candidates: list[dict] = []
    seen_cand_keys: set[tuple[str, int, int, str]] = set()
    max_effort = 1
    any_sec = False
    pr_desc: str | None = None
    notes: list[str] = []

    for i in succeeded:
        for pass_idx, raw_out in enumerate(outputs_by_bundle[i]):
            parsed = parse_raw_candidates(raw_out)
            for cand in parsed["candidates"]:
                ckey = (cand["path"], cand["start"], cand["end"], (cand.get("rest") or "").lower())
                if ckey not in seen_cand_keys:
                    seen_cand_keys.add(ckey)
                    all_candidates.append(cand)
            max_effort = max(max_effort, parsed["effort"])
            any_sec = any_sec or parsed["security_attention"]
            if i == 0 and pr_desc is None and parsed["pr_description"]:
                pr_desc = parsed["pr_description"]
            if len(bundles) == 1 and pass_idx == 0 and parsed["intro_lines"] and not parsed["candidates"]:
                notes.extend(parsed["intro_lines"])

    has_partial = bool(failed or incomplete_lenses)
    if failed or incomplete_lenses:
        bad_idxs = list(dict.fromkeys([*failed, *incomplete_lenses]))
        missed_paths = list(dict.fromkeys(f["path"] for i in bad_idxs for f in bundles[i]))[:8]
        missed_str = ", ".join(f"`{p}`" for p in missed_paths)
        notes.append(
            f"> ⚠️ **Partial coverage:** some review passes timed out within the budget "
            f"(affected files: {missed_str})."
        )

    if judge_enabled and all_candidates and os.path.isfile(judge_prompt_path) and (deadline - time.monotonic()) > 45:
        judge_prompt = open(judge_prompt_path, encoding="utf-8").read()
        cand_paths = list(dict.fromkeys(c["path"] for c in all_candidates))
        judge_rules = fetch_pr_context.collect_scoped_rules(cand_paths) or scoped_rules_md
        judge_input = build_judge_input(
            all_candidates,
            judge_prompt,
            gql_data=meta.get("graphql"),
            scoped_rules_md=judge_rules,
        )
        judge_models = [m for m in (primary_model, fallback_model) if m]
        judge_raw = invoke_pi(
            prompt_text=judge_input,
            model_candidates=judge_models,
            thinking="low",
            tools_arg="none",
            rubric_path=rubric_path,
            attempt_timeout_sec=120,
            deadline_monotonic=deadline,
            label="judge",
        )
        if judge_raw:
            decisions = extract_json_object(judge_raw)
            before_n = len(all_candidates)
            all_candidates = apply_judge_decisions(all_candidates, decisions)
            print(f"[judge] Kept {len(all_candidates)}/{before_n} findings.")

    max_findings = 8 if len(bundles) == 1 else min(15, 6 + 2 * len(bundles))
    final_md = assemble_review_markdown(
        candidates=all_candidates,
        effort=max_effort,
        security_attention=any_sec,
        extra_notes=notes,
        pr_description=pr_desc,
        max_findings=max_findings,
        has_partial_failure=has_partial,
    )
    final_md = redact_secret(final_md, api_key)
    with open("review.md", "w", encoding="utf-8") as fh:
        fh.write(final_md)
    print(f"Wrote review.md ({len(final_md)} bytes, {min(len(all_candidates), max_findings)} findings).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
