#!/usr/bin/env python3
"""Unit tests for scripts/post_review.py."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import post_review  # noqa: E402


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

    def test_missing_verdict_raises(self):
        with self.assertRaises(ValueError):
            post_review.parse_review_markdown("No verdict here", {})


if __name__ == "__main__":
    unittest.main()
