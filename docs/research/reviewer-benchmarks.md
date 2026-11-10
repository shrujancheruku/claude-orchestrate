# How published reviewer benchmarks are built

Research note for [#598](https://github.com/shrujancheruku/claude-orchestrate/issues/598), a child of [map #588](https://github.com/shrujancheruku/claude-orchestrate/issues/588). It feeds the measurement grilling ([#589](https://github.com/shrujancheruku/claude-orchestrate/issues/589)), which designs orchestrate's own escaped-defect record and planted-defect benchmark per review tier.

Read 2026-11-10 from primary sources only: the Human Oversight System (HOS) repository at its then-current `main` (commit `1482a93`, 2026-11-09) plus the HOS GitHub issues the finding cites (#1985, #1455, #1469), and the arXiv full texts of SWE-PRBench (Kumar 2026) and AgenticSCR (Charoenwet et al. 2026). Benchmarks those two papers cite were read from their arXiv abstract pages only, and are marked as such. Every number below names the table, section or comment it comes from, so a later claim can be traced to its owner.

Terms: a **planted** (or seeded) defect is one the benchmark author inserted; a **real** defect was found in the wild and reproduced; **ground truth** is the answer key a run is scored against; **drift** is any change to prompt, model, tooling or context between runs that would make two scores incomparable.

## At a glance

| | HOS ground-truth model eval (v2) | SWE-PRBench | AgenticSCR / SCRBench |
|---|---|---|---|
| Items | 8 review items (Track A) + 1 test-writing task in 2 conditions (Track B) | 350 PRs collected; 100-PR stratified sample scored | 144 commits, 107 CVEs, 92 repos |
| Defect origin | Real: drawn from one week of the repo's own review misses; one planted variant (hidden constant change) | Real: human review comments on merged PRs | Real: CVE-introducing commits; nothing seeded |
| Ground truth | Established by executing the code; corrected twice after runs | Human reviewer comments as written; "no comments are generated, synthesised, or modified" | Two authors manually label vulnerable lines from fix commit + CVE/CWE; about seven days |
| Scoring | result / cause / verdict; "dangerous" safe calls; false alarms on benign items; tests executed against buggy and fixed parsers | LLM judge (GPT-5.2) labels each comment CONFIRMED / PLAUSIBLE / FABRICATED; composite of 8 terms | Per comment: localization within +/-5 lines, CWE category match, relevance by LLM judge (GPT-4.1) |
| Runs per cell | 3 | 1 reported ("minor score variation across independent runs is expected") | Not stated |
| Decoding | Local models temperature 0; commercial CLIs at their defaults | Temperature 0 for agent and judge | Not stated |
| Prompt control | Byte-identical prompts, one script runs every model | ~200-token system prompt, three frozen context configurations | Agent explores the repo itself; baselines get the full diff |
| Separation reached | Commercial vs local: 0 vs 6-9 dangerous calls in 24; top commercial models indistinguishable at 24/24 | Top four indistinguishable (0.147-0.153); rank 4 vs 5 significant (0.147 vs 0.113), bootstrap CIs | AgenticSCR 17.5% vs best baseline 6.9% L&T&R; no CIs |

## 1. HOS: the ground-truth model evaluation (v1 2026-11-08, v2 run 2026-11-08, scored 2026-11-09)

Primary record: `research/findings/real-data-not-reviewer-diversity-caught-the-bug.md` in the HOS repo, and the comments on HOS issues [#1985](https://github.com/thurlow-research/HumanOversightSystem/issues/1985) (the v1 and v2 result tables, posted by the `scottthurlow-claude` account) and [#1455](https://github.com/thurlow-research/HumanOversightSystem/issues/1455) (the ruling and the v3 research agenda). The prompts, raw outputs and `scores.csv` are stated to live in a handoff directory `model-eval/runs/2026-11-08/`; that directory is not in the public repository (checked: no `model-eval` or `BRIEF.md` path in the clone), so the method below is what the finding and the issue comments say, not a re-run.

### Why it exists

PR #2268 changed the parser behind HOS's usage-pause gate. Real `/usage` output had gained "tip" lines such as `  47%  Trim .../CLAUDE.md`; the deny-by-default guard treated them as unrecognised limit lines, so every reading failed and, under the shipped `fail_mode=closed`, every autonomous cycle would pause. "Every lane approved it": the worker's reviewers, the overseer, and `agy` (Gemini) and `codex` (OpenAI) as cross-vendor second reviewers. Every lane saw the diff and synthetic fixtures; none saw the real capture the poller writes every five minutes (finding, "The Finding"). The benchmark was built to ask whether any model, given the real capture, would have caught it, and whether local models could take review roles.

### How the defects were chosen

Track A has eight items "drawn from that week's real review decisions" (finding) or, in the #1455 comment of 2026-11-08, "drawn from real misses (#2268, #2266, #2271, #2264)":

- A1: the #2268 guard, run against the real capture.
- A2: a surface-list lookup (is this path on the security list?), plus A2+ as a positive control where the path is on the list.
- A3: a "does this PR fully implement its issue" check.
- A4: a formatting-only diff, plus A4- (called "a planted variant with a hidden constant change" in the finding).
- A5 and A6: two "fixed" versions of the guard, each intended as a safe control.

So the item set is mostly real artifacts; the one deliberately planted item is the hidden constant change inside an otherwise formatting-only diff. Categories covered: a parser guard traced against real data, a lookup, spec completeness, a cosmetic diff with and without a hidden semantic change, and "safe" controls. In v1 the author reports that "A1 and A5 separated the models; A2, A2+, A3, A4, A4- were right for every model" and that "passing the easy items is not evidence of review ability" (#1985 v1 comment; #1455 comment of 2026-11-08).

Track B is one task: the model writes pytest cases for the parser from a spec, under two conditions, B1 spec only and B2 spec plus one real capture. A harness runs the suites against the buggy parser (`8ac989248`) and the "fixed" parser (the A6 guard).

### How prompts and models were frozen

From the v2 comment on #1985: "Every model got byte-identical prompts, with Claude, codex, agy and local models all run by the same script. Track A had 8 items x 3 runs. Local models ran at temperature 0 through the gateway. The commercial models were run through their own CLIs with no repo access." In v1 the commercial CLIs had "no cwd access beyond one isolated `input.txt`". The orchestrating session "knew the answers but never passed them to a subject". Local models were served by Ollama through a gateway; the Ollama version was recorded and the comment notes it "was upgraded 0.40.10 -> 0.40.15 after all local runs completed". Two limits the author names: `codex` and `agy` "do not report their served model", and v1's local prompts for A1/A5 were "condensed" (v2 removed that difference).

### How a run was scored

Track A scores three things per run: the **result** (the headline call), the **cause** (the explanation), and the **verdict** (safe / unsafe, full / incomplete, yes / no). A **dangerous** call is "a SAFE/FULL/YES call on an unsafe or incomplete item". **False alarms** are measured only on the two items whose correct answer is benign (A2 and A4), because by the end no safe guard control survived (see below). The cause "is scored by fixed regexes over the CAUSE line and only counts when the headline call is right. It was spot-checked and tightened once. Treat it as approximate." A model that never finished an item is recorded as **DNF**, "reported as a reliability result and not as missing data".

Track B scores **detect** ("at least one test fails only on the buggy parser"), **no false alarm** (nothing fails on the fixed parser) and **spec-match** (the fraction of the suite's expectations that match the spec; "judged by hand" in v1). Suites were "screened by an AST allowlist and run through a minimal pytest shim (real pytest is not importable in the sandbox), not read one by one".

Ground truth was "verified by executing the code, not by asking a model", and that discipline changed the key twice:

- v1: the brief's "corrected, safe" guard A5 accepted an in-block `  99%  Session limit reached` line; only `codex` caught it, and A5 was rescored unsafe.
- v2: the replacement safe control A6, built to the fix design the human-proxy had posted on #2229, accepted `91%  hourly quota`, `91%  Opus weekly cap`, `91%  Sessions used` and a non-breaking-hyphen `5-hour` line. "Claude, codex and agy told me so. I verified ... I corrected the key to UNSAFE after seeing the results." Also, A1's ground truth was corrected from seven tip lines to six tip lines plus one `Top MCP servers:` line.

The finding draws the general rule: "Benchmarks of reviewers need executed ground truth. The author's 'known safe' controls were wrong twice. Verify by execution, and expect the best reviewers to catch the author."

### Results (v2, 12 model variants, 3 runs per item)

Track A, from the #1985 v2 comment (the finding reproduces the first, fifth and seventh columns):

| Model (as served) | done/DNF | result | cause | verdict correct (of 24) | dangerous | A1 / A5 / A6 verdict ok | mean s |
|---|---|---|---|---|---|---|---|
| Claude Fable 5.1 | 24/0 | 24 | 24 | 24 | 0 | 3/3 / 3/3 / 3/3 | 45 |
| Claude Opus 5.5 | 24/0 | 24 | 24 | 24 | 0 | 3/3 / 3/3 / 3/3 | 24 |
| Claude Sonnet 5.5 | 24/0 | 24 | 24 | 24 | 0 | 3/3 / 3/3 / 3/3 | 16 |
| codex (CLI default) | 24/0 | 24 | 21 | 24 | 0 | 3/3 / 3/3 / 3/3 | 9 |
| agy (CLI default, Gemini) | 24/0 | 24 | 23 | 24 | 0 | 3/3 / 3/3 / 3/3 | 26 |
| agy gemini-3.8-flash-low | 24/0 | 24 | 23 | 23 | 1 | 3/3 / 3/3 / 2/3 | 18 |
| agy gemini-4-argon-high | 24/0 | 24 | 20 | 21 | 3 | 3/3 / 3/3 / 0/3 | 64 |
| qwen3-coder 30B (local) | 24/0 | 18 | 9 | 15 | 6 | 3/3 / 0/3 / 0/3 | 9 |
| qwen3-coder-next ~80B (local) | 18/6 DNF | 15/18 | 12/18 | 12/18 | 6 | 0/3 / DNF / 0/3 | n/a |
| devstral-small-2 24B (local) | 24/0 | 21 | 12 | 15 | 9 | 0/3 / 0/3 / 0/3 | 11 |
| qwen2.5-coder 32B (local) | 24/0 | 15 | 9 | 12 | 9 | 0/3 / 0/3 / 0/3 | 20 |
| devstral-2 123B (local) | 24/0 | 19 | 10 | 13 | 9 | 0/3 / 0/3 / 0/3 | 91 |

False alarms on A2 and A4 were zero for every model; "every failure was a miss". On A2+ (a path that is on the security list) two local models were wrong 3 of 3 and a third 2 of 3. `qwen3-coder-next` hit the gateway's 600 s lock on all 10 attempts for two items, "probably a greedy-decoding repetition loop".

Track B (3 runs per cell):

| Model | B1 spec only: detect / no false alarm | B2 spec + real capture: detect / no false alarm | spec-match B1 / B2 |
|---|---|---|---|
| Fable 5.1 | 0/3 / 3/3 | 3/3 / 3/3 | 1.00 / 1.00 |
| Opus 5.5 | 0/3 / 2/3 | 3/3 / 3/3 | 0.95 / 1.00 |
| Sonnet 5.5 | 1/3 / 3/3 | 3/3 / 2/3 | 1.00 / 0.98 |
| codex | 0/3 / 3/3 | 3/3 / 3/3 | 1.00 / 1.00 |
| agy argon-high | 0/3 / 3/3 | 3/3 / 3/3 | 1.00 / 1.00 |
| qwen3-coder 30B | 0/3 / 0/3 | 3/3 / 0/3 | 0.69 / 0.85 |
| qwen3-coder-next | 0/3 / 0/3 | 3/3 / 0/3 | 0.64 / 0.86 |
| devstral-small-2 | 0/3 / 0/3 | 0/3 / 0/3 | 0.67 / 0.54 |
| qwen2.5-coder 32B | 0/3 / 0/3 | 0/3 / 0/3 | 0.50 / 0.13 |
| devstral-2 | 0/3 / 0/3 | 0/3 / 0/3 | 0.00 / 0.00 |

Summarised in the finding as: spec only caught the bug in 1 of 15 commercial runs and 0 of 15 local runs; spec plus one real capture caught it in 15 of 15 commercial runs and 6 of 15 local runs. "Every local suite raised false alarms against the known-good parser."

The finding's conclusions, in its words: "The gap is commercial versus local, not between vendors"; "A stronger tier is not a safer tier" (Gemini's "high" tier called the flawed A6 guard safe on every run while its default caught it every time); "Size bought nothing" (the 123B local model was slowest and no safer than the 24B); "'Did not finish' is a result"; "Pick a review model by measured behaviour, not by tier name."

### What size separated what, and the author's own caveats

- v1 (one run per local model, seven items) was enough for the author to say which models failed the two hard items, but a one-item difference between local models was explicitly called noise: "That is one item at n=1, so it is noise" (#1455 comment, 2026-11-08). v1's recorded threats: "n=1 per local cell; 5 tasks, all from one week; the host session knew the answers; A5 ground truth was wrong at the outset".
- v2 (eight items, three runs, 24 cells per model) separated the commercial group (0 dangerous calls each) from the local group (6-9 each) without overlap. It did not separate the five best commercial variants from each other: all scored 24/24 on verdicts. The "high tier worse than default" result rests on one item (A6: 0/3 vs 3/3). v2's recorded threats: "n=3 per cell, and temperature 0 makes local repeats near-identical, so the split column is weak evidence for local models. All tasks come from one week of one repo. ... Cause scoring is heuristic."
- The ruling taken from it (ScottThurlow on #1455, 2026-11-08): local models are test-case proposers only, the harness feeds real captures and executes, a spec-grounded filter drops wrong expectations, output is advisory, "No local model gives review verdicts."

### How HOS plans to control drift next (v3 agenda, #1455 comment of 2026-11-09)

"Same frozen items, plus 2-3 new guard-style items (fresh ground truth by execution, so nothing is learned from v2)"; arms for each hypothesis (forced trace, reasoning model, higher precision, adversarial framing, tool use, plan-then-do, self-consistency N=5); metrics "verdict accuracy, dangerous calls, DNF rate, latency, and GPU-seconds per correct answer"; guardrails "`num_predict` cap, a wall-clock timeout under the gateway lock, at most one retry, and cancellation that reaches the host".

### HOS's earlier design for a planted-bug instrument (#1469)

Before the ground-truth eval, HOS specified a local-vs-frontier "agreement instrument" and two reviewers found it invalid as designed. The repairs ordered on #1469 are the closest thing in the HOS record to a specification for a planted-defect PR benchmark:

- "Evaluate against frozen pre-review repository snapshots"; "Exclude all outcome artifacts and reviewer findings from the retrieval corpus"; "Record the exact accessible corpus per run"; "Evaluate against a separately held-out adjudicated result set" (label leakage otherwise inflates agreement).
- A telemetry schema per run and per finding match: `run_id`, `model_digest`, `quantization`, `step`, `pr_ref`, `original_panel_vendor`, `finding_id`, `matched`, `match_confidence`.
- From the 2026-10-28 guidance comment: "Agentic parity: local candidates run in the same agent loop as the frontier reviewer"; "Planted-bug PR set: a fixed corpus of PRs with known, seeded defects (plus some clean PRs), so precision and recall are measurable against ground truth, not only against codex/agy agreement"; record model digest, quantization and effective `num_ctx` per call; "Scoring: count only findings that meet the #1456 evidence rule (concrete input + location); report hints separately."

The field run that motivated this (#2196, 2026-10-28, reported on #1455): three local 32B-class models produced 0 actionable findings against 16 for `codex` on a ~22 KB diff, with the stated confound that "codex ran agentically (read the repo, ran code); the local models saw only the diff in one pass. Single change, small n, no per-model prompt tuning, and a 32K context window at the time."

### Other HOS findings that bear on scoring a reviewer

- `cross-vendor-review-finds-real-bugs.md`: empirical false-positive rates by review mode on HOS's own framework: ~1 of ~8 findings (~12%) for a consistency review, and ~20 of 33 (~60%) non-actionable for an adversarial `codex` pass; one confirmed false positive was caused by a wrong code comment the reviewer trusted.
- `reviewer-agents-file-confident-non-reproducing-reports.md`: three of four "confident, specific, and permalink-backed" reports from a Claude-based reviewer did not reproduce; "Any quantitative study of AI-review precision must score against reproduction, never against the report's self-presented confidence."
- `nondeterministic-review-gate-converges-on-zero-new.md` and `operationalizing-a-nondeterministic-reviewer-as-a-gate.md`: an LLM reviewer over a rich corpus "returns a different set of findings each pass" and surfaced a genuine finding on every release pass (~14 distinct across successive runs at v0.1.1); "pass" had to be redefined as zero new, un-dispositioned findings.
- `agent-confidence-is-uninformative-for-defect-prediction.md` (HOS's reading of Ferdous et al. 2026): 99.9% of agent PRs self-report confidence 8-10 and breaking-change rates are flat (3.16-3.96%) across that range.
- `dogfooding-inflates-the-measured-intervention-rate.md`: a rate measured on the framework's own repository is biased upward because the framework's protected surfaces are the files its developers edit most; "a self-hosted safety mechanism does not sample its own target population."
- `docs/planning/SLR-Derived-Improvements.md` (HOS's notes on papers in its literature review; the underlying papers were not read for this note): "don't treat panel agreement as a quality proxy" (Vargas 2025); "LLM judges of review-comment usefulness barely beat chance (MCC ~ 0), so adoption/usefulness labels must come from human action data" (Karakaya et al. 2026); "pin reviewer decoding to temperature ~ 0 for reproducible verdicts" (Yu et al. 2024); "evaluate this work by escaped-defect movement, never by reviewer satisfaction" (Grunde-McLaughlin et al. 2026); "raise sampling temporarily after any model/prompt/threshold change ('watch windows')" (Eze 2026).

## 2. SWE-PRBench (Kumar 2026, arXiv:2603.26130)

Single-author preprint, v1 submitted 27 March 2026, no venue stated on arXiv. Dataset at https://huggingface.co/datasets/foundry-ai/swe-prbench, harness at https://github.com/FoundryHQ-AI/swe-prbench, pipeline v0.4.1.

**How PRs were chosen (Sections 3.1-3.6).** Repositories are scored on a Repository Quality Score out of 100 (review culture 30, PR recency 25, test quality 20, PR volume 15, contamination 10, the last being the inverse of star count); repositories under 60 are dropped, median retained score 74. Up to 30 merged PRs per repository from a six-month window ending at the freeze date, minimum PR age 30 days, about 3,000 raw PRs. A ten-stage filter keeps merged PRs with at least two substantive human comments (human author, at least 10 words, substance keywords or code references), at least one non-test file changed, not documentation-only, not Dependabot/Renovate, and not AI-dominated reviews (over 30% AI comments). A Review Value Score (0.25 depth + 0.20 complexity + 0.20 discussion + 0.15 test signal + 0.20 bug-fix signal) cuts 700 candidates to 350. Composition: Python 242 (69.1%), JavaScript 37, Go 35, TypeScript 21, Java 15; feature 214, bug fix 118, refactor 9, performance 8, security 1. The paper states the RVS threshold as 0.3 in one place and 0.35 in others.

**Ground truth.** The human review comments on the merged PR, taken after the fact from GitHub's review API; "no comments are generated, synthesised, or modified during dataset construction." There is no annotator pass, no severity on ground truth, and no taxonomy beyond three difficulty types assigned from diff-hunk ranges: Type1_Direct, "the issue is directly visible in the changed lines" (232 PRs, 66.3%); Type2_Contextual, "requires understanding how changed code interacts with surrounding unchanged code in the same file" (75, 21.4%); Type3_Latent_Candidate, "resides in files that import or depend on the changed files" (43, 12.3%). (Section 3.4 gives a different 34/40/26 split; the paper does not reconcile the two.) The paper reports no count of total human-flagged issues, no mean comments per PR, and no split of nitpicks versus defects.

**Context configurations (Section 4.2, Table 6).** Three frozen prompt bundles that "differ in layer composition, not in token volume": config_A diff only (task focus, key-changes summary, diff, minimal metadata; about 2,000 tokens), config_B diff plus file context (adds execution context and behaviour mapping; about 2,200), config_C full context (adds test signatures; about 2,500). The system prompt is about 200 tokens and asks for issues traceable to a specific line, no invented behaviour, a JSON array with P0/P1/P2 severity, and 4-6 issues per PR; it is described but not reproduced.

**Scoring (Sections 5.2-5.5, Appendix C).** An LLM judge (GPT-5.2, temperature 0) labels each agent comment CONFIRMED (same underlying issue, same file or area, same kind of change as a human comment), PLAUSIBLE or FABRICATED; matching is one-to-one bipartite on `all-mpnet-base-v2` embeddings with a 0.30 affinity cutoff. Composite per PR: `s = clamp(0.40 r + 0.25 p + 0.15 a + 0.10 q* + 0.05 e - 0.25 h - 0.15 rho - 0.10 phi)` where r recall, p precision, a semantic alignment, q* actionability, e efficiency (confirmed over total), h hallucination rate (FABRICATED fraction), rho redundancy, phi excess-plausible penalty above 0.70. PRs are weighted by `log(human_comments + 1)`. Judge validation: kappa 0.75 and 83.3% exact agreement on 30 stratified classifications; cross-judge kappa 0.616 on 127 comments from 10 PRs with Claude Sonnet 4.6. "No human performance baseline is available."

**Drift controls.** Temperature 0 for agent and judge; independent per-record API requests; frozen context bundles; a pipeline version number. The Reproducibility Note: "Frontier model APIs do not guarantee full determinism at temperature 0, so minor score variation across independent runs is expected. The two-tier ranking structure and A > B > C ordering are stable across runs." No repeated-run data is shown.

**Results (Table 8, 100-PR stratified sample of 40 Type1 / 40 Type2 / 20 Type3; eight models).** Mean composite and detection rate: Claude Haiku 4.5 0.153 / DR 0.306; Claude Sonnet 4.6 0.152 / 0.297; DeepSeek V3 0.150 / 0.312; Mistral Large 3 0.147 / 0.305; GPT-4o 0.113 / 0.220; GPT-4o-mini 0.108 / 0.210; Mistral Small 0.106 / 0.257; Llama 3.3 70B 0.079 / 0.223. Hallucination (FPR) 0.193-0.417. "All 8 models degrade monotonically from config_A to config_C"; Type2 scores for Claude Sonnet 4.6 fell 0.22 -> 0.10 -> 0.09 across A/B/C. The abstract's "15-31%" detection range and Table 8's DR column (0.210-0.312) do not match exactly.

**What size separated what.** 95% bootstrap confidence intervals (B = 10,000; the figure says N = 300, which the paper does not relate to the 100-PR sample). "The top four models (Haiku, Sonnet, DeepSeek, Mistral Large; s 0.147-0.153) are not statistically distinguishable, while the rank-4/rank-5 gap (0.147 vs. 0.113) is statistically significant." No p-values. Types 2 and 3 were oversampled "to ensure statistical power for per-difficulty analysis"; the paper does not justify 100 as a number and defers the 350-PR run to a camera-ready version. Limitations named in Section 8: 69.1% Python; subgroup statistics "should be interpreted cautiously"; a single judge family on the full leaderboard; contamination not fully excluded.

**Benchmarks it compares against (Table 1 and references).** CodeReviewer (Li et al., ESEC/FSE 2022), DeepCRCEval (Lu et al., FASE 2025) and RovoDev (no reference given); LAURA (Zhang et al., ASE 2025) and JudgeBench (arXiv:2410.12784) for judge methodology; SWE-Bench, SWE-Bench Pro and SWE Atlas as the task-benchmark contrast.

## 3. AgenticSCR and SCRBench (Charoenwet et al. 2026, arXiv:2601.19138)

arXiv v2 of 3 August 2026, "Accepted at the 41st IEEE/ACM International Conference on Automated Software Engineering (ASE'26), Industry Track"; the HTML carries DOI 10.1145/3832783.3834523 and the ASE 2026 proceedings (Munich, 12-16 October 2026). HOS's finding files cite it as "FSE 2026"; the arXiv record says ASE. Framework, dataset and results at https://doi.org/10.5281/zenodo.19908514. Authors are from Monash, Melbourne and Atlassian; the agent is built on the Atlassian Rovo Dev CLI scaffold.

**How defects were chosen (Section 4.1).** All real, none seeded. Starting from 3,256 CVEs curated by Iannone et al., the authors keep CVEs with at most three associated code changes, changes of at most 112 modified lines, and Python, JavaScript or TypeScript. Result: 144 commits, 107 CVEs, 92 repositories, 33 CWE types; median 54 changed lines per commit; vulnerable-line ratio 12.9%; 34.6% of CVEs have more than one introducing commit. The target class is "immature vulnerabilities: incomplete, latent, or context-dependent weaknesses introduced through small incremental changes", operationalised only through this CVE-linked selection. Each sandbox is built by reverting the introducing commit into staged changes on a base commit, so the reviewer sees a pre-integration change.

**Ground truth (Section 4.1.3).** Fix-commit lines are not used directly. The first and third authors, "both experienced in security analysis during code review", label as vulnerable the lines that were added or modified relative to the base commit, were later modified or removed in the fix commit, and align with the CVE description and CWE type, "with each decision cross-referenced against the CVE and CWE information"; "the process required approximately seven days to complete." Annotation bias is the internal-validity threat the authors name (Section 7).

**Scoring (Section 4.4).** Comment-level: "comment-level evaluation assesses each comment independently, enabling fair comparison across approaches" that emit very different numbers of comments. A comment is correct on localization (L) when its line is in the same file and within +/-5 lines of a ground-truth line; on type (T) when its CWE matches at the level of five high-level categories; on relevance (R) when a zero-shot LLM judge (GPT-4.1) returns True given the CWE id, name, description and the CVE description. L&T&R is the headline. The judge was checked against 70 manually labelled comments: F1 0.86, precision 0.88, recall 0.86. Recall against the full ground-truth set is not reported; the design "favors precision".

**Drift controls.** Model fixed (Claude Sonnet 4 for AgenticSCR and both LLM baselines; GPT-4.1 as judge); tool versions fixed (CodeQL 2.23.3, Semgrep 1.140, Snyk 1.130). Temperature, number of runs, seeds and variance are not reported anywhere in the paper. Context access is not matched: AgenticSCR explores the repository through tools (open_files, expand_code_chunks, grep, expand_folder, bash with git), while the LLM baselines receive the full diff (Section 4.3).

**Results (Table 3).**

| Approach | comments | L | T | R | L&T&R |
|---|---|---|---|---|---|
| AgenticSCR | 211 | 30.8% | 70.1% | 63.5% | 17.5% |
| Multi-Agent Reviewer (CodeAgent-style, Sonnet 4, full diff) | 1,009 | 26.3% | 16.4% | 24.4% | 5.2% |
| Static LLM (SecureReviewer prompt, Sonnet 4, full diff) | 652 | 28.4% | 44.0% | 46.5% | 6.9% |
| CodeQL 2.23.3 | 1,092 | 3.3% | 19.2% | 7.2% | 0.6% |
| Semgrep 1.140 | 376 | 5.1% | 74.7% | 26.3% | 3.7% |
| Snyk 1.130 | 318 | 1.6% | 39% | 13.2% | 0.9% |

The abstract's "at least 153% relative improvement" is 17.5% against 6.9%. The ablation (Table 5) runs from a Base configuration (1,070 comments, 7.3% L&T&R) through Base + SAST rules (362, 13.0%), Base + Guideline (757, 10.3%), Base + Guideline + CWE (371, 12.4%) to the full configuration (211, 17.5%); the abstract states this as "improving accuracy by 10.2 percentage points over a vanilla agentic baseline while reducing comment volume by 81%". Per CWE category (Table 4) AgenticSCR scores 23.4% injection, 21.4% authentication, 6.2% information exposure, 16.7% resource, 0.0% control-flow. No confidence intervals or significance tests appear; with one run and one model, the paper offers no estimate of what size difference it can resolve.

**Shadow deployment (Section 6).** 64 intermediate commits from PRs opened February-March 2026 in one Atlassian Python/JavaScript repository with 10-15 contributors; 41 security comments on 28 commits; two Product Security Engineers with at least three years' experience judged 22 of 41 (54%) worth developer attention, 6 of those 22 already addressed. The authors state this "measures perceived usefulness, not confirmed vulnerabilities". No inter-rater agreement is reported.

**Benchmarks and datasets it cites.** CodeReviewer (Li et al. 2022) as the dataset behind SecureReviewer (Liu et al. 2025, arXiv:2510.26457) and Haider et al. 2024 (arXiv:2411.10129); HumanEval (Cihan et al. 2025, arXiv:2505.20206); TransReview, AutoTransform and T5-Review as used by CodeAgent (Tang et al., EMNLP 2024); LAURA (Zhang et al., ASE 2025); CodeFuse-CR-Bench (Guo et al. 2025, arXiv:2509.14856); CVEfixes (Bhandari et al. 2021) and the Iannone et al. CVE set as vulnerability sources.

## 4. The review benchmarks they cite, from their abstracts only

None of these plants defects; all score against human-written comments or human-labelled quality.

- **CodeReviewer** (Li et al. 2022, ESEC/FSE 2022, arXiv:2203.09095): a dataset of real code changes and their review comments mined from open-source projects in nine languages, with a benchmark for three tasks: code-change quality estimation, review-comment generation and code refinement. Item counts are not in the abstract.
- **DeepCRCEval** (Lu et al. 2025, FASE 2025, arXiv:2412.18291): re-evaluates comment-generation benchmarks with human and LLM evaluators against developer-derived criteria and finds that "less than 10% of benchmark comments are high quality for automation"; that is, the ground truth of the comment-generation benchmarks is itself mostly low-value.
- **LAURA** (Zhang et al. 2025, ASE 2025, DOI 10.1109/ASE63991.2025.00245, arXiv:2512.01356): a retrieval-augmented comment generator, evaluated by the share of generated comments judged "either completely correct or at least helpful to developers" (42.2% with ChatGPT-4o, 40.4% with DeepSeek v3); the authors built a new dataset because existing ones held low-quality comments.
- **CodeFuse-CR-Bench** (Guo et al. 2025, arXiv:2509.14856): 601 instances from 70 Python projects across nine PR problem domains, each with issue, PR and repository state; scoring combines rule-based checks for location and syntax with model-based judgements of quality; the abstract reports that no single model dominates, Gemini 2.5 Pro scores highest overall, and models "vary in robustness to redundant context".
- **CodeAgent** (Tang et al. 2024, EMNLP 2024, DOI 10.18653/v1/2024.emnlp-main.632): the multi-agent reviewer AgenticSCR reimplements as its baseline; HOS's SLR notes report its drift-checker ablation as "a ~20-point swing".

## 5. What the sources say about proxies such as a round-1 "changes" rate

Orchestrate's cheaper-routing trials ([#513](https://github.com/shrujancheruku/claude-orchestrate/issues/513)) currently use as their quality floor "round-1 'changes' rate and rounds per PR no worse than the baseline": a count of reviewer verdicts with no ground truth behind it. None of the three sources uses such a rate as a quality measure, and each says something about why.

- **SWE-PRBench** treats the human reviewers' comments as the ground truth and scores a model by recall against them under an LLM judge, with hallucination, redundancy and excess-plausible penalties subtracted. A model that comments more is penalised through h, rho and phi, not rewarded; the prompt even caps the target at 4-6 issues. Its own caveat cuts the other way too: there is "no human performance baseline", so the human comments are an upper reference, not a verified defect list. Its detection rates (0.21-0.31) mean that even a reviewer scored against human ground truth misses most of what humans flagged, which is the figure HOS carries into its "minimum viable detection strategy" argument (`more-context-makes-llm-review-worse-not-better.md`).
- **AgenticSCR** scores each comment independently so that approaches emitting hundreds of comments and those emitting dozens are compared on the same footing, and its ablation shows volume and correctness moving in opposite directions (1,070 comments at 7.3% correct down to 211 at 17.5%; "reducing comment volume by 81%"). Its deployment measure, engineers judging 54% of comments worth attention, is labelled by the authors as perceived usefulness rather than confirmed vulnerabilities.
- **HOS** scores verdicts only against executed ground truth, because its reviewers filed "confident, well-evidenced bug reports that do not reproduce" (3 of 4 in one pass), its cross-vendor adversarial pass ran at ~60% non-actionable, its "known safe" answer keys were wrong twice, and its gate found a new genuine finding on every pass over a rich corpus. On the author side, HOS's reading of Ferdous et al. 2026 is that self-reported confidence is saturated and flat against defect rate. Its literature notes add that panel agreement should not be treated as a quality proxy, that LLM judgements of comment usefulness barely beat chance, and that presentation changes should be evaluated "by escaped-defect movement, never by reviewer satisfaction". The v1 eval's observation that every model passed the easy items, "so passing those is not evidence of review ability", is the same point from the benchmark side: a rate dominated by easy cases does not discriminate.

What the sources support, stated plainly: a reviewer's verdict count measures the reviewer's behaviour, not the code's defects; the three benchmarks all introduce an independent answer key (human comments, human-labelled lines, or execution) precisely because the reviewer's own output cannot score itself. None of the sources measures how a verdict rate correlates with escaped defects, so the question of whether the round-1 changes rate tracks quality is unanswered by them, not refuted.

## 6. What a benchmark of three reviewer tiers on a small Python/shell plugin repo would need

Only what the sources above support; design choices for orchestrate are #589's.

1. **An answer key that is not a model's opinion and not the author's belief.** HOS established ground truth by executing the code and still had to correct it twice, including a "safe" control that three strong reviewers rejected; its rule is "verify by execution, and expect the best reviewers to catch the author". SCRBench used two human labellers for about seven days on 144 commits; SWE-PRBench used post-hoc human comments and has no human baseline. For a repository of Python scripts and shell, the executable form (a test that fails only on the defective version, as in HOS Track B) is the form the sources show working.
2. **Items drawn from real misses, with benign controls, and at least one planted variant.** HOS's eight items came from one week of real review decisions plus one planted hidden-constant change; false alarms were measured on the two benign items; #1469 asks for "known, seeded defects (plus some clean PRs)". SWE-PRBench's difficulty types (visible in the diff, needs same-file context, needs cross-file context) are the only published taxonomy among the three that is defined by where the evidence sits rather than by vulnerability class.
3. **Frozen, identical inputs and a recorded environment.** HOS: byte-identical prompts, one script for every model, temperature 0 where the API allows it, commercial CLIs with no repository access, the answers held by the orchestrating session and never passed to a subject, the served model recorded where the CLI exposes it, the gateway version recorded (and the upgrade after the run noted), prompts, raw outputs and a `scores.csv` kept together. SWE-PRBench: frozen context bundles, temperature 0, a pipeline version, and a written admission that temperature 0 does not give determinism. #1469: a frozen pre-review snapshot with review outcomes excluded from anything the reviewer can read.
4. **Equal affordances across the things being compared, or the comparison is confounded.** SWE-PRBench makes context a controlled variable (A/B/C) and finds more context worse for every model; AgenticSCR concedes its agent and its baselines had unequal repository access; HOS's #2196 field run named unequal access as the biggest confound and #1469 requires "agentic parity". A tier comparison where the tiers differ in model and in what they may read measures both at once.
5. **Scores that separate the headline call, the reason and the cost of a miss.** HOS scores result, cause and verdict separately, counts "dangerous" calls (a safe verdict on an unsafe item) apart from plain misses, flags its regex cause-scoring as approximate, and records DNF as an outcome. SCRBench scores location within +/-5 lines and type separately from relevance. SWE-PRBench subtracts fabricated and redundant comments. All three refuse to let a reviewer's own confidence or comment count enter the score.
6. **Enough cells to separate the gap you expect, and a stated limit on what the size can resolve.** The sources give two anchors. Eight hard items at three runs each (24 cells per model) separated a 0-versus-6-to-9 dangerous-call gap with no overlap, left five commercial variants tied at 24/24, and rested its "higher tier was worse" claim on a single item; the author calls n=3 "weak evidence" for split items and a one-item n=1 difference "noise". One hundred PRs with 10,000-resample bootstrap intervals could not separate four models whose composites spanned 0.147-0.153, and did separate 0.147 from 0.113. Between three Anthropic tiers whose top members tie on HOS's items, the sources suggest the separation will come from a few hard items where the cheaper tier fails outright, not from an average over many easy ones, and that the easy items should be reported but not relied on.
7. **A re-run rule for drift.** HOS's v3 plan keeps the frozen items and adds fresh ones "so nothing is learned from v2"; its SLR notes recommend temporarily raised sampling after any model, prompt or threshold change; SWE-PRBench ties scores to a pipeline version and judge identity. A benchmark that is run once at design time and never after a model or prompt change cannot speak to the change.
8. **An escaped-defect record beside the benchmark, not instead of it.** HOS's literature notes evaluate review changes "by escaped-defect movement"; its own dogfooding finding warns that a rate measured on the framework's own repository over-samples the framework's protected files. Orchestrate's trials run on this repository, so an escaped-defect rate measured here inherits the same caveat, and the benchmark is the part that can be held constant across repositories.

## Sources

- Thurlow, S. (2026). Human Oversight System (HOS), documentation and research notes, https://github.com/thurlow-research/HumanOversightSystem (software archived at https://doi.org/10.5281/zenodo.21347272), docs CC BY 4.0, code MIT. Files read at commit `1482a93` (2026-11-09): `research/findings/real-data-not-reviewer-diversity-caught-the-bug.md`, `cross-vendor-review-finds-real-bugs.md`, `reviewer-agents-file-confident-non-reproducing-reports.md`, `nondeterministic-review-gate-converges-on-zero-new.md`, `operationalizing-a-nondeterministic-reviewer-as-a-gate.md`, `corroboration-ranked-review-reduces-noise-without-losing-coverage.md`, `more-context-makes-llm-review-worse-not-better.md`, `adversarial-framing-attack-on-reviewer-agents.md`, `agent-confidence-is-uninformative-for-defect-prediction.md`, `llms-belong-inside-deterministic-orchestration.md`, `dogfooding-inflates-the-measured-intervention-rate.md`, `docs/planning/SLR-Derived-Improvements.md`; GitHub issues #1985 (comments of 2026-11-07, 2026-11-08 and 2026-11-09), #1455 (comments of 2026-10-28, 2026-11-08 and 2026-11-09) and #1469 (body and comment of 2026-10-28).
- Kumar, D. (2026). SWE-PRBench: Benchmarking AI Code Review Quality Against Pull Request Feedback. arXiv preprint arXiv:2603.26130 (v1, 27 March 2026). https://arxiv.org/abs/2603.26130. Dataset https://huggingface.co/datasets/foundry-ai/swe-prbench; harness https://github.com/FoundryHQ-AI/swe-prbench.
- Charoenwet, W., Tantithamthavorn, K., Thongtanunam, P., Lin, H. Y., Jeong, M., and Wu, M. (2026). AgenticSCR: An Autonomous Agentic Secure Code Review for Immature Vulnerabilities Detection. Proceedings of the 41st IEEE/ACM International Conference on Automated Software Engineering (ASE 2026), Industry Track, Munich. DOI 10.1145/3832783.3834523; arXiv:2601.19138 (v2, 3 August 2026), https://arxiv.org/abs/2601.19138. Artifacts https://doi.org/10.5281/zenodo.19908514.
- Li, Z., Lu, S., Guo, D., Duan, N., Jannu, S., Jenks, G., Majumder, D., Green, J., Svyatkovskiy, A., Fu, S., and Sundaresan, N. (2022). Automating Code Review Activities by Large-Scale Pre-training (CodeReviewer). ESEC/FSE 2022. arXiv:2203.09095, https://arxiv.org/abs/2203.09095. Abstract only.
- Lu, J., Li, X., Hua, Z., Yu, L., Cheng, S., Yang, L., Zhang, F., and Zuo, C. (2025). DeepCRCEval: Revisiting the Evaluation of Code Review Comment Generation. FASE 2025 (ETAPS 2025). arXiv:2412.18291, https://arxiv.org/abs/2412.18291. Abstract only.
- Zhang, Y., Zhang, Y., Sun, Z., Jiang, Y., and Liu, H. (2025). LAURA: Enhancing Code Review Generation with Context-Enriched Retrieval-Augmented LLM. 40th IEEE/ACM International Conference on Automated Software Engineering (ASE 2025), pp. 2983-2995. DOI 10.1109/ASE63991.2025.00245; arXiv:2512.01356, https://arxiv.org/abs/2512.01356. Abstract only.
- Guo, H., Zheng, X., Liao, Z., Yu, H., Di, P., Zhang, Z., and Dai, H.-N. (2025). CodeFuse-CR-Bench: A Comprehensiveness-aware Benchmark for End-to-End Code Review Evaluation in Python Projects. arXiv:2509.14856 (v3, 23 October 2025), https://arxiv.org/abs/2509.14856. Abstract only.
- Tang, X., Kim, K., Song, Y., Lothritz, C., Li, B., Ezzini, S., Tian, H., Klein, J., and Bissyande, T. F. (2024). CodeAgent: Autonomous Communicative Agents for Code Review. EMNLP 2024, pp. 11279-11313. DOI 10.18653/v1/2024.emnlp-main.632. Cited via AgenticSCR's reference list and HOS's SLR notes; not read.
- Papers named only through HOS's `docs/planning/SLR-Derived-Improvements.md` and finding files, not read for this note: Ferdous et al. 2026 (MSR, DOI 10.1145/3793302.3793610); Vargas 2025; Karakaya et al. 2026; Yu et al. 2024; Grunde-McLaughlin et al. 2026; Eze 2026; Mitropoulos et al. 2026.
