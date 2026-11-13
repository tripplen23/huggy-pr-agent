---
description: Run Huggy Engine v3 code review on local git changes before opening a PR
argument-hint: "[base-ref]"
---
Call `huggy_prepare_diff` (with `baseRef: "${1:-}"` if provided) to generate the `L<num>` line-numbered diff and collect path-scoped rules.

Then execute Huggy's Engine v3 review protocol:
1. Form concrete hypotheses from the `L<num>` diff first — zero exploratory tool calls.
2. Batch all `grep` / `find` symbol and caller lookups in one parallel turn.
3. Batch narrow `read` calls (`offset` + `limit`) in one parallel turn.
4. Review like the senior engineer on call (**correct, safe, holds under expected load, tested, fast, lean**):
   - Concrete trigger case required ("no case, no finding") — zero hedging or rambling.
   - Prefer the smallest fix (deleting/reusing existing repo helpers over adding abstractions); respect documented `shortcut:` / `ponytail:` limits.
   - Report `High` / `Medium` defects (`confidence >= 80`) with exact `path:line` and concrete fixes.
   - Group minor `Low` items (`65–79`) separately under **Nitpicks**.
   - Drop anything `< 65` (pre-existing untouched code, style opinions, or ungrounded speculation).
