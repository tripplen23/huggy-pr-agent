# SOTA AI Pull Request Review Research (2025–2026)

Primary-source technical research across **GitHub Copilot Code Review**, **Anthropic Claude Code Review** (`claude-plugins-official/plugins/code-review` & `claude-code-security-review`), **Alibaba Open Code Review (`ocr`)**, **CodeRabbit**, **Cursor BugBot**, **Greptile v5**, **Graphite Diamond**, and the three live benchmarks (**Martian Code Review Bench**, **MacroscopeBench**, **SWR-Bench**).

---

## 1. Benchmark Reality Check: Precision vs. Recall Frontier

Live 2025–2026 benchmarks reveal an explicit trade-off between **recall** (catching bugs) and **precision** (avoiding developer-annoying noise):

- **Martian Code Review Bench** (1,050 scored PRs, offline + 200k+ online PRs; [leaderboard](https://codereview.withmartian.com/?mode=offline), [methodology](https://github.com/withmartian/code-review-benchmark/blob/main/methodology/full.md)):
  - High-recall tools often suffer low inline precision when they post minor suggestions directly on the diff: **GitHub Copilot** (`R = 66.5%`, `P = 38.5%`), **CodeRabbit** (`R = 59.5%`, `P = 32.6%`), **Qodo Deep** (`R = 64.6%`, `P = 67.1%`).
  - Ultra-conservative tools sit at the opposite extreme: **Graphite Diamond** (`P = 100%`, `R = 7.6%`).
  - **Crucial Martian finding (§5):** Precision is ill-defined without a repository specification; Martian explicitly grades both reviewers and judges against the repository's **`AGENTS.md` / `CLAUDE.md`**.
- **MacroscopeBench** (12,000+ backtracked real bug commits + 51 bug-free control commits; [blog](https://macroscope.com/blog/macroscopebench)):
  - Penalizes style/subjective optimization comments on diff lines as **false positives**.
  - Demonstrates run-to-run variance of several percentage points on identical diffs/prompts.
- **SWR-Bench** (1,000 GitHub PRs; [arXiv:2509.01494](https://arxiv.org/abs/2509.01494)):
  - Multi-review aggregation across independent passes + deduplication boosts F1 by up to **+43.67%**.

---

## 2. Architecture Comparison Across Top Systems

| Mechanism | GitHub Copilot | Anthropic Claude Review | Alibaba OCR (`ocr`) | CodeRabbit | Cursor BugBot | Greptile v5 | **`huggy-pr-agent` (v1.6)** |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Deterministic × Agent split** | Static tools + `grep`/`glob`/`view` | Plugin + validator subagents | **6-gate filter + Go worker pool** | Sandbox + 50+ linters as hints | Agentic loop + validator | Repo graph + hypothesis swarm | **Python pre/post guards + Pi** |
| **Rule pinning (anti-injection)** | Head branch *(vulnerable)* | **Base branch only** (`REVIEW.md`) | Base/config chain | Config + repo docs | **Base branch only** (`bugbot.yaml`) | `.greptile/` cascade | **Base branch (`git show base:`)** |
| **Path-scoped rules** | `.github/instructions/*.instructions.md` (`applyTo`) | Subdirectory `CLAUDE.md` / `REVIEW.md` upward walk | Per-glob `{path, rule}` | `path_instructions` + `ast-grep` | Subdirectory `BUGBOT.md` upward walk | Per-dir `.greptile/rules.md` | Root `AGENTS.md` / `CLAUDE.md` *(upgradeable!)* |
| **Finder vs. Judge separation** | Internal threshold (29% silent) | Parallel finders → **0–100 scorer (`≥80`)** + hard exclusions | `MAIN_TASK` → `REVIEW_FILTER_TASK` | Ensemble → Judge filter | **Aggressive finder** → Validator model | Hypothesis agents → addressed-rate pruning | Finder → Judge pass *(currently permissive)* |
| **Nitpick handling** | Grouped comments | 🔴 Important / 🟡 Nit / ⚪ Pre-existing | Strict precision filter | **`chill` mode collapses nits in summary** | Drops style/docs categories | Custom exclusions | Inline `Low` badge *(upgradeable!)* |
| **Model inversion (reviewer ≠ author)** | Multi-model (`Max` tier) | Single vendor | Configurable | Multi-model ensemble | Multi-model | **Explicit (+10% bug catch)** | **Supported (e.g. Vertex Gemini reviewing Claude PRs)** |

---

## 3. Seven Transferable Engineering Mechanisms for `huggy-pr-agent` v2/v3

### 1. Cursor BugBot's Prompt Inversion + Anthropic's `≥ 80` Confidence Judge
- **Sources:** [Cursor — Building BugBot](https://cursor.com/blog/building-bugbot), [Anthropic `/code-review` prompt](https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/plugins/code-review/commands/code-review.md), [Anthropic `claude-code-security-review`](https://github.com/anthropics/claude-code-security-review).
- **Insight:** Telling the *finder* prompt to "be conservative" makes agentic loops timid on subtle cross-file bugs. Cursor shifted to **investigative finder prompts** and moved precision enforcement to the **Judge pass**. Meanwhile, Anthropic's Judge scores each candidate `0–100` (keeping only `≥ 80`), receives the exact `CLAUDE.md` / `AGENTS.md` rubric to verify cited rules actually exist, and enforces an explicit **Hard Exclusion List** (no pre-existing untouched bugs, no linter/formatter trivia, no speculative "what-ifs" without a concrete broken input).

### 2. CodeRabbit's Two-Channel `chill` vs. `assertive` Nitpick Separation
- **Sources:** [CodeRabbit Configuration](https://docs.coderabbit.ai/reference/configuration), [CodeRabbit `llms-full.txt`](https://docs.coderabbit.ai/llms-full.txt).
- **Insight:** Posting `Low` / nitpick comments inline on the diff is the #1 driver of review fatigue and low benchmark precision. Under `chill` (default), only **`High` and `Medium` verified defects** become inline diff threads; **`Low` / Nitpick** items are placed inside a collapsed `<details><summary>🧹 Nitpicks (N)</summary>` section in the sticky summary comment.

### 3. GitHub Copilot's "Review-Shaped" Tool Discipline (−20% Cost, Zero Quality Loss)
- **Source:** [GitHub Blog — Better tools made Copilot code review worse](https://github.blog/ai-and-ml/github-copilot/better-tools-made-copilot-code-review-worse-heres-how-we-actually-improved-it/).
- **Insight:** Every tool output enters working context permanently. Giving `grep`/`find`/`read` without ordering causes noisy `search → read → search → read` browsing loops. Encoding a strict 4-step sequence — *(1) form specific questions from the diff first, (2) batch `grep`/`find` symbol & caller discovery in one turn, (3) batch narrow `read` calls with `offset`/`limit` only on needed ranges, (4) one simpler escaped retry on `grep` failure* — cut Copilot's review cost by ~20% without losing recall.

### 4. Base-Branch Path-Scoped Rule Resolution (`AGENTS.md` Hierarchy + `.instructions.md`)
- **Sources:** [Anthropic Customize Docs](https://code.claude.com/docs/en/code-review-customize), [GitHub Custom Instructions](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/add-custom-instructions/add-repository-instructions), [Cursor BugBot Docs](https://cursor.com/docs/bugbot).
- **Insight:** Walk `git ls-tree` on the **`base` branch** (preserving prompt-injection immunity!) to collect:
  1. Root `AGENTS.md` / `CLAUDE.md` / `REVIEW.md` / `.github/copilot-instructions.md`
  2. Ancestor directory `AGENTS.md` / `CLAUDE.md` files along each changed file's path (monorepo scoping)
  3. Matching `.github/instructions/*.instructions.md` (`applyTo:` globs, honoring `excludeAgent: "code-review"`).

### 5. Alibaba OCR's `.gitattributes` `linguist-generated` Gate & Content-Anchored Line Relocation
- **Sources:** [Alibaba `open-code-review` Architecture](https://open-codereview.ai/docs/architecture), [Graphite Customization](https://graphite.com/docs/ai-review-customization).
- **Insight:**
  1. Honor `linguist-generated` / `linguist-vendored` in `base:.gitattributes` (plus `*.pb.go`, `*.gen.*`, `dist/`, `vendor/`) before bundling so generated files neither consume review budget nor attract false comments.
  2. When a finding's `start..end` drifts slightly, match the suggestion/identifier context lines against the diff hunk with a sliding window before falling back to disarming.

### 6. Actionable Failed-CI Log Snippets in `<pr_context>`
- **Source:** [CodeRabbit CI/CD Pipeline Analysis](https://docs.coderabbit.ai/pr-reviews/cicd-pipeline-analysis).
- **Insight:** `fetch_pr_context.py` already inspects `statusCheckRollup`. When a GitHub Actions `CheckRun` on the PR head has `conclusion == "FAILURE"`, fetching the tail / `##[error]` lines (bounded to ~3 KB) lets the review agent connect a red CI check directly to the offending diff line.

### 7. Optional Multi-Lens Aggregation (`review_depth: standard | deep`)
- **Sources:** [SWR-Bench](https://arxiv.org/abs/2509.01494), [Anthropic Code Review Levels](https://code.claude.com/docs/en/code-review), [Greptile Model Inversion](https://www.greptile.com/blog/model-inversion).
- **Insight:** Running 2 complementary passes in `deep` mode (Pass 1: *Logic, Callers, State & Concurrency*; Pass 2: *Security, Trust Boundaries & `AGENTS.md` Invariants*) — optionally across two distinct model families — and merging through the `≥ 80` Judge captures up to +35–43% more real bugs while keeping precision constant.
