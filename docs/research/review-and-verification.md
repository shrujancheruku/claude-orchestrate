# Review and verification: evidence for orchestrating AI coding agents

Ticket #603, part of the #588 evidence map. Area prefix `RV`. Sibling files: `docs/research/reviewer-benchmarks.md` (branch `research/reviewer-benchmarks`, #598: how HOS, SWE-PRBench and SCRBench are built and scored) and `docs/research/reviewer-framing-and-context.md` (branch `research/reviewer-framing-and-context`, #599: the framing-attack and context-size evidence in detail). This file goes wider and only summarises those two where a finding depends on them.

**Summary.** The strongest evidence (A) says a model cannot reliably review or repair its own output without an external signal: intrinsic self-correction lowers accuracy (GPT-4 on GSM8K 95.5% to 89.0% after two rounds), self-repair at matched budget gains at most 8% and feedback from a stronger model or a human (1.58x) is what lifts it, and evaluators favour their own generations in proportion to how well they recognise them. Execution is the signal that works: generated tests lift pass@1 by 18.8 points (CodeT), removing test generation from Reflexion drops below baseline (0.52 vs 0.60), reproduction plus regression tests add 6.3 points of patch selection (Agentless), and a hybrid of executed tests and an LLM verifier reaches 51% where either alone saturates at 42 to 43% (R2E-Gym); yet tests are weak oracles (UTBoost: 345 wrongly "passed" patches, 24.4% of SWE-bench Verified entries affected). An independent LLM reviewer catches real bugs (CriticGPT critiques preferred over human critiques 62.8%, CI 60.5 to 65.1) but hallucinates more than humans, is flipped by prompt cues (small judges move up to 37 points, consistency as low as 50%), and finds only 15 to 31% of what humans flag on real PRs (SWE-PRBench). Cross-vendor review, review panels, debate and corroboration ranking are plausible and cheap to try but rest on B and C evidence: directional case studies (Zietsman: Gemini 5/5 where Claude and Codex 0/5 on one planted bug, not significant), QA-domain panel results (PoLL), and debate that does not beat self-consistency at equal cost. Human review of agentic PRs accepts 83.8% but changes 45.1% of what it merges, 67.9% of rejections carry no written reason, and an automated reviewer in industry lengthened PR closure from 5h52 to 8h20 while 73.8% of its comments were resolved: review volume and precision, not just recall, decide whether the loop pays.

## Sources

### 1. Huang et al. 2024, intrinsic self-correction degrades reasoning (ICLR 2024)

Huang, J., Chen, X., Mishra, S., Zheng, H. S., Yu, A. W., Song, X., Zhou, D. (2024). Large Language Models Cannot Self-Correct Reasoning Yet. ICLR 2024. arXiv:2310.01798. https://arxiv.org/abs/2310.01798

Design: controlled experiment; the same model revises its own answer with no external feedback ("intrinsic self-correction"), compared with an oracle-label variant and with multi-agent debate at matched call counts. Population: GPT-3.5, GPT-4, GPT-4-Turbo, Llama-2-70B on GSM8K, CommonSenseQA, HotpotQA.

Results (Table 3 and 4): GPT-4 GSM8K 95.5 standard, 91.5 after round 1, 89.0 after round 2; GPT-3.5 CommonSenseQA 75.8 to 38.1 to 41.8; Llama-2 GSM8K 62.0 to 43.5 to 36.5. With oracle labels telling the model when to stop, GPT-4 GSM8K rises to 97.5 (Table 2), which is where earlier "self-correction works" claims came from. Multi-agent debate vs self-consistency at equal responses (Table 7, GSM8K): 6 responses, debate 83.2 vs self-consistency 85.3; 9 responses, debate 83.0 vs 88.2. Models change correct answers to wrong ones about as often as the reverse.

Limitations: reasoning benchmarks, not code; 2023 models; the oracle condition is the only one that helps, which is the point.

For orchestrate: a worker asked to "review your own diff" with no new signal is expected to make it worse, not better; the review step has to bring a signal the author did not have (a test run, a different model or context, the ticket's criteria read fresh). Debate does not buy more than voting at the same price.

### 2. Olausson et al. 2024, self-repair is bottlenecked by the model's own feedback (ICLR 2024)

Olausson, T. X., Inala, J. P., Wang, C., Gao, J., Solar-Lezama, A. (2024). Is Self-Repair a Silver Bullet for Code Generation? ICLR 2024. arXiv:2306.09896. https://arxiv.org/abs/2306.09896

Design: controlled experiment at matched token budgets (N_p = 50 initial programs, N_f = 25 feedback strings, 1,000 bootstrapped sub-trees per setting), plus a human study (16 participants, 40 failing GPT-4 programs, 20 APPS tasks). Population: Code Llama, GPT-3.5, GPT-4 on HumanEval and 300 APPS tasks.

Results: GPT-4 self-repair beats the no-repair baseline by up to 8% on APPS; 10 initial samples with 1 repair each gives 1.05x pass@20, 2 samples with 10 repairs each gives 0.97x pass@22 (more repair rounds on fewer drafts is worse than more drafts). Repair success rate on APPS: GPT-3.5 with its own feedback 4.7%, GPT-3.5 with GPT-4 feedback 11.5% (Table 2). GPT-4 repair success 33.3% with GPT-4 feedback vs 52.6% with human feedback, 1.58x.

Limitations: competitive programming tasks; 2023 models; the "stronger model" condition is one direction of model mix only.

For orchestrate: the feedback source is the lever. A reviewer stronger than the worker (or a human) is worth more than another round from the same model; breadth (more candidates) beats depth (more rounds) at equal cost; the three-round cap is consistent with this.

### 3. McAleese et al. 2024, CriticGPT: trained critics catch bugs humans miss and hallucinate more than humans (OpenAI preprint)

McAleese, N., Pokorny, R. M., Ceron Uribe, J. F., Nitishinskaya, E., Trebacz, M., Leike, J. (2024). LLM Critics Help Catch LLM Bugs. Preprint, arXiv:2407.00215. https://arxiv.org/abs/2407.00215

Design: controlled human-preference study with confidence intervals on critiques of ChatGPT-written code, with naturally occurring bugs and with human-inserted ("tampered") bugs; comparison of critiques by contractors (median 50 minutes per example, median five years of Python), ChatGPT, CriticGPT, and human-plus-CriticGPT teams. Population: GPT-4-family critics trained with RLHF; OpenAI contractors.

Results: on code with naturally occurring errors, CriticGPT critiques are preferred over human critiques 62.8% (95% CI 60.5 to 65.1, Table 3), and human-plus-CriticGPT over human 60.4%; the introduction gives "more than 80%" for inserted bugs. LLM critics catch substantially more inserted bugs than contractors (Figure 1b, counts not in text). Hallucinated bugs and nitpicks are far more frequent in LLM critiques than in human ones; CriticGPT lowers them relative to ChatGPT and the human-machine team sits between (Figure 7, rates only in the figure). Of ChatGPT training completions rated "flawless", critiques found a problem that substantially lowered the rating in 24% of cases vs 6% on a no-critique replication. Force Sampling Beam Search (28 samples per input) trades comprehensiveness against spurious claims along a Pareto curve.

Limitations: industry preprint; the critic was trained for this; the "human" baseline is contractors, not code owners; exact hallucination rates are not in the text.

For orchestrate: an LLM reviewer finds real bugs that a human reviewer would miss, and it also files findings that are wrong; the reviewer's output has to be filtered (a finding names a `path:line` and a concrete failure) and the worker needs a way to contest a finding that does not reproduce. Human-plus-model is the lowest-hallucination configuration.

### 4. Panickssery, Bowman, Feng 2024, evaluators favour their own generations (NeurIPS 2024)

Panickssery, A., Bowman, S. R., Feng, S. (2024). LLM Evaluators Recognize and Favor Their Own Generations. NeurIPS 2024 (oral). arXiv:2404.13076. https://arxiv.org/abs/2404.13076

Design: controlled experiment with fine-tuning to vary self-recognition and measure self-preference, with confounder controls. Population: GPT-4 and Llama 2 evaluating summaries (XSUM, CNN/DailyMail), against human and other-model outputs.

Results: out of the box, GPT-4 and Llama 2 distinguish their own text with non-trivial accuracy; fine-tuning shows a linear correlation between self-recognition and the strength of self-preference bias, and the controlled experiments support a causal reading.

Limitations: summarisation, not code; the effect size in code review is unmeasured.

For orchestrate: the baseline worry about a same-model reviewer is real and mechanistic, not folklore; a different model (or at least a different context with no access to the author's reasoning) is the mitigation, and the size of the effect on code is an open measurement.

### 5. Kenton et al. 2024 and Khan et al. 2024, debate helps a weak judge mainly under information asymmetry

Kenton, Z. et al. (2024). On scalable oversight with weak LLMs judging strong LLMs. NeurIPS 2024. arXiv:2407.04622. https://arxiv.org/abs/2407.04622
Khan, A. et al. (2024). Debating with More Persuasive LLMs Leads to More Truthful Answers. ICML 2024. arXiv:2402.06782. https://arxiv.org/abs/2402.06782

Design: controlled experiments with weak judges (LLM and human) over debate, consultancy and direct QA protocols. Population: Gemini-family debaters and judges (Kenton); Claude 2.1 and GPT-4 debaters, human and LLM judges on QuALITY (Khan).

Results: Khan: with the most persuasive debaters, non-expert LLM judges reach 76% and human judges 88% on QuALITY, vs 48% and 60% naive and 54% and 78% with consultancy. Kenton: debate beats consultancy on every task; stronger debaters raise judge accuracy "more modestly than in previous studies"; against direct QA, debate wins on extractive QA with information asymmetry and is mixed elsewhere.

Limitations: reading-comprehension tasks where the judge cannot see the text; no code; consultancy is a weak baseline (50% of consultants argue the wrong side); Khan's accuracy is inflated by incorrect debaters conceding (secondary).

For orchestrate: debate earns its cost only where the judge lacks the artefact (here the reviewer has the diff, the tests and the repo, so the asymmetry is small); an adversarial "argue it is wrong" second reviewer is a flag for L, not a default.

### 6. Smit et al. 2024, multi-agent debate does not reliably beat self-consistency (ICML 2024)

Smit, A., Grinsztajn, N., Duckworth, P., Barrett, T., Pretorius, A. (2024). Should we be going MAD? A Look at Multi-Agent Debate Strategies for LLMs. ICML 2024, PMLR 235:45883-45905. arXiv:2311.17371. https://proceedings.mlr.press/v235/smit24a.html

Design: benchmark of debate protocols against self-consistency and reasoning-path ensembles with hyperparameter tuning. Population: GPT-3.5-era models on medical and general QA.

Results: current debate systems "do not reliably outperform" self-consistency and ensembling; they are more sensitive to hyperparameters; a tuned agreement-intensity variant can surpass non-debate protocols.

Limitations: QA, not code; one model generation.

For orchestrate: with Huang Table 7, two independent sources say debate is a tuning-heavy way to buy what voting buys; reject as a default review paradigm.

### 7. Shinn et al. 2023, Reflexion: the gain comes from self-generated tests, and bad tests hurt (NeurIPS 2023)

Shinn, N., Cassano, F., Gopinath, A., Narasimhan, K., Yao, S. (2023). Reflexion: Language Agents with Verbal Reinforcement Learning. NeurIPS 2023. arXiv:2303.11366. https://arxiv.org/abs/2303.11366

Design: benchmark with ablation. Population: GPT-4 on HumanEval, MBPP (Python and Rust), LeetcodeHard.

Results (Tables 1, 2, ablation): HumanEval Python 80.1 to 91.0 pass@1; MBPP Python 80.1 to 77.1 (a drop); HumanEval Rust 60.0 to 68.0; LeetcodeHard 7.5 to 15.0. Ablation on the 50 hardest Rust problems: base 0.60, Reflexion without test generation 0.52, without self-reflection 0.60, full 0.68. The MBPP drop tracks the false-positive rate of generated tests (unit tests pass, solution fails): 16.3% on MBPP vs 1.4% on HumanEval. The authors prefer false negatives (good solution, failing test) because reflection can repair a test but not a wrongly accepted solution.

Limitations: function-level tasks; one model; test quality measured only post hoc.

For orchestrate: a verification loop with no executable check is worse than no loop; a loop with a wrong check accepts wrong code. The worker's "run the suite before opening the PR" step is the carrier of value; a test the worker writes to confirm its own change is a false-positive risk, which is why the reviewer's "new tests would fail without the change" check at M matters.

### 8. Madaan et al. 2023, Self-Refine: real gains, most in the first iteration (NeurIPS 2023)

Madaan, A. et al. (2023). Self-Refine: Iterative Refinement with Self-Feedback. NeurIPS 2023. arXiv:2303.17651. https://arxiv.org/abs/2303.17651

Design: benchmark across seven tasks, up to four feedback-refine iterations, same model as generator, critic and refiner. Population: GPT-3.5, ChatGPT, GPT-4.

Results (Table 1): Code Optimization (% programs optimized, PIE) GPT-4 27.3 to 36.0, ChatGPT 23.9 to 27.5, GPT-3.5 14.8 to 23.0; Code Readability GPT-4 27.4 to 56.2 (preference-based). Section 4: Code Optimization average 22.0 at y0 to 28.8 at y3 with most of the gain in the first iteration and "diminishing returns" after.

Limitations: the metrics for code tasks are a performance speed-up and a preference score, not correctness; Huang et al. (source 1) argue such gains depend on tasks where the model has a usable self-signal.

For orchestrate: one refine round on a soft target (readability, performance) pays; a second and third pay little. Treats "rounds" as the budget: round 1 should carry the blocking findings, and a review round that adds only minors is not worth a worker cycle.

### 9. Chen et al. 2023, CodeT: generated tests plus dual agreement (ICLR 2023)

Chen, B., Zhang, F., Nguyen, A., Zan, D., Lin, Z., Lou, J.-G., Chen, W. (2023). CodeT: Code Generation with Generated Tests. ICLR 2023. arXiv:2207.10397. https://arxiv.org/abs/2207.10397

Design: benchmark; the model generates tests and candidates, executes, and ranks by "dual execution agreement" (candidates that agree with each other on the generated tests). Population: five models including code-davinci-002 on HumanEval, MBPP, APPS, CodeContests.

Results: HumanEval pass@1 65.8% with code-davinci-002, an absolute 18.8-point gain over the model's baseline and over 20 points over the prior state of the art.

Limitations: ranking among many samples, not reviewing one PR; 2022 models.

For orchestrate: agreement between independently generated artefacts (tests from one place, code from another) is a verifier that needs no ground truth; it is the mechanism behind Agentless's selection and behind a "test designer separate from the coder" paradigm.

### 10. Xia et al. 2024, Agentless: reproduction plus regression tests select better patches

Xia, C. S., Deng, Y., Dunn, S., Zhang, L. (2024). Agentless: Demystifying LLM-based Software Engineering Agents. Preprint, arXiv:2407.01489 (v2). https://arxiv.org/abs/2407.01489

Design: benchmark with a patch-selection ablation (Table 4), 40 candidate patches and 40 reproduction tests per issue. Population: Claude 3.5 Sonnet on SWE-bench Lite (300 issues).

Results: majority voting alone 77 resolved (25.67%); plus regression tests 81 (27.00%); plus generated reproduction tests 96 (32.00%), at $0.25 extra per issue; oracle over all sampled patches 126 (42.0%). Reproduction tests are scored with CodeT's dual execution agreement.

Limitations: one model, one benchmark, Lite (which the authors show has problematic issues); selection, not single-PR review.

For orchestrate: existing tests (regression) are a cheap first filter and a generated failing-then-passing reproduction test is the second; together they close a quarter of the gap to the oracle. The reviewer's suite run at M and L is the regression half; the reproduction half (a test from the ticket's criteria, written before or independently of the fix) is a candidate step.

### 11. Jain et al. 2025, R2E-Gym: execution and LLM verifiers are complementary

Jain, N., Singh, J., Shetty, M., Zheng, L., Sen, K., Stoica, I. (2025). R2E-Gym: Procedural Environments and Hybrid Verifiers for Scaling Open-Weights SWE Agents. Preprint, arXiv:2504.07164. https://arxiv.org/abs/2504.07164

Design: benchmark with ablation of verifier types at test time. Population: open-weight 32B agent on SWE-bench Verified.

Results: execution-based verifier and execution-free (LLM) verifier each saturate at 42 to 43% Best@K; the hybrid reaches 51%. Execution-based verifiers cannot separate patches with identical test pass counts; the execution-free verifier leans on the agent's reasoning trace, not only the patch.

Limitations: open-weight models; selection among K rollouts, not a PR review.

For orchestrate: tests and an LLM reviewer catch different things, which is the case for running both at M and L rather than treating a green suite as approval. Giving the reviewer the worker's report (its reasoning) and not only the diff is supported here and cuts against "diff only"; see source 20 for the opposite pull on context size.

### 12. Brown et al. 2024 and Ehrlich et al. 2025, repeated sampling is bottlenecked by the verifier

Brown, B., Juravsky, J., Ehrlich, R., Clark, R., Le, Q. V., Ré, C., Mirhoseini, A. (2024). Large Language Monkeys: Scaling Inference Compute with Repeated Sampling. Preprint, arXiv:2407.21787. https://arxiv.org/abs/2407.21787
Ehrlich, R., Brown, B., Juravsky, J., Clark, R., Ré, C., Mirhoseini, A. (2025). CodeMonkeys: Scaling Test-Time Compute for Software Engineering. Preprint, arXiv:2501.14723. https://arxiv.org/abs/2501.14723

Design: benchmark with scaling curves and selection ablations. Population: DeepSeek-Coder-V2-Instruct on SWE-bench Lite (Brown); Claude 3.5 Sonnet on SWE-bench Verified (Ehrlich).

Results: Brown: coverage on SWE-bench Lite rises from 15.9% at one sample to 56% at 250; where a checker exists (unit tests) the gain is realised, where it does not, majority voting and reward models plateau. Ehrlich: 57.4% on Verified for about $2,300 of inference; candidate coverage 69.8%, random selection 45.8%, so model-written tests plus model selection close about half the gap; selecting over an ensemble of top submissions' edits reaches 66.2%, above the best member.

Limitations: cost of hundreds of samples; selection among candidates, not review of one.

For orchestrate: a best-of-N worker paradigm is only as good as its verifier; without executable acceptance tests it is money for nothing. An ensemble that picks between two workers' PRs beats either worker, if a verifier exists.

### 13. Yu et al. 2025, UTBoost: passing tests is not correctness (ACL 2025)

Yu, B., Zhu, Y., He, P., Kang, S. (2025). UTBoost: Rigorous Evaluation of Coding Agents on SWE-Bench. ACL 2025. arXiv:2506.09289. https://arxiv.org/abs/2506.09289

Design: large field audit; an LLM test generator augments SWE-bench tests and leaderboard patches are re-scored.

Results: 36 task instances with insufficient tests; 345 patches previously labelled as passing are erroneous; 40.9% of SWE-bench Lite and 24.4% of SWE-bench Verified leaderboard entries affected, with 18 and 11 ranking changes. Separately, 32.67% of successful patches had the solution leaked in the issue text (secondary: from the abstract as quoted in search).

Limitations: SWE-bench only; the augmenting tests are themselves LLM-written.

For orchestrate: a green suite is necessary, not sufficient; the reviewer's correctness read at M is the complement, and the escaped-defect record (#589) is the only honest measure of what the suite plus reviewer missed.

### 14. Shi et al. 2025, an LLM judge agrees with humans only where humans agree (Google)

Shi, S., Wei, R., Tufano, M., Cambronero, J., Cheng, R., Ivančić, F., Rondon, P. (2025). Towards a Human-in-the-Loop Framework for Reliable Patch Evaluation Using an LLM-as-a-Judge. Preprint, arXiv:2511.10865 (AI-SQE '26 workshop per v2). https://arxiv.org/abs/2511.10865

Design: measurement with inter-rater statistics. Population: 115 candidate patches for 48 sanitizer-reported bugs at Google; three developer raters; an LLM judge given a human-refined rubric.

Results: independent developers agree poorly on patch validity (Fleiss's kappa 0.31). On the unanimous subset the judge reaches Cohen's kappa 0.75, recall 0.94, precision 0.80; on the full set kappa 0.57, recall 0.93, precision 0.65.

Limitations: 115 patches; one company's bug class; the rubric is human-edited.

For orchestrate: the judge's precision falls where the question is genuinely contested; a rubric per ticket (the acceptance criteria) is what makes the judge usable, and a reviewer verdict on an ambiguous criterion should route to a human rather than block or approve. A 0.31 human kappa also caps how exact any planted-defect benchmark pass criterion can be.

### 15. LLM judges for code: CodeJudge, CodeJudgeBench, CodeJudge-Eval

Tong, W., Zhang, T. (2024). CodeJudge: Evaluating Code Generation with Large Language Models. EMNLP 2024, pp. 20032-20051. arXiv:2410.02184. https://aclanthology.org/2024.emnlp-main.1118
Jiang, H., Chen, Y., Cao, Y., Lee, H., Tan, R. T. (2025). CodeJudgeBench: Benchmarking LLM-as-a-Judge for Coding Tasks. Preprint arXiv:2507.10535 (ACL 2026 per the ACL Anthology listing, secondary). https://arxiv.org/abs/2507.10535
Zhao, Y., Luo, Z., Tian, Y., Lin, H., Yan, W., Li, A., Ma, J. (2024). CodeJudge-Eval: Can Large Language Models be Good Judges in Code Understanding? Preprint arXiv:2408.10718. https://arxiv.org/abs/2408.10718

Design: benchmarks of execution-free judging against execution ground truth. Population: CodeJudge: four judge LLMs, four datasets, five languages; CodeJudgeBench: 26 judge models on 5,352 pairs (code generation 2,103, repair 2,409, test generation 840); CJ-Eval: 12 LLMs on APPS-derived judging.

Results: CodeJudge's "analyze then summarize" prompting beats a GPT-3.5 baseline even with Llama-3-8B, but competition-level tasks remain hard (average accuracy about 80.56%, secondary). CodeJudgeBench: thinking models "significantly outperform" non-thinking ones (Qwen3-8B beats trained 70B judges); pairwise beats pointwise scoring; swapping response order changes pairwise accuracy substantially; judging the full response with comments beats judging extracted code; all judges show significant randomness. CJ-Eval: even GPT-4o and Claude 3.5 Sonnet "struggle" (macro F1 around 50 on the simplest task, secondary).

Limitations: function-level problems; judges are asked "is this correct", not "find the defect in this PR".

For orchestrate: a reviewer model should be a thinking model at M and L (consistent with the L "high effort" rule); a small non-thinking model as judge is close to chance on hard code; pairwise framings (this diff vs the previous head, or two workers' PRs) are more reliable than a lone score; and the reviewer should read the worker's report, not only the diff.

### 16. Zhao, Esmaeili, Fard 2026, prompt cues move code judges by tens of points

Zhao, Z., Esmaeili, A., Fard, F. (2026). Bias in the Loop: Auditing LLM-as-a-Judge for Software Engineering. Preprint, arXiv:2604.16790. https://arxiv.org/abs/2604.16790

Design: ablation injecting twelve prompt biases (position, authority, bandwagon, chain-of-thought, distraction, diversity, final-only, model-name, refined, self-enhance, sentiment, verbosity) into CodeJudgeBench pairs, with order swapping and test-retest. Population: Qwen3-4B, Qwen2.5-Coder-3B, a GPT model (partial runs).

Results: Qwen2.5-Coder-3B CodeGen accuracy 60.99 to 92.56 under "sentiment" and to 30.36 under "verbosity"; TestGen 62.41 to 25.36 under verbosity; with the gold answer in position B, sentiment drives it to 8.44. Qwen3-4B: bandwagon and model-name cut TestGen from 58.43 to 43.80 and 43.48. GPT: distraction cuts TestGen 77.46 to 60.96; "refined" raises CodeRepair 82.93 to 92.53 (a cue that inflates). Test-retest consistency with no bias: Qwen3-4B 70.6 to 91.4%, Qwen2.5-Coder-3B 50.4 to 65.8%.

Limitations: small open judges carry the largest effects; the GPT runs are partial; pairwise setting.

For orchestrate: anything the author writes into what the judge reads (PR body, commit messages, comments in the diff, a "refined" or "I tested this" claim) is a bias vector with a measured effect on small judges; this is the general case of the framing attacks in #599. Cheaper-routing trials (#513/#514) that put a small model in the reviewer seat must measure consistency, not only agreement.

### 17. Kumar 2026, SWE-PRBench: frontier reviewers find 15 to 31% of human-flagged issues and more context hurts

Kumar, D. (2026). SWE-PRBench: Benchmarking AI Code Review Quality Against Pull Request Feedback. Preprint, arXiv:2603.26130. https://arxiv.org/abs/2603.26130 (detail in #598 and #599)

Design: benchmark; 350 merged PRs with human review comments as ground truth, three frozen context configurations, LLM-as-judge scoring validated at kappa 0.75. Population: eight frontier models.

Results: 15 to 31% of human-flagged issues detected on diff-only; every model degrades monotonically from diff-only to full context; a 2,000-token diff-plus-summary prompt beats a 2,500-token full-context prompt for all eight; top four models statistically indistinguishable (mean 0.147 to 0.153), bottom four at or below 0.113.

Limitations: human comments as ground truth miss what humans missed; single author; judge-scored.

For orchestrate: the ceiling of an LLM-only review on real PRs is low, so the suite and the gate carry weight; context is a budget, not a free good; models cluster, so paying for the top model over the next one buys little on this measure.

### 18. Charoenwet et al. 2026, AgenticSCR: a validator stage cuts comment volume 81% while raising correct comments 2.5x

Charoenwet, W., Tantithamthavorn, K., Thongtanunam, P., Lin, H. Y., Jeong, M., Wu, M. (2026). AgenticSCR: An Autonomous Agentic Secure Code Review for Immature Vulnerabilities Detection. arXiv:2601.19138 (v2 comments: ASE '26 Industry Track; the ticket cites FSE '26). https://arxiv.org/abs/2601.19138 (detail in #598 and #599)

Design: benchmark (SCRBench: 107 CVEs, 144 commits, 92 repositories, 33 CWEs, three languages, line-level ground truth) plus shadow deployment at Atlassian. Population: Claude Sonnet 4 as base model for the agent and both LLM baselines; GPT-4.1 as relevance judge (F1 0.86).

Results: comments correct on localisation, type and relevance: AgenticSCR 17.5% (211 comments), static LLM reviewer 6.9% (652), multi-agent reviewer 5.2% (1,009), Semgrep 3.7%, Snyk 0.9%, CodeQL 0.6%. Comment volume cut 81% in ablation; incorrect comments 71.3 to 85% fewer than baselines. Shadow deployment: 22 of 41 comments (54%) judged worth developer attention by two security engineers. Diff-centric context by design.

Limitations: security defects only; one base model; "worth attention" is not "confirmed vulnerability".

For orchestrate: a second pass that filters the first pass's findings (a validator with a knowledge base, here CWE) is where precision came from; the multi-agent reviewer without such a filter was the noisiest of all. A reviewer's finding list should be filtered before it reaches the worker, and volume is itself a quality metric.

### 19. Framing attacks on review agents: Mitropoulos et al. 2026 and Melo et al. 2026 (Sevra-Bench)

Mitropoulos, D., Alexopoulos, N., Alexopoulos, G., Spinellis, D. (2026). Measuring and Exploiting Contextual Bias in LLM-Assisted Security Code Review. Preprint, arXiv:2603.18740 (v2). https://arxiv.org/abs/2603.18740
Melo, R., Fogliato, R., Zhou, S., Thaker, P., Wu, Z. S. (2026). SEVRA-BENCH: Social Engineering of Vulnerabilities in Review Agents. Preprint, arXiv:2606.13757. https://arxiv.org/abs/2606.13757 (detail in #599)

Design: controlled framing conditions (6 LLMs, five framings) then attacks on 17 CVEs in 10 projects (Mitropoulos); benchmark of 8 review agents on about 1,062 challenge PRs (2,250 total) that reverse a historical vulnerability fix under 15 social-engineering framings (Melo).

Results: Mitropoulos: "bug-free" framing has the strongest effect; template attacks are ineffective and can backfire; an LLM-assisted iterative refinement attack reaches 100% success; metadata redaction plus an explicit instruction restored detection in all affected cases. Melo: Claude Opus 4.7, GPT-5.5 and GLM-5 keep refusal above about 70% under every framing; the weaker five drop below 35 to 40% refusal under "prior approval signal", "dependency compatibility claim" and "fake bug fix"; Grok Code Fast falls to about 10% under "emotional appeal". No defence is tested; the authors recommend treating PR metadata as adversarial and separating claim extraction from claim verification.

Limitations: security PRs; the benchmark keeps only PRs at least one agent approved, so rates are worst-case.

For orchestrate: the trust boundary in #592 (strip the body before dispatch) is the tested mitigation; model choice also matters by tens of points, which bears on cheaper routing of the reviewer.

### 20. Li et al. 2025, CoTDeceptor: obfuscation that evades reasoning reviewers transfers across models

Li, H., Li, M., Zuo, J., Li, S., Li, X., Wu, H., Lu, Y., He, X. (2025). CoTDeceptor: Adversarial Code Obfuscation Against CoT-Enhanced LLM Code Agents. Preprint, arXiv:2512.21250. https://arxiv.org/abs/2512.21250

Design: adversarial benchmark; an RL-driven obfuscator evolved against one detector, tested on others. Population: DeepSeek-R1, GPT-5, distilled DeepSeek and Qwen detectors; a Codex-based agent in a case study.

Results: bypasses 14 of 15 vulnerability categories vs 2 for the prior method; strategies evolved against DeepSeek-R1 "frequently transfer to GPT-5"; a weaker generator fools a stronger detector; the Codex agent did not flag the obfuscated CSRF case. The within-family vs cross-family transfer comparison is not made, and no ensemble numbers are reported (HOS's "transfers within a family, ensembles resist" reading is secondary).

Limitations: deliberately malicious code, not agent-written bugs.

For orchestrate: a cross-vendor reviewer is not a guarantee against a motivated author; deterministic scanners and tests stay in the chain.

### 21. Zietsman 2026, a cross-family panel on five planted bugs (directional)

Zietsman, C. (2026). The Specification as Quality Gate: Three Hypotheses on AI-Assisted Code Review. Preprint, arXiv:2603.25773. https://arxiv.org/abs/2603.25773

Design: single-author case study; three experiments; Experiment 3: five functions with one planted domain-convention bug each, four reviewers, five runs each, 100 runs manually scored, no specification context; BDD scenarios as the comparator. Population: Claude Sonnet 4.6, Codex gpt-5.4, Gemini, Amazon Q (Claude-based).

Results: the executable BDD scenario caught all five. ICAO reserve-fuel bug: Gemini 5/5, Claude 0/5, Codex 0/5, Q 0/5 (Claude asserted the swapped values were correct). ICD-10 sequence bug: all four 0/5. IMO number: Claude, Codex, Gemini 5/5, Q 0/5. Experiment 2 (Claude reviewing Claude): four of five bugs caught 4/5 or better, one 0/5. The author marks the results "directional and not statistically significant" and notes an unused import acted as a hint (removing it dropped Q from 5/5 to 2/5).

Limitations: N=5 bugs; planted; one run family per model; hypotheses, not a test of them.

For orchestrate: the only direct same-family vs cross-family comparison found is weak but points the expected way: a different family catches some of what the author's family misses, and nothing LLM-based catches what only a spec encodes. The "executable spec first, deterministic check second, AI review for the residual" ordering is the structure orchestrate already has (gate, suite, reviewer); cross-vendor is a flag to measure, not a proven default.

### 22. Verga et al. 2024, a panel of three small judges beats one large judge at a seventh of the cost (QA)

Verga, P. et al. (2024). Replacing Judges with Juries: Evaluating LLM Generations with a Panel of Diverse Models. Preprint, arXiv:2404.18796. https://arxiv.org/abs/2404.18796

Design: benchmark across three judge settings and six datasets. Population: a panel of Command R, Claude 3 Haiku and GPT-3.5 vs a single GPT-4 judge; QA and Chatbot Arena.

Results: the panel correlates better with human judgments than GPT-4 alone while costing over seven times less, and pooling across families reduces intra-model bias.

Limitations: QA, not code; 2024 models; a vendor paper.

For orchestrate: a cross-vendor panel of cheap reviewers at S or as the corroboration layer at L is a plausible cheaper-routing design; the code-domain analogue is unmeasured, so it is a flag with a measure attached.

### 23. Cihan et al. 2025, an automated reviewer in industry: comments resolved, PRs slower (ICSE SEIP 2025)

Cihan, U., Haratian, V., İçöz, A., Gül, M. K., Devran, Ö., Bayendur, E. F., Uçar, B. M., Tüzün, E. (2025). Automated Code Review In Practice. ICSE 2025 SEIP. arXiv:2412.18531. https://arxiv.org/abs/2412.18531

Design: field dataset before and after deployment, plus a survey. Population: Qodo PR-Agent-based tool at Beko; 238 practitioners, ten projects; analysis on three projects, 4,335 PRs, 1,568 with automated reviews.

Results: average PR closure time rose from 5h52 to 8h20 (trend varied by project); 73.8% of automated comments marked resolved; practitioners report better bug detection and awareness alongside faulty reviews, unnecessary corrections and irrelevant comments.

Limitations: no control group; three projects; the tool's model is 2024-era.

For orchestrate: an LLM reviewer's cost shows up as latency and worker churn, not only tokens; the round-1 changes rate and time-to-merge per tier belong in the profile; "unnecessary corrections" is the failure a minor-findings policy ("ride along, nobody re-reviews them") is designed to absorb.

### 24. Human review of agentic PRs: Watanabe et al., Nakashima et al., Watanabe (Kan) et al.

Watanabe, M., Li, H., Kashiwa, Y., Reid, B., Iida, H., Hassan, A. E. (2025/2026). On the Use of Agentic Coding: An Empirical Study of Pull Requests on GitHub. arXiv:2509.14745 (v3, 9 Feb 2026; TOSEM acceptance per the ticket and secondary listings, the arXiv page lists no venue). https://arxiv.org/abs/2509.14745
Nakashima, S., Ishimoto, Y., Kondo, M., McIntosh, S., Kamei, Y. (2026). Why Agentic-PRs Get Rejected: A Comparative Study of Coding Agents. Preprint, arXiv:2602.04226. https://arxiv.org/abs/2602.04226
Watanabe, K., Tsuchida, R., Monno, T., Huang, B., Yamasaki, K., Fan, Y., Shimari, K., Matsumoto, K. (2026). How AI Coding Agents Communicate: A Study of Pull Request Description Characteristics and Human Review Responses. Preprint, arXiv:2602.17084 (MSR '26 per secondary listing). https://arxiv.org/abs/2602.17084
Dataset: Li, H., Zhang, H., Hassan, A. E. (2025). The Rise of AI Teammates in Software Engineering (SE) 3.0. Preprint, arXiv:2507.15003 (AIDev: over 456,000 PRs from five agents across about 61,000 repositories, secondary).

Design: field datasets. Population: 567 Claude Code PRs in 157 projects (Watanabe M.); 37,433 PRs with 654 rejected PRs hand-inspected (Nakashima, Table 1); 33,596 agent PRs (Watanabe K.).

Results: Watanabe M.: 83.8% of agent PRs merged (human PRs 91.00%, secondary); 54.9% merged without further change, 45.1% needed changes, mostly bug fixes, documentation and project-standard adherence. Nakashima Table 1 acceptance: Codex 85.8% (N 20,993), Cursor 74.6% (1,347), Claude Code 71.3% (380), Devin 55.5% (4,673), Copilot 55.0% (3,891), human 82.6% (6,149); 67.9% of rejected PRs carry no written reason (69.0% agentic vs 62.4% human); seven rejection modes appear only for agents, including "too large", "no AI confidence" and "context limitation". Watanabe K. Table 4 merge rates: Codex 82.6%, Cursor 65.22%, Claude Code 59.0%, Devin 53.76%, Copilot 43.0%; more structured descriptions (headers, lists) associate with faster reviewer response and completion, but "structure does not determine merges".

Limitations: observational; agents differ in which repos they run in and who presses the button (Codex PRs are often author-reviewed by the same developer); Claude Code N is small; merge rates differ across the three studies by sampling.

For orchestrate: humans change almost half of what they merge from agents, so a reviewer that catches "project-specific standards" (the S checklist) addresses the most frequent human edit; a structured PR body is cheap and associates with faster human response; and because two thirds of human rejections are silent, orchestrate's own review log is the only place the reason will be recorded.

### 25. CodeRabbit 2025, AI-assisted PRs carry 1.7x the review findings (vendor report)

CodeRabbit (2025, 17 Dec). State of AI vs Human Code Generation Report. https://coderabbit.ai/blog/state-of-ai-vs-human-code-generation-report

Design: industry report; 470 open-source PRs (320 AI-co-authored, 150 human-only) scored by CodeRabbit's own reviewer.

Results: 10.83 issues per AI PR vs 6.45 human; 90th percentile 26 vs under half that; logic and correctness issues up 75%; critical security issues 341 vs 240 per 100 PRs; "critical and major defects up to 1.7x".

Limitations: the measuring instrument is the vendor's LLM reviewer, so these are reviewer findings, not confirmed defects; authorship inferred from co-author trailers; no human adjudication reported.

For orchestrate: the only field number on defect density of agent PRs, and it is C-grade; use it to size the review budget per PR, not to claim a defect rate.

### 26. Anthropic 2026, a standalone evaluator is tractable where self-evaluation is not (vendor engineering post)

Rajasekaran, P. (2026, 24 Mar). Harness design for long-running application development. Anthropic Engineering. https://www.anthropic.com/engineering/harness-design-long-running-apps
Anthropic (2026). Best practices for Claude Code. https://code.claude.com/docs/en/best-practices

Design: single-system field observation; a planner, generator and evaluator harness for multi-hour builds. Population: Claude Sonnet 4.5, Opus 4.5, Opus 4.6.

Results: asked to grade their own work, agents "confidently praise the work"; "tuning a standalone evaluator to be skeptical turns out to be far more tractable" than making the generator self-critical; out of the box the evaluator "noticed real issues but then approved the work anyway" and tested superficially ("Out of the box, Claude is a poor QA agent"); calibrating it with few-shot scored examples reduced drift; evaluator scores rose over 5 to 15 iterations before plateauing; a solo run ($9, 20 minutes) shipped broken wiring where the harness ($200, 6 hours) shipped a playable game; the later simplified harness ran the evaluator once at the end, "worth the cost when the task sits beyond what the current model does reliably solo". The best-practices page: `/code-review` runs "in a fresh subagent that never saw your conversation. It isn't swayed by the reasoning that produced the code"; "A reviewer that's asked to find problems will always find something" (hence "skip style"); ask for evidence, the command and its output; a reviewer subagent with `tools: Read, Grep, Glob` is a limit "enforced by Claude Code rather than requested in a prompt"; a subagent is "the same model (or a smaller one) on the same task", "a fresh pair of eyes ... but not a smarter answer".

Limitations: anecdotal; no counts; the vendor's own product.

For orchestrate: this is the design orchestrate has (read-only reviewer agent, fresh context, evidence lines in the verdict) stated by the model vendor, with two cautions it adds: a defect-hunting frame over-produces unless scoped, and a same-model reviewer buys independence, not capability.

### 27. GitHub 2026, the metrics GitHub itself reports for Copilot code review (vendor changelog)

GitHub (2026, 5 Nov). Usage metrics API adds code review details and addressed rate. https://github.blog/changelog/2026-11-05-usage-metrics-api-adds-code-review-details-and-addressed-rate ; GitHub (2026, 8 Apr). Copilot-reviewed pull request merge metrics now in the usage metrics API. https://github.blog/changelog/2026-04-08-copilot-reviewed-pull-request-merge-metrics-now-in-the-usage-metrics-api

Design: product metric definitions, no values published.

Results: `total_comments_addressed / total_comments_evaluated` is the addressed rate, where "addressed" is LLM-judged from the diff, thread, reactions and resolution state at merge time; `total_merged_no_human_change_request_after_review` counts PRs merged where "no person requested changes after Copilot's first review"; median time from PR creation to merge for Copilot-reviewed PRs.

Limitations: definitions only; a vendor's choice of what to show.

For orchestrate: two measures the largest deployer settled on, the addressed rate and merged-with-no-human-change-request, map onto orchestrate's review events (threads closed "fixed" vs "left", and merge with zero human change requests) and belong in the profile.

### 28. Human Oversight System 2026, field notes on cross-vendor review and reviewer hallucination (C)

Thurlow, S. (2026). Human Oversight System (HOS), documentation and research notes, https://github.com/thurlow-research/HumanOversightSystem (software archived at https://doi.org/10.5281/zenodo.21347272), docs CC BY 4.0, code MIT. Findings read: `cross-vendor-review-finds-real-bugs.md`, `reviewer-agents-file-confident-non-reproducing-reports.md`, `corroboration-ranked-review-reduces-noise-without-losing-coverage.md`, `gate-on-computed-signal-not-self-reported-verdict.md`, `three-tier-review-cost-model.md`, `llm-reviewer-can-mask-deterministic-scanner-failures.md`.

Design: single-system field observations.

Results: Gemini and Codex reviewers over Claude-authored code found one critical and three high defects on the first run and 13 structural findings plus 3 governance gaps on the second, with one confirmed false positive across both sessions; in one triage pass three of four reviewer-filed bug reports, each with file:line citations and permalinks, did not reproduce; the fix adopted was to rank findings by cross-reviewer corroboration, to re-derive any scrutiny-loosening label from the diff rather than accept the author's self-report, to run cross-vendor review at transition points (pre-PR, release) not per change, and never to let an LLM summary rewrite a deterministic scanner failure (citing Parris 2026, AIRA, arXiv:2604.17587, secondary, not verified here).

Limitations: one team, Claude-authored code, counts without denominators.

For orchestrate: the same two faces as CriticGPT at field scale (real catches, confident false reports); the operational rules (corroboration ranking, computed not self-reported gate signals, gate failures verbatim) are rules orchestrate can adopt without new evidence.

## Implementable findings

| id | finding | grade | source | for orchestrate |
|---|---|---|---|---|
| RV-01 | A model revising its own answer with no external signal loses accuracy: GPT-4 GSM8K 95.5 to 89.0 after two rounds, Llama-2 62.0 to 36.5. | A | Huang 2024 | reject: no "review your own diff" step for the worker; every review round must carry new signal (suite result, reviewer finding, ticket criterion). |
| RV-02 | Self-repair at matched budget gains at most 8%; more drafts beat more repair rounds (1.05x vs 0.97x). | A | Olausson 2024 | rule: keep the three-round cap; after round 2 with blocking findings, prefer a fresh worker on the branch over a fourth round. |
| RV-03 | Feedback from a stronger source lifts repair: GPT-3.5 4.7% with own feedback, 11.5% with GPT-4 feedback; human feedback 1.58x GPT-4's. | A | Olausson 2024 | default: the reviewer model is never weaker than the worker model at M and L; a human comment outranks a reviewer finding. |
| RV-04 | Trained LLM critiques are preferred over human critiques 62.8% (CI 60.5 to 65.1) and catch more inserted bugs, but hallucinate bugs and nitpick more than humans. | A | McAleese 2024 | default: keep the independent LLM reviewer; rule: a finding without `path:line` and a concrete failure or broken rule is dropped before it reaches the worker. |
| RV-05 | Human-plus-model critics hallucinate less than the model alone at similar coverage. | A | McAleese 2024 | default: L risk notes are addressed to the human, not auto-blocking; the human's merge gate stays. |
| RV-06 | Evaluators favour their own generations in proportion to self-recognition (causal, linear). | A | Panickssery 2024 | flag: cross-model reviewer; measure: same-model vs cross-model round-1 approval rate on the same PRs. |
| RV-07 | Debate does not beat self-consistency at equal responses (83.0 vs 88.2 at 9) and is hyperparameter-sensitive. | A/B | Huang 2024; Smit 2024 | reject: no debate between reviewers as a default; if two reviewers disagree, run the suite or ask the human, do not run a third round of argument. |
| RV-08 | Debate raises a weak judge's accuracy (LLM 48 to 76%, human 60 to 88%) only where the judge lacks the artefact. | A | Khan 2024; Kenton 2024 | flag: an adversarial "argue this PR is wrong" second reviewer at L only, where the human merger cannot read the diff. |
| RV-09 | Removing self-generated tests from Reflexion drops below baseline (0.52 vs 0.60); the gain is the tests, not the reflection. | B | Shinn 2023 | default: the worker's pre-PR suite run is mandatory; a worker loop without an executable check is disallowed. |
| RV-10 | Generated tests with a 16.3% false-positive rate turned a 11-point gain into a 3-point loss (MBPP). | B | Shinn 2023 | rule: the reviewer's M check "the new tests would fail without the change" is blocking, not minor. |
| RV-11 | Self-refine gains concentrate in iteration 1 (22.0 to 28.8 over three, mostly first) with diminishing returns. | B | Madaan 2023 | rule: round 1 must contain every blocking finding the reviewer has; a later round that only adds minors is not a round. |
| RV-12 | Generated tests plus dual execution agreement lift pass@1 by 18.8 points. | B | Chen 2023 (CodeT) | flag: a test-designer agent writes acceptance tests from the ticket independently of the worker; the two must agree before review. |
| RV-13 | Regression tests then generated reproduction tests raise selection from 25.67% to 27.00% to 32.00% at $0.25 extra per issue. | B | Xia 2024 | default: suite at M and L (already); flag: a reproduction test for bug tickets written before the fix and attached to the PR. |
| RV-14 | Execution and LLM verifiers saturate at 42 to 43% each and reach 51% combined. | B | Jain 2025 | default: a green suite does not skip the reviewer at M and L; S keeps CI as its signal. |
| RV-15 | The execution-free verifier gains from the agent's reasoning trace, not only the patch. | B | Jain 2025; Jiang 2025 | default: the reviewer reads the worker report with the diff; contradiction with RV-21 (context size) resolved by passing the report, not the transcript. |
| RV-16 | Best-of-N coverage rises 15.9% to 56% but is realised only with a checker; voting and reward models plateau. | B | Brown 2024; Ehrlich 2025 | flag: two workers per L ticket with reviewer selection, only when the ticket has executable acceptance tests. |
| RV-17 | 345 SWE-bench patches labelled passing were wrong; 24.4% of Verified entries affected. | A | Yu 2025 | measure: escaped defects per tier (#589) is the only measure of what suite plus reviewer missed; never report "suite pass" as "correct". |
| RV-18 | A rubric-guided LLM judge reaches kappa 0.75 where humans agree and 0.57 overall; humans agree at kappa 0.31. | B | Shi 2025 | default: the ticket's acceptance criteria are the reviewer's rubric (already); rule: a criterion the reviewer marks ambiguous routes to the human, not to `changes`. |
| RV-19 | Thinking judges significantly outperform non-thinking; pairwise beats pointwise; order swaps change verdicts; all judges show significant randomness. | B | Jiang 2025 | rule: M and L reviewers are thinking models at medium or high effort; cheaper routing may put a small model at S only; measure: run the S reviewer twice on a sample to record consistency. |
| RV-20 | Prompt cues move a small code judge by up to 37 points (62.41 to 25.36) and its test-retest consistency is as low as 50.4%. | B | Zhao 2026 | rule: author-written text is stripped from the reviewer's input (#592); measure: consistency rate as a routing gate for any model trialled as reviewer. |
| RV-21 | Frontier reviewers detect 15 to 31% of human-flagged issues diff-only, and detection falls monotonically as context grows; 2,000 tokens of diff plus summary beat 2,500 of full context. | B | Kumar 2026 | rule: diff-centric reviewer context with a budget per tier (#592); reject: "load the whole repo for L". |
| RV-22 | Top four frontier models are indistinguishable as reviewers (0.147 to 0.153); the bottom four fall to 0.113 or lower. | B | Kumar 2026 | flag: cheaper routing of the reviewer within the top cluster; measure: planted-defect benchmark per model (#598). |
| RV-23 | A validator stage cut comment volume 81% and raised correct comments from 6.9% to 17.5%; an unfiltered multi-agent reviewer was the noisiest (5.2% of 1,009). | B | Charoenwet 2026 | flag: a filter pass over the reviewer's findings before posting at L; measure: findings per PR and the share closed "fixed" vs "left". |
| RV-24 | 54% of a deployed agentic reviewer's comments were judged worth developer attention. | B | Charoenwet 2026 | measure: the same ratio from orchestrate's threads (fixed vs left) is the precision proxy per tier. |
| RV-25 | PR metadata framing flips reviewers; redaction plus an explicit instruction restored detection in all affected cases; weaker agents fall below 35 to 40% refusal under three framings while the top three stay above 70%. | B | Mitropoulos 2026; Melo 2026 | rule: strip the body and commit messages before dispatch (#592); reject: routing the reviewer to a bottom-tier model. |
| RV-26 | Obfuscation evolved against one reasoning detector transfers to another vendor's; 14 of 15 categories bypassed. | B | Li 2025 | rule: deterministic scanners and the suite stay in the chain regardless of reviewer vendor; the reviewer never masks a scanner failure. |
| RV-27 | On five planted bugs, a different-family reviewer caught one (5/5) that the author's family missed (0/5); all four missed another; the executable spec caught all five. Not significant. | C | Zietsman 2026 | flag: cross-vendor reviewer at L; measure: catch rate by vendor on the planted-defect benchmark before making it default. |
| RV-28 | A panel of three cheap cross-family judges correlates better with humans than one GPT-4 judge at under a seventh of the cost (QA). | B | Verga 2024 | flag: a cheap cross-vendor panel at S with corroboration; measure against the single-reviewer baseline on the benchmark. |
| RV-29 | An automated reviewer in industry: 73.8% of comments resolved, average PR closure 5h52 to 8h20. | A | Cihan 2025 | measure: time-to-merge and rounds per tier in the profile; rule: minors ride along and are never re-reviewed (already). |
| RV-30 | 83.8% of agent PRs merge but 45.1% of merged ones needed changes, mostly bug fixes, docs and project standards; 67.9% of rejections carry no reason. | B | Watanabe 2025/26; Nakashima 2026 | default: the S checklist (project rules, docs, scope) targets the most frequent human edit; measure: record the reason for every `changes` and every human rejection in the review event. |
| RV-31 | Structured PR descriptions associate with faster human response and completion but do not determine merges. | B | Watanabe K. 2026 | default: keep the structured PR body for humans, and strip it for the reviewer (RV-25): the two readers get different inputs. |
| RV-32 | AI-co-authored PRs draw 1.7x the reviewer findings (10.83 vs 6.45 per PR, p90 26) by the vendor's own reviewer. | C | CodeRabbit 2025 | measure: findings per PR by tier as a sizing input; never cite as a defect rate. |
| RV-33 | Self-grading "confidently praises the work"; a standalone evaluator is tractable to make skeptical but approves anyway out of the box until calibrated with scored examples. | C | Rajasekaran 2026 | default: a separate read-only reviewer (already); flag: calibrate the reviewer prompt with two or three scored example reviews from the repo. |
| RV-34 | "A reviewer that's asked to find problems will always find something"; scope it ("skip style") or it adds abstraction and dead tests. | C | Anthropic best practices | rule: the defect-hunting frame proposed in #592 pairs with the existing "style the linters enforce is theirs" and "scope creep is discovered work, not a finding". |
| RV-35 | A same-model subagent buys a fresh pair of eyes, "not a smarter answer". | C | Anthropic best practices | rule: independence (fresh context, no transcript) is the default; capability comes from model and effort, set per tier. |
| RV-36 | The largest deployer reports an LLM-judged addressed rate and "merged with no human change request after first review". | C | GitHub 2026 | measure: add both to `scripts/profile.py` from review events and merge records. |
| RV-37 | Cross-vendor reviewers over Claude code found 1 critical and 3 high defects with one false positive over two sessions; 3 of 4 confident reviewer bug reports in another pass did not reproduce. | C | Thurlow 2026 (HOS) | rule: a reviewer finding about behaviour is backed by a targeted run or marked unverified; the worker may close an unverified finding "left: does not reproduce" with the run that shows it. |
| RV-38 | A scrutiny-loosening label (tier, risk) must be re-derived from the diff, never taken from the author. | C | Thurlow 2026 (HOS) | rule: tier comes from the diff and the config's risk paths (already script-owned); a worker's `size:` claim never lowers it. |
| RV-39 | Contradiction: reasoning-trace context helps the verifier (RV-15) while more context lowers detection (RV-21). | B | Jain 2025 vs Kumar 2026 | measure: A/B the reviewer with diff plus worker report against diff plus summary on the planted-defect benchmark. |
| RV-40 | Contradiction: cross-vendor review is supported mechanistically (RV-06) and directionally (RV-27, RV-37) but shared blind spots and transferable evasions (RV-26) cap it. | B/C | Panickssery 2024; Zietsman 2026; Li 2025 | flag with a measure: adopt cross-vendor at L only after the benchmark shows a catch-rate gain over same-vendor. |

## Candidate paradigms

1. **Independent fresh-context reviewer** (current default): a read-only agent with the diff, the ticket and the repo rules, no transcript. Backed by RV-01, RV-04, RV-06, RV-33, RV-35. Best grade A.
2. **Execution-first verification**: suite before review, reviewer re-runs at M and L, a green suite never skips the reviewer. Backed by RV-09, RV-13, RV-14, RV-17. Best grade A (RV-17) and B.
3. **Independent acceptance tests**: a test-designer agent (or the worker, before the fix) writes failing tests from the ticket; code and tests must agree before review. Backed by RV-10, RV-12, RV-13. Best grade B. Flag.
4. **Stronger-than-author reviewer**: reviewer model and effort at or above the worker's per tier; human comments outrank reviewer findings. Backed by RV-03, RV-05, RV-19. Best grade A.
5. **Cross-vendor reviewer at L**: a different model family reviews, with the suite and scanners unchanged. Backed by RV-06, RV-27, RV-28, RV-37, capped by RV-26 and RV-40. Best grade A for the mechanism, C for the code-domain effect. Flag with measure.
6. **Cheap panel with corroboration**: two or three small cross-vendor reviewers at S, findings ranked by agreement, singletons dropped or demoted. Backed by RV-23, RV-28, RV-37. Best grade B. Flag with measure.
7. **Finding filter before post**: a validator pass over the reviewer's findings (reproduce, check against repo rules, drop unlocated ones) before the worker sees them. Backed by RV-04, RV-23, RV-37. Best grade A (RV-04) and B.
8. **Bounded rounds**: three rounds, round 1 carries all blocking findings, a fresh worker replaces a fourth round. Backed by RV-02, RV-11, RV-29. Best grade A.
9. **Diff-centric, stripped input**: body and commit messages stripped, worker report passed, context budget per tier. Backed by RV-15, RV-20, RV-21, RV-25, RV-31, RV-39. Best grade B. Partly default (via #592), partly measure.
10. **Best-of-two workers with reviewer selection at L**: only when executable acceptance tests exist. Backed by RV-16, RV-12. Best grade B. Flag.
11. **Adversarial second reviewer or debate**: rejected as default (RV-07), flag at L where the merging human cannot read the diff (RV-08). Best grade A against.
12. **Rubric-routed ambiguity**: a criterion the reviewer cannot settle routes to the human rather than to `changes` or `approve`. Backed by RV-18, RV-05. Best grade B.
13. **Measured review**: escaped defects per tier, addressed rate, merged-with-no-human-change-request, findings per PR, consistency rate per reviewer model, time-to-merge per tier. Backed by RV-17, RV-19, RV-20, RV-24, RV-29, RV-30, RV-36. Best grade A.

## Open questions

- No study measures same-model vs cross-model review on agent-written PRs with a defect ground truth at N above a handful; RV-06 is on summaries, RV-27 is five bugs. The planted-defect benchmark (#598) is the only way to settle it for orchestrate.
- Whether passing the worker's report helps (RV-15) or dilutes (RV-21) a reviewer on real PRs is untested; the two sources differ in task (selection among rollouts vs review of one PR).
- No source measures the precision of an LLM reviewer's `changes` verdict against a human adjudicator on agent PRs; the resolved-comment rates (73.8%, 54%) are proxies that count what the author chose to act on.
- The CriticGPT hallucination and nitpick rates are in figures only; no paper gives a per-finding false-positive rate for a frontier reviewer on PRs.
- Whether a "find the defect" frame raises recall more than it raises noise on real PRs is unmeasured; #599 covers the framing sources and the best-practices page warns of over-finding.
- Cheap-panel results (RV-28) are QA-only; no code-review panel ablation was found. The SLEAN case study (Vargas 2025, arXiv:2510.10010) indexed by HOS was not chased.
- The AIDev merge rates differ across three studies by sampling, and Codex's high rate reflects author-reviewed PRs; no study separates "reviewed by someone other than the person who launched the agent".
- How often agentic PRs are rejected for "distrust of AI" rather than a defect (Nakashima: seven agent-only modes, frequencies under 2% each) is small in that sample and unmeasured elsewhere.
- Google's human kappa of 0.31 on patch validity implies any pass criterion for a reviewer benchmark should expect disagreement; how to set a pass bar per tier remains for #589.
- Self-correction training (SCoRe, Kumar et al. 2024, arXiv:2409.12917) may change RV-01 for newer models; not chased, and the frontier models in RV-21 and RV-25 still show the failure modes.

## Sources list

- Huang, J., Chen, X., Mishra, S., Zheng, H. S., Yu, A. W., Song, X., Zhou, D. (2024). Large Language Models Cannot Self-Correct Reasoning Yet. ICLR 2024. arXiv:2310.01798. https://arxiv.org/abs/2310.01798
- Olausson, T. X., Inala, J. P., Wang, C., Gao, J., Solar-Lezama, A. (2024). Is Self-Repair a Silver Bullet for Code Generation? ICLR 2024. arXiv:2306.09896. https://arxiv.org/abs/2306.09896
- McAleese, N., Pokorny, R. M., Ceron Uribe, J. F., Nitishinskaya, E., Trebacz, M., Leike, J. (2024). LLM Critics Help Catch LLM Bugs. Preprint, arXiv:2407.00215. https://arxiv.org/abs/2407.00215
- Panickssery, A., Bowman, S. R., Feng, S. (2024). LLM Evaluators Recognize and Favor Their Own Generations. NeurIPS 2024. arXiv:2404.13076. https://arxiv.org/abs/2404.13076
- Kenton, Z. et al. (2024). On scalable oversight with weak LLMs judging strong LLMs. NeurIPS 2024. arXiv:2407.04622. https://arxiv.org/abs/2407.04622
- Khan, A. et al. (2024). Debating with More Persuasive LLMs Leads to More Truthful Answers. ICML 2024. arXiv:2402.06782. https://arxiv.org/abs/2402.06782
- Smit, A., Grinsztajn, N., Duckworth, P., Barrett, T., Pretorius, A. (2024). Should we be going MAD? A Look at Multi-Agent Debate Strategies for LLMs. ICML 2024, PMLR 235:45883-45905. arXiv:2311.17371. https://proceedings.mlr.press/v235/smit24a.html
- Shinn, N., Cassano, F., Gopinath, A., Narasimhan, K., Yao, S. (2023). Reflexion: Language Agents with Verbal Reinforcement Learning. NeurIPS 2023. arXiv:2303.11366. https://arxiv.org/abs/2303.11366
- Madaan, A. et al. (2023). Self-Refine: Iterative Refinement with Self-Feedback. NeurIPS 2023. arXiv:2303.17651. https://arxiv.org/abs/2303.17651
- Chen, B., Zhang, F., Nguyen, A., Zan, D., Lin, Z., Lou, J.-G., Chen, W. (2023). CodeT: Code Generation with Generated Tests. ICLR 2023. arXiv:2207.10397. https://arxiv.org/abs/2207.10397
- Xia, C. S., Deng, Y., Dunn, S., Zhang, L. (2024). Agentless: Demystifying LLM-based Software Engineering Agents. Preprint, arXiv:2407.01489. https://arxiv.org/abs/2407.01489
- Jain, N., Singh, J., Shetty, M., Zheng, L., Sen, K., Stoica, I. (2025). R2E-Gym: Procedural Environments and Hybrid Verifiers for Scaling Open-Weights SWE Agents. Preprint, arXiv:2504.07164. https://arxiv.org/abs/2504.07164
- Brown, B., Juravsky, J., Ehrlich, R., Clark, R., Le, Q. V., Ré, C., Mirhoseini, A. (2024). Large Language Monkeys: Scaling Inference Compute with Repeated Sampling. Preprint, arXiv:2407.21787. https://arxiv.org/abs/2407.21787
- Ehrlich, R., Brown, B., Juravsky, J., Clark, R., Ré, C., Mirhoseini, A. (2025). CodeMonkeys: Scaling Test-Time Compute for Software Engineering. Preprint, arXiv:2501.14723. https://arxiv.org/abs/2501.14723
- Yu, B., Zhu, Y., He, P., Kang, S. (2025). UTBoost: Rigorous Evaluation of Coding Agents on SWE-Bench. ACL 2025. arXiv:2506.09289. https://arxiv.org/abs/2506.09289
- Shi, S., Wei, R., Tufano, M., Cambronero, J., Cheng, R., Ivančić, F., Rondon, P. (2025). Towards a Human-in-the-Loop Framework for Reliable Patch Evaluation Using an LLM-as-a-Judge. Preprint, arXiv:2511.10865. https://arxiv.org/abs/2511.10865
- Tong, W., Zhang, T. (2024). CodeJudge: Evaluating Code Generation with Large Language Models. EMNLP 2024. arXiv:2410.02184. https://aclanthology.org/2024.emnlp-main.1118
- Jiang, H., Chen, Y., Cao, Y., Lee, H., Tan, R. T. (2025). CodeJudgeBench: Benchmarking LLM-as-a-Judge for Coding Tasks. Preprint, arXiv:2507.10535. https://arxiv.org/abs/2507.10535
- Zhao, Y., Luo, Z., Tian, Y., Lin, H., Yan, W., Li, A., Ma, J. (2024). CodeJudge-Eval: Can Large Language Models be Good Judges in Code Understanding? Preprint, arXiv:2408.10718. https://arxiv.org/abs/2408.10718
- Zhao, Z., Esmaeili, A., Fard, F. (2026). Bias in the Loop: Auditing LLM-as-a-Judge for Software Engineering. Preprint, arXiv:2604.16790. https://arxiv.org/abs/2604.16790
- Kumar, D. (2026). SWE-PRBench: Benchmarking AI Code Review Quality Against Pull Request Feedback. Preprint, arXiv:2603.26130. https://arxiv.org/abs/2603.26130
- Charoenwet, W., Tantithamthavorn, K., Thongtanunam, P., Lin, H. Y., Jeong, M., Wu, M. (2026). AgenticSCR: An Autonomous Agentic Secure Code Review for Immature Vulnerabilities Detection. ASE '26 Industry Track (per arXiv v2). arXiv:2601.19138. https://arxiv.org/abs/2601.19138
- Mitropoulos, D., Alexopoulos, N., Alexopoulos, G., Spinellis, D. (2026). Measuring and Exploiting Contextual Bias in LLM-Assisted Security Code Review. Preprint, arXiv:2603.18740. https://arxiv.org/abs/2603.18740
- Melo, R., Fogliato, R., Zhou, S., Thaker, P., Wu, Z. S. (2026). SEVRA-BENCH: Social Engineering of Vulnerabilities in Review Agents. Preprint, arXiv:2606.13757. https://arxiv.org/abs/2606.13757
- Li, H., Li, M., Zuo, J., Li, S., Li, X., Wu, H., Lu, Y., He, X. (2025). CoTDeceptor: Adversarial Code Obfuscation Against CoT-Enhanced LLM Code Agents. Preprint, arXiv:2512.21250. https://arxiv.org/abs/2512.21250
- Zietsman, C. (2026). The Specification as Quality Gate: Three Hypotheses on AI-Assisted Code Review. Preprint, arXiv:2603.25773. https://arxiv.org/abs/2603.25773
- Verga, P. et al. (2024). Replacing Judges with Juries: Evaluating LLM Generations with a Panel of Diverse Models. Preprint, arXiv:2404.18796. https://arxiv.org/abs/2404.18796
- Cihan, U., Haratian, V., İçöz, A., Gül, M. K., Devran, Ö., Bayendur, E. F., Uçar, B. M., Tüzün, E. (2025). Automated Code Review In Practice. ICSE 2025 SEIP. arXiv:2412.18531. https://arxiv.org/abs/2412.18531
- Watanabe, M., Li, H., Kashiwa, Y., Reid, B., Iida, H., Hassan, A. E. (2025/2026). On the Use of Agentic Coding: An Empirical Study of Pull Requests on GitHub. arXiv:2509.14745 (TOSEM acceptance: secondary). https://arxiv.org/abs/2509.14745
- Nakashima, S., Ishimoto, Y., Kondo, M., McIntosh, S., Kamei, Y. (2026). Why Agentic-PRs Get Rejected: A Comparative Study of Coding Agents. Preprint, arXiv:2602.04226. https://arxiv.org/abs/2602.04226
- Watanabe, K., Tsuchida, R., Monno, T., Huang, B., Yamasaki, K., Fan, Y., Shimari, K., Matsumoto, K. (2026). How AI Coding Agents Communicate: A Study of Pull Request Description Characteristics and Human Review Responses. Preprint, arXiv:2602.17084. https://arxiv.org/abs/2602.17084
- Li, H., Zhang, H., Hassan, A. E. (2025). The Rise of AI Teammates in Software Engineering (SE) 3.0: How Autonomous Coding Agents Are Reshaping Software Engineering. Preprint, arXiv:2507.15003. https://arxiv.org/abs/2507.15003
- CodeRabbit (2025, 17 Dec). State of AI vs Human Code Generation Report. https://coderabbit.ai/blog/state-of-ai-vs-human-code-generation-report
- Rajasekaran, P. (2026, 24 Mar). Harness design for long-running application development. Anthropic Engineering. https://www.anthropic.com/engineering/harness-design-long-running-apps
- Anthropic (2026). Best practices for Claude Code. https://code.claude.com/docs/en/best-practices
- GitHub (2026, 5 Nov). Usage metrics API adds code review details and addressed rate. https://github.blog/changelog/2026-11-05-usage-metrics-api-adds-code-review-details-and-addressed-rate
- GitHub (2026, 8 Apr). Copilot-reviewed pull request merge metrics now in the usage metrics API. https://github.blog/changelog/2026-04-08-copilot-reviewed-pull-request-merge-metrics-now-in-the-usage-metrics-api
- Thurlow, S. (2026). Human Oversight System (HOS), documentation and research notes, https://github.com/thurlow-research/HumanOversightSystem (software archived at https://doi.org/10.5281/zenodo.21347272), docs CC BY 4.0, code MIT.
