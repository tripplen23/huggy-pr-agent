#!/usr/bin/env python3
"""Post or update an animated 'Huggy is reviewing...' progress card on the PR sticky comment."""

from __future__ import annotations

import json
import os
import sys
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import fetch_pr_context  # noqa: E402
import post_review  # noqa: E402
import run_review  # noqa: E402

DEFAULT_SVG_URL = "https://raw.githubusercontent.com/tripplen23/huggy-pr-agent/v1/assets/huggy-reviewing.svg"


def build_progress_markdown(
    files: list[dict],
    num_bundles: int,
    last_sha: str | None = None,
    svg_url: str = DEFAULT_SVG_URL,
    previous_body: str = "",
) -> str:
    marker = f"<!-- pi-review sha={last_sha} -->" if last_sha else post_review.MARKER
    total_lines = sum(f["changed_lines"] for f in files)
    bundle_note = (
        f" across **{num_bundles} parallel bundles**" if num_bundles > 1 else ""
    )
    scope_items = [
        f"- `{f['path']}` (+{f['additions']} / -{f['deletions']})" for f in files[:30]
    ]
    if len(files) > 30:
        scope_items.append(f"- *(...and {len(files) - 30} more files)*")

    lines = [
        marker,
        "> [!NOTE]",
        f'> <img src="{svg_url}" alt="Huggy is reviewing this PR" width="208" />',
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
    if prev_clean and "Currently reviewing " not in prev_clean:
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


def main() -> int:
    repo = os.environ.get("REPO") or ""
    pr_raw = os.environ.get("PR") or ""
    token = os.environ.get("GH_TOKEN") or ""
    temp = os.environ.get("RUNNER_TEMP", "/tmp")
    svg_url = os.environ.get("PI_PROGRESS_SVG_URL") or DEFAULT_SVG_URL

    if not (repo and pr_raw.isdigit() and token):
        return 0
    pr = int(pr_raw)

    diff_path = os.path.join(temp, "diff.patch")
    meta_path = os.path.join(temp, "pr-meta.json")
    diff_text = open(diff_path, encoding="utf-8", errors="replace").read() if os.path.isfile(diff_path) else ""
    files = fetch_pr_context.parse_annotated_files(diff_text)
    if not files:
        return 0

    meta = json.loads(open(meta_path, encoding="utf-8").read()) if os.path.isfile(meta_path) else {}
    last_sha = meta.get("last_reviewed_sha")
    bundles = run_review.bundle_review_files(files)

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
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None

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
