#!/usr/bin/env python3
"""Unit tests for scripts/post_review.py and scripts/fetch_pr_context.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_pr_context  # noqa: E402
import post_progress  # noqa: E402
import post_review  # noqa: E402
import run_review  # noqa: E402


class TestPostReview(unittest.TestCase):
    def test_parse_diff_hunks_handles_spaces_and_quoted_paths(self):
        diff = (
            "diff --git a/docs/How It Works.md b/docs/How It Works.md\n"
            "--- a/docs/How It Works.md\t\n"
            "+++ b/docs/How It Works.md\t\n"
            "@@ -10,2 +10,4 @@\n"
            " line 10\n"
            "+line 11\n"
            "+line 12\n"
            " line 13\n"
            'diff --git "a/docs/A \\342\\200\\224 B.md" "b/docs/A \\342\\200\\224 B.md"\n'
            '--- "a/docs/A \\342\\200\\224 B.md"\n'
            '+++ "b/docs/A \\342\\200\\224 B.md"\n'
            "@@ -1,1 +1,2 @@\n"
            " first\n"
            "+second\n"
        )
        hunks = post_review.parse_diff_hunks(diff)
        self.assertEqual(hunks[("docs/How It Works.md", 11)], 1)
        self.assertEqual(hunks[("docs/How It Works.md", 12)], 1)
        self.assertEqual(hunks[("docs/A — B.md", 2)], 2)

    def test_parse_review_renders_colored_badges_and_keeps_suggestions(self):
        diff = (
            "diff --git a/src/app.ts b/src/app.ts\n"
            "--- a/src/app.ts\n"
            "+++ b/src/app.ts\n"
            "@@ -20,3 +20,5 @@\n"
            " const a = 1;\n"
            "+const b = 2;\n"
            "+const c = 3;\n"
            " const d = 4;\n"
        )
        hunks = post_review.parse_diff_hunks(diff)
        md = """## Review by Pi

**Verdict:** needs changes

### `src/app.ts:21-22` · High · Guard the boundary before writing

Explain why in two sentences.

```suggestion
const b = check(2);
const c = check(3);
```
"""
        verdict, intro, findings = post_review.parse_review_markdown(md, hunks)
        self.assertEqual(verdict, "**Verdict:** needs changes")
        self.assertEqual(intro, [])
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["start"], 21)
        self.assertEqual(f["end"], 22)
        self.assertTrue(f["same_hunk"])
        self.assertIn("https://img.shields.io/badge/High-dc2626", f["comment"])
        self.assertIn("```suggestion", f["comment"])
        self.assertNotIn("```suggestion", f["summary"])

    def test_merge_pr_description_preserves_author_notes(self):
        # 1. Empty existing body -> wrapped block
        merged1 = post_review.merge_pr_description("", "## Summary\n- Initial")
        self.assertIn("<!-- pi-description-start -->", merged1)
        self.assertIn("## Summary\n- Initial", merged1)

        # 2. Existing author notes around markers -> author notes outside markers kept
        with_notes = "Fixes #42\n\n" + merged1
        merged2 = post_review.merge_pr_description(with_notes, "## Summary\n- V2")
        self.assertTrue(merged2.startswith("Fixes #42"))
        self.assertNotIn("- Initial", merged2)
        self.assertIn("- V2", merged2)

        # 3. Inline `<pr_description>` inside a review finding is NOT treated as a block tag
        inline_mention = (
            "## Review by Pi\n\n**Verdict:** nits\n\n"
            "### `a.py:1` · Low · Inline tag mention\n\n"
            "Mentioning `<pr_description>` inline must not truncate this finding.\n\n"
            "<pr_description>\n## Real Block\n</pr_description>\n"
        )
        cleaned, desc = post_review.extract_pr_description(inline_mention)
        self.assertEqual(desc, "## Real Block")
        self.assertIn("Mentioning `<pr_description>` inline must not truncate this finding.", cleaned)

    def test_find_last_reviewed_sha_and_incremental_context(self):
        comments = [
            {"author": {"login": "mallory"}, "body": "<!-- pi-review sha=deadbeef1234 -->\nspoofed"},
            {"author": {"login": "github-actions"}, "body": "<!-- pi-review sha=a1b2c3d4e5f6 -->\n## Review by Pi"},
        ]
        self.assertEqual(fetch_pr_context.find_last_reviewed_sha(comments), "a1b2c3d4e5f6")
        sample = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "title": "feat: test",
                        "body": "Body",
                        "baseRefName": "main",
                        "headRefName": "feat",
                        "author": {"login": "alice"},
                        "commits": {"nodes": []},
                        "comments": {"nodes": comments},
                        "reviewThreads": {"nodes": []},
                    }
                }
            }
        }
        inc_diff = (
            "diff --git a/src/b.ts b/src/b.ts\n"
            "--- a/src/b.ts\n"
            "+++ b/src/b.ts\n"
            "@@ -1,1 +1,2 @@\n"
            " const a = 1;\n"
            "+const b = 2;\n"
        )
        ctx = fetch_pr_context.format_pr_context(
            sample,
            incremental_diff_text=inc_diff,
            last_reviewed_sha="a1b2c3d4e5f6",
        )
        self.assertIn("Incremental Changes Since Last Review (`a1b2c3d..HEAD`, 1 files)", ctx)
        self.assertIn("L2     +const b = 2;", ctx)

    def test_cross_hunk_range_disarms_suggestion(self):
        diff = (
            "diff --git a/src/app.ts b/src/app.ts\n"
            "--- a/src/app.ts\n"
            "+++ b/src/app.ts\n"
            "@@ -10,2 +10,2 @@\n"
            "-old10\n"
            "+new10\n"
            " line11\n"
            "@@ -30,2 +30,2 @@\n"
            "-old30\n"
            "+new30\n"
            " line31\n"
        )
        hunks = post_review.parse_diff_hunks(diff)
        md = """## Review by Pi

**Verdict:** nits

### `src/app.ts:10-30` · Low · Cross-hunk range

Body text.

```suggestion
replacement
```
"""
        _, _, findings = post_review.parse_review_markdown(md, hunks)
        f = findings[0]
        self.assertFalse(f["same_hunk"])
        self.assertEqual(f["eff_start"], 30)
        disarmed = post_review.disarm_suggestion(f["comment"])
        self.assertNotIn("```suggestion", disarmed)

    def test_extract_pr_description(self):
        raw = """## Review by Pi

**Verdict:** looks good

All good.

<pr_description>
## Summary
- Updated description matching the final commits.
</pr_description>
"""
        cleaned, desc = post_review.extract_pr_description(raw)
        self.assertEqual(desc, "## Summary\n- Updated description matching the final commits.")
        self.assertNotIn("<pr_description>", cleaned)
        verdict, intro, findings = post_review.parse_review_markdown(cleaned, {})
        self.assertEqual(verdict, "**Verdict:** looks good")
        self.assertEqual(findings, [])

    def test_format_pr_context_includes_description_and_threads(self):
        sample = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "title": "feat: add socket server",
                        "body": "Initial description",
                        "baseRefName": "master",
                        "headRefName": "feat-socket",
                        "author": {"login": "alice"},
                        "closingIssuesReferences": {
                            "nodes": [
                                {"number": 42, "title": "Socket server must reject unauthenticated frames", "body": "Must refuse frames before hello.", "state": "OPEN"}
                            ]
                        },
                        "commits": {
                            "nodes": [
                                {
                                    "commit": {
                                        "oid": "1234567890",
                                        "messageHeadline": "feat: init",
                                        "messageBody": "Detailed why in commit body.",
                                        "statusCheckRollup": {
                                            "state": "FAILURE",
                                            "contexts": {
                                                "nodes": [
                                                    {"__typename": "CheckRun", "name": "unit-tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
                                                    {"__typename": "StatusContext", "context": "ci/woodpecker/pr/e2e", "state": "FAILURE", "description": "Pipeline failed"},
                                                ]
                                            },
                                        },
                                    }
                                }
                            ]
                        },
                        "comments": {"nodes": []},
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "T1",
                                    "isResolved": True,
                                    "isOutdated": False,
                                    "path": "src/a.ts",
                                    "startLine": 10,
                                    "line": 12,
                                    "comments": {
                                        "nodes": [
                                            {"databaseId": 101, "author": {"login": "github-actions"}, "body": "<!-- pi --> High issue"},
                                            {"databaseId": 102, "author": {"login": "alice"}, "body": "Intentional per spec"},
                                        ]
                                    },
                                }
                            ]
                        },
                    }
                }
            }
        }
        diff = (
            "diff --git a/pnpm-lock.yaml b/pnpm-lock.yaml\n"
            "--- a/pnpm-lock.yaml\n"
            "+++ b/pnpm-lock.yaml\n"
            "@@ -1,1 +1,2 @@\n"
            " lock\n"
            "+more\n"
            "diff --git a/src/a.ts b/src/a.ts\n"
            "--- a/src/a.ts\n"
            "+++ b/src/a.ts\n"
            "@@ -10,2 +10,3 @@\n"
            " const x = 1;\n"
            "+const y = 2;\n"
            " const z = 3;\n"
        )
        ctx = fetch_pr_context.format_pr_context(sample, comment_id=102, diff_text=diff)
        self.assertIn("Linked Issues (Requirements & Acceptance Criteria)", ctx)
        self.assertIn("Issue #42: Socket server must reject unauthenticated frames", ctx)
        self.assertIn("Must refuse frames before hello.", ctx)
        self.assertIn("Detailed why in commit body.", ctx)
        self.assertIn("CI & Test Status on Latest Commit", ctx)
        self.assertIn("`unit-tests`: **SUCCESS**", ctx)
        self.assertIn("`ci/woodpecker/pr/e2e`: **FAILURE** (Pipeline failed)", ctx)
        self.assertIn("Review Files Checklist (`<review_files>`, 1 files)", ctx)
        self.assertNotIn("pnpm-lock.yaml", ctx)
        self.assertIn("L11    +const y = 2;", ctx)
        self.assertIn("Target Review Thread for This Command", ctx)
        self.assertIn("Previously Resolved Review Threads", ctx)
        self.assertIn("Intentional per spec", ctx)

    def test_nearest_hunk_relocation_snaps_and_disarms_suggestion(self):
        diff = (
            "diff --git a/src/app.ts b/src/app.ts\n"
            "--- a/src/app.ts\n"
            "+++ b/src/app.ts\n"
            "@@ -20,2 +20,3 @@\n"
            " const a = 1;\n"
            "+const b = 2;\n"
            " const c = 3;\n"
        )
        hunks = post_review.parse_diff_hunks(diff)
        md = """## Review by Pi

**Verdict:** nits

### `src/app.ts:25` · Low · Slightly drifted line

Body text.

```suggestion
const c = 4;
```
"""
        _, _, findings = post_review.parse_review_markdown(md, hunks)
        f = findings[0]
        self.assertTrue(f["relocated"])
        self.assertEqual(f["end"], 22)
        self.assertEqual(f["eff_start"], 22)

    def test_missing_verdict_raises(self):
        with self.assertRaises(ValueError):
            post_review.parse_review_markdown("No verdict here", {})

    def test_validate_suggestion_guards_noop_placeholder_and_truncation(self):
        file_lines = [
            "if (existingUser) {",
            "  if (existingUser.banned) return { error: 'restricted' };",
            "  targetUserId = existingUser.id;",
            "  await db.updateTable('user').execute();",
            "}",
        ]
        # 1. Valid balanced replacement -> kept
        valid_md = "Fix:\n\n```suggestion\nif (existingUser) {\n  return ok();\n}\n```"
        self.assertIn("```suggestion", post_review.validate_suggestion_against_file(valid_md, file_lines, 1, 5))

        # 2. Byte-identical no-op -> disarmed
        noop_md = "Fix:\n\n```suggestion\n" + "\n".join(file_lines) + "\n```"
        self.assertNotIn("```suggestion", post_review.validate_suggestion_against_file(noop_md, file_lines, 1, 5))

        # 3. Ellipsis placeholder -> disarmed
        placeholder_md = "Fix:\n\n```suggestion\nif (existingUser) {\n  // ... rest unchanged\n}\n```"
        self.assertNotIn("```suggestion", post_review.validate_suggestion_against_file(placeholder_md, file_lines, 1, 5))

        # 4. Truncated unclosed block (including 2-line ranges & multi-block bodies) -> disarmed
        two_line = ["if (ready) {", "}"]
        truncated_2 = "Fix:\n\n```suggestion\nif (ready) {\n  return;\n```"
        self.assertNotIn("```suggestion", post_review.validate_suggestion_against_file(truncated_2, two_line, 1, 2))

        multi_block = valid_md + "\n\nSecond bad block:\n\n```suggestion\nif (existingUser) {\n  // ... rest\n```"
        checked_multi = post_review.validate_suggestion_against_file(multi_block, file_lines, 1, 5)
        self.assertEqual(checked_multi.count("```suggestion"), 1)

        # 5. Path traversal / outside-worktree protection
        self.assertIsNone(post_review.safe_worktree_file(".", "/proc/self/environ"))
        self.assertIsNone(post_review.safe_worktree_file(".", "../../etc/passwd"))
        self.assertIsNone(post_review.safe_worktree_file(".", ".git/config"))

    def test_smart_bundling_keeps_counterparts_and_splits_10k_loc_pr(self):
        # Counterparts share stem_family_key ("inbox" and Python "test_" prefix)
        self.assertEqual(run_review.stem_family_key("app/api/v1/inbox/route.ts"), "inbox")
        self.assertEqual(run_review.stem_family_key("lib/contracts/inbox.ts"), "inbox")
        self.assertEqual(run_review.stem_family_key("lib/outreach/inbox-view.ts"), "inbox")
        self.assertEqual(run_review.stem_family_key("tests/inbox.test.ts"), "inbox")
        self.assertEqual(run_review.stem_family_key("tests/test_post_review.py"), "post_review")
        self.assertEqual(run_review.stem_family_key("scripts/post_review.py"), "post_review")

        # 10,000-line PR across 25 modules splits into bounded bundles
        huge_files = [
            {"path": f"src/mod_{i}/service_{i}.ts", "additions": 350, "deletions": 50, "changed_lines": 400, "annotated": "..."}
            for i in range(25)
        ]
        bundles = run_review.bundle_review_files(huge_files, max_files=10, max_lines=1800)
        self.assertGreaterEqual(len(bundles), 6)
        for b in bundles:
            self.assertLessEqual(len(b), 10)
            self.assertLessEqual(sum(x["changed_lines"] for x in b), 1800)

        # Single 5,000-line file also splits across hunk boundaries
        giant_annotated = "diff --git a/big.ts b/big.ts\n--- a/big.ts\n+++ b/big.ts\n" + "\n".join(
            "@@ -1,1 +1,1000 @@\n" + "\n".join(f"L{ln:<5d} +line {ln}" for ln in range(1, 1001))
            for _ in range(5)
        )
        single_giant = [{"path": "big.ts", "additions": 5000, "deletions": 0, "changed_lines": 5000, "annotated": giant_annotated}]
        giant_bundles = run_review.bundle_review_files(single_giant, max_files=10, max_lines=1800)
        self.assertGreaterEqual(len(giant_bundles), 3)

        # Adaptive timeout scales automatically within the 60m job ceiling (19m..50m)
        small_total, _ = run_review.compute_timeout_budget("auto", num_bundles=1, total_lines=200, max_parallel=3)
        huge_total, huge_bundle = run_review.compute_timeout_budget("auto", num_bundles=len(bundles), total_lines=10000, max_parallel=3)
        explicit_total, _ = run_review.compute_timeout_budget("75", num_bundles=len(bundles), total_lines=10000, max_parallel=3)
        self.assertEqual(small_total, 19 * 60)
        self.assertGreaterEqual(huge_total, 30 * 60)
        self.assertLessEqual(huge_total, 50 * 60)
        self.assertGreaterEqual(huge_bundle, 10 * 60)
        self.assertEqual(explicit_total, 75 * 60)

        # Stripping PR description section preserves trailing extra_instructions
        tpl = "Intro\n\nFinally, check the `Current PR Description` in `<pr_context>`:\n<pr_description>\nx\n</pr_description>\n\nAdditional repository instructions:\nKeep foo."
        stripped = run_review.strip_pr_description_section(tpl)
        self.assertNotIn("<pr_description>", stripped)
        self.assertIn("Additional repository instructions:\nKeep foo.", stripped)

        # Progress banner preserves sha= and embeds pi-reviewing.svg
        prog = post_progress.build_progress_markdown(huge_files[:2], num_bundles=2, last_sha="abc1234567")
        self.assertTrue(prog.startswith("<!-- pi-review sha=abc1234567 -->"))
        self.assertIn("pi-reviewing.svg", prog)
        self.assertIn("2 parallel bundles", prog)

    def test_judge_decisions_and_verdict_recompute(self):
        raw = """## Review by Pi

**Verdict:** needs changes
**Effort:** 4 · **Security:** Needs attention

### `src/a.ts:10-12` · High · Hallucinated missing guard

Guard is missing.

### `src/b.ts:20` · Low · Minor naming nit

```suggestion
const count = 1;
```
"""
        parsed = run_review.parse_raw_candidates(raw)
        self.assertEqual(len(parsed["candidates"]), 2)
        kept = run_review.apply_judge_decisions(
            parsed["candidates"],
            {
                "decisions": [
                    {"index": 0, "action": "drop", "reason": "guard exists at L8"},
                    {"index": 1, "action": "disarm_suggestion", "reason": "incomplete"},
                ]
            },
        )
        self.assertEqual(len(kept), 1)
        self.assertNotIn("```suggestion", kept[0]["body_text"])
        final_md = run_review.assemble_review_markdown(
            candidates=kept,
            effort=parsed["effort"],
            security_attention=parsed["security_attention"],
            extra_notes=[],
            pr_description=None,
        )
        # High was dropped, only Low remains -> Verdict automatically adjusts to `nits`!
        self.assertIn("**Verdict:** nits", final_md)

        # If all findings are dropped -> Verdict automatically adjusts to `looks good`!
        clean_md = run_review.assemble_review_markdown(
            candidates=[],
            effort=2,
            security_attention=True,
            extra_notes=[],
            pr_description=None,
        )
        self.assertIn("**Verdict:** looks good", clean_md)
        self.assertIn("**Security:** No issues", clean_md)

        # Partial failure with 0 findings must NOT claim `looks good`
        partial_md = run_review.assemble_review_markdown(
            candidates=[],
            effort=2,
            security_attention=False,
            extra_notes=["> ⚠️ Partial coverage"],
            pr_description=None,
            has_partial_failure=True,
        )
        self.assertIn("**Verdict:** incomplete (partial coverage)", partial_md)


if __name__ == "__main__":
    unittest.main()
