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
COUNTERPART_SUFFIX_RE = re.compile(
    r"(?:[._-](?:test|tests|spec|specs|mock|mocks|view|schema|schemas|types|contract|contracts|dto)|Tests|Test|Spec)$"
)
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def stem_family_key(path: str) -> str:
    """Group implementation, contract, route, and test counterparts under one normalized stem."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    base = parts[-1] if parts else path
    # Strip compound extensions (.test.ts, .d.ts, etc.)
    stem = re.sub(r"\.[a-zA-Z0-9]+$", "", base)
    stem = re.sub(r"\.(?:d|test|spec|stories|gen)$", "", stem, flags=re.I)
    cleaned = COUNTERPART_SUFFIX_RE.sub("", stem).lower()
    if cleaned in GENERIC_FILENAMES or len(cleaned) <= 2:
        # Use nearest meaningful parent directory (skipping [id] or (app) route groups)
        for parent in reversed(parts[:-1]):
            p_clean = re.sub(r"^[\[(]|[\])]$", "", parent).lower()
            if p_clean and p_clean not in ("src", "app", "lib", "pkg", "internal", "cmd", "api", "v1", "v2", "tests", "test"):
                return p_clean
    return cleaned or base.lower()


def top_dir_key(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    return "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "")


def bundle_review_files(
    files: list[dict],
    max_files: int = 10,
    max_lines: int = 1800,
) -> list[list[dict]]:
    """Deterministically pack reviewable files into semantic bundles."""
    if not files:
        return []
    total_lines = sum(f["changed_lines"] for f in files)
    if len(files) <= max_files and total_lines <= max_lines:
        return [files]

    families: dict[str, list[dict]] = {}
    for f in files:
        families.setdefault(stem_family_key(f["path"]), []).append(f)

    # Split any oversized single family into slices of <= max_files sorted by path
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
        for item in chunk if len(chunk) == 1 else [None]:
            pass
        chunk_lines = sum(x["changed_lines"] for x in chunk)
        if cur_bundle and (len(cur_bundle) + len(chunk) > max_files or cur_lines + chunk_lines > max_lines):
            # If chunk itself has multiple files exceeding max_lines, pack file-by-file
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
            bundles.append(cur_bundle)
            cur_bundle = []
            cur_lines = 0

        if not cur_bundle and len(chunk) > 1 and chunk_lines > max_lines:
            for single in chunk:
                s_lines = single["changed_lines"]
                if cur_bundle and (len(cur_bundle) + 1 > max_files or cur_lines + s_lines > max_lines):
                    bundles.append(cur_bundle)
                    cur_bundle = []
                    cur_lines = 0
                cur_bundle.append(single)
                cur_lines += s_lines
        else:
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
    """Return (total_wall_seconds, per_bundle_attempt_seconds)."""
    parallel = max(1, min(max_parallel, max(1, num_bundles)))
    waves = max(1, math.ceil(max(1, num_bundles) / parallel))
    raw = (timeout_input or "auto").strip().lower()
    if raw in ("", "auto"):
        total_min = min(85, max(20, 15 + 5 * waves + total_lines // 800))
    else:
        try:
            total_min = max(5, int(raw))
        except ValueError:
            total_min = 30
    total_sec = total_min * 60
    per_bundle_sec = min(25 * 60, max(8 * 60, int((total_sec / waves) * 0.85)))
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
    for idx, candidate in enumerate(model_candidates):
        if not candidate:
            continue
        remaining = deadline_monotonic - time.monotonic()
        if remaining < 25:
            sys.stderr.write(f"[{label}] Timeout budget exhausted before attempt {idx + 1}\n")
            break
        this_timeout = min(attempt_timeout_sec, max(25, int(remaining)))

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

        if idx + 1 < len(model_candidates) and (deadline_monotonic - time.monotonic()) > 40:
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

    for c in candidates:
        body_text = "\n".join(c["body_lines"]).strip()
        full_path = os.path.join(worktree_root, c["path"])
        if os.path.isfile(full_path):
            flines = open(full_path, encoding="utf-8", errors="replace").read().splitlines()
            body_text = post_review.validate_suggestion_against_file(body_text, flines, c["start"], c["end"])
        c["body_text"] = body_text

        sev = "medium"
        parts = [p.strip() for p in re.split(r"\s*·\s*", c["rest"], maxsplit=1) if p.strip()]
        if parts and parts[0].lower() in SEVERITY_RANK:
            sev = parts[0].lower()
        c["severity"] = sev

    return {
        "verdict": verdict,
        "effort": effort_num,
        "security_attention": sec_attn,
        "intro_lines": intro_lines,
        "candidates": candidates,
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


def apply_judge_decisions(candidates: list[dict], decisions_obj: dict | None) -> list[dict]:
    """Filter and disarm candidates according to Judge JSON output (fail-open on missing entries)."""
    if not decisions_obj or not isinstance(decisions_obj.get("decisions"), list):
        return candidates

    action_by_idx: dict[int, str] = {}
    for item in decisions_obj["decisions"]:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        act = (item.get("action") or "keep").strip().lower()
        if isinstance(idx, int) and act in ("keep", "drop", "disarm_suggestion"):
            action_by_idx[idx] = act

    kept: list[dict] = []
    for i, c in enumerate(candidates):
        act = action_by_idx.get(i, "keep")
        if act == "drop":
            sys.stdout.write(f"[judge] Dropped candidate [{i}] {c['path']}:{c['start']}-{c['end']}\n")
            continue
        updated = dict(c)
        if act == "disarm_suggestion":
            updated["body_text"] = post_review.disarm_suggestion(updated["body_text"])
        kept.append(updated)
    return kept


def build_judge_input(candidates: list[dict], judge_prompt: str, worktree_root: str = ".") -> str:
    blocks = [judge_prompt.strip(), "", "## Candidate Findings to Verify", ""]
    for i, c in enumerate(candidates):
        loc = f"{c['path']}:{c['start']}-{c['end']}" if c["start"] != c["end"] else f"{c['path']}:{c['start']}"
        full_path = os.path.join(worktree_root, c["path"])
        snippet_lines = []
        if os.path.isfile(full_path):
            flines = open(full_path, encoding="utf-8", errors="replace").read().splitlines()
            lo = max(1, c["start"] - 10)
            hi = min(len(flines), c["end"] + 10)
            for ln_no in range(lo, hi + 1):
                marker = ">>" if c["start"] <= ln_no <= c["end"] else "  "
                snippet_lines.append(f"{marker} L{ln_no:<5d} {flines[ln_no - 1]}")
        else:
            snippet_lines.append("(file does not exist in working tree)")

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
) -> str:
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (SEVERITY_RANK.get(pair[1].get("severity", "medium"), 1), pair[0]),
    )
    selected = [c for _, c in ranked[:max_findings]]

    if not selected:
        verdict = "looks good"
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
    if not secret:
        return text
    return text.replace(secret, "[redacted]")


def main() -> int:
    temp = os.environ.get("RUNNER_TEMP", "/tmp")
    api_key = os.environ.get("PI_API_KEY") or ""
    api_key_env = os.environ.get("PI_API_KEY_ENV") or "OPENCODE_API_KEY"
    if not api_key:
        sys.stderr.write("PI_API_KEY is empty\n")
        return 1
    os.environ[api_key_env] = api_key

    primary_model = os.environ.get("PI_MODEL") or "opencode-go/deepseek-v4.1-flash"
    fallback_model = os.environ.get("PI_FALLBACK_MODEL") or ""
    model_candidates = [m for m in (primary_model, primary_model, fallback_model) if m]
    thinking = os.environ.get("PI_THINKING") or "high"
    review_tools = os.environ.get("PI_REVIEW_TOOLS") or "read,grep,find,ls"
    timeout_input = os.environ.get("PI_REVIEW_TIMEOUT_MINUTES") or "auto"
    max_parallel = max(1, int(os.environ.get("PI_MAX_PARALLEL_BUNDLES") or "3"))
    judge_enabled = (os.environ.get("PI_JUDGE_PASS") or "true").strip().lower() == "true"

    brief_template_path = os.path.join(temp, "brief-template.md")
    rubric_path = os.path.join(temp, "rubric.md")
    diff_path = os.path.join(temp, "diff.patch")
    meta_path = os.path.join(temp, "pr-meta.json")
    judge_prompt_path = os.path.join(temp, "pi-action", "prompts", "judge.md")

    brief_template = open(brief_template_path, encoding="utf-8").read()
    diff_text = open(diff_path, encoding="utf-8", errors="replace").read() if os.path.isfile(diff_path) else ""
    meta = json.loads(open(meta_path, encoding="utf-8").read()) if os.path.isfile(meta_path) else {}

    parsed_files = fetch_pr_context.parse_annotated_files(diff_text)
    if not parsed_files:
        empty_md = (
            "## Review by Pi\n\n"
            "**Verdict:** looks good\n"
            "**Effort:** 1 · **Security:** No issues\n\n"
            "No reviewable source files changed in this diff (only lockfiles, generated files, or deletions).\n"
        )
        open("review.md", "w", encoding="utf-8").write(empty_md)
        print("Skipped LLM call: 0 reviewable files after filtering.")
        return 0

    bundles = bundle_review_files(parsed_files)
    total_lines = sum(f["changed_lines"] for f in parsed_files)
    total_budget_sec, per_bundle_sec = compute_timeout_budget(
        timeout_input, len(bundles), total_lines, max_parallel
    )
    deadline = time.monotonic() + total_budget_sec
    print(
        f"Review plan: {len(parsed_files)} files ({total_lines} changed lines) -> "
        f"{len(bundles)} bundle(s), parallel={min(max_parallel, len(bundles))}, "
        f"budget={total_budget_sec // 60}m (per-bundle={per_bundle_sec // 60}m)."
    )

    def run_bundle(idx: int, b_files: list[dict]) -> tuple[int, str | None]:
        b_paths = {f["path"] for f in b_files}
        siblings = [f for f in parsed_files if f["path"] not in b_paths]
        b_diff_path = os.path.join(temp, f"diff-numbered-b{idx}.patch")
        label = f"Bundle {idx + 1}/{len(bundles)}" if len(bundles) > 1 else ""
        ctx_md = fetch_pr_context.format_pr_context(
            meta.get("graphql") or {},
            diff_text=diff_text,
            incremental_diff_text=meta.get("incremental_diff") or "",
            last_reviewed_sha=meta.get("last_reviewed_sha"),
            annotated_diff_path=b_diff_path,
            bundle_files=b_files,
            sibling_files=siblings if len(bundles) > 1 else None,
            bundle_label=label,
        )
        t_template = brief_template
        if len(bundles) > 1 and idx > 0:
            # Only Bundle 1 synthesizes `<pr_description>` so parallel workers don't race
            t_template = re.sub(
                r"Finally, check the `Current PR Description`.*",
                "",
                t_template,
                flags=re.S,
            ).strip()
        full_prompt = f"{t_template}\n\n{ctx_md}" if ctx_md else t_template
        out = invoke_pi(
            prompt_text=full_prompt,
            model_candidates=model_candidates,
            thinking=thinking,
            tools_arg=review_tools,
            rubric_path=rubric_path,
            attempt_timeout_sec=per_bundle_sec,
            deadline_monotonic=deadline,
            label=f"bundle-{idx + 1}/{len(bundles)}",
            require_verdict=True,
        )
        return idx, out

    results_by_idx: dict[int, str | None] = {}
    workers = min(max_parallel, len(bundles))
    if workers <= 1:
        for i, b in enumerate(bundles):
            _, out = run_bundle(i, b)
            results_by_idx[i] = out
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(run_bundle, i, b) for i, b in enumerate(bundles)]
            for fut in concurrent.futures.as_completed(futs):
                i, out = fut.result()
                results_by_idx[i] = out

    succeeded = [i for i in range(len(bundles)) if results_by_idx.get(i)]
    failed = [i for i in range(len(bundles)) if not results_by_idx.get(i)]
    if not succeeded:
        sys.stderr.write("All review bundles failed.\n")
        return 1

    all_candidates: list[dict] = []
    max_effort = 1
    any_sec = False
    pr_desc: str | None = None
    notes: list[str] = []

    for i in succeeded:
        parsed = parse_raw_candidates(results_by_idx[i] or "")
        all_candidates.extend(parsed["candidates"])
        max_effort = max(max_effort, parsed["effort"])
        any_sec = any_sec or parsed["security_attention"]
        if i == 0 and parsed["pr_description"]:
            pr_desc = parsed["pr_description"]
        if len(bundles) == 1 and parsed["intro_lines"] and not parsed["candidates"]:
            notes.extend(parsed["intro_lines"])

    if failed:
        missed_paths = [f["path"] for i in failed for f in bundles[i]][:8]
        missed_str = ", ".join(f"`{p}`" for p in missed_paths)
        notes.append(
            f"> ⚠️ **Partial coverage:** reviewed {len(succeeded)}/{len(bundles)} file bundles within the timeout budget "
            f"(skipped/timed out on: {missed_str})."
        )

    # Independent Judge / Fact-Checker pass when findings exist and time remains
    if judge_enabled and all_candidates and os.path.isfile(judge_prompt_path) and (deadline - time.monotonic()) > 45:
        judge_prompt = open(judge_prompt_path, encoding="utf-8").read()
        judge_input = build_judge_input(all_candidates, judge_prompt)
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
    )
    final_md = redact_secret(final_md, api_key)
    with open("review.md", "w", encoding="utf-8") as fh:
        fh.write(final_md)
    print(f"Wrote review.md ({len(final_md)} bytes, {min(len(all_candidates), max_findings)} findings).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
