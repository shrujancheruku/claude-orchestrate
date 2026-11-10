# Planning and decomposition: evidence for orchestrating AI coding agents

Research for [#604](https://github.com/shrujancheruku/claude-orchestrate/issues/604) (map [#588](https://github.com/shrujancheruku/claude-orchestrate/issues/588)). Area prefix `PL`. Sources fetched 2026-11-10 from arXiv abstracts and HTML full texts, venue pages, vendor engineering posts and API docs. Figures are quoted from the primary source unless marked "(secondary)".

**Summary.** (1) Repeated sampling with a test-based verifier is the best-measured lever: coverage on SWE-bench Lite rises log-linearly from 15.9% at 1 sample to 56% at 250 (Brown et al. 2024), and selected (not oracle) resolve rates rise by +6 to +11 points at N=8–16 with execution or learned verifiers (Agentless, SWE-Gym, R2E-Gym, CodeMonkeys, DeepSWE); the gain is capped by the verifier's false-positive rate and the optimum is usually under 10 attempts (Stroebl et al.). (2) An explicit plan before code helps when the model is weak or the task long: +25.4% relative pass@1 (Self-planning, TOSEM), 5/6 vs 0/6 repositories (CodePlan), 9.85% → 44.24% success with a planner plus dynamic replanning (Plan-and-Act, ICML 2025); no controlled plan-vs-no-plan ablation exists on SWE-bench Verified, and vendors' best Verified scores come from minimal two-tool ReAct scaffolds. (3) Tree search over trajectories (SWE-Search, DARS, CodeTree) buys +6 to +9 points on SWE-bench Lite at 5× to 14× the cost, measured on 2024-generation models; OpenAI's o3 result and Anthropic's scaffolds argue the gain migrates into the model. (4) Self-repair by the same model is a poor use of budget compared with fresh samples (Olausson et al., ICLR 2024); feedback from a stronger model or a human is what makes repair pay (33.3% → 52.6% with human feedback). (5) Reasoning effort is a per-role dial, not a global one: vendors report medium effort matching a smaller model's best score at 76% fewer output tokens (Opus 4.5) and adaptive thinking cutting the cheapest 10% of turns by 93.7% (GPT-5-Codex); the evidence is thin and vendor-reported, and nothing isolates planning on SWE-bench Verified with a controlled design.

## Sources

### 1. Olausson et al. 2024, Is Self-Repair a Silver Bullet for Code Generation? (ICLR 2024)

Citation: Olausson, T. X., Inala, J. P., Wang, C., Gao, J., Solar-Lezama, A. (2024). Is Self-Repair a Silver Bullet for Code Generation? ICLR 2024. arXiv:2306.09896. https://arxiv.org/abs/2306.09896
Design: controlled comparison of self-repair trees against i.i.d. sampling at matched sample budget (pass@k) and matched token budget (pass@t); human-feedback study.
Population: GPT-4, GPT-3.5, Code Llama on HumanEval and APPS.
Headline results: GPT-4 on APPS, 10 initial samples with one repair each (20 total) reached 1.05× the pass@20 baseline; 2 initial samples with 10 repairs each (22 total) reached 0.97×; gains up to 8% for GPT-4 on APPS, up to 3% for GPT-3.5 on HumanEval, up to 10% relative for Code Llama on HumanEval. For a fixed budget, "the number of initial programs matters more than the number of repair samples per feedback string"; more repairs per feedback often reduced performance at lower budgets. Feedback from a stronger model "significantly improved performance". Human feedback raised repair success from 33.3% to 52.6% (1.58×), competition problems 3.67% → 14.67%.
Limitations: 2023 models; function-level benchmarks, not repositories; the pass@t appendix reports repair as "somewhat less beneficial" in sequential settings.
For orchestrate: a worker re-prompted with its own review of its own code is the weakest loop. A second round should come with new information: the reviewer's finding, a failing test, or a fresh worker. It supports the existing "third round goes to you" rule and argues against adding a self-review pass inside the worker.

### 2. Pan et al. 2025, Training Software Engineering Agents and Verifiers with SWE-Gym (ICML 2025)

Citation: Pan, J., Wang, X., Neubig, G., Jaitly, N., Ji, H., Suhr, A., Zhang, Y. (2025). Training Software Engineering Agents and Verifiers with SWE-Gym. ICML 2025. arXiv:2412.21139. https://arxiv.org/abs/2412.21139
Design: benchmark with inference-time scaling ablation; a learned outcome verifier selects the best of N trajectories (Best@N) against the oracle Pass@N.
Population: fine-tuned Qwen-2.5-32B agent in OpenHands on SWE-bench Verified; MoatlessTools on Lite.
Headline results: Verified Best@1 20.6% → Best@8 29.8% → Best@16 32.0%; oracle Pass@8 37.8%, Pass@16 42.8%, so the verifier leaves an 8.0 to 10.8 point gap. Best@k "exhibits strong linearity on a logarithmic scale". On Lite the 7B verifier "plateaus after k = 4" while the 32B verifier "continues improving even at k = 8" (19.7% → 26.3%).
Limitations: open-weight 32B policy at 20.6% baseline; learned verifier, not tests; gains measured in a training paper.
For orchestrate: best-of-N selected by a verifier roughly halves the distance to oracle; the quality of the selector, not N, is the constraint. A weak selector plateaus by N=4.

### 3. Erdogan et al. 2025, Plan-and-Act (ICML 2025)

Citation: Erdogan, L. E., Lee, N., Kim, S., Moon, S., Furuta, H., Anumanchipalli, G., Keutzer, K., Gholami, A. (2025). Plan-and-Act: Improving Planning of Agents for Long-Horizon Tasks. ICML 2025. arXiv:2503.09572. https://arxiv.org/abs/2503.09572
Design: ablation ladder from a no-planner ReAct executor to planner plus dynamic replanning plus CoT, same executor.
Population: Llama-3.3-70B planner and executor on WebArena-Lite and WebVoyager (web agents, not code).
Headline results: WebArena-Lite success with the base executor: no planner 9.85%; zero-shot planner 14.21%; trained planner 29.63%; plus dynamic replanning 44.24%; full method 57.58% (prior SOTA WebRL 49.1%). With a fine-tuned executor, no planner reaches 36.97% and the best planner setting 48.48% before CoT.
Limitations: web navigation; much of the gain comes from training the planner on synthetic data, which an orchestrator cannot do; the largest single jump (+14.6 points) is from replanning, not the upfront plan.
For orchestrate: the plan that pays is one the agent is allowed to revise after observations; a frozen upfront plan recovers only part of the gain. A worker brief that asks for a plan should also permit "update the plan when the code disagrees".

### 4. Zhang et al. 2025, ReCAP (NeurIPS 2025)

Citation: Zhang, Z., Chen, T., Xu, W., Pentland, A., Pei, J. (2025). ReCAP: Recursive Context-Aware Reasoning and Planning for Large Language Model Agents. NeurIPS 2025. arXiv:2510.23822. https://arxiv.org/abs/2510.23822
Design: hierarchical plan-ahead decomposition with parent-plan re-injection; benchmark comparison to ReAct baselines; cost reported.
Population: GPT-4.1 on SWE-bench Verified (500 tasks), GPT-4o on ALFWorld, Robotouille.
Headline results: SWE-bench Verified 44.8% vs mini-SWE-agent (ReAct) 39.58% (baseline from swebench.com, not re-run). ALFWorld 91.0% vs ReAct 74.0% at $118.40 vs $37.89 for 134 tasks (about 3×). Robotouille +32% (sync) and +29% (async) pass@1. Ablation on one Robotouille task: original 80%, no_think 60%, flattened "level_3" 10%.
Limitations: the SWE-bench comparison is not controlled (different runs, leaderboard baseline); no ablation removes plan-ahead decomposition itself; ALFWorld gain costs 3×.
For orchestrate: the only peer-reviewed plan-then-execute number on SWE-bench Verified is +5.2 points, uncontrolled, at roughly 3× cost elsewhere. Treat hierarchical planning as a flag, not a default.

### 5. Takerngsaksiri et al. 2025, Human-In-the-Loop Software Development Agents (ICSE-SEIP 2025)

Citation: Takerngsaksiri, W., Pasuksmit, J., Thongtanunam, P., Tantithamthavorn, C., Zhang, R., Jiang, F., Li, J., Cook, E., Chen, K., Wu, M. (2025). Human-In-the-Loop Software Development Agents. ICSE-SEIP 2025. arXiv:2411.12924. https://arxiv.org/abs/2411.12924
Design: field deployment of HULA (plan → human approval → code → human review → PR) inside Atlassian JIRA; offline evaluation on SWE-bench Verified.
Population: 663 JIRA issues; 2,600 practitioners by September 2024; survey N=109 (75% response).
Headline results: plan generated for 79%; plan approved by the engineer in 82% (433/527); PR raised for 25% (95/376) of code-generated issues; 59% (56/95) of raised PRs merged. Offline: 31% of Verified issues pass all tests; perfect file localization 84%. Survey: 41% agreed the plan aligned with the issue; 67% disagreed that the code completely solved the task without human involvement.
Limitations: single company; the plan gate's effect is not isolated against a no-plan arm; self-selected usage.
For orchestrate: a plan gate before code acts as a real filter (82% approve → 25% PR), which is the strongest field evidence that humans will approve plans but reject code. It supports a lead-approved plan for large tickets and measuring the approve-then-abandon rate.

### 6. Jiang et al. 2024, Self-planning Code Generation with Large Language Models (TOSEM)

Citation: Jiang, X., Dong, Y., Wang, L., Fang, Z., Shang, Q., Li, G., Jin, Z., Jiao, W. (2024). Self-planning Code Generation with Large Language Models. ACM TOSEM. arXiv:2303.06689. https://arxiv.org/abs/2303.06689
Design: controlled prompting ablation (direct generation vs chain-of-thought vs plan-then-implement) with human assessment.
Population: multiple LLMs on multi-language function-level benchmarks (HumanEval family, MBPP family).
Headline results: "a relative improvement of up to 25.4% in Pass@1" over direct generation and up to 11.9% over chain-of-thought; human raters scored correctness, readability and robustness higher.
Limitations: function-level tasks; 2023 models; "up to" figures.
For orchestrate: a plan sketch before code is cheap and consistently non-negative at the function level. It is the evidence for keeping "write the steps first" in the worker brief, with no claim it transfers to repository tasks.

### 7. Prasad et al. 2024, ADaPT: As-Needed Decomposition and Planning (NAACL 2024 Findings)

Citation: Prasad, A., Koller, A., Hartmann, M., Clark, P., Sabharwal, A., Bansal, M., Khot, T. (2024). ADaPT: As-Needed Decomposition and Planning with Language Models. NAACL 2024 Findings. arXiv:2311.05772. https://arxiv.org/abs/2311.05772
Design: controlled comparison of iterative executors, plan-and-execute, and recursive decomposition triggered only on executor failure.
Population: GPT-3.5/GPT-4 class models on ALFWorld, WebShop, TextCraft (agent tasks, not code).
Headline results: success up to 28.3 points higher on ALFWorld, 27 on WebShop, 33 on TextCraft than strong baselines; decomposition depth adapts to task complexity and executor strength.
Limitations: not code; gains against 2023 baselines.
For orchestrate: decompose lazily. Give the ticket to one worker; split into sub-issues only when the worker stops or fails, which is what the lead already does with discovered work. Upfront decomposition of every ticket is not supported.

### 8. Kim et al. 2024, LLMCompiler (ICML 2024) and Xu et al. 2023, ReWOO

Citation: Kim, S., Moon, S., Tabrizi, R., Lee, N., Mahoney, M. W., Keutzer, K., Gholami, A. (2024). An LLM Compiler for Parallel Function Calling. ICML 2024. arXiv:2312.04511. https://arxiv.org/abs/2312.04511. Xu, B., Peng, Z., Lei, B., Mukherjee, S., Liu, Y., Xu, D. (2023). ReWOO: Decoupling Reasoning from Observations for Efficient Augmented Language Models. Preprint. arXiv:2305.18323. https://arxiv.org/abs/2305.18323
Design: plan-first DAG execution vs ReAct interleaving, measured on latency, cost and accuracy.
Population: GPT and open models on multi-tool QA and reasoning tasks.
Headline results: LLMCompiler vs ReAct: up to 3.7× latency speedup, up to 6.7× cost saving, up to about 9% accuracy improvement. ReWOO: 5× token efficiency and 4% accuracy gain on HotpotQA.
Limitations: tool-calling QA, not code edits; the gains come from parallelising independent calls, which interleaving cannot do.
For orchestrate: plan-first pays when the plan exposes independent work that can run in parallel. That is the orchestrator's dispatch step (greedy slots over non-overlapping paths), not the worker's inner loop.

### 9. Yao et al. 2023, ReAct (ICLR 2023) and Wang et al. 2023, Plan-and-Solve (ACL 2023)

Citation: Yao, S., Zhao, J., Yu, D., Du, N., Shafran, I., Narasimhan, K., Cao, Y. (2023). ReAct: Synergizing Reasoning and Acting in Language Models. ICLR 2023. arXiv:2210.03629. https://arxiv.org/abs/2210.03629. Wang, L., Xu, W., Lan, Y., Hu, Z., Lan, Y., Lee, R. K.-W., Lim, E.-P. (2023). Plan-and-Solve Prompting. ACL 2023. arXiv:2305.04091. https://arxiv.org/abs/2305.04091
Design: prompting ablations.
Population: PaLM/GPT-3 class models; ALFWorld, WebShop, HotpotQA, FEVER; ten reasoning datasets.
Headline results: ReAct beats imitation and RL baselines by 34 and 10 absolute points on ALFWorld and WebShop with one or two examples. Plan-and-Solve "consistently outperforms" zero-shot CoT across ten datasets by removing missing-step errors (no single figure in the abstract).
Limitations: 2022-2023 models; neither is a coding-agent result.
For orchestrate: the two paradigms the ticket names are both foundational prompting results on non-code tasks. Nothing here ranks them against each other for repository work; the later sources above do.

### 10. Brown et al. 2024, Large Language Monkeys

Citation: Brown, B., Juravsky, J., Ehrlich, R., Clark, R., Le, Q. V., Ré, C., Mirhoseini, A. (2024). Large Language Monkeys: Scaling Inference Compute with Repeated Sampling. Preprint. arXiv:2407.21787. https://arxiv.org/abs/2407.21787
Design: repeated-sampling measurement over four orders of magnitude of samples with coverage (pass@k) and a cost table.
Population: DeepSeek-Coder-V2-Instruct, Llama-3, Gemma, Pythia on SWE-bench Lite, CodeContests, MATH, GSM8K, MiniF2F.
Headline results: SWE-bench Lite solve rate 15.9% with one sample to 56% with 250 samples (single-sample SOTA 43%). Coverage is "often log-linear" and fits c ≈ exp(a·k^b). Cost table (Moatless Tools): 5 DeepSeek samples solve 29.62% for $10.8 total vs GPT-4o one sample 24.00% for $39 and Claude 3.5 Sonnet 26.70% for $51. Majority voting and reward models "plateau around 100 samples" while coverage passes 95% (MATH, Llama-3-8B: coverage 82.9% → 98.44% from 100 to 10,000 samples; best selector 40.50% → 41.41%). 11.3% of SWE-bench Lite problems had flaky test suites.
Limitations: coverage, not selected success; the 56% assumes an oracle picks the right patch; 2024 open model; the 29.62% and 15.9% come from different setups.
For orchestrate: more cheap attempts beat one expensive attempt only if something can tell them apart. Without a verifier the usable gain stops near 100 samples, and for a practical orchestrator far sooner. It also justifies rerunning flaky verify commands before trusting a red result.

### 11. Stroebl, Kapoor, Narayanan 2024, The Limits of Inference Scaling Through Resampling

Citation: Stroebl, B., Kapoor, S., Narayanan, A. (2024). The Limits of Inference Scaling Through Resampling (earlier title: Inference Scaling fLaws). Preprint. arXiv:2411.17501. https://arxiv.org/abs/2411.17501
Design: resampling against imperfect unit-test verifiers, 200 samples per task, 1,000 orderings, cost-benefit sweep; analytical model.
Population: GPT-4o, Llama 3.1 8B/70B, Code Llama 7B/13B, Phi-3, Command on HumanEval+ and MBPP+.
Headline results: a verifier that accepts wrong answers caps accuracy regardless of samples; single-sample accuracy and false-positive rate are linearly related across families, so weaker models cannot reach stronger ones by resampling. The optimal number of attempts is "often fewer than 10"; at a cost-benefit ratio of 4 the optimal K is 5 or fewer for all Llama and Code Llama models; with false-positive cost 10× the benefit, K = 0 for almost all models. False positives have lower code quality on all four style metrics measured.
Limitations: function-level benchmarks with known weak tests; the model is theoretical beyond the measured range.
For orchestrate: resampling is only as good as the verify commands and the reviewer; every false pass is paid for downstream. Cap N low, and never resample where the ticket has no test.

### 12. Ehrlich et al. 2025, CodeMonkeys

Citation: Ehrlich, R., Brown, B., Juravsky, J., Clark, R., Ré, C., Mirhoseini, A. (2025). CodeMonkeys: Scaling Test-Time Compute for Software Engineering. Preprint. arXiv:2501.14723. https://arxiv.org/abs/2501.14723
Design: serial (iterations) × parallel (trajectories) scaling with model-written tests and a selection state machine; full cost accounting.
Population: Claude 3.5 Sonnet (edits, tests, selection), Qwen2.5-Coder-32B (context) on SWE-bench Verified.
Headline results: 57.4% selected; coverage 69.8% with 10 trajectories × 8 iterations; random selection 45.8%, so selection closes about half the gap to oracle. "The first few serial iterations have the largest impact"; after that equal-cost configurations reach similar coverage. Total cost $2,291.90 (edits 59.6%, tests 19.2%, context 14.6%, selection 5.8%). Ensemble of top leaderboard submissions: 66.2% selected vs best member 62.8% and coverage 80.8%.
Limitations: one model generation; $4.58 per issue; selection still loses 12 points to oracle.
For orchestrate: the first extra iteration or attempt is worth the most; selection is the hard part; one deep trajectory avoids the selection problem. The "generate a test alongside the edit" step is what made selection work.

### 13. Xia et al. 2024, Agentless

Citation: Xia, C. S., Deng, Y., Dunn, S., Zhang, L. (2024). Agentless: Demystifying LLM-based Software Engineering Agents. Preprint. arXiv:2407.01489. https://arxiv.org/abs/2407.01489. Repository: https://github.com/OpenAutoCoder/Agentless
Design: three-phase pipeline (localize, repair, validate) with sampling and selection ablations and per-issue cost.
Population: GPT-4o on SWE-bench Lite and Verified; Agentless 1.5 with Claude 3.5 Sonnet (repository README).
Headline results: Lite 32.00% (96/300), Verified 38.80% (194/500), $0.70 and 78,166 tokens per issue, vs SWE-agent $1.62–$2.53 and roughly 500k tokens. Selection ablation on Lite: majority voting 25.67%, + regression tests 27.00%, + reproduction tests 32.00%; any-sample upper bound 42.0%; resolve rate plateaus around 40 samples; reproduction tests from 1 candidate to 40 candidates lift fixes from 90 to 96. Agentless 1.5 with Claude 3.5 Sonnet: 40.7% Lite, 50.8% Verified.
Limitations: the sampling plateau is model- and benchmark-specific; closed agents beat the pipeline when the issue has no location clue.
For orchestrate: a fixed localize → repair → validate shape beat 2024 agents at a third of the cost; execution-based selection (regression plus a reproduction test) added 6.3 points over voting. Orchestrate's verify commands are the regression half; a failing test written first is the reproduction half.

### 14. Antoniades et al. 2025, SWE-Search

Citation: Antoniades, A., Örwall, A., Zhang, K., Xie, Y., Goyal, A., Wang, W. (2025). SWE-Search: Enhancing Software Agents with Monte Carlo Tree Search and Iterative Refinement. Preprint (v6). arXiv:2410.20285. https://arxiv.org/abs/2410.20285
Design: MCTS over agent states with an LLM value agent and discriminator; five models; cost appendix.
Population: GPT-4o, GPT-4o-mini, Qwen-2.5-72B, DeepSeek-V2.5, Llama-3.1-70B on SWE-bench Lite in Moatless Tools.
Headline results: resolve rate vs the adapted baseline: GPT-4o 25.7 → 31.0, GPT-4o-mini 13.0 → 17.0, Qwen-72B 18.0 → 24.7, DeepSeek 16.3 → 21.0, Llama 13.6 → 17.7, reported as "a 23% relative improvement". The discriminator picked the correct solution 84% of the time vs 73% for the value function. Total API cost: GPT-4o $40.86 → $576.00 (about 14×); open models about 5×. Pass@5 adds 1 to 5 points over pass@1.
Limitations: 2024 models well below current frontier; the 23% is an average of per-model deltas; search settings differ between text and table.
For orchestrate: search over trajectories is a 5× to 14× cost multiplier for +4 to +7 points on weak models. There is no measurement of it on a frontier model, and vendors' minimal scaffolds now score higher without it.

### 15. Aggarwal et al. 2025, DARS

Citation: Aggarwal, V., Kamal, O., Japesh, A., Jin, Z., Schölkopf, B. (2025). DARS: Dynamic Action Re-Sampling to Enhance Coding Agent Performance by Adaptive Tree Traversal. Preprint. arXiv:2503.14269. https://arxiv.org/abs/2503.14269
Design: branching at high-impact actions (edit, append, create, submit) with execution feedback; cost and attempt counts reported.
Population: Claude 3.5 Sonnet V2, GPT-4o, Gemini 1.5 Pro, Gemini 2.0 Flash on SWE-bench Lite.
Headline results: pass@1 47% (with a DeepSeek-R1 reviewer) and pass@k 55% with Claude 3.5 Sonnet V2 vs Moatless 38.3%, Agentless-1.5 40.7%, SWE-Agent 23.0%. Cost per instance: $12.24 vs $1.61 for the improved SWE-Agent (7.6×); GPT-4o $7.92 vs $0.80. Branching at "edit" gave the most coverage (31.3%); one expansion per action type per branch was most cost-efficient; coverage saturates at depth 50 and 300 iterations.
Limitations: Lite only; the 47% includes a second model as reviewer; 7–10 attempts per issue on average.
For orchestrate: where to branch matters more than how much: the edit and submit points. The cheapest version of this for an orchestrator is a second worker started from the first worker's localization, not a full tree.

### 16. Jain et al. 2025, R2E-Gym and Ma et al. 2025, Thinking Longer, Not Larger

Citation: Jain, N., Singh, J., Shetty, M., Zheng, L., Sen, K., Stoica, I. (2025). R2E-Gym: Procedural Environments and Hybrid Verifiers for Scaling Open-Weights SWE Agents. Preprint. arXiv:2504.07164. https://arxiv.org/abs/2504.07164. Ma, Y., Li, Y., Dong, Y., Jiang, X., Cao, R., Chen, J., Huang, F., Li, B. (2025). Thinking Longer, Not Larger: Enhancing Software Engineering Agents via Scaling Test-Time Compute. Preprint. arXiv:2503.23803. https://arxiv.org/abs/2503.23803
Design: best-of-N with execution-based, execution-free and hybrid verifiers; internal (trained reasoning) vs external (beam search) test-time compute.
Population: 32B open-weight agents on SWE-bench Verified.
Headline results: R2E-Gym 32B pass@1 34.4%; each verifier type alone saturates at 42–43%; hybrid reaches 51%. Ma et al.: 32B with internal TTC 37.60%, plus external search at budget 8 (beam width 4) 46.00%, against Claude 3.5 Sonnet v2 46.20% and o1 45.60%; the hardest difficulty bucket "slightly decreased at higher budgets".
Limitations: open 32B models; search evaluated on 100 issues; no cost.
For orchestrate: two unlike verifiers (run the tests and have a judge read the diff) select better than either alone, which is the shape of orchestrate's script gate plus reviewer. Extra search budget can hurt on the hardest tickets.

### 17. Trae Research Team 2025, Trae Agent and Ding & Zhang 2026, SWE-Replay

Citation: Gao, P., Tian, Z., et al. (2025). Trae Agent: An LLM-based Agent for Software Engineering with Test-time Scaling. Technical report. arXiv:2507.23370. https://arxiv.org/abs/2507.23370. Ding, Y., Zhang, L. (2026). SWE-Replay: Efficient Test-Time Scaling for Software Engineering Agents. Preprint. arXiv:2601.22129. https://arxiv.org/abs/2601.22129
Design: ensemble generation, pruning and selection (Trae); replaying archived trajectories from significant exploration steps instead of sampling from scratch (SWE-Replay).
Population: three frontier LLMs on SWE-bench Verified; SWE-bench Verified, Pro, Multilingual.
Headline results: Trae Agent 75.20% pass@1 on Verified, an average +10.22% over four ensemble baselines (configuration not in the abstract). SWE-Replay cuts cost by up to 17.4% versus naive test-time scaling while maintaining or improving resolve rate by up to 3.8%.
Limitations: Trae is a vendor technical report; SWE-Replay's models and N are not in the abstract.
For orchestrate: current leaderboard tops use selection over several candidates; the cost of a second candidate can be cut by starting it from the first candidate's exploration, which maps onto resuming a stalled worker's branch rather than starting clean.

### 18. Snell et al. 2024, Scaling LLM Test-Time Compute Optimally and Wu et al. 2024, Inference Scaling Laws

Citation: Snell, C., Lee, J., Xu, K., Kumar, A. (2024). Scaling LLM Test-Time Compute Optimally can be More Effective than Scaling Model Parameters. Preprint. arXiv:2408.03314. https://arxiv.org/abs/2408.03314. Wu, Y., Sun, Z., Li, S., Welleck, S., Yang, Y. (2024). Inference Scaling Laws. Preprint. arXiv:2408.00724. https://arxiv.org/abs/2408.00724
Design: compute-matched comparison of verifier search and revision strategies by prompt difficulty; strategy sweeps across model sizes.
Population: PaLM 2 and Llemma models on MATH.
Headline results: compute-optimal allocation improves efficiency "by more than 4×" over best-of-N; a smaller model with test-time compute beats a 14× larger one on problems where it has "non-trivial success rates"; Llemma-7B with tree search "consistently outperforms the Llemma-34B model".
Limitations: math, not code; the benefit "critically varies depending on the difficulty of the prompt".
For orchestrate: spend extra attempts on tickets the model sometimes solves, not on ones it never solves or always solves. A per-ticket difficulty estimate (the scout's path count, diff size tier) is the allocation signal.

### 19. Shen et al. 2025, Thinking vs. Doing

Citation: Shen, J., Bai, H., Zhang, L., Zhou, Y., Setlur, A., Tong, S., Caples, D., Jiang, N., Zhang, T., Talwalkar, A., Kumar, A. (2025). Thinking vs. Doing: Agents that Reason by Scaling Test-Time Interaction. Preprint. arXiv:2506.07976. https://arxiv.org/abs/2506.07976
Design: equal-token comparison of per-step thinking (budget forcing), best-of-n and more interaction steps; RL curriculum (TTI).
Population: Gemma 3 12B on WebArena and WebVoyager.
Headline results: at equal budgets, forcing longer per-step thinking plateaued at about 0.26 success and changed the chosen action only about 15% of the time; best-of-n gave the smallest gains; more interaction steps had the steepest trend (WebArena 23% → ≥28% by prompting alone). TTI: WebVoyager 64.8% vs 55.8% zero-shot; WebArena 26.1% vs 18.3%.
Limitations: web agents; 12B model.
For orchestrate: for an agent with tools, more turns beat more thinking per turn at equal cost. Turn caps on workers cost more than effort caps do.

### 20. Bairi et al. 2023, CodePlan

Citation: Bairi, R., Sonwane, A., Kanade, A., Vageesh D C, Iyer, A., Parthasarathy, S., Rajamani, S., Ashok, B., Shet, S. (2023). CodePlan: Repository-level Coding using LLMs and Planning. Preprint (also FSE 2024). arXiv:2309.12499. https://arxiv.org/abs/2309.12499
Design: planning over a dependency graph with change-impact analysis vs the same LLM and context without planning.
Population: C# package migration and Python temporal edits across six repositories (2 to 97 files each).
Headline results: CodePlan passes validity checks in 5/6 repositories; the no-planning baseline in 0/6.
Limitations: two task types; one vendor team's repositories; 2023 model.
For orchestrate: for multi-file mechanical changes a dependency-ordered plan is the difference between building and not building. That is a decomposition the orchestrator could produce for migration-type parents: ordered sub-issues from a dependency walk.

### 21. Islam et al. 2024, MapCoder and Li et al. 2024, CodeTree

Citation: Islam, M. A., Ali, M. E., Parvez, M. R. (2024). MapCoder: Multi-Agent Code Generation for Competitive Problem Solving. ACL 2024. arXiv:2405.11403. https://arxiv.org/abs/2405.11403. Li, J., Le, H., Zhou, Y., Xiong, C., Savarese, S., Sahoo, D. (2024). CodeTree: Agent-guided Tree Search for Code Generation with Large Language Models. Preprint. arXiv:2411.04329. https://arxiv.org/abs/2411.04329
Design: multi-agent retrieve → plan → code → debug with ablations; tree search over plan, generate and refine with execution feedback.
Population: GPT-3.5/GPT-4 on HumanEval, MBPP, APPS, CodeContests, xCodeEval.
Headline results: MapCoder pass@1 HumanEval 93.9%, CodeContests 28.5%; removing the planning agent costs an average 16.7% and removing the debugging agent 17.5% (ChatGPT, HumanEval). CodeTree with GPT-4o: HumanEval 95.1, MBPP 98.7, CodeContests 43.0; SWE-bench gains stated without a figure.
Limitations: function-level and contest benchmarks; the SWE-bench number is not given.
For orchestrate: at function level the plan and debug stages each carry roughly a sixth of the gain; nothing here speaks to repositories.

### 22. Deng et al. 2025, SWE-Bench Pro and Zhou et al. 2026, SWE-RPG

Citation: Deng, X., Da, J., et al. (2025). SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks? Preprint. arXiv:2509.16941. https://arxiv.org/abs/2509.16941. Zhou, X., Chong, C. Y., Kim, K., Peng, Y., Shu, R., et al., Lo, D. (2026). A Unified Issue Resolution Benchmark for Requirement Clarification, Planning, and Code Generation for Coding Agents (SWE-RPG). Preprint. arXiv:2608.09072. https://arxiv.org/abs/2608.09072
Design: benchmark ablation removing human-written requirements and interface specifications; failure-stage attribution over trajectories.
Population: GPT-5 (high), Claude Opus 4.1, Sonnet 4.5 and others on 731 public Pro tasks; Claude Code, Codex, OpenCode with six LLMs on 163 SWE-RPG tasks.
Headline results: Pro public set with full problem statement, requirements and interface: GPT-5 25.9%, Opus 4.1 22.7%; problem statement only: 8.40% and 8.20%. Top Pro score Sonnet 4.5 43.6%. SWE-RPG average resolve 31.5%; implicit-requirement recovery is the main bottleneck in 24.5% to 46.0% of runs.
Limitations: Pro's ablation uses a capped-budget setting (Table 5); SWE-RPG is a v1 preprint.
For orchestrate: the ticket's requirements and interface are worth about 3× in resolve rate. Spending planning effort before dispatch, in the ticket (to-spec, to-tickets), is better supported than any planning step inside the worker.

### 23. Ma et al. 2026, ZORO

Citation: Ma, J., Wang, S., Kung, J. H., Chilton, L. B. (2026). ZORO: Active Controls to Support Rule Following in Vibe Coding. Preprint (v3). arXiv:2604.15625. https://arxiv.org/abs/2604.15625. Indexed by Thurlow (2026) HOS SLR-Derived-Improvements as `JCTP8VXP`.
Design: technical evaluation over 36 sessions (9 tasks × 4 conditions) plus a user study N=12.
Population: Cline with Claude Sonnet 4.6; Codex for the user study.
Headline results: rule-following 51% (±0.08) with a passive AGENTS.md vs 80% (±0.08) when rules are written into the plan and each step must show evidence; process rules 35% → 87%; feature completeness 0.64 vs 0.77 (not significant); about 4× tokens.
Limitations: small N; one run per task; 4× token overhead.
For orchestrate: a plan artifact is where rules bind. Asking the worker to list the config's rules against its plan steps, and to cite evidence per step in the PR, is a cheap way to raise compliance; the cost is tokens.

### 24. OpenAI 2025, Competitive Programming with Large Reasoning Models

Citation: El-Kishky, A., Wei, A., et al. (OpenAI) (2025). Competitive Programming with Large Reasoning Models. Preprint. arXiv:2502.06807. https://arxiv.org/abs/2502.06807
Design: vendor comparison of a hand-engineered test-time pipeline (o1-ioi) against a later RL-scaled model (o3) with no pipeline.
Population: IOI 2024, Codeforces.
Headline results: o1-ioi with hand-crafted test-time strategies placed in the 49th percentile live and reached gold only under relaxed constraints; o3 "achieves gold without hand-crafted domain-specific strategies or relaxed constraints".
Limitations: vendor-run; contest programming, not repositories; percentile for o3 not stated.
For orchestrate: hand-built search and sampling harnesses have a short half-life; a model generation later they are matched by the model alone. Any search paradigm should be a flag that can be switched off per model.

### 25. Anthropic 2025, How we built our multi-agent research system

Citation: Anthropic (2025). How we built our multi-agent research system. Engineering post. https://www.anthropic.com/engineering/multi-agent-research-system
Design: industry report with an internal eval and a BrowseComp regression.
Population: Claude Opus 4 lead with Sonnet 4 subagents.
Headline results: multi-agent beat single-agent Opus 4 by 90.2% on an internal research eval; on BrowseComp, token usage alone explained 80% of performance variance, with tool calls and model choice making up 95%; agents use about 4× chat tokens and multi-agent about 15×; effort is scaled by rule: 1 agent and 3–10 calls for a fact, 2–4 subagents with 10–15 calls for a comparison, 10+ subagents for complex research; the lead plans in extended thinking and saves the plan to memory; parallel subagents cut research time by up to 90%. "Most coding tasks have fewer parallelizable parts than research."
Limitations: research, not code; vendor-run; no cost-matched single-agent baseline.
For orchestrate: tokens spent is the best single predictor of success in this report, which makes the per-role profile a measurement of effort rather than waste; and the vendor itself says coding parallelises less than research.

### 26. Anthropic 2024–2025, SWE-bench posts and model announcements

Citation: Anthropic (2025). Raising the bar on SWE-bench Verified with Claude 3.5 Sonnet. https://www.anthropic.com/engineering/swe-bench-sonnet. Anthropic (2025). Introducing Claude Sonnet 4.5. https://www.anthropic.com/news/claude-sonnet-4-5. Anthropic (2025). Introducing Claude Opus 4.5. https://www.anthropic.com/news/claude-opus-4-5
Design: vendor benchmark reports.
Population: Claude 3.5 Sonnet, Sonnet 4.5, Opus 4.5 on SWE-bench Verified.
Headline results: 49% with "a prompt, a Bash Tool ... and an Edit Tool", the model "free to choose how it moves from step to step", a suggested five-step approach in the prompt (explore, reproduce, edit, rerun, edge cases), many runs taking "hundreds of turns" and ">100k tokens". Sonnet 4.5: 77.2% with no test-time compute; 82.0% with parallel attempts, rejection of patches that break visible regression tests, and an internal scoring model. Opus 4.5: at medium effort matches Sonnet 4.5's best Verified score with 76% fewer output tokens; at high effort 4.3 points above with 48% fewer tokens.
Limitations: vendor-run; the parallel-compute selector is internal; no planning ablation reported.
For orchestrate: the vendor's own best single-run numbers come from a ReAct loop with a suggested plan in the prompt, not a planning stage; its +4.8 points from parallel sampling needs a regression-test filter plus a selector, which is exactly the script gate plus reviewer shape.

### 27. OpenAI 2025, GPT-5, GPT-5-Codex and GPT-5.1-Codex-Max posts

Citation: OpenAI (2025). Introducing GPT-5 for developers. https://openai.com/index/introducing-gpt-5-for-developers/. OpenAI (2025). Introducing upgrades to Codex. https://openai.com/index/introducing-upgrades-to-codex/. OpenAI (2025). Building more with GPT-5.1-Codex-Max. https://openai.com/index/gpt-5-1-codex-max/. (Pages return 403 to automated fetch; figures confirmed from openai.com search snippets, so treat as secondary.)
Design: vendor reports, including internal traffic statistics.
Population: GPT-5, GPT-5-Codex, GPT-5.1-Codex-Max on SWE-bench Verified and OpenAI employee traffic.
Headline results (secondary): GPT-5 74.9% Verified vs o3 69.1%, "22% fewer output tokens and 45% fewer tool calls" than o3 at high effort; `reasoning_effort` gained `minimal` beside low, medium (default), high. GPT-5-Codex: for the bottom 10% of turns by generated tokens it "uses 93.7% fewer tokens than GPT-5"; for the top 10% it spends "twice as long reasoning, editing and testing". Codex-Max at medium beats GPT-5.1-Codex at medium "while using 30% fewer thinking tokens"; 73.7% (Codex high) vs 77.9% (Codex-Max xhigh).
Limitations: all vendor-reported; no sample sizes for traffic statistics.
For orchestrate: effort is being pushed into the model as adaptive thinking; an orchestrator's remaining lever is the ceiling (effort level) per role, and the vendors' own trend is to lower it for well-specified work.

### 28. Vendor effort controls and Claude Code guidance

Citation: Anthropic. Effort (Claude API docs). https://platform.claude.com/docs/en/build-with-claude/effort. OpenAI. Reasoning models guide. https://developers.openai.com/api/docs/guides/reasoning. Anthropic. Best practices for Claude Code. https://code.claude.com/docs/en/best-practices. Anthropic (2024). Building effective agents. https://www.anthropic.com/engineering/building-effective-agents
Design: API documentation and practitioner guidance.
Headline statements: Anthropic effort docs: "Lower effort also means fewer and terser tool calls"; for agentic coding on Sonnet 5.5 "start with `medium` for well-specified tasks and move to `high` for harder or longer ones"; `xhigh` is for "long-running agentic and coding tasks (over 30 minutes)"; at `max` the model "runs slower and costs more than at `xhigh`, often for only a small gain"; "run an effort sweep on your own evals". OpenAI: `high` is "recommended for complex workflows and agentic tasks"; `xhigh` "only ... when your evals show a clear benefit". Claude Code best practices: effort "replaces most of what people used to do with prompt wording"; "`think hard` ... are ordinary prompt text"; "Handing the same task to a subagent doesn't make the answer any better. A fresh reviewer does help"; `max` on ordinary tasks "tends to overthink". Building effective agents: "finding the simplest solution possible, and only increasing complexity when needed"; orchestrator-workers suits "coding products that make complex changes to multiple files each time"; agents "often trade latency and cost for better task performance".
Limitations: no measurements; guidance changes per model generation.
For orchestrate: the vendors converge on medium for well-specified tickets, high for review and hard tickets, and a measured sweep rather than a fixed setting; prompt phrases are not an effort control.

## Implementable findings

| id | finding | grade | source | for orchestrate |
|---|---|---|---|---|
| PL-01 | Repeated sampling raises SWE-bench Lite coverage log-linearly from 15.9% (1 sample) to 56% (250 samples); usable only with a verifier. | B | Brown et al. 2024 | flag: `--samples=N` per ticket, enabled only where the config has verify commands |
| PL-02 | With imperfect test verifiers the optimal number of attempts is "often fewer than 10", and 5 or fewer at a 4:1 cost ratio; false positives cap accuracy. | B | Stroebl et al. 2024 | rule: cap any sampling flag at N≤5 and refuse it for tickets with no test command |
| PL-03 | Execution-based selection beats voting: majority 25.67% → + regression tests 27.00% → + reproduction tests 32.00% on Lite; plateau near 40 samples. | B | Xia et al. 2024 | default: worker writes a failing reproduction test before the fix; the gate runs regression plus that test |
| PL-04 | A learned verifier lifts Best@1 20.6% to Best@16 32.0% on Verified but leaves 10.8 points to oracle; a weak verifier plateaus at k=4. | A | Pan et al. 2025 | measure: log pass-rate of the first vs second attempt per repo to see whether a selector would earn its cost |
| PL-05 | Two unlike verifiers (execution and judge) select better than either: 42–43% each → 51% combined on Verified. | B | Jain et al. 2025 | default: keep the script gate (tests) and the reviewer (judgment) as separate, both-required checks |
| PL-06 | The first few serial iterations give most of the coverage gain; equal-cost configurations then converge; selection recovers about half the gap to oracle. | B | Ehrlich et al. 2025 | rule: at most one extra attempt per ticket before escalating; prefer one deep worker over several shallow ones |
| PL-07 | Self-repair by the same model yields 0.97–1.05× the i.i.d. baseline at equal budget; initial samples matter more than repair rounds. | A | Olausson et al. 2024 | reject: do not add a self-review or "fix your own code" loop inside the worker; a second round needs new information |
| PL-08 | Feedback from a stronger model or a human makes repair pay: human feedback took repair success from 33.3% to 52.6%. | A | Olausson et al. 2024 | default: review feedback comes from a different context (reviewer, lead, user), as now |
| PL-09 | A planner lifts a ReAct executor from 9.85% to 29.63%, and dynamic replanning to 44.24% on WebArena-Lite; most of the gain is replanning. | A | Erdogan et al. 2025 | flag: worker plan step that is explicitly revisable after observations; never a frozen plan |
| PL-10 | Plan-then-implement gives up to 25.4% relative pass@1 over direct generation at function level. | A | Jiang et al. 2024 | default: the worker brief asks for a short ordered plan before the first edit |
| PL-11 | Hierarchical plan-ahead scores 44.8% vs 39.58% for a ReAct baseline on Verified (uncontrolled) and costs about 3× on ALFWorld. | A | Zhang et al. 2025 | flag: hierarchical decomposition inside the worker, off by default, profiled when on |
| PL-12 | Decomposing only when the executor fails beats upfront plan-and-execute by up to 28–33 points on agent tasks. | A | Prasad et al. 2024 | default: one worker per ticket; the lead splits into sub-issues only on a stop or failure |
| PL-13 | Plan-first DAG execution beats ReAct by up to 3.7× latency and 6.7× cost when calls are independent. | A | Kim et al. 2024 | default: plan at the orchestrator (greedy dispatch over non-overlapping paths), not inside the worker loop |
| PL-14 | A plan gate before code acts as a filter in the field: 82% of plans approved, 25% of code-generated issues reach a PR, 59% of PRs merge. | A | Takerngsaksiri et al. 2025 | flag: lead-approved plan for L-tier tickets; measure approve-then-abandon |
| PL-15 | Removing human-written requirements and interface specs drops resolve rate from 25.9% to 8.40% (GPT-5) and 22.7% to 8.20% (Opus 4.1). | B | Deng et al. 2025 | default: tickets carry requirements and interface; a takeable ticket without them gets `needs-info` |
| PL-16 | Implicit-requirement recovery is the main failure stage in 24.5% to 46.0% of agent runs. | B | Zhou et al. 2026 | measure: tag worker stops and review rejections by stage (requirement, plan, code) |
| PL-17 | Rules bound to plan steps with per-step evidence raise rule-following from 51% to 80% at about 4× tokens. | B | Ma et al. 2026 | flag: worker lists the config's rules against its plan and cites evidence per step in the PR body |
| PL-18 | Dependency-ordered planning passes 5/6 multi-file migration repositories; the same LLM without planning passes 0/6. | B | Bairi et al. 2023 | flag: for migration parents, the scout emits an ordered sub-issue list from a dependency walk |
| PL-19 | MCTS over agent states adds +4 to +7 points on Lite for five 2024 models at 5× (open models) to 14× (GPT-4o) the cost. | B | Antoniades et al. 2025 | reject: no tree search in the default worker; cost multiplier exceeds any measured gain on frontier models |
| PL-20 | Branching at edit and submit actions reaches 47% pass@1 on Lite at $12.24 per issue vs $1.61 for the linear agent (7.6×). | B | Aggarwal et al. 2025 | flag: on a failed first attempt, a second worker starts from the first worker's localization, not from scratch |
| PL-21 | Extra search budget "slightly decreased" results in the hardest difficulty bucket. | B | Ma et al. 2025 | rule: no second attempt for tickets the reviewer or scout rates hardest; hand them back instead |
| PL-22 | Compute-optimal allocation gives >4× efficiency over uniform best-of-N; extra compute pays only on prompts the model sometimes solves. | B | Snell et al. 2024 | rule: allocate any sampling or high effort by predicted difficulty (tier, path count), never uniformly |
| PL-23 | At equal tokens, more interaction steps beat longer per-step thinking (plateau at about 0.26 success; thinking changed the action 15% of the time). | B | Shen et al. 2025 | reject: do not cap worker turns to save tokens; cap effort instead |
| PL-24 | Token usage explains 80% of performance variance on BrowseComp; multi-agent uses about 15× chat tokens; coding parallelises less than research. | C | Anthropic 2025 | measure: tokens per ticket in the profile as an effort signal; expect smaller parallel gains than research |
| PL-25 | Vendors' best single-run Verified scores (49%, 77.2%) use a two-tool ReAct loop with a suggested step list in the prompt and no planning stage. | C | Anthropic 2025 | default: suggested steps in the worker brief; no mandatory planning tool |
| PL-26 | Parallel attempts plus a regression-test filter plus a scoring model add +4.8 points (77.2% → 82.0%) for Sonnet 4.5. | C | Anthropic 2025 | flag: the same shape as PL-01 to PL-03; vendor confirmation that it still pays on a frontier model |
| PL-27 | At medium effort Opus 4.5 matches Sonnet 4.5's best Verified score with 76% fewer output tokens; at high it is +4.3 points with 48% fewer. | C | Anthropic 2025 | flag: per-role effort (medium worker on well-specified tickets, high reviewer on risk paths) |
| PL-28 | Adaptive thinking cuts the cheapest 10% of turns by 93.7% tokens and doubles time on the top 10%; Codex-Max at medium uses 30% fewer thinking tokens than its predecessor. | C (secondary) | OpenAI 2025 | measure: effort sweep per role on the repo's own tickets before fixing a level |
| PL-29 | Hand-crafted test-time strategies (o1-ioi, 49th percentile live) were overtaken by the next model with none (o3 gold). | B | OpenAI 2025 | rule: every search or sampling paradigm is a flag with a per-model default, re-measured each model generation |
| PL-30 | Contradiction: coverage keeps rising log-linearly past 1,000 samples (PL-01) yet usable accuracy is capped and optimal N<10 (PL-02); selectors plateau near 100 samples. | B | Brown et al. 2024; Stroebl et al. 2024 | rule: treat coverage curves as upper bounds; budget by the selector, not the generator |
| PL-31 | Contradiction: plan-first beats ReAct on web tasks (PL-09) and tool DAGs (PL-13), but no controlled plan-vs-no-plan ablation exists on SWE-bench Verified, and the best Verified single runs are ReAct loops (PL-25). | A/C | Erdogan et al. 2025; Anthropic 2025 | measure: run the worker with and without a plan step on the same tickets before promoting PL-10 beyond a brief line |
| PL-32 | Vendors converge on medium effort for well-specified agentic coding, high for hard or long work, `xhigh`/`max` only with measured gain; prompt phrases are not an effort control. | C | Anthropic docs; OpenAI docs | default: medium for workers on tickets with requirements, high for reviewers; drop "think hard" wording from prompts |

## Candidate paradigms

- **Best-of-N with test selection** (PL-01, PL-02, PL-03, PL-05, PL-26): N workers per ticket, keep the patch that passes regression plus a reproduction test, reviewer breaks ties. Best evidence B; A for the verifier gap (PL-04).
- **Second attempt from localization** (PL-06, PL-20, Trae/SWE-Replay): on a failed or rejected first PR, one more worker that starts from the first worker's file list and test, then escalate. Best evidence B.
- **Plan-then-execute worker** (PL-09, PL-10, PL-17): the worker writes a short revisable plan with the config's rules attached, then edits; plan goes in the PR body. Best evidence A (function-level and web), unmeasured on SWE-bench Verified (PL-31).
- **Lead-approved plan gate** (PL-14): for L-tier or risk-path tickets the lead approves the plan before code. Best evidence A (field N=663, uncontrolled).
- **Lazy decomposition** (PL-12, PL-16): no upfront splitting; the lead decomposes on a worker stop. Best evidence A (agent tasks, not code).
- **Dependency-ordered decomposition for migrations** (PL-18, PL-13): the scout walks the dependency graph and emits ordered sub-issues for mechanical multi-file parents. Best evidence B.
- **Difficulty-routed compute** (PL-21, PL-22, PL-27, PL-32): effort level and attempt budget set by tier and path count; none for the hardest. Best evidence B.
- **Trajectory search (MCTS / action re-sampling)** (PL-19, PL-20, PL-29): rejected as default; a flag for weak or cheap models where 5× to 14× cost is acceptable. Best evidence B.
- **Interaction over deliberation** (PL-23, PL-25): generous turn caps, moderate effort, suggested steps in the brief. Best evidence B.
- **Rule-anchored plans** (PL-17): rules bound to plan steps with evidence per step. Best evidence B.
- **Same-model self-repair loop** (PL-07): rejected. Best evidence A.

## Open questions

- No controlled plan-vs-no-plan ablation exists on SWE-bench Verified or Pro with a frontier model; every plan-then-execute number is either function-level, web, or uncontrolled against a leaderboard baseline.
- Whether best-of-N still pays on 2026 frontier models at pass@1 above 75%: the vendor figure (+4.8 points) uses an internal selector; the open figures are from 32B models and 2024 Sonnet.
- The right N for a repository whose test suite is partial: Stroebl's bound depends on the false-positive rate of the verify commands, which no source measures for real repositories.
- Whether a lead-approved plan changes merge rate or only filters: HULA has no no-gate arm.
- Cost of lazy vs upfront decomposition for coding: ADaPT's gains are on agent tasks; SWE-RPG attributes failures to requirements, not to decomposition.
- Whether effort tiers per role move escaped-defect rate: all effort evidence is vendor token-and-score reporting with no review-quality outcome.
- Flaky tests: 11.3% of SWE-bench Lite suites were flaky; no source gives a rerun policy that separates flakiness from a wrong patch.

## Sources list

Olausson, T. X., Inala, J. P., Wang, C., Gao, J., Solar-Lezama, A. (2024). Is Self-Repair a Silver Bullet for Code Generation? ICLR 2024. arXiv:2306.09896. https://arxiv.org/abs/2306.09896
Pan, J., Wang, X., Neubig, G., Jaitly, N., Ji, H., Suhr, A., Zhang, Y. (2025). Training Software Engineering Agents and Verifiers with SWE-Gym. ICML 2025. arXiv:2412.21139. https://arxiv.org/abs/2412.21139
Erdogan, L. E., Lee, N., Kim, S., Moon, S., Furuta, H., Anumanchipalli, G., Keutzer, K., Gholami, A. (2025). Plan-and-Act: Improving Planning of Agents for Long-Horizon Tasks. ICML 2025. arXiv:2503.09572. https://arxiv.org/abs/2503.09572
Zhang, Z., Chen, T., Xu, W., Pentland, A., Pei, J. (2025). ReCAP: Recursive Context-Aware Reasoning and Planning for Large Language Model Agents. NeurIPS 2025. arXiv:2510.23822. https://arxiv.org/abs/2510.23822
Takerngsaksiri, W., Pasuksmit, J., Thongtanunam, P., Tantithamthavorn, C., Zhang, R., Jiang, F., Li, J., Cook, E., Chen, K., Wu, M. (2025). Human-In-the-Loop Software Development Agents. ICSE-SEIP 2025. arXiv:2411.12924. https://arxiv.org/abs/2411.12924
Jiang, X., Dong, Y., Wang, L., Fang, Z., Shang, Q., Li, G., Jin, Z., Jiao, W. (2024). Self-planning Code Generation with Large Language Models. ACM TOSEM. arXiv:2303.06689. https://arxiv.org/abs/2303.06689
Prasad, A., Koller, A., Hartmann, M., Clark, P., Sabharwal, A., Bansal, M., Khot, T. (2024). ADaPT: As-Needed Decomposition and Planning with Language Models. NAACL 2024 Findings. arXiv:2311.05772. https://arxiv.org/abs/2311.05772
Kim, S., Moon, S., Tabrizi, R., Lee, N., Mahoney, M. W., Keutzer, K., Gholami, A. (2024). An LLM Compiler for Parallel Function Calling. ICML 2024. arXiv:2312.04511. https://arxiv.org/abs/2312.04511
Xu, B., Peng, Z., Lei, B., Mukherjee, S., Liu, Y., Xu, D. (2023). ReWOO: Decoupling Reasoning from Observations for Efficient Augmented Language Models. Preprint. arXiv:2305.18323. https://arxiv.org/abs/2305.18323
Yao, S., Zhao, J., Yu, D., Du, N., Shafran, I., Narasimhan, K., Cao, Y. (2023). ReAct: Synergizing Reasoning and Acting in Language Models. ICLR 2023. arXiv:2210.03629. https://arxiv.org/abs/2210.03629
Wang, L., Xu, W., Lan, Y., Hu, Z., Lan, Y., Lee, R. K.-W., Lim, E.-P. (2023). Plan-and-Solve Prompting: Improving Zero-Shot Chain-of-Thought Reasoning by Large Language Models. ACL 2023. arXiv:2305.04091. https://arxiv.org/abs/2305.04091
Brown, B., Juravsky, J., Ehrlich, R., Clark, R., Le, Q. V., Ré, C., Mirhoseini, A. (2024). Large Language Monkeys: Scaling Inference Compute with Repeated Sampling. Preprint. arXiv:2407.21787. https://arxiv.org/abs/2407.21787
Stroebl, B., Kapoor, S., Narayanan, A. (2024). The Limits of Inference Scaling Through Resampling. Preprint. arXiv:2411.17501. https://arxiv.org/abs/2411.17501
Ehrlich, R., Brown, B., Juravsky, J., Clark, R., Ré, C., Mirhoseini, A. (2025). CodeMonkeys: Scaling Test-Time Compute for Software Engineering. Preprint. arXiv:2501.14723. https://arxiv.org/abs/2501.14723
Xia, C. S., Deng, Y., Dunn, S., Zhang, L. (2024). Agentless: Demystifying LLM-based Software Engineering Agents. Preprint. arXiv:2407.01489. https://arxiv.org/abs/2407.01489; repository https://github.com/OpenAutoCoder/Agentless
Antoniades, A., Örwall, A., Zhang, K., Xie, Y., Goyal, A., Wang, W. (2025). SWE-Search: Enhancing Software Agents with Monte Carlo Tree Search and Iterative Refinement. Preprint. arXiv:2410.20285. https://arxiv.org/abs/2410.20285
Aggarwal, V., Kamal, O., Japesh, A., Jin, Z., Schölkopf, B. (2025). DARS: Dynamic Action Re-Sampling to Enhance Coding Agent Performance by Adaptive Tree Traversal. Preprint. arXiv:2503.14269. https://arxiv.org/abs/2503.14269
Jain, N., Singh, J., Shetty, M., Zheng, L., Sen, K., Stoica, I. (2025). R2E-Gym: Procedural Environments and Hybrid Verifiers for Scaling Open-Weights SWE Agents. Preprint. arXiv:2504.07164. https://arxiv.org/abs/2504.07164
Ma, Y., Li, Y., Dong, Y., Jiang, X., Cao, R., Chen, J., Huang, F., Li, B. (2025). Thinking Longer, Not Larger: Enhancing Software Engineering Agents via Scaling Test-Time Compute. Preprint. arXiv:2503.23803. https://arxiv.org/abs/2503.23803
Gao, P., Tian, Z., Meng, X., Wang, X., Hu, R., Xiao, Y., Liu, Y., Zhang, Z., Chen, J., Gao, C., Lin, Y., Xiong, Y., Peng, C., Liu, X. (2025). Trae Agent: An LLM-based Agent for Software Engineering with Test-time Scaling. Technical report. arXiv:2507.23370. https://arxiv.org/abs/2507.23370
Ding, Y., Zhang, L. (2026). SWE-Replay: Efficient Test-Time Scaling for Software Engineering Agents. Preprint. arXiv:2601.22129. https://arxiv.org/abs/2601.22129
Snell, C., Lee, J., Xu, K., Kumar, A. (2024). Scaling LLM Test-Time Compute Optimally can be More Effective than Scaling Model Parameters. Preprint. arXiv:2408.03314. https://arxiv.org/abs/2408.03314
Wu, Y., Sun, Z., Li, S., Welleck, S., Yang, Y. (2024). Inference Scaling Laws: An Empirical Analysis of Compute-Optimal Inference for Problem-Solving with Language Models. Preprint. arXiv:2408.00724. https://arxiv.org/abs/2408.00724
Shen, J., Bai, H., Zhang, L., Zhou, Y., Setlur, A., Tong, S., Caples, D., Jiang, N., Zhang, T., Talwalkar, A., Kumar, A. (2025). Thinking vs. Doing: Agents that Reason by Scaling Test-Time Interaction. Preprint. arXiv:2506.07976. https://arxiv.org/abs/2506.07976
Bairi, R., Sonwane, A., Kanade, A., Vageesh D C, Iyer, A., Parthasarathy, S., Rajamani, S., Ashok, B., Shet, S. (2023). CodePlan: Repository-level Coding using LLMs and Planning. Preprint. arXiv:2309.12499. https://arxiv.org/abs/2309.12499
Islam, M. A., Ali, M. E., Parvez, M. R. (2024). MapCoder: Multi-Agent Code Generation for Competitive Problem Solving. ACL 2024. arXiv:2405.11403. https://arxiv.org/abs/2405.11403
Li, J., Le, H., Zhou, Y., Xiong, C., Savarese, S., Sahoo, D. (2024). CodeTree: Agent-guided Tree Search for Code Generation with Large Language Models. Preprint. arXiv:2411.04329. https://arxiv.org/abs/2411.04329
Deng, X., Da, J., et al. (2025). SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks? Preprint. arXiv:2509.16941. https://arxiv.org/abs/2509.16941
Zhou, X., Chong, C. Y., Kim, K., Peng, Y., Shu, R., Wu, Z., Han, X., Yuan, G., Zhuang, Z., Kim, J., Ju, J., Ju, S., Yoon, T., Lo, D. (2026). A Unified Issue Resolution Benchmark for Requirement Clarification, Planning, and Code Generation for Coding Agents. Preprint. arXiv:2608.09072. https://arxiv.org/abs/2608.09072
Ma, J., Wang, S., Kung, J. H., Chilton, L. B. (2026). ZORO: Active Controls to Support Rule Following in Vibe Coding. Preprint. arXiv:2604.15625. https://arxiv.org/abs/2604.15625
El-Kishky, A., Wei, A., Saraiva, A., Minaiev, B., Selsam, D., Dohan, D., Song, F., Lightman, H., Clavera, I., Pachocki, J., Tworek, J., et al. (OpenAI) (2025). Competitive Programming with Large Reasoning Models. Preprint. arXiv:2502.06807. https://arxiv.org/abs/2502.06807
Anthropic (2025). How we built our multi-agent research system. https://www.anthropic.com/engineering/multi-agent-research-system
Anthropic (2025). Raising the bar on SWE-bench Verified with Claude 3.5 Sonnet. https://www.anthropic.com/engineering/swe-bench-sonnet
Anthropic (2025). Introducing Claude Sonnet 4.5. https://www.anthropic.com/news/claude-sonnet-4-5
Anthropic (2025). Introducing Claude Opus 4.5. https://www.anthropic.com/news/claude-opus-4-5
Anthropic. Effort, Claude API documentation. https://platform.claude.com/docs/en/build-with-claude/effort
Anthropic. Best practices for Claude Code. https://code.claude.com/docs/en/best-practices
Anthropic (2024). Building effective agents. https://www.anthropic.com/engineering/building-effective-agents
OpenAI (2025). Introducing GPT-5 for developers. https://openai.com/index/introducing-gpt-5-for-developers/ (secondary: fetched via search snippets)
OpenAI (2025). Introducing upgrades to Codex. https://openai.com/index/introducing-upgrades-to-codex/ (secondary: fetched via search snippets)
OpenAI (2025). Building more with GPT-5.1-Codex-Max. https://openai.com/index/gpt-5-1-codex-max/ (secondary: fetched via search snippets)
OpenAI. Reasoning models guide. https://developers.openai.com/api/docs/guides/reasoning
Thurlow, S. (2026). Human Oversight System (HOS), documentation and research notes, https://github.com/thurlow-research/HumanOversightSystem (software archived at https://doi.org/10.5281/zenodo.21347272), docs CC BY 4.0, code MIT. Used only as an index: its SLR-Derived-Improvements pointed to Ma et al. 2026 (ZORO) and Takerngsaksiri et al. 2025 (HULA), both chased to arXiv above.
