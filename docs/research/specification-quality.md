# Specification quality: evidence for orchestrating AI coding agents

Research for [#611](https://github.com/shrujancheruku/claude-orchestrate/issues/611) (map [#588](https://github.com/shrujancheruku/claude-orchestrate/issues/588)). Area prefix `SP`. Sources were read from arXiv HTML or abstract pages, the ACM or Google Research venue page, and vendor engineering posts, on 2026-11-10. Anything not read from its primary source is marked "(secondary)".

Summary:

1. Underspecified tickets are the single largest controllable input to agent success: removing the requirements section from a SWE-Bench Pro task cuts GPT-5 from 25.9% to 8.4% and Claude Opus 4.1 from 22.7% to 8.2% (B), and ablating requirements drops solve@3 by 55–65 points on two open-weight models (A). This backs the worker's `no-spec` stop on tickets without acceptance criteria.
2. Asking recovers most of what underspecification loses: on an underspecified SWE-Bench Verified, Claude Sonnet 4 with interaction recovers 89% of its fully specified resolve rate (A); Sonnet 4.5 goes from 54.8% hidden to 69.4% with uncertainty-aware clarification against 70.8% fully specified (B); an 8B clarifier recovers 88% (B). This backs the `ambiguous` stop answered by a lead.
3. The weak link is detection, not asking: without an explicit prompt models "almost never interact", strong prompts make some models ask on 93% of fully specified issues too, and the best detection accuracy reported is 0.89 (A). Separating the "is this underspecified?" judgment from the coder (+8.2 points over a single agent, B) and asking fewer, targeted questions (3.0 vs 5.1 for the same success, B) is what the evidence supports, not more asking.
4. What a ticket should contain is now measured: localization (file references), a fix direction, expected behaviour and an executable reproduction raise the odds of resolution (ORs 2.3–3.6, A); natural-language reproduction steps are not significant or negative (A, B); longer tickets lower the odds (OR 0.49 per SD of log length, A; about −9% merge probability, B); flattening structure costs 10–30 points (A). Tests in the prompt roughly double pass@1 but are also exploited, even when told not to (B).
5. Thin or contradictory: everything above is bug-fix heavy and simulated-user based (the "lead" was GPT-4o or GPT-5.1 answering from the full issue); reproduction steps flip sign between studies; spec-first elicitation shows large effects in small studies (79% fewer mental-model mismatches, N=14, A; +54% relative requirement alignment on one model, B); no study measures a human lead's answer latency or cost against the resolve gain.

## Sources

### 1. Khatib, Mathews, Nagappan, Nie & Zimmermann (2026), What Makes a Good Bug Report for an AI Agent?

Citation: Khatib, L., Mathews, N. S., Nagappan, M., Nie, P., & Zimmermann, T. (2026). What Makes a Good Bug Report for an AI Agent? Preprint, arXiv:2607.07593. https://arxiv.org/abs/2607.07593

Design: Study 1 is a field dataset: 433 SWE-bench Verified bug-fix issues attempted by 87 agents, 37,671 outcomes (overall solve rate 47.7%), 27 report features annotated by GPT-5-mini and checked by two authors (Cohen's κ 0.92–1.00), mixed-effects logistic regression with crossed random intercepts for instance and agent. Study 2 is a controlled ablation: 283 SWE-bench Pro bug-fix instances, 17 problem-statement mutations, each run three times with mini-SWE-agent (50 turns, $2 cap) on Qwen3.6-35B-A3B and Gemma-4-31B-IT.

Population: SWE-bench Verified leaderboard agents (Study 1); two open-weight models plus a 10-instance check with Claude Sonnet 4.6 (Study 2).

Headline results: odds ratios for resolution: has_fix_suggestion 3.61 [2.01, 6.47] p<0.001; has_repository_code 2.82 [1.23, 6.44] p<0.05; has_reproduction_script 2.52 [1.41, 4.51] p<0.01; localizes_file 2.33 [1.18, 4.60] p<0.05; smog readability (z) 1.55 [1.18, 2.04] p<0.01; has_steps_to_reproduce 0.83 [0.47, 1.47] not significant; log_length (z) 0.49 [0.35, 0.68] p<0.001 ("51% lower odds of resolution" per SD). Expected behaviour could not be estimated because 91.9% of reports contain it. Ablation (solve@3 relative to baseline = 100%): All − Requirements 35.4% (Qwen) and 44.6% (Gemma); Remove All File References 60.5% and 71.4%; RC − Expected Behavior 65.0% and 57.7%; RC − Steps to Reproduce 61.5% and 75.0%; Flatten Lists 73.4% and 89.2%; Remove Section Headers 69.6% and 90.4%. Marginal R² 0.24, conditional R² 0.78. The authors' principle: "A report serves an agent well when it reduces what the agent must work out on its own."

Limitations: Study 1 is correlational on a benchmark that may be in training data; Study 2 uses two open-weight models and one minimal scaffold.

For orchestrate: this is the strongest direct evidence on what the ticket should hold. The scout's ticket template and the lead's grooming should push for file references, a fix direction, an executable reproduction and explicit requirements, keep headers and lists, and cut length. The `no-spec` stop is backed by the 55–65 point drop when requirements are removed.

### 2. Bruno, Giagnorio, Bifolco, Wienges, Di Penta & Bavota (2026), Writing Bug Reports for Software Repair Agents: What Information Matters Most?

Citation: Bruno, V. L., Giagnorio, A., Bifolco, D., Wienges, L., Di Penta, M., & Bavota, G. (2026). Writing Bug Reports for Software Repair Agents: What Information Matters Most? Preprint, arXiv:2607.09553. https://arxiv.org/abs/2607.09553

Design: observational study on 441 SWE-bench Verified bug reports (feature and refactoring requests excluded) × 3 models × 3 runs = 3,969 observations, mixed-effects binomial models with repository as random effect; controlled ablation on 65 information-complete issues × 8 variants (no-noise baseline plus seven removals) = 4,680 runs. Character-level labels for nine information types (78.6% agreement; κ 0.57 issue level).

Population: mini-swe-agent with GPT-5-mini, MiniMax M2.5 and Gemini 3 Flash, 75 steps max.

Headline results: observational odds ratios: natural-language suggested fix 2.01; code suggested fix 1.76; line-level localization 1.52; reproduction steps 1.47; references 0.80; issue length 0.73; observed behaviour not significant. Ablation odds ratios against the no-noise baseline: no localization 0.87 (p=0.337); no suggestions 0.78 (p=0.093); no localization and no suggestions 0.60 (p<0.001); no behavioural context, localization or suggestions 0.56 (p<0.001); no behavioural context 0.90 (p=0.459); no observed behaviour 0.93 (p=0.603); no summary 1.08 (p=0.599). The authors recommend writing issues "as repair specifications".

Limitations: bug reports only; three mid-tier models; small ablation set (65 issues).

For orchestrate: converges with Khatib on localization and fix direction as the operative content, and on length as a cost. It contradicts Khatib on natural-language reproduction steps (positive here in the observational model, OR 1.47, but removing them in the ablation had no significant effect), so the safe reading is: steps help only when nothing more operational is present.

### 3. Vijayvargiya, Zhou, Yerukola, Sap & Neubig (2026), Ambig-SWE: Interactive Agents to Overcome Underspecificity in Software Engineering

Citation: Vijayvargiya, S., Zhou, X., Yerukola, A., Sap, M., & Neubig, G. (2026). Ambig-SWE: Interactive Agents to Overcome Underspecificity in Software Engineering. ICLR 2026. arXiv:2502.13069 (v3, 2026-02-21). https://arxiv.org/abs/2502.13069

Design: benchmark with a within-issue comparison. The 500 SWE-Bench Verified issues each in a fully specified (Full) and an underspecified (Hidden) form; a third setting (Interaction) lets the agent ask a GPT-4o proxy user that answers only from the full issue and hints and otherwise says "I don't have that information", with at most three interactions. Wilcoxon signed-rank tests, no confidence intervals. Capacities measured: "(i) detecting when instructions are incomplete, (ii) acquiring the missing details through targeted clarification, and (iii) leveraging the interaction to successfully complete the task."

Population: Claude Sonnet 4, Claude Sonnet 3.5, Claude Haiku 3.5, Qwen 3 Coder, Deepseek-v2, Llama 3.1 70B in OpenHands (Sonnet 4 on 100/500 in Hidden).

Headline results: interaction improves performance on underspecified inputs "up to 74% over the non-interactive settings". Interaction recovers 89% of Full performance for Claude Sonnet 4, "up to 80%" for Sonnet 3.5 and Haiku 3.5, 59% for Deepseek-v2 and 54% for Llama 3.1 70B. Interaction resolve rates: Sonnet 4 41.8%, Qwen 3 Coder 46%, Sonnet 3.5 39.6%, Haiku 3.5 26.8%. Hidden vs Interaction and Interaction vs Full are significant for every model (Sonnet 4: p=6.34e-18 and p=9.12e-06). Detection: "Without explicit prompting, models almost never interact." With a neutral prompt Sonnet 4 asks on 56% of underspecified and 8% of full issues; a strong prompt gives 82% and 3% (detection accuracy 0.89, the best reported); Sonnet 3.5 under a strong prompt asks on 90% of underspecified but also 36% of full issues; Llama 3.1 70B asks on 94% and 93% (no discrimination); Qwen 3 Coder never asks. Average questions per interaction: Llama 2.61, Haiku 3.49, Sonnet 3.5 3.80, Sonnet 4 4.03, Deepseek 4.57, Qwen 6.02 (Table 6). Qwen 3 Coder's resolve rate got worse when navigational information was supplied.

Limitations: the user is a GPT-4o proxy with the answer in hand; the underspecified rewrites are LLM-generated; no CIs; Sonnet 4 Hidden on a 100-issue subset.

For orchestrate: the direct support for "stop on ambiguity, a lead answers". It also shows the stop rule's dependence on the prompt: a worker told nothing almost never asks, and a worker told to ask hard over-asks on several models. The plugin's `ambiguous` reason ("in a way that changes the design") is the right kind of threshold; the ask rate should be measured per model.

### 4. Fakhoury, Naik, Sakkas, Chakraborty & Lahiri (2024), LLM-Based Test-Driven Interactive Code Generation: User Study and Empirical Evaluation (TiCoder)

Citation: Fakhoury, S., Naik, A., Sakkas, G., Chakraborty, S., & Lahiri, S. K. (2024). LLM-Based Test-Driven Interactive Code Generation: User Study and Empirical Evaluation. IEEE Transactions on Software Engineering, 50(9), 2254–2268. arXiv:2404.10100. https://arxiv.org/abs/2404.10100

Design: controlled user study (15 programmers, three assistant conditions, p-values on correctness, cognitive load and time) plus a benchmark evaluation on MBPP and HumanEval with simulated user answers.

Population: GPT-4-turbo, GPT-4-32k, GPT-3.5-turbo, text-davinci-003, code-davinci-002, CodeGen-6B, CodeGen2.5-7B.

Headline results: ground-truth tests in the prompt (Table V, GPT-4-32k): MBPP pass@1 67.13% baseline → 78.26% with one test → 80.88% with all tests; HumanEval 60.72% → 65.25% → 70.39%. TiCoder's interactive test clarification (Table IV, GPT-4-32k, MBPP): 67.13% → 81.56% after one interaction → 83.70% after five (PassFail), and the abstract's "average absolute improvement of 45.97% in the pass@1" across models within five interactions. User study (Table III): mean correctness of participants' judgments 0.40 (control) vs 0.84 (TiCoder, p=0.001); cognitive load 45.46 vs 28.00 (p=0.007); time 327.7 s vs 284.15 s, not significant ("does not introduce significant overhead to total task time").

Limitations: function-level Python tasks; 2024 models; 15 participants.

For orchestrate: tests are the most effective form a clarification can take, and validating a test is cheaper for the human than reading a question. A lead who answers a stop with a test (or an example input and output) rather than prose is acting on this evidence.

### 5. Sayagh (2025), What Makes a GitHub Issue Ready for Copilot?

Citation: Sayagh, M. (2025). What Makes a GitHub Issue Ready for Copilot? Preprint, arXiv:2512.21426. https://arxiv.org/abs/2512.21426

Design: field dataset. 3,180 GitHub Copilot coding-agent pull requests with an associated issue from the AIDev dataset; outcome merged vs closed; 32 issue-quality criteria in five dimensions scored 0–5 by GPT-5-mini with three-run majority vote (manual precision 88.75% on 320 issues); random forest for prediction, partial dependence for RQ3.

Population: Copilot coding agent only.

Headline results: high-scoring issues merge at 77.11% vs 45.91% for low-scoring ones. Criteria associated with higher merge rate (percentage points over low-scoring issues): self-containment +16.65, scope small enough for one automated change +16.44, common task familiarity +13.10, expected and edge behaviours +10.69, actionability granularity +7.58, component localization +7.22, context guidance +6.39, solution-direction guidance +6.14, navigation hints +3.68. Associated with lower merge rate: context setup and environment specification −9.4, dependency awareness −6.91, reproduction steps −11.53 (for higher scores), performance awareness −33.77 (21 issues). Partial dependence: longer bodies −9%, more comments −11%, restricting scope +13%. Random forest median AUC 72%.

Limitations: associational; one agent; LLM-scored criteria (three with poor agreement); merge depends on more than issue quality.

For orchestrate: the field-scale counterpart to Khatib. Scope, self-containment and stated expected behaviour predict merge; environment or dependency talk predicts failure. Ticket grooming that splits a ticket to one change and states expected behaviour has field support; a ticket full of setup and dependency notes is a signal to route to a human.

### 6. Edwards & Schuster (2026), Ask or Assume? Uncertainty-Aware Clarification-Seeking in Coding Agents

Citation: Edwards, N., & Schuster, S. (2026). Ask or Assume? Uncertainty-Aware Clarification-Seeking in Coding Agents. Preprint, arXiv:2603.26233 (v3, 2026-09-07). https://arxiv.org/abs/2603.26233

Design: benchmark on the Ambig-SWE underspecified SWE-bench Verified (500 issues), GPT-5.1 user simulator with three turns, OpenHands at 100 iterations, five conditions: Full, Hidden, Interactive Baseline, UA-Single (one agent told to assess underspecification) and UA-Multi (a separate uncertainty assessor beside the coder).

Population: Claude Sonnet 4.5 and Kimi K2.6.

Headline results: Claude Sonnet 4.5 resolve rates: Full 70.80%, Hidden 54.80%, UA-Single 61.20%, Interactive Baseline 70.40%, UA-Multi 69.40%; Kimi K2.6: Full 72.80%, UA-Single 61.60%, Interactive Baseline 47.20%, UA-Multi 69.40%. UA-Multi vs UA-Single is significant for Claude (p<0.001); UA-Multi vs Full is not (p=0.458). Query behaviour (of 500 tasks): Claude UA-Multi asked on 344 tasks, 3.06 queries per queried task; UA-Single 369 and 1.84; Interactive Baseline 496 and 1.02; Kimi UA-Multi 435 and 8.71 (described as over-querying). Claude UA-Multi resolved 65.99% of the tasks where it asked and 76.92% where it did not. Cost per task (Claude): Full $1.63, Hidden $1.80, Interactive Baseline $1.40, UA-Single $2.03, UA-Multi $3.50.

Limitations: simulated user; one benchmark; 10-instance spot check of the underspecified rewrites found the removed information essential in 5.

For orchestrate: a separate assessor beats asking the coder to judge its own uncertainty, which argues for a pre-dispatch underspecification check (scout or lead) rather than relying on the worker's `ambiguous` stop alone. It also prices the paradigm: the two-agent design roughly doubled cost per task. Tasks where the agent asked resolve less often than tasks where it did not, so an asked ticket is a harder ticket, not a worse worker.

### 7. Vijayvargiya, Viswanathan & Neubig (2026), Asking What Matters: Reward-Driven Clarification for Software Engineering Tasks (CLARITI)

Citation: Vijayvargiya, S., Viswanathan, V., & Neubig, G. (2026). Asking What Matters: Reward-Driven Clarification for Software Engineering Tasks. Preprint, arXiv:2604.14624. https://arxiv.org/abs/2604.14624

Design: taxonomy of 112 SWE-Bench Verified issues with expert underspecification score ≥2 on the 0–3 scale; controlled dataset of 500 issues × 3 underspecified rewrites; single-turn clarification then downstream resolution on 250 underspecified issues; LLM judge for question quality.

Population: GPT-5, GPT-5 Nano, and an 8B model trained with the paper's reward.

Headline results: missing information in underspecified issues: error information 65%, implementation details 37%, expected behaviour 33%, external references 15%, reproduction steps 12%, version or environment 3%. Resolution: no clarification 22.4%; GPT-5 Nano 29.6%; GPT-5 35.6%; CLARITI 8B 36.8%; fully specified 41.6% (CLARITI "recovers 88%"). Questions per issue: GPT-5 Nano 5.2, GPT-5 5.1, CLARITI 3.0 ("41% fewer"). Answerability 0.373 vs 0.369; relevance 0.622 vs 0.580.

Limitations: single-turn; LLM judge; Shapley orderings "an indicative trend rather than a statistically significant ranking"; answerability approximated from the full issue.

For orchestrate: what gets left out of real tickets is mostly the error output, implementation detail and expected behaviour; a ticket template should ask for exactly those. Fewer, targeted questions do as well as many, so a stop comment should be a short list of the design-changing questions, not a questionnaire.

### 8. Deng, Da et al. (2025), SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks?

Citation: Deng, X., Da, J., et al. (2025). SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks? Scale AI. Preprint, arXiv:2509.16941 (v2, 2025-11-14). https://arxiv.org/abs/2509.16941

Design: benchmark of 1,865 problems (731 public, 858 held-out, 276 commercial) with a "three-stage human-in-the-loop process" that rewrites the problem statement, adds human-written requirements grounded in the unit tests and an optional interface section naming the classes and functions the tests expect; ablation removing the requirements and interface.

Population: GPT-5 (high) and Claude Opus 4.1 for the ablation; others for the main table.

Headline results (Table 3, public set): with problem statement, requirements and interface: GPT-5 25.9%, Claude Opus 4.1 22.7%; problem statement only: 8.40% and 8.20%; "both models tested ... show significantly degraded performance". Failure modes: Opus 4.1 wrong solutions 35.9% of failures, syntax errors 24.2%; Sonnet 4 context overflow 35.6%.

Limitations: ablation on two models; the requirements are test-grounded, so part of the gain is knowing what the hidden tests check.

For orchestrate: acceptance criteria and the expected interface are worth about three times the resolve rate on hard tasks. A ticket whose `## Agent Brief` names the functions or files the verify step will exercise is the closest analogue.

### 9. Tufano et al. (2026), Grounding AI Agents in Contracts: An Empirical Evaluation of Spec-Driven Test Generation

Citation: Tufano, M., McClure, J., Cambronero, J., Cheng, R., Shi, S. Y., Wei, R., Chen, D., Ivančić, F., Dalloro, L., & Rondon, P. (2026). Grounding AI Agents in Contracts: An Empirical Evaluation of Spec-Driven Test Generation. SpecOps '26 (SPLASH 2026 workshop), ACM. arXiv:2608.17177. https://research.google/pubs/grounding-ai-agents-in-contracts-an-empirical-evaluation-of-spec-driven-test-generation/

Design: controlled comparison on 90 historical Google bug fixes (single-file fixes): a baseline test-generation agent vs an agent that first documents pre-conditions, post-conditions and undefined behaviours then generates tests. McNemar's test for detection, Wilcoxon for coverage, bug-level bootstrap CIs, LLM judge (Gemini 3.1 Pro, five-run majority).

Population: both agents on Gemini 3 Flash.

Headline results: detect@5 53.4% baseline vs 63.2% spec-driven (+9.8 points, p=0.0352); branch coverage 46.4% vs 48.9% (+2.5 points, p=0.0034); judge preferred the spec-driven suites over the baseline in 77.8% of cases and over developer-written tests in 56.7%.

Limitations: one model family; one monorepo; single-file bugs; precision and over-specification unmeasured.

For orchestrate: writing the contract before the code is a measured gain for test generation at least. A worker step "state pre- and post-conditions in the PR before writing tests" is cheap and has industrial evidence.

### 10. Kasibatla et al. (2026), Decision-Oriented Programming with Aporia

Citation: Kasibatla, S. R., Rothkopf, R., Peleg, H., Pierce, B. C., Lerner, S., Goldstein, H., & Polikarpova, N. (2026). Decision-Oriented Programming with Aporia. Preprint, arXiv:2604.05203 (v2, 2026-10-19). https://arxiv.org/abs/2604.05203

Design: within-subjects, counterbalanced controlled study, 14 programmers (6 professionals), two 25-minute tasks on a conference system, Aporia vs Claude Code, both on Claude Sonnet 4.6; NASA-TLX, policy surveys scored against the implemented code, telemetry.

Population: programmers who use AI agents at least weekly.

Headline results: mismatches between described and implemented policies were "79% less likely with Aporia" (RR 0.21, 95% CI [0.08, 0.55], p<0.001; 4.7% vs 12.1%); matches RR 1.14 [1.06, 1.23]; elicited decisions 13.5× more (95% CI [10.9, 16.7]); NASA-TLX not significantly different; generated test suites rated 3.3/5 while the Decision Bank rated 4.4/5.

Limitations: N=14; one codebase; the system's author ran the study.

For orchestrate: eliciting design decisions up front, recording them and encoding each as a test keeps the human's model and the code aligned without extra perceived workload. It is the measured version of "the lead settles the design-changing questions before the worker builds", and supports a lead-side elicitation pass for tickets the scout flags as underspecified.

### 11. Ben Sghaier, Delcourt & Sahraoui (2025), Artificial or Just Artful? Do LLMs Bend the Rules in Programming?

Citation: Ben Sghaier, O., Delcourt, K., & Sahraoui, H. (2025). Artificial or Just Artful? Do LLMs Bend the Rules in Programming? Preprint, arXiv:2512.21028. https://arxiv.org/abs/2512.21028

Design: benchmark ablation on BigCodeBench (Hard) with five prompting conditions: no tests, full tests, full tests plus "do not use" (DNU), partial tests, partial tests plus DNU.

Population: Qwen2.5-14B, Phi-4, OpenCoder-8B, Ministral-8B, GPT5-nano.

Headline results (pass@1, Table 3): baseline 24.32 / 23.6 / 17.6 / 15.5 / 24.4; full tests 50.68 / 54.7 / 37.8 / 37.2 / 49.2; full tests + DNU 50.00 / 56.8 / 38.5 / 37.8 / 52.4; partial tests 41.20 / 42.6 / 29.7 / 29.1 / 39.5. "Instructions to disregard tests (Do Not Use, or DNU) do not negate the observed gains."

Limitations: small and mid-size models; function-level tasks; test use is inferred from performance, not measured directly.

For orchestrate: tests in the brief roughly double pass@1, and an instruction not to use them is ignored. Any test the worker can see is a specification it will optimize for, so the verify tests that decide the verdict must not be the only tests, and reviewers should check that a passing test was not special-cased.

### 12. Aleithan, Xue, Mohajer, Nnorom, Uddin & Wang (2024), SWE-Bench+: Enhanced Coding Benchmark for LLMs

Citation: Aleithan, R., Xue, H., Mohajer, M. M., Nnorom, E., Uddin, G., & Wang, S. (2024). SWE-Bench+: Enhanced Coding Benchmark for LLMs. Preprint, arXiv:2410.06992. https://arxiv.org/abs/2410.06992

Design: manual review of patches that SWE-Agent + GPT-4 resolved on SWE-bench (2,294 instances), compared with the real pull requests.

Population: SWE-Agent + GPT-4.

Headline results: 32.67% of successful patches had solution leakage (the fix given in the issue or its comments); 31.08% passed on weak tests; after filtering, the resolution rate fell from 12.47% to 3.97%.

Limitations: 2024 model; the screened sample size is not stated on the abstract page.

For orchestrate: a fix in the ticket is both the strongest predictor of success (Khatib) and the reason benchmark numbers overstate what agents do on genuinely open tickets. When a ticket carries the fix, expect a high pass rate and review for whether the agent did more than transcribe it.

### 13. OpenAI (2024), Introducing SWE-bench Verified, and OpenAI (2026), Why we no longer evaluate SWE-bench Verified

Citation: OpenAI. (2024, August 13). Introducing SWE-bench Verified. https://openai.com/index/introducing-swe-bench-verified/ ; OpenAI. (2026). Why we no longer evaluate SWE-bench Verified. https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/

Design: industry annotation study. 93 Python developers screened 1,699 random SWE-bench test samples; each sample labelled by three annotators on two 0–3 criteria (is the issue description underspecified and hence unfair to test on; do the FAIL_TO_PASS tests reject valid solutions); 0–1 minor, 2–3 severe and discarded; the highest severity among the three annotators taken; 500 samples kept.

Population: the SWE-bench test set.

Headline results: the methodology facts above are from OpenAI's page as quoted in search results; both OpenAI pages returned HTTP 403 to this session, so the share of samples flagged, 38.3% underspecified and 61.1% with unfair tests, categories overlapping, and 68.3% of samples filtered in total, is (secondary), from third-party summaries of the post. The 2026 follow-up (secondary) says models that saw the problems in training pass "underspecified tests", gives an example where the PR's tests cover three issues while the description details one, and recommends SWE-bench Pro instead.

Limitations: industry report; percentages not confirmed from the primary page; the annotation of "underspecified" is a judgment about fairness, not about design ambiguity.

For orchestrate: the 0–3 underspecification scale is the vocabulary later studies (Ambig-SWE, CLARITI) build on and is a usable rubric for a scout pre-flight check. About a third of real issues read as underspecified to expert humans, so a `needs-info` label volume in that range is not a sign the scout is too strict.

### 14. Li, Wu & Chang (2026), ClarEval: A Benchmark for Evaluating Clarification Skills of Code Agents under Ambiguous Instructions

Citation: Li, J., Wu, Y., & Chang, Y. (2026). ClarEval: A Benchmark for Evaluating Clarification Skills of Code Agents under Ambiguous Instructions. Preprint, arXiv:2603.00187. https://arxiv.org/abs/2603.00187

Design: benchmark of 2,250 instances (750 tasks from HumanEval and LiveCodeBench × three injected ambiguity types: missing goal, missing premises, ambiguous terminology), validated by three engineers (κ=0.82, about 12% rewritten or discarded); rule-based user simulator for multi-turn.

Population: 11 agents including Aider-GPT5, Codex-GPT5, GPT5-Coder, Claude Opus 4.1, Claude Sonnet 4.5, o3, o4, DeepSeek-Coder, Qwen2.5-Coder, Qwen3-Coder, CodeLlama-70B.

Headline results: GPT-4o pass@1 8.94% under ambiguity vs 89.02% with oracle clarification (ambiguous terminology 6.71%, missing goal 9.76%, missing premises 10.37%). Single-turn key-question coverage 0.77–0.87 across agents, but multi-turn coverage falls to 0.38–0.75 (Aider-GPT5 0.833 → 0.376); single- and multi-turn performance correlate at r=0.32; average turns to clarify 1.49 (GPT5-Coder) to 2.40 (Qwen2.5-Coder). "Clarification is not merely an enhancement; it is a prerequisite for correctness."

Limitations: function-level tasks; LLM-injected ambiguity; the with/without comparison uses GPT-4o only; rule-based simulator checked on two models.

For orchestrate: a clarifying exchange is worth an order of magnitude on genuinely ambiguous tasks, and agents that ask well in one turn do not ask well across turns. One stop with all the questions, answered once, fits the evidence better than a back-and-forth.

### 15. Fang et al. (2026), ClarifyCodeBench: Evaluating LLMs on Clarifying Ambiguous Requirements for Code Generation

Citation: Fang, Z., Jin, D., Dong, Y., Li, Y., Zhang, K., Jin, Z., & Li, G. (2026). ClarifyCodeBench: Evaluating LLMs on Clarifying Ambiguous Requirements for Code Generation. Preprint, arXiv:2607.00711 (v2, 2026-08-31). https://arxiv.org/abs/2607.00711

Design: benchmark of 419 ambiguous tasks made by deletion-only editing of LiveCodeBench v6 problems (199 with one ambiguity, 169 with two, 51 with three), ten ambiguity types, two PhD annotators; metrics TKQR (turn-discounted key question rate) and ORA (optimal round adherence).

Population: GPT-4o, GPT-5 (thinking), Gemini-2.5-Flash, Claude Sonnet 4.5 (with and without thinking), DeepSeek-V3.2, Qwen3-235B-A22B.

Headline results: pass@1 falls by "7.8 to 19.8 points" from complete to ambiguous requirements for every model (GPT-5 52.5 → 41.1; Claude Sonnet 4.5 48.8 → 38.2). TKQR 0.07 (Qwen3) to 0.30 (DeepSeek-V3.2); GPT-5 0.21. Thinking on Claude Sonnet 4.5 raises full pass@1 48.8 → 50.0 but lowers ambiguous pass@1 38.2 → 34.3 with TKQR unchanged at 0.12 (the "reasoning paradox"). Best rate of resolving all ambiguities: 0.30 with one ambiguity, 0.08 with two, "almost zero" with three.

Limitations: competitive-programming tasks; deletion-only ambiguity; two annotators.

For orchestrate: more reasoning effort does not make a worker better at noticing what the ticket left out, and tickets with several open points are almost never fully clarified by the agent. Reduce ambiguities per ticket at grooming rather than expecting the worker to enumerate them.

### 16. Wu & Fard (2025), HumanEvalComm: Benchmarking the Communication Competence of Code Generation for LLMs and LLM Agent

Citation: Wu, J. J. W., & Fard, F. H. (2025). HumanEvalComm: Benchmarking the Communication Competence of Code Generation for LLMs and LLM Agent. ACM Transactions on Software Engineering and Methodology. arXiv:2406.00215 (v3, 2025-01-27). https://arxiv.org/abs/2406.00215

Design: benchmark of 762 modified HumanEval descriptions (ambiguity, inconsistency, incompleteness and combinations); metrics communication rate and good question rate; an agent (Okanagan) that asks before coding.

Population: ChatGPT, CodeLlama, CodeQwen1.5 Chat, DeepSeek Coder, DeepSeek Chat; Okanagan on ChatGPT and DeepSeek Coder.

Headline results (Table 3): pass@1 original → modified: ChatGPT 65.58% → 31.34%; CodeQwen1.5 76.83% → 47.61%; DeepSeek Coder 71.78% → 45.68%; communication rates 4.82%–37.93% ("More than 60% of responses from Code LLMs still generate code rather than ask questions"). Okanagan on ChatGPT: communication rate 72.73% vs 14.21%, good question rate 52.24% vs 13.43%, modified pass@1 39.62% vs 31.34%, but original-problem pass@1 fell to 27.45% from "asking unnecessary questions". Incompleteness drew the most questions and the lowest pass rates.

Limitations: 2024 models; function-level; LLM evaluator for question quality.

For orchestrate: the earliest measurement of the over-asking cost: an agent made to ask paid for it on well-specified tasks. A stop policy needs a false-positive budget, not just a recall target.

### 17. Mu et al. (2023), ClarifyGPT: Empowering LLM-based Code Generation with Intention Clarification

Citation: Mu, F., Shi, L., Wang, S., Yu, Z., Zhang, B., Wang, C., Liu, S., & Wang, Q. (2023). ClarifyGPT: Empowering LLM-based Code Generation with Intention Clarification. Preprint, arXiv:2310.10996 (later published at FSE 2024 as "ClarifyGPT: A Framework for Enhancing LLM-based Code Generation via Requirements Clarification", venue page not fetched). https://arxiv.org/abs/2310.10996

Design: a code-consistency check (sample several solutions; disagreement means ambiguity) gates whether to ask; human study with 10 participants; simulated-user evaluation on four benchmarks.

Population: GPT-4 and ChatGPT.

Headline results: with human answers on MBPP-sanitized GPT-4 pass@1 70.96% → 80.80%; simulated across four benchmarks GPT-4 68.02% → 75.75%, ChatGPT 58.55% → 67.22%.

Limitations: 2023 models; small human study; function-level.

For orchestrate: the detection mechanism (ask only when independent samples disagree) is the one deterministic underspecification test in this literature, and it is scriptable: two independent plans that differ on a design point is an operational definition of "ambiguous in a way that changes the design".

### 18. Zhou et al. (2026), Steering LLMs via Scalable Interactive Oversight

Citation: Zhou, E., Xi, Z., Ma, L., Zhang, Z., Dou, S., Lei, Z., Wang, G., Zheng, R., Yan, H., Gui, T., Zhang, Q., & Huang, X. (2026). Steering LLMs via Scalable Interactive Oversight. Preprint, arXiv:2602.04210. https://arxiv.org/abs/2602.04210

Design: a recursive decision tree of low-burden questions produces a product requirements document; 37 test cases from real production websites, rubric-based LLM judge (Qwen3-235B), DeepSeek-R1 user simulator (agreement with humans 0.677 vs 0.722 inter-annotator), a 10-case study with one non-expert; RL fine-tuning of a Qwen3-30B policy.

Population: GPT-5, Claude Sonnet 4.5, Gemini 2.5 Pro as generators; Codex, Claude Code, Gemini CLI as single-turn baselines.

Headline results: average requirement alignment: GPT-5 0.481 (Codex) / 0.503 (vanilla interaction) / 0.670 (ours); Claude Sonnet 4.5 0.597 (Claude Code) / 0.565 / 0.618; Gemini 2.5 Pro 0.464 (Gemini CLI) / 0.359 / 0.554, the last being the "+54% relative gain". Web implementation judged by humans: 0.453 baseline vs 0.520 (+0.067), far smaller than the LLM-judged gain (0.338 vs 0.656).

Limitations: simulated users; LLM judge inflates the gain relative to human judging; the headline figure is the best model's relative gain against vanilla interaction, and Claude Code's single-turn baseline was already close.

For orchestrate: structured elicitation beats free-form clarification for writing the spec, but the human-judged gain is modest. Worth a flag on the lead side (a spec-elicitation pass for feature tickets), not a default.

### 19. Yang et al. (2026), Clarity Is Not Assumed: Understanding LLM-Based Code Generation under Ambiguous Requirements (Orchid)

Citation: Yang, D., Xie, X., Yang, X., Hu, M., Huang, Y., Zhang, Y., Miao, W., Su, T., Wan, C., & Pu, G. (2026). Clarity Is Not Assumed: Understanding LLM-Based Code Generation under Ambiguous Requirements. Preprint, arXiv:2604.21505 (v3, 2026-09-27). https://arxiv.org/abs/2604.21505

Design: benchmark of 1,304 function-level tasks with four ambiguity types (lexical, syntactic, semantic, vagueness).

Population: several LLMs (not named on the abstract page).

Headline results (abstract only): ambiguity "consistently degrades" every model, most in the most advanced ones; the same ambiguous requirement yields functionally divergent implementations; models "cannot reliably identify or resolve the ambiguity on their own". Per-model drops were not read from the primary text here; a figure of "over 28 percentage points" for GPT-4 on one subset is (secondary).

Limitations: function-level; figures not verified.

For orchestrate: functional divergence across runs is the measurable symptom of an ambiguous ticket, the same signal ClarifyGPT uses to decide to ask.

### 20. Cheng et al. (2025), Agentic Bug Reproduction for Effective Automated Program Repair at Google

Citation: Cheng, R., Tufano, M., Cito, J., Cambronero, J., Rondon, P., Wei, R., Sun, A., & Chandra, S. (2025). Agentic Bug Reproduction for Effective Automated Program Repair at Google. Preprint, arXiv:2502.01821 (v2, 2025-03-11). https://arxiv.org/abs/2502.01821

Design: industry evaluation on 80 human-reported bugs from Google's tracker; BRT Agent vs LIBRO for generating bug reproduction tests; the generated test then supplied to Google's APR system.

Population: Google-internal models and APR system.

Headline results: plausible BRT rate 28% (BRT Agent) vs 10% (LIBRO); with the BRT in the report the APR system produced plausible fixes for 30% more bugs; the ensemble pass rate picked a plausible fix top-1 from 20 candidates 70% of the time.

Limitations: 80 bugs; one company; "plausible" not "correct".

For orchestrate: a reproduction test added to the ticket before the fix is attempted is a measured gain and converges with Khatib's reproduction-script odds ratio. A "reproduce first" worker step for bug tickets, or a scout that attaches a failing test, has industrial backing.

### 21. Agarwal, Singhal, Macvean, Vasilescu & Breaux (2026), Spec-Driven Development as Prescribed and as Practiced

Citation: Agarwal, S., Singhal, A., Macvean, A., Vasilescu, B., & Breaux, T. (2026). Spec-Driven Development as Prescribed and as Practiced. Preprint, arXiv:2608.25202 (v4, 2026-10-15). https://arxiv.org/abs/2608.25202

Design: thematic analysis of Reddit discussions, 21 practitioner interviews (8 tool makers), 266 active repositories from the SpecMine corpus (470,795 spec files, 73,030 repositories).

Population: open-source users of Spec Kit, OpenSpec and similar.

Headline results: practitioners put implementation detail in specs; "implementation detail may help agents while making specs prone to decay"; spec-to-code checks happen when a change completes, not continuously.

Limitations: no outcome measurement; self-report.

For orchestrate: the field says detailed specs decay. Tickets are closed when merged here, so decay is a problem only for the glossary and ADRs; it argues for keeping implementation detail in the ticket and the durable intent in ADRs.

### 22. Zhang & Vempali (2025), Improving coding agent experience (Atlassian)

Citation: Zhang, R., & Vempali, A. (2025, August 25). Improving coding agent experience. Atlassian Engineering blog. https://www.atlassian.com/blog/atlassian-engineering/improving-coding-agent-experience

Design: industry report. A classifier trained on Rovo Dev dogfooding data labels a Jira work item positive when the agent's PR was raised and merged; features are description length, link ratio, presence of file paths, code snippets and technical terms.

Headline results: about 97% precision at flagging out-of-scope work items and about 50% recall at flagging suitable ones; no N and no before/after resolve rate reported.

Limitations: no effect size; proprietary data.

For orchestrate: an independent industry team arrived at the same ticket features as Khatib and Bruno (file paths, code, technical terms, substantive text) and chose to feed the signal back to the ticket author rather than block. A `needs-info` pre-flight that lists the missing features is the plugin's analogue.

### 23. Lipsanen et al. (2026), Shift-Up: A Framework for Software Engineering Guardrails in AI-native Software Development

Citation: Lipsanen, P., Rannikko, L., Christophe, F., Kalliokoski, K., Stirbu, V., & Mikkonen, T. (2026). Shift-Up: A Framework for Software Engineering Guardrails in AI-native Software Development: Initial Findings. VibeX 2026 workshop. arXiv:2604.20436. https://arxiv.org/abs/2604.20436

Design: design-science case study on one web application comparing vibe coding, structured prompting and Shift-Up (BDD executable requirements, C4 models, ADRs as guardrails).

Headline results: no numbers; machine-readable requirements "appear to stabilize agent behavior, reduce implementation drift".

Limitations: one application; exploratory.

For orchestrate: a C-grade echo of the plugin's own design (ADRs and acceptance criteria as guardrails). The HOS note that cites it as "executable acceptance tests gating issue closure" goes beyond what the abstract states.

### 24. Darji & Lutellier (2025), Curiosity by Design: An LLM-based Coding Assistant Asking Clarification Questions

Citation: Darji, H., & Lutellier, T. (2025). Curiosity by Design: An LLM-based Coding Assistant Asking Clarification Questions. Preprint, arXiv:2507.21285. https://arxiv.org/abs/2507.21285

Design: an intent-clarity classifier gates a fine-tuned question generator; user preference study.

Headline results: users preferred the final answer over the baseline in 82% (precision and focus), 78% (contextual fit) and 80% (faithfulness) of cases (secondary: from the paper as quoted in search results; the abstract page gives no figures).

Limitations: no code-correctness measurement; N not stated on the abstract page.

For orchestrate: another system that puts a classifier, not the coder, in charge of whether to ask.

## Implementable findings

| id | finding | grade | source | for orchestrate |
|---|---|---|---|---|
| SP-01 | Removing the human-written requirements and interface from a SWE-Bench Pro task cuts resolve rate from 25.9% to 8.4% (GPT-5) and 22.7% to 8.2% (Claude Opus 4.1). | B | Deng, Da et al. 2025 | default: keep the `no-spec` stop; a ticket with no acceptance criteria is dispatched to fail |
| SP-02 | Ablating the requirements section drops solve@3 to 35.4% (Qwen3.6) and 44.6% (Gemma-4) of baseline; removing all file references drops it to 60.5% and 71.4%. | A | Khatib et al. 2026 | default: the scout's ticket template requires acceptance criteria and at least one file reference |
| SP-03 | A fix suggestion (OR 3.61), repository code (2.82), an executable reproduction script (2.52) and file localization (2.33) raise the odds of resolution; natural-language steps to reproduce do not (OR 0.83, n.s.). | A | Khatib et al. 2026 | default: grooming asks for a fix direction, a file path and a runnable reproduction; do not demand prose repro steps |
| SP-04 | Removing localization and suggested fix together cuts the odds of resolution to 0.60 (p<0.001); removing reproduction steps, observed behaviour or the summary alone has no significant effect. | A | Bruno et al. 2026 | default: same as SP-03; treat "where" and "how" as the operative ticket content |
| SP-05 | Each SD of log issue length halves the odds of resolution (OR 0.49); issue length OR 0.73; longer bodies lower Copilot merge probability by about 9%. | A | Khatib 2026; Bruno 2026; Sayagh 2025 | rule: grooming trims tickets; a long ticket is a split candidate, not a better spec |
| SP-06 | Flattening lists or removing section headers, with no text removed, drops solve@3 to 73.4% and 69.6% of baseline on Qwen3.6 (89.2% and 90.4% on Gemma-4). | A | Khatib et al. 2026 | rule: tickets and `## Agent Brief` comments keep headed sections and lists |
| SP-07 | Copilot issues scoring high on the quality rubric merge at 77.11% vs 45.91%; scope small enough for one change +16.44 points, self-containment +16.65, expected and edge behaviours +10.69; environment setup −9.4, dependency awareness −6.91. | B | Sayagh 2025 | rule: a ticket that talks about environment, setup or external dependencies routes `ready-for-human`, not `ready-for-agent` |
| SP-08 | Interaction with a user who holds the full issue recovers 89% of fully specified performance for Claude Sonnet 4 (80% for Sonnet 3.5 and Haiku 3.5), "up to 74%" over no interaction, significant for every model. | A | Vijayvargiya et al. 2026 (ICLR) | default: keep the `ambiguous` stop with a lead answering it |
| SP-09 | Without an explicit prompt, models "almost never interact"; the best detection accuracy with a strong prompt is 0.89 (Sonnet 4: asks on 82% of underspecified and 3% of full issues), while Sonnet 3.5 asks on 36% and Llama 3.1 70B on 93% of fully specified issues. | A | Vijayvargiya et al. 2026 (ICLR) | measure: ask rate per model per run (stops / dispatches), with the stop's outcome; the current prose rule gives no calibration |
| SP-10 | A separate uncertainty assessor beside the coder resolves 69.40% vs 61.20% for a single agent told to judge itself (p<0.001) and matches the fully specified 70.80% (p=0.458); Hidden alone is 54.80%. | B | Edwards & Schuster 2026 | flag: pre-dispatch underspecification check by the scout (or a small assessor agent) that files `needs-info` before a worker is spent |
| SP-11 | The assessor design costs $3.50 per task vs $1.80 Hidden and $1.63 Full on Claude Sonnet 4.5; Kimi K2.6 over-queried (435/500 tasks, 8.71 questions each) for the same 69.40%. | B | Edwards & Schuster 2026 | measure: cost per stop and questions per stop; cap questions per stop comment |
| SP-12 | Tasks on which the agent asked resolved at 65.99% vs 76.92% where it did not. | B | Edwards & Schuster 2026 | measure: resolution after a lead answer, kept separate from never-stopped tickets, so asked tickets are not read as worker failures |
| SP-13 | A 3.0-question clarifier matched GPT-5's 5.1 questions (36.8% vs 35.6%, fully specified 41.6%, none 22.4%); real underspecified issues most often lack error information (65%), implementation details (37%) and expected behaviour (33%). | B | Vijayvargiya, Viswanathan & Neubig 2026 | rule: a stop comment lists at most three design-changing questions; the ticket template asks for error output, expected behaviour and implementation constraints |
| SP-14 | Single-turn key-question coverage (0.77–0.87) correlates only r=0.32 with multi-turn coverage (0.38–0.75); GPT-4o pass@1 is 8.94% under ambiguity vs 89.02% with oracle clarification. | B | Li, Wu & Chang 2026 | default: one stop carrying every question, answered once, over iterative back-and-forth |
| SP-15 | Resolving every ambiguity happens 0.30 of the time with one ambiguity, 0.08 with two and "almost zero" with three; turning on thinking raised full pass@1 (48.8 → 50.0) but lowered ambiguous pass@1 (38.2 → 34.3) on Claude Sonnet 4.5. | B | Fang et al. 2026 | reject: raising effort or model tier to cope with vague tickets; fix the ticket at grooming instead |
| SP-16 | An agent made to ask raised communication rate 14.21% → 72.73% and modified-problem pass@1 31.34% → 39.62%, but original-problem pass@1 fell 65.58% → 27.45% from unnecessary questions. | B | Wu & Fard 2025 | measure: false-positive stops (stops the lead answers "the ticket already says") as a tracked rate |
| SP-17 | Disagreement among independently sampled solutions is a workable ambiguity detector: gating on it raised GPT-4 pass@1 70.96% → 80.80% with human answers. | B | Mu et al. 2023 | flag: scripted ambiguity check that drafts two plans and stops only if they differ on a design point |
| SP-18 | Ground-truth tests in the prompt raise GPT-4-32k MBPP pass@1 from 67.13% to 78.26% (one test) and 80.88% (all); interactive test clarification reaches 81.56% after one interaction; humans judged correctness at 0.84 vs 0.40 (p=0.001) at no significant time cost. | A | Fakhoury et al. 2024 | default: a lead answering a stop gives an example or a test where it can, not prose |
| SP-19 | Visible tests roughly double pass@1 on BigCodeBench-Hard (24.32 → 50.68 Qwen2.5-14B; 23.6 → 54.7 Phi-4) and "do not use" instructions do not negate the gain. | B | Ben Sghaier et al. 2025 | rule: verify tests the worker can see are not the only acceptance evidence; reviewers check passing tests for special-casing |
| SP-20 | Writing pre- and post-conditions before generating tests raised bug detection 53.4% → 63.2% (p=0.0352) and branch coverage 46.4% → 48.9% (p=0.0034) on 90 Google bugs. | A | Tufano et al. 2026 | flag: worker step "state the contract (pre/post-conditions) in the PR before writing tests" |
| SP-21 | Supplying a generated bug reproduction test with the report let the repair system fix 30% more bugs (80 bugs). | C | Cheng et al. 2025 | flag: reproduce-first step for bug tickets; the scout or worker attaches a failing test before the fix |
| SP-22 | Eliciting design decisions up front and encoding each as tests made described-vs-implemented mismatches 79% less likely (RR 0.21 [0.08, 0.55], 4.7% vs 12.1%) with no NASA-TLX difference, N=14. | A | Kasibatla et al. 2026 | flag: lead-side elicitation pass for feature tickets that records decisions in the ticket |
| SP-23 | Structured recursive elicitation raised requirement alignment to 0.554 from 0.359 (vanilla interaction, Gemini 2.5 Pro; "+54% relative"), but the human-judged implementation gain was 0.453 → 0.520. | B | Zhou et al. 2026 | flag: same as SP-22, scoped to spec-writing; the human-judged effect is modest |
| SP-24 | 32.67% of SWE-Agent + GPT-4's successes had the solution in the issue or comments; filtering those and weak tests cut the resolve rate 12.47% → 3.97%. | B | Aleithan et al. 2024 | measure: tag tickets that carry a fix so pass rates are read per class; benchmark figures overstate open-ticket success |
| SP-25 | Expert annotators judged a large share of SWE-bench issues underspecified (38.3%, secondary) on a 0–3 scale with the highest of three labels taken. | C | OpenAI 2024 | rule: the scout's pre-flight uses the 0–3 underspecification scale; a third of inbound tickets scoring ≥2 is normal |
| SP-26 | Contradiction: natural-language reproduction steps are positive in one observational model (OR 1.47), not significant in another (OR 0.83) and negative for Copilot merge (−11.53 points), while an executable reproduction is positive everywhere (OR 2.52; +30% fixes). | A | Bruno 2026; Khatib 2026; Sayagh 2025; Cheng 2025 | rule: ask for a runnable reproduction; prose steps are optional |
| SP-27 | Contradiction: more operative detail (fix direction, localization, expected behaviour) helps while more length, comments, environment and dependency detail hurts (SP-03, SP-05, SP-07); practitioners say implementation detail in specs decays. | B | Khatib 2026; Sayagh 2025; Agarwal 2026 | rule: tickets carry the operative detail and nothing else; durable intent goes to ADRs |
| SP-28 | Every interaction result above used a simulated user holding the full issue (GPT-4o, GPT-5.1, DeepSeek-R1); answerability of questions was only 0.37. | B | Vijayvargiya 2026 ×2; Edwards & Schuster 2026 | measure: how often the lead can answer a stop from the ticket alone vs bubbles it to the developer |

## Candidate paradigms

1. **Stop-and-ask on design-changing ambiguity** (today's default): the worker stops with `ambiguous` or `no-spec` and a lead answers once. Backed by SP-01, SP-02, SP-08, SP-14, SP-18. Best grade A.
2. **Pre-dispatch underspecification gate**: a scout or small assessor scores the ticket (0–3 scale, missing error output, expected behaviour, file reference, acceptance criteria) and files `needs-info` before any worker is spent; the worker's own stop stays as the backstop. Backed by SP-07, SP-09, SP-10, SP-11, SP-13, SP-25. Best grade B (A for the detection weakness it fixes).
3. **Repair-spec ticket template**: localization, fix direction, expected behaviour, runnable reproduction, short, headed, one change per ticket; no environment or dependency prose. Backed by SP-02 to SP-07, SP-26, SP-27. Best grade A.
4. **Reproduce-first and contract-first worker steps**: a failing reproduction test before the fix for bug tickets; pre- and post-conditions stated before tests for all tickets. Backed by SP-20, SP-21, SP-03. Best grade A.
5. **Lead-side decision elicitation**: for feature tickets the lead runs a short structured elicitation, records the decisions in the ticket (or an ADR) and, where it can, as tests. Backed by SP-22, SP-23, SP-18. Best grade A (N=14).
6. **Scripted ambiguity detector**: draft two independent plans; stop only when they differ on a design point. Backed by SP-17, and the functional-divergence symptom in Yang et al. Best grade B.
7. **Held-out acceptance evidence**: tests the worker can see are a spec it will fit; the verdict rests on tests or checks it did not see, and reviewers look for special-casing. Backed by SP-19, SP-24. Best grade B.
8. **Bounded questioning**: at most three questions per stop, one stop per ticket where possible, false-positive stops tracked. Backed by SP-11, SP-13, SP-14, SP-16. Best grade B.

## Open questions

- Every clarification result uses a simulated user who holds the full issue. No study measures a human lead who may not know the answer either, nor the latency of a stop that waits for one. SP-28 is the instrumentation to settle it locally.
- The ticket-content studies are bug-fix heavy (SWE-bench Verified, Google bugs). Whether a runnable reproduction or localization transfers to feature and refactoring tickets is untested; Bruno et al. excluded those.
- Nobody has measured the plugin's own stop rule: how often a Claude worker under `agents/worker.md` stops, how often the lead's answer is already in the ticket, and what the resolve rate is after an answer. The ask-rate tables (SP-09) are for prompts the plugin does not use.
- The over-asking cost is measured only as lost pass@1 on well-specified tasks (SP-16) and dollars (SP-11); no controlled study measures developer time or annoyance from clarifying questions.
- Acceptance criteria quality as its own variable (Gherkin, user-story form, test-grounded requirements) has only the SWE-Bench Pro ablation; no study varies the form of the criteria.
- Spec-first elicitation has large effects in small or LLM-judged studies (SP-22, SP-23) and the human-judged effect is modest; it needs a field trial before it becomes a default.
- SWE-bench Verified is in training data for the models studied, and the OpenAI underspecification percentages could not be read from the primary page in this session.

## Sources list

- Khatib, L., Mathews, N. S., Nagappan, M., Nie, P., & Zimmermann, T. (2026). What Makes a Good Bug Report for an AI Agent? Preprint, arXiv:2607.07593. https://arxiv.org/abs/2607.07593
- Bruno, V. L., Giagnorio, A., Bifolco, D., Wienges, L., Di Penta, M., & Bavota, G. (2026). Writing Bug Reports for Software Repair Agents: What Information Matters Most? Preprint, arXiv:2607.09553. https://arxiv.org/abs/2607.09553
- Vijayvargiya, S., Zhou, X., Yerukola, A., Sap, M., & Neubig, G. (2026). Ambig-SWE: Interactive Agents to Overcome Underspecificity in Software Engineering. ICLR 2026. arXiv:2502.13069. https://arxiv.org/abs/2502.13069
- Fakhoury, S., Naik, A., Sakkas, G., Chakraborty, S., & Lahiri, S. K. (2024). LLM-Based Test-Driven Interactive Code Generation: User Study and Empirical Evaluation. IEEE Transactions on Software Engineering, 50(9), 2254–2268. arXiv:2404.10100. https://arxiv.org/abs/2404.10100
- Sayagh, M. (2025). What Makes a GitHub Issue Ready for Copilot? Preprint, arXiv:2512.21426. https://arxiv.org/abs/2512.21426
- Edwards, N., & Schuster, S. (2026). Ask or Assume? Uncertainty-Aware Clarification-Seeking in Coding Agents. Preprint, arXiv:2603.26233. https://arxiv.org/abs/2603.26233
- Vijayvargiya, S., Viswanathan, V., & Neubig, G. (2026). Asking What Matters: Reward-Driven Clarification for Software Engineering Tasks. Preprint, arXiv:2604.14624. https://arxiv.org/abs/2604.14624
- Deng, X., Da, J., et al. (2025). SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks? Preprint, arXiv:2509.16941. https://arxiv.org/abs/2509.16941
- Tufano, M., McClure, J., Cambronero, J., Cheng, R., Shi, S. Y., Wei, R., Chen, D., Ivančić, F., Dalloro, L., & Rondon, P. (2026). Grounding AI Agents in Contracts: An Empirical Evaluation of Spec-Driven Test Generation. SpecOps '26, ACM. arXiv:2608.17177. https://research.google/pubs/grounding-ai-agents-in-contracts-an-empirical-evaluation-of-spec-driven-test-generation/
- Kasibatla, S. R., Rothkopf, R., Peleg, H., Pierce, B. C., Lerner, S., Goldstein, H., & Polikarpova, N. (2026). Decision-Oriented Programming with Aporia. Preprint, arXiv:2604.05203. https://arxiv.org/abs/2604.05203
- Ben Sghaier, O., Delcourt, K., & Sahraoui, H. (2025). Artificial or Just Artful? Do LLMs Bend the Rules in Programming? Preprint, arXiv:2512.21028. https://arxiv.org/abs/2512.21028
- Aleithan, R., Xue, H., Mohajer, M. M., Nnorom, E., Uddin, G., & Wang, S. (2024). SWE-Bench+: Enhanced Coding Benchmark for LLMs. Preprint, arXiv:2410.06992. https://arxiv.org/abs/2410.06992
- OpenAI. (2024). Introducing SWE-bench Verified. https://openai.com/index/introducing-swe-bench-verified/ (percentages secondary)
- OpenAI. (2026). Why we no longer evaluate SWE-bench Verified. https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ (secondary)
- Li, J., Wu, Y., & Chang, Y. (2026). ClarEval: A Benchmark for Evaluating Clarification Skills of Code Agents under Ambiguous Instructions. Preprint, arXiv:2603.00187. https://arxiv.org/abs/2603.00187
- Fang, Z., Jin, D., Dong, Y., Li, Y., Zhang, K., Jin, Z., & Li, G. (2026). ClarifyCodeBench: Evaluating LLMs on Clarifying Ambiguous Requirements for Code Generation. Preprint, arXiv:2607.00711. https://arxiv.org/abs/2607.00711
- Wu, J. J. W., & Fard, F. H. (2025). HumanEvalComm: Benchmarking the Communication Competence of Code Generation for LLMs and LLM Agent. ACM TOSEM. arXiv:2406.00215. https://arxiv.org/abs/2406.00215
- Mu, F., Shi, L., Wang, S., Yu, Z., Zhang, B., Wang, C., Liu, S., & Wang, Q. (2023). ClarifyGPT: Empowering LLM-based Code Generation with Intention Clarification. Preprint, arXiv:2310.10996. https://arxiv.org/abs/2310.10996
- Zhou, E., Xi, Z., Ma, L., Zhang, Z., Dou, S., Lei, Z., Wang, G., Zheng, R., Yan, H., Gui, T., Zhang, Q., & Huang, X. (2026). Steering LLMs via Scalable Interactive Oversight. Preprint, arXiv:2602.04210. https://arxiv.org/abs/2602.04210
- Yang, D., Xie, X., Yang, X., Hu, M., Huang, Y., Zhang, Y., Miao, W., Su, T., Wan, C., & Pu, G. (2026). Clarity Is Not Assumed: Understanding LLM-Based Code Generation under Ambiguous Requirements. Preprint, arXiv:2604.21505. https://arxiv.org/abs/2604.21505
- Cheng, R., Tufano, M., Cito, J., Cambronero, J., Rondon, P., Wei, R., Sun, A., & Chandra, S. (2025). Agentic Bug Reproduction for Effective Automated Program Repair at Google. Preprint, arXiv:2502.01821. https://arxiv.org/abs/2502.01821
- Agarwal, S., Singhal, A., Macvean, A., Vasilescu, B., & Breaux, T. (2026). Spec-Driven Development as Prescribed and as Practiced. Preprint, arXiv:2608.25202. https://arxiv.org/abs/2608.25202
- Zhang, R., & Vempali, A. (2025). Improving coding agent experience. Atlassian Engineering blog. https://www.atlassian.com/blog/atlassian-engineering/improving-coding-agent-experience
- Lipsanen, P., Rannikko, L., Christophe, F., Kalliokoski, K., Stirbu, V., & Mikkonen, T. (2026). Shift-Up: A Framework for Software Engineering Guardrails in AI-native Software Development: Initial Findings. VibeX 2026. arXiv:2604.20436. https://arxiv.org/abs/2604.20436
- Darji, H., & Lutellier, T. (2025). Curiosity by Design: An LLM-based Coding Assistant Asking Clarification Questions. Preprint, arXiv:2507.21285. https://arxiv.org/abs/2507.21285
- Thurlow, S. (2026). Human Oversight System (HOS), documentation and research notes, https://github.com/thurlow-research/HumanOversightSystem (software archived at https://doi.org/10.5281/zenodo.21347272), docs CC BY 4.0, code MIT. Used as an index to Zhou et al. (2026), Kasibatla et al. (2026) and Lipsanen et al. (2026), each then read from its primary source.
