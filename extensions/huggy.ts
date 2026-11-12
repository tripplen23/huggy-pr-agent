/**
 * Huggy PR Agent — Pi Extension
 *
 * Brings Huggy's Engine v3 deterministic × agent code review and CI workflow
 * setup/sync directly into interactive and headless Pi sessions:
 *   - `/huggy review [base-ref]` — review current branch/worktree changes locally
 *   - `/huggy setup`             — install `.github/workflows/huggy.yml` in this repo
 *   - `/huggy sync`              — upgrade an existing workflow & 1-click Dependabot config
 *   - `huggy_prepare_diff` tool  — pre-number `L<num>` diffs, filter generated files, and collect path-scoped rules
 */

import { execFile } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type { AutocompleteItem } from "@earendil-works/pi-tui";
import { Type } from "typebox";

const execFileAsync = promisify(execFile);
const PACKAGE_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SCRIPTS_DIR = join(PACKAGE_ROOT, "scripts");
const INSTALL_DOC = join(PACKAGE_ROOT, "INSTALL.md");

const SUBCOMMANDS: Array<{ value: string; label: string }> = [
	{ value: "review", label: "review — Run Huggy Engine v3 code review on local branch changes" },
	{ value: "review deep", label: "review deep — Dual-lens (Logic/Callers + Security/Spec) local review" },
	{ value: "setup", label: "setup — Configure Huggy PR Agent GitHub Actions in this repository" },
	{ value: "sync", label: "sync — Upgrade existing Huggy/Pi PR workflows & 1-click Dependabot sync" },
];

async function detectBaseRef(cwd: string, explicitBase?: string): Promise<string> {
	if (explicitBase?.trim()) return explicitBase.trim();
	for (const candidate of ["origin/main", "origin/master", "main", "master", "HEAD~1"]) {
		try {
			await execFileAsync("git", ["rev-parse", "--verify", "--quiet", candidate], { cwd });
			return candidate;
		} catch {
			// try next ref
		}
	}
	return "HEAD";
}

export default function huggyExtension(pi: ExtensionAPI) {
	pi.registerTool({
		name: "huggy_prepare_diff",
		label: "Prepare Huggy L-Numbered Diff",
		description:
			"Prepare a deterministic Huggy review payload for local git changes: filters lockfiles and `.gitattributes` `linguist-generated` paths, prefixes every added/context line with `L<num>`, and collects matching path-scoped `AGENTS.md` / `CLAUDE.md` / `.instructions.md` rules.",
		parameters: Type.Object({
			baseRef: Type.Optional(
				Type.String({
					description: "Base git ref to diff against (defaults to origin/main, origin/master, or HEAD).",
				}),
			),
		}),
		async execute(_toolCallId, params, signal, _onUpdate, ctx) {
			const base = await detectBaseRef(ctx.cwd, params.baseRef);
			const pySnippet = `
import json, subprocess, sys
sys.path.insert(0, ${JSON.stringify(SCRIPTS_DIR)})
import fetch_pr_context

base = sys.argv[1]
mb_proc = subprocess.run(["git", "merge-base", base, "HEAD"], capture_output=True, text=True)
diff_target = mb_proc.stdout.strip() if mb_proc.returncode == 0 and mb_proc.stdout.strip() else base
diff_text = subprocess.check_output(["git", "-c", "core.quotePath=false", "diff", diff_target], text=True)

gitattr = ""
try:
    gitattr = open(".gitattributes", encoding="utf-8").read()
except OSError:
    pass
globs = fetch_pr_context.parse_gitattributes_generated(gitattr)
files = fetch_pr_context.parse_annotated_files(diff_text, globs)
paths = [f["path"] for f in files]
scoped = fetch_pr_context.collect_scoped_rules(paths, base_ref="HEAD")
micro = fetch_pr_context.language_micro_rules(paths)

out = [f"# Huggy Prepared Diff (base: {diff_target}, {len(files)} reviewable files)"]
if files:
    out.append("\\n## Review Files Checklist")
    for f in files:
        out.append(f"- \`{f['path']}\` (+{f['additions']} / -{f['deletions']})")
if micro:
    out.append("\\n## Language Checks\\n" + "\\n".join(micro))
if scoped:
    out.append("\\n## Path-Scoped Rules\\n" + scoped)
annotated = "\\n".join(f["annotated"] for f in files)
out.append("\\n## Line-Numbered Diff\\n\`\`\`diff\\n" + annotated[:60000] + "\\n\`\`\`")
print("\\n".join(out))
`;
			const { stdout } = await execFileAsync("python3", ["-c", pySnippet, base], {
				cwd: ctx.cwd,
				maxBuffer: 10 * 1024 * 1024,
				signal,
			});
			return {
				content: [{ type: "text", text: stdout }],
				details: { baseRef: base },
			};
		},
	});

	pi.registerCommand("huggy", {
		description: "Huggy PR Agent: `review [base]`, `review deep [base]`, `setup`, or `sync`",
		getArgumentCompletions: (prefix: string): AutocompleteItem[] | null => {
			const trimmed = prefix.trimStart();
			const matches = SUBCOMMANDS.filter((item) => item.value.startsWith(trimmed));
			return matches.length > 0 ? matches : null;
		},
		handler: async (args, ctx) => {
			const trimmed = args.trim();
			if (trimmed === "setup") {
				pi.sendUserMessage(
					`Read ${INSTALL_DOC} and set up Huggy PR Agent (\`tripplen23/huggy-pr-agent@v1\`) on this repository, tailoring \`setup_cmd\`, \`verify_cmd\`, and \`guarded_paths\` to this project's stack.`,
				);
				return;
			}

			if (trimmed === "sync") {
				pi.sendUserMessage(
					`Read ${INSTALL_DOC} and inspect this repository's existing \`.github/workflows/\` PR review/fix files. Upgrade any outdated \`pi-pr-agent\` or \`huggy-pr-agent\` workflow to the latest canonical \`tripplen23/huggy-pr-agent@v1\` template (preserving repo-specific models, secrets, \`setup_cmd\`, \`verify_cmd\`, and \`guarded_paths\`), and ensure 1-click update sync via \`@v1\` or \`.github/dependabot.yml\` (\`github-actions\` ecosystem) is configured cleanly.`,
				);
				return;
			}

			const isDeep = /^review\s+deep(?:\s|$)/i.test(trimmed) || trimmed.toLowerCase() === "deep";
			const restBase = trimmed
				.replace(/^review(?:\s+deep)?\s*/i, "")
				.replace(/^deep\s*/i, "")
				.trim();
			const baseRef = await detectBaseRef(ctx.cwd, restBase || undefined);

			ctx.ui.notify(
				`Running Huggy ${isDeep ? "deep " : ""}review against ${baseRef}...`,
				"info",
			);

			const deepNote = isDeep
				? "Run a **deep dual-lens audit**: explicitly check BOTH (1) Functional Correctness, Broken Callers & State Integrity AND (2) Security, Auth/Trust Boundaries & AGENTS.md/CLAUDE.md Spec Compliance."
				: "Focus on broken callers/contracts, security/auth boundaries, state integrity, concrete boundary bugs, and AGENTS.md/CLAUDE.md rules.";

			pi.sendUserMessage(
				`Call \`huggy_prepare_diff\` with \`baseRef: "${baseRef}"\` first. Then follow the Review-Shaped Tool Loop: form hypotheses from the \`L<num>\` diff, batch any \`grep\`/\`find\` caller lookups in one wave, batch narrow \`read\` calls (\`offset\` + \`limit\`) in one wave, and apply the >= 80 confidence filter (drop speculative/style/pre-existing noise; group minor 65-79 observations under a collapsed Nitpicks section). ${deepNote}`,
			);
		},
	});
}
