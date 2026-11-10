# Multi-agent architectures: evidence for orchestrating AI coding agents

Research note for [#602](https://github.com/shrujancheruku/claude-orchestrate/issues/602), a child of Map #588. Every figure below was read from the primary paper (arXiv HTML, latest version as of 2026-11-10), the vendor's own post or the project's own repository; where a venue is known only from outside the paper it is marked "(secondary)". Area prefix `MA`.

1. **Splitting one coding task across several agents does not beat one capable agent on SWE-bench-class work.** The only controlled study (Kim et al. 2025/2026, 260 configurations, tools, prompts and tokens held equal) finds every multi-agent topology slightly below the single agent on SWE-bench Verified (centralized −3.1%, independent −14.9%) once the single-agent baseline passes about 45%; the leaderboard census (Martinez & Franch 2026) finds no architecture that consistently wins, and Warp, nFactorial and Cursor each report retreating from in-task role splits. The 2023-24 role-separation papers (MetaGPT, ChatDev, MapCoder, AgentCoder, Self-collaboration) show large gains, but on GPT-3.5/GPT-4 and on function-level benchmarks where the single-agent baseline was 45-61%.
2. **What does hold up is structure around the agent, not more agents inside it:** a pipeline or orchestrator that owns the control flow, script-run tests used to select or reject patches (Agentless: majority vote 25.67% → +regression 27.00% → +reproduction test 32.00%; Trae Agent: pruning + selector + vote worth 3.4-5.6 points each, Wilcoxon p < 0.0073), an independent tester or reviewer that did not write the code (AgentCoder: tests written by a separate agent are 87.8% accurate versus 61.0% when the same conversation writes code then tests; MAGIS: removing the per-developer QA reviewer costs 13.94% → 10.63%), and a well-designed agent-computer interface (SWE-agent: +64% over a raw shell).
3. **The failure modes of multi-agent systems are measured and specific.** MAST (1,642 traces, κ = 0.88) attributes 44% of failures to specification and system design (step repetition 15.7%, not recognising completion 12.4%, disobeying the task 11.8%), 32% to inter-agent misalignment (reasoning-action mismatch 13.2%, derailment 7.4%, not asking for clarification 6.8%) and 24% to verification (incorrect 9.1%, missing 8.2%, premature termination 6.2%); verifiers that only check compilation are explicitly called out. Independent parallel agents with no central check amplify trace-level errors 17.2× against 4.4× under a centralized orchestrator (Kim et al.).
4. **Oversight, not peers, is what rescues a stuck agent.** An Expert agent that reviews an Executor every 25 steps resolved 24 of 108 issues a single OpenHands agent had failed (22.2%) where a stronger single model recovered 7; MAST's topology fix "only the superior agent may close the conversation" gave ChatDev +15.6 points where prompt edits gave +9.4; Cursor's flat, self-coordinating swarm failed and was replaced by planners, workers and a judge.
5. **Thin or contradictory:** no study isolates "planner, coder, tester, reviewer" roles on a frontier model with the same budget as one agent (AgentForge 2026 claims +26 points but against a 14% GPT-4o baseline that is half the published figure); MAST's topology-beats-prompts result reverses on GPT-4 for AG2 (p = 0.4); whether multi-agent gains survive the 2025-26 models is unknown because the controlled study used 20-issue subsets, and Anthropic's own +90.2% multi-agent result is on research, with the explicit caveat that most coding tasks are not parallelisable enough.

## Sources

### 1. Kim et al. 2025/2026, Towards a Science of Scaling Agent Systems (grade A)

- **Citation:** Kim, Y., et al. (Google Research and collaborators). *Towards a Science of Scaling Agent Systems.* arXiv:2512.08296 (v3, 8 Apr 2026). Preprint. https://arxiv.org/abs/2512.08296. Companion post: https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/
- **Design:** controlled evaluation of 260 configurations: six benchmarks × five architectures (single agent; independent, centralized, decentralized and hybrid multi-agent) × 8-9 models from three families, with tools, prompts and compute standardised and total reasoning tokens matched across architectures (about 4,800 per trial). Mixed-effects regression with five-fold cross-validation; bootstrap confidence intervals in Table 16.
- **Population:** OpenAI, Google and Anthropic models (Claude Sonnet 4 and 4.5 on the coding benchmarks); SWE-bench Verified and Terminal-Bench use 20-instance subsets, the other four benchmarks 50-100 instances per configuration.
- **Headline results:** relative change versus the single agent ranges from +80.8% (Finance Agent, centralized) to −70.0% (PlanCraft, independent); all multi-agent variants degrade sequential planning by 39-70%. On SWE-bench Verified the single-agent mean is 0.522 and every multi-agent variant is lower: hybrid −2.1% (0.511), centralized −3.1% (0.506), decentralized −5.4% (0.494), independent −14.9% (0.444). On Terminal-Bench (single-agent 0.344) independent is +1.7% and centralized −19.2%. Capability saturation: coordination stops paying once the single-agent baseline is above ≈0.45. Trace-level error amplification: single 1.0×, centralized 4.4×, hybrid 5.1×, decentralized 7.8×, independent 17.2×, although the amplification term is not significant once efficiency and overhead are controlled for. Tool-coordination trade-off β = −0.096. The selection rule picks the best architecture for 87% of held-out configurations (random 20%, capability-only 54%); R²_CV = 0.373 (0.413 with a task-grounded capability index).
- **Limitations:** 20-issue SWE-bench subsets; token budgets matched, so the multi-agent systems could not spend more than the single agent; architectures are generic (no code-specific roles); v3 of a preprint.
- **For orchestrate:** the strongest evidence that an orchestrator should not split one ticket into planner/coder/tester sub-agents on a frontier model; it supports a centralized orchestrator as an error-containment point for work that is genuinely decomposable (independent tickets), which is orchestrate's level of parallelism.

### 2. Cemri et al. 2025, Why Do Multi-Agent LLM Systems Fail? (MAST) (grade A)

- **Citation:** Cemri, M., Pan, M. Z., Yang, S., Agrawal, L. A., Chopra, B., Tiwari, R., Keutzer, K., Parameswaran, A., Klein, D., Ramchandran, K., Zaharia, M., Gonzalez, J. E., Stoica, I. *Why Do Multi-Agent LLM Systems Fail?* arXiv:2503.13657 (v3, 26 Oct 2025). NeurIPS 2025 (secondary). https://arxiv.org/abs/2503.13657
- **Design:** grounded-theory analysis of 150+ traces by six annotators, inter-annotator agreement κ = 0.88 after three rounds; an o1-based LLM annotator (accuracy 94%, κ = 0.77; κ = 0.79 on two unseen systems) applied to 1,642 traces from seven frameworks (ChatDev, MetaGPT, HyperAgent, AppWorld, AG2, Magentic-One, OpenManus). Two intervention case studies with repeated runs and Wilcoxon tests.
- **Population:** GPT-4, GPT-4o, GPT-4o-mini, Claude 3.7 Sonnet, Qwen2.5-Coder-32B, CodeLlama-7B; coding (ProgramDev, HumanEval, SWE-bench Lite via HyperAgent), maths and general-agent tasks.
- **Headline results:** 14 failure modes in three categories. FC1 specification and system design 44.2%: disobey task spec 11.8%, disobey role 1.5%, step repetition 15.7%, loss of history 2.8%, unaware of termination 12.4%. FC2 inter-agent misalignment 32.4%: conversation reset 2.2%, fail to ask for clarification 6.8%, task derailment 7.4%, information withholding 0.85%, ignored other agent's input 1.9%, reasoning-action mismatch 13.2%. FC3 verification 23.5%: premature termination 6.2%, no or incomplete verification 8.2%, incorrect verification 9.1%. The paper notes that many verifiers only perform superficial checks such as whether the code compiles. Interventions on ChatDev (GPT-4o): ProgramDev-v0 baseline 25.0% → role-prompt fixes 34.4% → cyclic topology where only the CTO can close 40.6% (+15.6); HumanEval 89.6 → 90.3 → 91.5. On AG2 MathChat: GPT-4 baseline 84.75 ± 1.94 → improved prompt 89.75 ± 1.44, new three-role topology 85.50 ± 1.18 (p = 0.4, not significant); GPT-4o 84.25 → 89.00 / 88.83 (p = 0.03). MetaGPT versus ChatDev on the same benchmark: MetaGPT has 60-68% fewer FC1/FC2 failures but 1.56× more FC3 failures; GPT-4o has 39% fewer FC1 failures than Claude 3.7 Sonnet inside MetaGPT. HyperAgent's dominant modes on SWE-bench Lite are step repetition and incorrect verification.
- **Limitations:** failures are labelled, not causally attributed; most traces are 2023-era frameworks; the coding frameworks are toy-app generators, not repository agents; interventions are small N (32 tasks).
- **For orchestrate:** the failure vocabulary for the run log; the evidence that a verifier must run something rather than read; the topology lesson that only the superior (orchestrator or script) may close a round.

### 3. Xia et al. 2024, Agentless (grade B)

- **Citation:** Xia, C. S., Deng, Y., Dunn, S., Zhang, L. *Agentless: Demystifying LLM-based Software Engineering Agents.* arXiv:2407.01489 (v2). FSE 2025 (secondary). https://arxiv.org/abs/2407.01489
- **Design:** benchmark plus component ablations on SWE-bench Lite (300 issues) with gpt-4o-2024-05-13; a three-phase pipeline (hierarchical localization, repair by sampling, patch validation) with no agent control of the flow.
- **Headline results:** 32.00% (96/300) at $0.70 per issue, then the best open-source result, against SWE-agent 23.00% at $1.62 (Claude 3.5 Sonnet) and 18.33% at $2.53 (GPT-4o), AutoCodeRover 19.00% at $0.45 (GPT-4), OpenDevin CodeAct v1.8 26.67% at $1.14, CodeR 28.33% at $3.34. Localization: prompting alone finds the right file 78.67%, embeddings 67.67% (70.33% after folder filtering), combined 81.67%; a class/function *skeleton* localises better than the complete file (58.33% vs 53.67%) at a seventh of the cost ($0.02 vs $0.15); skipping the intermediate level ("direct from file-level") is worse and dearer (47.00%, $0.18). Repair: greedy location 88 fixes, merged samples 85, four location sets × 10 samples 96; one greedy sample per set already gives 80; gains plateau at about 40 samples; if any sample counted, 126 (42.0%) would be solved. Selection: majority voting alone 77 (25.67%) → plus existing regression tests 81 (27.00%) → plus generated reproduction tests 96 (32.00%) for $0.25 more; 213/300 issues yield a test that reproduces the bug. SWE-bench Lite-S removes 51 ambiguous or over-specified issues (249 remain); Agentless scores 84/249 (33.73%), rank 9 either way. The paper's indictment of agents: complex tool design, "an agent can take upwards of 30 or 40 turns", and limited self-reflection so one wrong step is amplified.
- **Limitations:** GPT-4o era; Lite only; cost figures exclude the test-run compute; no ablation on newer models or on Verified.
- **For orchestrate:** the clearest ablation that script-run tests (regression first, reproduction second) are what turn many candidates into a correct one; also that a scout should return a skeleton, not whole files.

### 4. Yang et al. 2024, SWE-agent (grade B)

- **Citation:** Yang, J., Jimenez, C. E., Wettig, A., Lieret, K., Yao, S., Narasimhan, K., Press, O. *SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering.* arXiv:2405.15793 (v3, 11 Nov 2024). NeurIPS 2024 (secondary). https://arxiv.org/abs/2405.15793
- **Design:** benchmark on SWE-bench full and Lite plus interface ablations on Lite, GPT-4 Turbo (gpt-4-1106-preview) and Claude 3 Opus.
- **Headline results:** 12.47% on the full test set ($1.59) and 18.00% on Lite ($1.67) with GPT-4 Turbo; Claude 3 Opus 10.46% / 13.00%. A Shell-only agent with the same model scores 11.00% on Lite (7.33% without a demonstration), so the ACI is a 64% relative gain; RAG gets 2.67%, so the agent is 8-13× dearer and 6.7× better. Ablations (Lite, GPT-4 Turbo, default 18.0): edit without linting 15.0 (−3.0); no edit command 10.3 (−7.7); iterative search 12.0 (−6.0) and no search 15.7 (−2.3), that is, a bad search tool is worse than none; viewer of 30 lines 14.3 (−3.7) and whole file 12.7 (−5.3) against the 100-line default; full history 15.0 (−3.0) against keeping only the last five observations; no demonstration 16.3 (−1.7). HumanEvalFix 87.7 / 89.7 / 87.9 (Python/JS/Java). Trajectories start with reproduction or localisation and then run edit-then-execute loops; recovery odds fall as failed edits accumulate.
- **Limitations:** 2024 models; single runs per ablation on 300 issues.
- **For orchestrate:** the scaffold around one agent is worth more than extra agents (the whole ACI is +7 points; collapsing history is +3); a linting guard on edits is a measurable rule.

### 5. Zhang et al. 2024, AutoCodeRover (grade B)

- **Citation:** Zhang, Y., Ruan, H., Fan, Z., Roychoudhury, A. *AutoCodeRover: Autonomous Program Improvement.* ISSTA 2024, doi:10.1145/3650212.3680384; arXiv:2404.05427. https://arxiv.org/abs/2404.05427
- **Design:** benchmark plus one ablation (adding spectrum-based fault localisation and a test-validated retry loop), GPT-4 (gpt-4-0125-preview).
- **Headline results:** SWE-bench Lite 19.00% (57) at 195 s and 37k tokens ($0.43) per task against SWE-agent 18.00% at 245k tokens ($2.51); full SWE-bench 12.42% vs 12.47%; pass@3 26.00%. With SBFL hints and three validated retries (ACR-sbfl) 22.00% (66), seven of them unique. Two-thirds of the "resolved" patches were judged correct and acceptable on inspection. The two tools complement each other (26 and 23 unique solves).
- **Limitations:** GPT-4 era; SBFL needs a failing test, which real issues often lack.
- **For orchestrate:** structured code-search APIs over an AST beat free shell exploration on cost by 6×; an execution-based hint plus a test-gated retry loop is worth 3 points.

### 6. Wang et al. 2024/2025, OpenHands (grade B)

- **Citation:** Wang, X., et al., Neubig, G. *OpenHands: An Open Platform for AI Software Developers as Generalist Agents.* arXiv:2407.16741. ICLR 2025 (per the arXiv listing). https://arxiv.org/abs/2407.16741
- **Design:** platform paper with benchmark results across 15 tasks; a single generalist CodeActAgent, an event stream, sandboxed runtime and an `AgentDelegateAction` for multi-agent delegation.
- **Headline results:** CodeActAgent v1.8 on SWE-bench Lite: claude-3-5-sonnet 26.0% ($1.10), gpt-4o 22.0% ($1.72), gpt-4o-mini 7.0% ($0.01). HumanEvalFix 79.3% zero-shot. The delegation test: on WebArena the BrowsingAgent alone scores 8.5 / 14.8 / 15.5 (gpt-4o-mini / gpt-4o / claude-3.5-sonnet) and CodeActAgent delegating to it scores 8.3 / 14.5, so handing a sub-task to a specialist agent recovered the specialist's score but added nothing.
- **Limitations:** no ablation of the agent itself; the delegation comparison is incidental.
- **For orchestrate:** the "single generalist agent plus sandbox" design is the baseline every multi-agent claim must beat; delegation is a cost, not a gain, unless the sub-agent can do something the parent cannot.

### 7. Hong et al. 2023/2024, MetaGPT (grade B)

- **Citation:** Hong, S., et al., Schmidhuber, J. *MetaGPT: Meta Programming for a Multi-Agent Collaborative Framework.* arXiv:2308.00352. ICLR 2024 (secondary). https://arxiv.org/abs/2308.00352
- **Design:** design paper with benchmarks and a role ablation; five roles (Product Manager, Architect, Project Manager, Engineer, QA Engineer) following SOPs, structured documents, a publish-subscribe message pool and an executable-feedback loop (up to 3 retries). GPT-4.
- **Headline results:** HumanEval 85.9% and MBPP 87.7% pass@1; executable feedback adds 4.2 and 5.4 points. SoftwareDev (70 tasks): executability 3.75/4 against ChatDev 2.25 and MetaGPT-without-feedback 3.67; human revision cost 0.83 vs 2.5 vs 2.25; tokens 31,255 vs 19,292 vs 24,613. Role ablation (two tasks): Engineer alone executability 1.0, 10 revisions, $0.915; + Product Manager 2.0, 6.5, $1.059; + Architect 2.5, 4.0, $1.204; + Project Manager instead 2.0, 3.5, $1.251; all four 4.0, 2.5, $1.385.
- **Limitations:** two-task ablation with no variance; GPT-4-2023; "executability" is a 1-4 rubric; MAST later finds MetaGPT has 1.56× ChatDev's verification failures.
- **For orchestrate:** evidence that written artefacts between roles (PRD, design, task list) reduce rework on a 2023 model; the cost of each extra role is small in dollars but the benchmark is tiny.

### 8. Qian et al. 2023/2024, ChatDev (grade B)

- **Citation:** Qian, C., et al. *ChatDev: Communicative Agents for Software Development.* arXiv:2307.07924. ACL 2024 (per the arXiv listing). https://arxiv.org/abs/2307.07924
- **Design:** design paper with evaluation on SRDD (1,200 prompts), significance tests against baselines, pairwise human and GPT-4 preference, and an ablation of phases, dehallucination and roles. ChatGPT-3.5, temperature 0.2.
- **Headline results:** completeness / executability / consistency / quality: GPT-Engineer 0.5022 / 0.3583 / 0.7887 / 0.1419; MetaGPT 0.4834 / 0.4145 / 0.7601 / 0.1523; ChatDev 0.5600 / 0.8800 / 0.8021 / 0.3953 (differences p < 0.05). Humans prefer ChatDev 90.16% vs GPT-Engineer and 88.00% vs MetaGPT. Cost: 22,949 tokens and 148 s per project against GPT-Engineer's 7,182 tokens and 15.6 s. Ablation: removing all role descriptions is the largest hit (executability 0.88 → 0.58, quality 0.3953 → 0.2212); removing communicative dehallucination drops quality to 0.3094; stopping after coding (no review or test phases) gives executability 0.77 and quality 0.2512; the testing phase is what moves executability.
- **Limitations:** GPT-3.5; metrics are automatic proxies for toy applications; roles are removed all at once.
- **For orchestrate:** role prompts and a test phase are each worth measurable quality on a weak model; the instructor/assistant pairing (one proposes, one critiques) is the origin of the worker/reviewer split.

### 9. Islam et al. 2024, MapCoder (grade B)

- **Citation:** Islam, M. A., Ali, M. E., Parvez, M. R. *MapCoder: Multi-Agent Code Generation for Competitive Problem Solving.* arXiv:2405.11403. ACL 2024 (secondary). https://arxiv.org/abs/2405.11403
- **Design:** four prompting "agents" (retrieval, planning, coding, debugging) with agent-removal ablations on HumanEval using ChatGPT (gpt-3.5-turbo-1106); GPT-4 (gpt-4-1106-preview) and Gemini Pro for main results.
- **Headline results:** pass@1 with GPT-4: HumanEval 93.9%, MBPP 83.1%, APPS 22.0%, CodeContests 28.5%, xCodeEval 45.3%; CodeContests pass@1 matches AlphaCodium's pass@5 (28.5% vs 29%). Ablation: removing the Debugging agent alone costs 17.5%, and 24.83% on average over all removals; the Planning agent is second at 16.7% average. The debugger uses only the problem's sample I/O, because AI-generated tests are a liability: 8% of Reflexion's HumanEval failures and 15% of its MBPP failures came from wrong generated tests, and with ChatGPT in place of GPT-4 Reflexion drops 26.3%.
- **Limitations:** the ablation table is an image in the HTML, so only the text deltas are quoted; function-level benchmarks; 2023-24 models.
- **For orchestrate:** the execution-feedback step, not the extra persona, carries the gain; generated tests can mislead a weaker model, which argues for regression tests before generated ones.

### 10. Huang et al. 2023/2024, AgentCoder (grade B)

- **Citation:** Huang, D., Zhang, J. M., Luck, M., Bu, Q., Qing, Y., Cui, H. *AgentCoder: Multi-Agent-based Code Generation with Iterative Testing and Optimisation.* arXiv:2312.13010 (v3, 24 May 2024). Preprint. https://arxiv.org/abs/2312.13010
- **Design:** three agents (programmer, test designer, test executor, the last a Python script) with agent-removal ablations and a direct test of separating the programmer from the test writer. GPT-3.5-turbo and GPT-4; 14 LLMs and 16 baselines overall.
- **Headline results:** GPT-4: HumanEval 96.3%, MBPP 91.8% at 56.9K / 66.3K tokens against the prior best 90.2% / 78.9% at 138.2K / 206.5K. GPT-3.5 ablation (HumanEval / MBPP): programmer alone 61.0 / 47.9; + test designer 64.0 / 62.3; + test executor 64.6 / 69.3; all three 79.9 / 89.9. RQ6: when one agent writes the code and then the tests in the same conversation, test accuracy is 61.0 / 51.8; with a separate test-designer agent it is 87.8 / 89.9. The test designer's accuracy (89.6% / 91.4% with GPT-4) beats MetaGPT's (79.3% / 84.4%).
- **Limitations:** function-level benchmarks; GPT-3.5 for the ablation; no repository context.
- **For orchestrate:** the cleanest measurement that a tester who did not see the implementation writes tests that actually test; it is the evidence behind keeping the reviewer and the worker in separate contexts.

### 11. Dong et al. 2023/2024, Self-collaboration Code Generation (grade B)

- **Citation:** Dong, Y., Jiang, X., Jin, Z., Li, G. *Self-collaboration Code Generation via ChatGPT.* ACM TOSEM (accepted); arXiv:2304.07590 (v3, 11 May 2024). https://arxiv.org/abs/2304.07590
- **Design:** analyst, coder and tester roles over GPT-3.5 with a waterfall-with-feedback loop; role ablations and alternative team shapes.
- **Headline results:** HumanEval 57.3 → 74.4 pass@1 (+29.9%); 29.9-47.1% relative gains across four benchmarks. Role ablation (NL-only prompts, HumanEval / HumanEval-ET): coder 45.1 / 35.3; + analyst 54.9 / 45.2; + tester 56.8 / 45.2; both 63.5 / 51.9 (+40.8% / +47.1%). Adding a compiler to the tester gives 64.6 / 50.0 (no better); a driver/observer pair 57.4 / 44.6. Role instructions 64.4 beat the same stages without personas (60.0 zero-shot, 58.1 four-shot).
- **Limitations:** GPT-3.5 only; function-level.
- **For orchestrate:** the "plan then code then test" decomposition and persona framing each measurably helped a weak model; whether either survives on a frontier model is untested here.

### 12. Tao et al. 2024, MAGIS (grade B)

- **Citation:** Tao, W., et al. *MAGIS: LLM-Based Multi-Agent Framework for GitHub Issue Resolution.* arXiv:2403.17927 (v2). NeurIPS 2024 (secondary). https://arxiv.org/abs/2403.17927
- **Design:** Manager, Repository Custodian (BM25 file ranking), Developer and a QA Engineer paired with each Developer; ablations removing QA and removing the PR "hints". GPT-4 on the full SWE-bench test set (2,294).
- **Headline results:** 13.94% resolved (97.39% of patches apply) against GPT-4 direct 1.74%; without QA 10.63%; without hints 10.28%; without both 8.71%. On SWE-bench Lite (appendix) 25.33%, and 23.33% without QA.
- **Limitations:** uses PR hints by default, which later leaderboards forbid; GPT-4 era; QA is a reviewer without test execution.
- **For orchestrate:** a per-worker reviewer round is worth about a quarter of the solves on a 2024 model (13.94 → 10.63), the only issue-level measurement of a code-review role.

### 13. Phan et al. 2024/2025, HyperAgent (grade B)

- **Citation:** Phan, H. N., Nguyen, T. N., Nguyen, P. X., Bui, N. D. Q. *HyperAgent: Generalist Software Engineering Agents to Solve Coding Tasks at Scale.* arXiv:2409.16299 (v3, 5 Sep 2025). Preprint. https://arxiv.org/abs/2409.16299
- **Design:** a Planner delegating to Navigator, Editor and Executor child agents over an asynchronous queue, with a different model per role (GPT-4 / Claude 3 Sonnet as strong, Claude 3 Haiku as weak, Llama-3-70B/8B open); role and tool ablations on "SWE-bench Tiny" (100 Lite instances).
- **Headline results:** HyperAgent-Full-1 33.00% on Verified ($1.82, 320 s) and 26.00% on Lite, beside SWE-agent + Claude 3.5 Sonnet 33.60% ($1.79) and Agentless + GPT-4o 33.20% ($0.34); the cheaper Lite-1 configuration 30.20% at $0.45. Role ablation (Lite-1 on Tiny, the Planner absorbing the removed role): full 24.00% at $0.48; no Navigator 9.00% at $1.32; no Editor 11.00% at $1.49; no Executor 16.00% at $0.76. Tool ablation: no code search 3% vs 8%, with context and ranking 14-15%; go_to_definition unusable without proximity search (9% → 15%). Error analysis names early exit "due to hallucination that the task has been solved" and loss of detail "since the communication between agents can lose details about real execution result".
- **Limitations:** 100-instance ablation; not peer reviewed; MAST finds step repetition and incorrect verification dominate its failures.
- **For orchestrate:** supports cheaper models for navigation and execution under a strong planner, with a 3-point, 4× cost trade; it also documents the information-loss failure of relaying results through a coordinator.

### 14. Fourney et al. 2024, Magentic-One (grade B)

- **Citation:** Fourney, A., et al. (Microsoft Research). *Magentic-One: A Generalist Multi-Agent System for Solving Complex Tasks.* arXiv:2411.04468. Preprint. https://arxiv.org/abs/2411.04468
- **Design:** an Orchestrator keeping a task ledger (facts, guesses, plan) and a progress ledger (five questions each step), directing WebSurfer, FileSurfer, Coder and ComputerTerminal; agent-removal and orchestrator-replacement ablations on the GAIA validation set with GPT-4o; AutoGenBench for repeated, isolated runs.
- **Headline results:** GAIA 38%, WebArena 32.8%, AssistantBench 27.7%, statistically competitive with the leaders. Replacing the ledger orchestrator with AutoGen's plain GroupChat speaker selection cuts GAIA performance by 31%; removing any one worker agent costs 21% (Coder, Executor) to 39% (FileSurfer). All agents reset their context after each plan revision.
- **Limitations:** not a coding benchmark; GPT-4o and o1 only.
- **For orchestrate:** the measured value of an orchestrator that tracks progress and re-plans against one that merely picks who speaks next; the ledger is the analogue of orchestrate's pass record.

### 15. Antoniades et al. 2024/2025, SWE-Search (grade B)

- **Citation:** Antoniades, A., et al. *SWE-Search: Enhancing Software Agents with Monte Carlo Tree Search and Iterative Refinement.* arXiv:2410.20285 (v6, 2 Apr 2025). ICLR 2025 (secondary). https://arxiv.org/abs/2410.20285
- **Design:** MCTS over agent states with a Value agent and a Discriminator (multi-agent debate over up to five candidates), against a matched Moatless-Adapted baseline on SWE-bench Lite across five models.
- **Headline results:** resolve rate baseline → SWE-Search: GPT-4o 25.7 → 31.0 (+17%), GPT-4o-mini 13.0 → 17.0 (+24%), Qwen2.5-72B 18.0 → 24.7 (+27%), DeepSeek-V2.5 16.3 → 21.0 (+22%), Llama-3.1-70B 13.6 → 17.7 (+23%); mean +23%. The value function picks the right candidate 73% of the time; the debate raises selection accuracy to 84%.
- **Limitations:** search multiplies cost; the debate is measured only as a selector.
- **For orchestrate:** backtracking search is a paradigm worth a toggle for hard tickets, and a second-opinion selection step is worth 11 points of selection accuracy.

### 16. Ehrlich et al. 2025, CodeMonkeys (grade B)

- **Citation:** Ehrlich, R., et al. *CodeMonkeys: Scaling Test-Time Compute for Software Engineering.* arXiv:2501.14723 (v2, 3 Feb 2025). Preprint. https://arxiv.org/abs/2501.14723
- **Design:** every issue gets 10 parallel (edit, test) trajectories from paired state machines iterating on execution feedback; selection by generated tests then a model vote; Claude 3.5 Sonnet with Qwen2.5-Coder-32B scanning every file for context.
- **Headline results:** 57.4% on SWE-bench Verified for about $2,300 in total; selecting over an ensemble of existing top submissions ("Barrel of Monkeys") reaches 66.2%, above the best member alone. Splitting a fixed budget between serial iteration and parallel samples gives similar coverage either way.
- **Limitations:** budget far above a per-ticket run; Claude 3.5 Sonnet.
- **For orchestrate:** parallel candidates plus test-based selection is a measured paradigm; the serial-versus-parallel indifference means a longer single worker run is as good as several short ones for coverage, with selection being the hard part.

### 17. Trae Research Team 2025, Trae Agent (grade B)

- **Citation:** Trae Research Team (ByteDance). *Trae Agent: An LLM-based Agent for Software Engineering with Test-time Scaling.* arXiv:2507.23370 (31 Jul 2025). Technical report. https://arxiv.org/abs/2507.23370
- **Design:** generate many patches, prune (deduplicate, regression-test), then a selector agent with tools and majority voting; five single-component ablations on SWE-bench Verified with Gemini 2.5 Pro, Claude 3.7 Sonnet, GPT-4.1 and a mixture; Wilcoxon signed-rank tests.
- **Headline results:** 75.20% pass@1, first on the Verified leaderboard at the time. Ablation (Gemini / Claude 3.7 / GPT-4.1 / mixture): full 62.27 / 66.40 / 59.00 / 65.67; without pruning 58.40 / 63.60 / 56.20 / 61.80; without dedup 60.00 / 64.00 / 57.00 / 63.20; without regression tests 60.20 / 64.60 / 56.80 / 63.40; prompting-based selection in place of the selector agent 59.80 / 64.60 / 57.20 / 61.80; without majority voting 59.60 / 63.60 / 57.40 / 62.60. Average gains: pruning 5.57, dedup 3.73, regression 3.42, selector agent 4.08, voting 4.14 points; all p < 7.32 × 10⁻³.
- **Limitations:** vendor report; the 75.20% headline uses a model mixture; the base single-patch rate is not in the ablation table.
- **For orchestrate:** the most recent ablation of selection machinery on frontier models: regression tests as a pruning gate and a tool-using selector are each worth 3-4 points.

### 18. Soni et al. 2025, OpenHands-Versa (grade B)

- **Citation:** Soni, A. B., Li, B., Wang, X., Chen, V., Neubig, G. *Coding Agents with Multimodal Browsing are Generalist Problem Solvers.* arXiv:2506.03011. Preprint. https://arxiv.org/abs/2506.03011
- **Design:** one generalist agent (OpenHands plus multimodal browsing, search and file conversion) compared with specialised single- and multi-agent systems on three benchmarks; claude-3-7-sonnet and claude-sonnet-4.
- **Headline results:** GAIA 51.16%, SWE-Bench Multimodal 34.43%, The Agent Company 33.14% full completion; absolute gains of 1.3, 9.1 and 9.1 points over the best published open systems. Magentic-One (gpt-4o + o1) 37.87% on GAIA; OWL-roleplaying, a multi-agent system at 58.18% on GAIA validation, scores 4.00% full completion on The Agent Company; Agentless-Lite 25.34% on SWE-Bench M.
- **Limitations:** the comparison systems were tuned for other domains; one model family.
- **For orchestrate:** one worker role with complete tools generalises; specialised sub-agents tuned per benchmark do not transfer.

### 19. Xia et al. 2025, Live-SWE-agent, with mini-SWE-agent (grade B)

- **Citation:** Xia, C. S., Wang, Z., Yang, Y., Wei, Y., Zhang, L. *Live-SWE-agent: Can Software Engineering Agents Self-Evolve on the Fly?* arXiv:2511.13646 (v3, 24 Nov 2025). Preprint. https://arxiv.org/abs/2511.13646. Baseline: SWE-agent team, *mini-SWE-agent* README, https://github.com/SWE-agent/mini-swe-agent
- **Design:** a bash-only agent (mini-SWE-agent, "some 100 lines of python", no tools other than bash) allowed to write its own tools while solving; one patch per issue, no test-time scaling.
- **Headline results:** SWE-bench Verified, mini-SWE-agent → Live-SWE-agent: GPT-5-Mini 59.8% → 63.0%, GPT-5 65.0% → 68.4%, Claude 4.5 Sonnet 70.6% ($0.56) → 75.4% ($0.68), Gemini 3 Pro 74.2% ($0.46) → 77.4% ($0.48). SWE-Bench Pro 45.8% versus SWE-agent 43.6% with Claude 4.5 Sonnet. The README's own claim: "Scores >74% on the SWE-bench verified benchmark".
- **Limitations:** vendor-adjacent (the Agentless group); no multi-agent arm.
- **For orchestrate:** the 2025 baseline a multi-agent design must beat is a single bash agent at 70-74% on Verified; the scaffold's marginal value on frontier models is 3-5 points.

### 20. Liu et al. 2025, An Empirical Study on Failures in Automated Issue Solving (grade B)

- **Citation:** Liu, S., et al. *An Empirical Study on Failures in Automated Issue Solving.* arXiv:2509.13941 (17 Sep 2025). Preprint. https://arxiv.org/abs/2509.13941
- **Design:** three tools on SWE-bench Verified (OpenHands-CodeAct-2.1, "Tools + Claude 3.5 Sonnet", Agentless-1.5), manual coding of 150 failed instances by four annotators into 3 phases, 9 categories and 25 subcategories; then an Expert-Executor experiment.
- **Headline results:** the tools resolve 49-53% with complementary coverage. 51.3% of Agentless failures contain a localisation error; 49.1% (OpenHands) and 51.7% (Tools + Claude) of agentic failures sit in iteration and validation. About 65% of root causes are flawed reasoning, including "cognitive deadlocks" where the agent repeats a failing approach; about 25% knowledge deficiency. An Expert agent reviewing an Executor (both Claude 3.5 Sonnet, passive review every 25 steps, 40 rounds) resolved 24 of 108 issues the single OpenHands agent had failed (22.2%); a single OpenHands agent on Claude 4 Sonnet resolved 7 of the same 108.
- **Limitations:** the Expert-Executor run is on the failure subset only, not a full benchmark; no cost reported.
- **For orchestrate:** the measured case for a supervisor that intervenes on a stalled worker rather than for parallel peers; also an objective trigger (non-progressive iteration) to instrument.

### 21. Martinez & Franch 2025/2026, Dissecting the SWE-Bench Leaderboards (grade B)

- **Citation:** Martinez, M., Franch, X. *Dissecting the SWE-Bench Leaderboards: Profiling Submitters and Architectures of LLM- and Agent-Based Repair Systems.* arXiv:2506.17208 (v3, 5 Feb 2026); RQ1 published at ICSE-SEIP 2026. https://arxiv.org/abs/2506.17208
- **Design:** census of every SWE-bench Lite and Verified submission with public descriptions, classified into six architecture groups (G1 no agent; G2/G3 fixed workflow with one/many agents; G4/G5 scaffolded with one/many agents; G6 emergent single agent); Kruskal-Wallis and Dunn tests on scores.
- **Headline results:** "no single architecture consistently achieves state-of-the-art performance". On Verified the top entry (75.2%) is in G3 (fixed workflow, multiple agents; 5 submissions); G6 (single agent, emergent workflow) has the most submissions (31) and reaches 73.2% with the second-best median; groups differ (H = 19.41, p = 0.0070), with G1 significantly below G3, G4 and G6. Warp (71% on Verified) reports trying dedicated testing, planning and reasoning agents and finding a single primary agent the most consistent; nFactorial's first entry (25.8%) was a fixed three-phase pipeline and its later ones hand control to the agent. The paper also records Cognition's argument against multi-agent coding systems and Anthropic's caveat that "most coding tasks" are not a good fit.
- **Limitations:** observational; self-described architectures; model generation confounds everything.
- **For orchestrate:** the field-level picture: at the frontier, scaffold choice is not what separates submissions, and practitioners who tried in-task role splits went back to one agent.

### 22. Li et al. 2025, SWE-Debate (grade B)

- **Citation:** Li, H., et al. *SWE-Debate: Competitive Multi-Agent Debate for Software Issue Resolution.* arXiv:2507.23348. Preprint. https://arxiv.org/abs/2507.23348
- **Design:** multiple fault-propagation chains, a localisation debate and a plan debate before patching; same-model comparison on SWE-bench Verified with DeepSeek-V3-0324.
- **Headline results:** 41.4% (207/500) against SWE-Search 35.4% and SWE-agent 38.8% on the same model.
- **Limitations:** one open model; debate cost not isolated.
- **For orchestrate:** a +2.6 point same-model gain over SWE-agent from structured debate over localisation and plans; small next to the cost.

### 23. Kumar et al. 2026, AgentForge (grade B, treat with caution)

- **Citation:** Kumar, R., et al. *AgentForge: Execution-Grounded Multi-Agent LLM Framework for Autonomous Software Engineering.* arXiv:2604.13120 (13 Apr 2026). Preprint. https://arxiv.org/abs/2604.13120
- **Design:** Planner, Coder, Tester, Debugger and Critic over GPT-4o with a mandatory Docker sandbox; agent-removal ablation on 100 SWE-bench Lite tasks.
- **Headline results:** 40.0% on Lite against a single-agent GPT-4o baseline of 14.0% and ReAct 12.0%. Ablation: full 42.0%, no Critic 38.0%, no Debugger 31.0%, no Tester 28.0%, no Planner 19.0%, single agent 14.0%.
- **Limitations:** the 14.0% GPT-4o baseline is well under published single-agent figures for the same model (OpenHands CodeActAgent 22.0%, Agentless 32.00%), so the +26-point claim is against a weak baseline; not peer reviewed; 100-task ablation.
- **For orchestrate:** the only 2026 role ablation; the ordering (tester and debugger loop > planner > critic) is consistent with the older papers, the magnitude is not trustworthy.

### 24. Li et al. 2024, More Agents Is All You Need, with CodeR and MASAI (grade B)

- **Citation:** Li, J., Zhang, Q., Yu, Y., Fu, Q., Ye, D. *More Agents Is All You Need.* TMLR; arXiv:2402.05120 (v2). https://arxiv.org/abs/2402.05120. Chen, D., et al. *CodeR: Issue Resolving with Multi-Agent and Task Graphs.* arXiv:2406.01304. Arora, D., et al. (Microsoft Research). *MASAI: Modular Architecture for Software-engineering AI Agents.* arXiv:2406.11638.
- **Design and results:** Li et al.: sampling-and-voting across up to 40 instances scales accuracy with ensemble size; Llama2-13B at ensemble 15 matches Llama2-70B on GSM8K; gains are larger on harder tasks and weaker models. CodeR: Manager, Reproducer, Fault Localizer, Editor, Verifier over task graphs, 28.33% (85/300) on SWE-bench Lite with GPT-4 at $3.34 per issue (Agentless Table 1), MASAI: five sub-agents (test template generator, issue reproducer, edit localizer, fixer, ranker), 28.33% on Lite.
- **Limitations:** "more agents" is sampling, not coordination, and Kim et al. explicitly rebut the framing; CodeR and MASAI publish no role ablations.
- **For orchestrate:** parallel sampling helps weak models most; the 2024 multi-agent SWE-bench systems cost 5× Agentless for fewer solves, which is the cost side of role separation.

### 25. Anthropic engineering posts and Claude Code documentation (grade C)

- **Citations:** Anthropic. *How we built our multi-agent research system.* 13 Jun 2025. https://www.anthropic.com/engineering/multi-agent-research-system. Anthropic. *Effective harnesses for long-running agents.* 26 Nov 2025. https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents. Anthropic. *Building effective agents.* 19 Dec 2024. https://www.anthropic.com/engineering/building-effective-agents. Anthropic. Claude Code documentation, best-practices and subagents pages, read 2026-11-10. https://code.claude.com/docs/en/sub-agents
- **Design:** industry reports with internal evaluations; no public data.
- **Headline results:** Research: "a multi-agent system with Claude Opus 4 as the lead agent and Claude Sonnet 4 subagents outperformed single-agent Claude Opus 4 by 90.2% on our internal research eval"; on BrowseComp "token usage by itself explains 80% of the variance", three factors 95%; agents use about 4× and multi-agent systems about 15× the tokens of chat; "most coding tasks involve fewer truly parallelizable tasks than research, and LLM agents are not yet great at coordinating and delegating to other agents in real time"; early failures were spawning 50 subagents for simple queries and duplicated searches; fixes were explicit scaling rules (1 agent and 3-10 tool calls for a fact, 2-4 subagents with 10-15 calls for a comparison, >10 for complex research), delegation briefs with "an objective, an output format, guidance on the tools and sources to use, and clear task boundaries", parallel spawning that "cut research time by up to 90%", subagent outputs written to a filesystem "to minimize the game of telephone", and a 20-query eval. Harness: with Opus 4.5 the failure patterns were one-shotting too much at once, leaving half-implemented undocumented features, and a later session declaring the job done prematurely; a third was marking features complete without end-to-end testing; the fix is an initializer agent that writes a 200+ item feature list marked failing, a progress file and a git commit, then one feature per session. Building effective agents: find the simplest solution possible; orchestrator-workers is the pattern for coding changes whose subtasks cannot be predicted in advance; evaluator-optimizer pairs a generator with a critic. Claude Code docs: a subagent works in its own context window and returns a summary; the cost is tokens and the time a fresh subagent spends re-gathering context; "by default it's the same model (or a smaller one) on the same task. You get parallelism or a fresh pair of eyes out of it, but not a smarter answer"; `/code-review` runs in a fresh subagent that "isn't swayed by the reasoning that produced the code."
- **Limitations:** internal evals, research not coding; no numbers for the harness post.
- **For orchestrate:** the vendor's own position matches the controlled study: parallelise across independent work, keep one agent per dependent task, make the reviewer a fresh context, budget effort explicitly, and pass artefacts through files or PRs rather than through the orchestrator's context.

### 26. Cursor 2026, Scaling long-running autonomous coding (grade C)

- **Citation:** Cursor. *Scaling long-running autonomous coding.* Blog, January 2026. https://cursor.com/blog/scaling-agents
- **Design:** field report of hundreds of concurrent agents on one codebase for about a week (a browser, "over 1 million lines of code across 1,000 files") and a three-week Solid-to-React migration (+266K/−193K).
- **Headline results:** flat self-coordination through a shared file with locks failed: without hierarchy agents became risk-averse, avoided hard tasks, churned without progress and nobody owned outcomes. The fix was planners (recursive, spawning sub-planners), workers that do not coordinate with each other, and a judge at the end of each cycle, after which the next iteration starts fresh. An integrator role for conflict resolution became a bottleneck and was removed. GPT-5.2 was a better planner than the coding-tuned GPT-5.1-Codex; Opus 4.5 tended to stop earlier and take shortcuts. Periodic fresh starts are still needed to combat drift.
- **Limitations:** no metrics, no baseline, prototype output.
- **For orchestrate:** independent confirmation of the planner/worker/judge shape with fresh passes, of per-role model choice, and a reject for a standing integrator agent.

## Implementable findings

| id | finding | grade | source | for orchestrate |
|---|---|---|---|---|
| MA-01 | With tools, prompts and tokens matched, every multi-agent topology scores below a single agent on SWE-bench Verified (centralized −3.1%, hybrid −2.1%, decentralized −5.4%, independent −14.9%) once the single-agent baseline exceeds ≈45%. | A | Kim et al. 2026 | reject: do not split one ticket into planner/coder/tester sub-agents; one worker owns a ticket end to end |
| MA-02 | Independent parallel agents with no central check amplify trace-level errors 17.2× versus 4.4× under a centralized orchestrator. | A | Kim et al. 2026 | default: every worker output passes the script gate and a reviewer before merge; parallelism only across independent tickets |
| MA-03 | Centralized coordination gains +80.8% on decomposable work and loses 39-70% on sequentially dependent work; decomposability, not complexity, predicts the sign. | A | Kim et al. 2026 | rule: the touch-map overlap check is the decomposability test; tickets with shared files run serially, never as coordinated sub-agents |
| MA-04 | 15.7% of multi-agent failures are step repetition and ≈65% of single-agent issue-solving failures are flawed reasoning that ends in a repeated, non-progressing loop. | A / B | MAST; Liu et al. 2025 | measure: log repeated identical tool calls and no-diff iterations per worker as a stall signal in the run log |
| MA-05 | 12.4% of failures are not recognising completion and 6.2% premature termination; HyperAgent workers exit on a hallucinated completion and Opus 4.5 sessions declare the job done early. | A / C | MAST; HyperAgent; Anthropic harness | rule: a worker report without a passing test run or CI link is not a hand-off; the script gate, not the worker, marks done |
| MA-06 | Verifiers that only check compilation account for 17.3% of failures (8.2% missing, 9.1% incorrect verification); systems with explicit verifiers still fail less overall. | A | MAST | rule: reviewers must execute (tests, CI) and cite output; script-owned verdicts stay the source of truth |
| MA-07 | Changing topology so only the superior agent can close a round gave ChatDev +15.6 points; prompt edits alone +9.4; on AG2 with GPT-4 the topology change was not significant (p = 0.4). | A | MAST | default: only the orchestrator (via script) closes a review round; workers cannot self-approve. Contradiction: effect is model-dependent |
| MA-08 | 6.8% of failures are proceeding on wrong assumptions instead of asking; Agentless finds 51 of 300 Lite issues ambiguous or over-specified. | A / B | MAST; Agentless | default: keep the worker's stop-and-ask path (`needs-info`), and measure how often it fires |
| MA-09 | Patch selection by majority vote 25.67% → +existing regression tests 27.00% → +generated reproduction test 32.00%; pruning by regression tests is worth 3.42 points and dedup 3.73 on 2025 models (p < 0.0073). | B | Agentless; Trae Agent | rule: regression suite before any generated test in the gate; reproduction test required in the worker brief for bug tickets |
| MA-10 | Tests written by a separate agent are 87.8% / 89.9% accurate against 61.0% / 51.8% when the same conversation writes the code and then the tests. | B | AgentCoder | default: the reviewer never receives the worker's transcript or reasoning, only the diff, the ticket and test output |
| MA-11 | Removing the per-developer QA reviewer drops SWE-bench resolution 13.94% → 10.63%. | B | MAGIS | default: every PR gets a reviewer round, even the S tier |
| MA-12 | A supervising Expert agent reviewing an Executor every 25 steps resolved 22.2% (24/108) of issues a single agent had failed; a stronger single model resolved 7/108. | B | Liu et al. 2025 | flag: `--supervise` sends a scout-grade reviewer into a worker that has stalled (MA-04 signal) instead of restarting it |
| MA-13 | A ledger-keeping orchestrator that plans, tracks progress and re-plans beats plain next-speaker selection by 31%; agents reset context after each plan revision. | B | Magentic-One | default: the pass record and tracker rebuild are the ledger; keep fresh worker contexts per pass |
| MA-14 | Collapsing history to the last five observations beats full history by 3.0 points; a 30-line or whole-file viewer loses 3.7 / 5.3 points; a bad search tool is worse than none (−6.0 vs −2.3). | B | SWE-agent | rule: scout and worker tool output is capped and summarised; do not add tools without measuring |
| MA-15 | A class/function skeleton localises better than whole files (58.33% vs 53.67%) at a seventh of the cost; prompting plus embedding retrieval finds the file 81.67% of the time. | B | Agentless | rule: scout answers "which files" with signatures and line ranges, not file bodies |
| MA-16 | Linting-gated edits are worth 3.0 points; the odds of recovery fall with each failed edit. | B | SWE-agent | rule: a guard-hook lint on Edit/Write for the repo's languages, rejecting syntax-breaking edits |
| MA-17 | Handing a sub-task to a specialist agent recovered the specialist's score but added nothing (14.5 vs 14.8 on WebArena); a generalist agent with full tools beats specialised multi-agent systems across three domains (OWL 58.18% on GAIA but 4.00% on The Agent Company). | B | OpenHands; OpenHands-Versa | reject: specialised coder/tester/doc sub-agents; one worker role with the full toolset |
| MA-18 | Cheaper models for navigation and execution under a strong planner cost 3 points for a 4× cost saving (30.20% at $0.45 vs 33.00% at $1.82); the best planner was not the coding-tuned model. | B / C | HyperAgent; Cursor | default: keep per-role routing; measure: planner and scout model swaps separately in the cost profile |
| MA-19 | Relaying results through a coordinator loses execution detail (HyperAgent error analysis; Anthropic "game of telephone"). | B / C | HyperAgent; Anthropic | default: workers report through the PR, branch and tracker; the orchestrator holds decisions only |
| MA-20 | Removing all role descriptions is the largest single hit in ChatDev (executability 0.88 → 0.58); role instructions beat the same stages without personas (64.4 vs 60.0). | B | ChatDev; Self-collaboration | default: each role keeps a written charter; measured only on GPT-3.5, so re-measure before investing |
| MA-21 | Separate plan-then-code-then-test stages lift GPT-3.5 from 45.1 to 63.5 on HumanEval; removing the planner stage in a 2026 pipeline drops 42% → 19%. | B | Self-collaboration; AgentForge | flag: `--plan-first` asks the worker for a written plan before edits; evidence is weak-model or weak-baseline |
| MA-22 | Many sampled candidates plus test-based selection raises Verified from the best single system to 66.2%; coverage is indifferent to serial versus parallel allocation; gains plateau near 40 samples. | B | CodeMonkeys; Agentless | flag: `--candidates=N` runs N workers on one hard ticket and lets the gate's tests pick; cost multiplies by N |
| MA-23 | A tool-using selector agent with majority vote is worth 4.08 and 4.14 points over prompt-only selection and no vote. | B | Trae Agent | flag: with `--candidates`, a reviewer-tier selector that runs the tests on each candidate, with vote on ties |
| MA-24 | Search over trajectories with a value agent gives +23% relative across five models; a debate selector lifts selection accuracy 73% → 84%; a chain debate gains +2.6 points same-model. | B | SWE-Search; SWE-Debate | flag: low priority; cost multiplies and the gain shrinks with stronger models |
| MA-25 | Multi-agent systems use ≈15× the tokens of chat and ≈4× that of a single agent; token volume explains 80% of research-eval variance. | C | Anthropic research post | measure: `--profile` reports tokens per role per ticket so MA-01 can be tested on this repo |
| MA-26 | Explicit effort-scaling rules (1 agent and 3-10 calls, 2-4 subagents, >10 subagents) stopped over-investment in simple queries. | C | Anthropic research post | default: the S/M/L review tiers are this rule; rule: cap worker turns and scout calls per tier |
| MA-27 | Delegation briefs need an objective, an output format, tool guidance and boundaries; vague briefs caused duplicated and missed work. | C | Anthropic research post | rule: the brief template carries those four fields and the touch map |
| MA-28 | Flat self-coordination through a shared file made agents risk-averse and churning; an integrator agent for merges created bottlenecks and was removed. | C | Cursor | reject: peer-to-peer worker coordination and a standing integrator agent; keep the script sweep |
| MA-29 | An initializer that writes a failing feature list, a progress file and a commit, then one feature per session, fixed doing too much at once and declaring done early on Opus 4.5. | C | Anthropic harness | default: one ticket per worker session; rule: a worker leaves a clean, committed state and a report before stopping |
| MA-30 | Contradiction: role split papers show +30-47% on GPT-3.5/GPT-4 (MetaGPT, ChatDev, MapCoder, AgentCoder, Self-collaboration) while the controlled frontier-model study (MA-01) and leaderboard census show no gain; Warp and nFactorial abandoned in-task role splits. | A / B | Kim et al.; Martinez & Franch; 2023-24 papers | measure: an orchestrate A/B on this repo (one worker vs plan-first vs supervise) before adopting any role split as default |
| MA-31 | Contradiction: multi-agent beat single Opus 4 by 90.2% on research, but the same team says most coding tasks lack parallelisable sub-tasks. | C | Anthropic research post | default: parallelise the backlog, not the ticket |
| MA-32 | No single architecture consistently leads the leaderboards (groups differ, p = 0.0070); the single-agent emergent group has the most entries (31) and reaches 73.2%. | B | Martinez & Franch | measure: the "scientifically backed" claim must say "structure around one agent", not "multi-agent" |

## Candidate paradigms

1. **Orchestrator-workers over independent tickets** : one planner assigns disjoint work, workers never coordinate, a judge or gate closes each cycle, fresh contexts per pass. Backed by MA-02, MA-03, MA-07, MA-13, MA-19, MA-28, MA-29. Best grade A.
2. **Single generalist worker with a strong ACI** : one agent owns a ticket end to end with capped tool output, lint-gated edits and skeleton-level navigation. Backed by MA-01, MA-14, MA-15, MA-16, MA-17; the 70-77% Verified baselines in source 19. Best grade A.
3. **Independent verifier** : a reviewer or tester with its own context that never saw the worker's reasoning and must execute. Backed by MA-06, MA-10, MA-11. Best grade B (A for the failure share).
4. **Script-owned selection gate** : regression tests, then reproduction tests, then dedup and vote decide what merges or which candidate wins. Backed by MA-05, MA-09, MA-23. Best grade B.
5. **Supervisor on stall** : a periodic expert check-in on a stuck worker rather than a restart or a peer. Backed by MA-04, MA-12. Best grade B.
6. **Sample-and-select** : N workers on one ticket, gate picks. Backed by MA-22, MA-23. Best grade B; cost ×N.
7. **Plan-first staging** : written plan or design artefact before edits, planner may use a different model. Backed by MA-18, MA-20, MA-21. Best grade B on weak models only.
8. **Trajectory search or debate** : MCTS, value agent, debate selector. Backed by MA-24. Best grade B; shrinking returns.
9. **Agentless pipeline** : fixed localise-repair-validate phases with no agent control, for cheap or small tickets. Backed by MA-09, MA-15; sources 3 and 21 (G3 top of Verified). Best grade B.
10. **Effort-tiered routing** : explicit budgets and model per role and per tier. Backed by MA-18, MA-25, MA-26. Best grade C with B support.
11. **Session-reset harness** : initializer, progress file, one unit per session, clean state on exit. Backed by MA-13, MA-29. Best grade B/C.
12. **Rejected: in-task role swarm** : planner/coder/tester/critic sub-agents sharing one ticket, peer coordination, standing integrator. MA-01, MA-17, MA-28, MA-30 argue against.

## Open questions

- No study runs planner/coder/tester/reviewer roles against one agent on a 2025-26 model at equal token budget on real repositories; Kim et al. use generic roles and 20-issue subsets, AgentForge a weak baseline. MA-30 is the experiment orchestrate could run itself.
- Whether a reviewer round still pays at 70%+ single-agent solve rates: MAGIS measured it at 13.94%; Trae's selector and regression gates are the nearest frontier evidence and they measure selection, not review.
- Whether a supervisor's 22.2% rescue rate holds outside the failure subset and what it costs per ticket.
- Does collapsing history (SWE-agent) or resetting contexts (Magentic-One, Cursor) still help models with 200K-1M contexts, or does it now lose information?
- Which MAST modes dominate orchestrate's own runs; the taxonomy has not been applied to a worktree-based GitHub workflow.
- Role prompts: every positive measurement is on GPT-3.5/GPT-4; no frontier-model ablation of persona text exists.
- Contamination: SWE-bench Lite/Verified are in training data for 2025 models; none of the 2025 sources controls for it, and the Agentless Lite-S finding shows the benchmark itself has 17% problematic items.

## Sources list

- Kim, Y., et al. (2025/2026). Towards a Science of Scaling Agent Systems. Preprint, arXiv:2512.08296. https://arxiv.org/abs/2512.08296
- Cemri, M., Pan, M. Z., Yang, S., Agrawal, L. A., Chopra, B., Tiwari, R., Keutzer, K., Parameswaran, A., Klein, D., Ramchandran, K., Zaharia, M., Gonzalez, J. E., Stoica, I. (2025). Why Do Multi-Agent LLM Systems Fail? NeurIPS 2025 (secondary); arXiv:2503.13657. https://arxiv.org/abs/2503.13657
- Xia, C. S., Deng, Y., Dunn, S., Zhang, L. (2024). Agentless: Demystifying LLM-based Software Engineering Agents. FSE 2025 (secondary); arXiv:2407.01489. https://arxiv.org/abs/2407.01489
- Yang, J., Jimenez, C. E., Wettig, A., Lieret, K., Yao, S., Narasimhan, K., Press, O. (2024). SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering. NeurIPS 2024 (secondary); arXiv:2405.15793. https://arxiv.org/abs/2405.15793
- Zhang, Y., Ruan, H., Fan, Z., Roychoudhury, A. (2024). AutoCodeRover: Autonomous Program Improvement. ISSTA 2024, doi:10.1145/3650212.3680384; arXiv:2404.05427. https://arxiv.org/abs/2404.05427
- Wang, X., et al., Neubig, G. (2024). OpenHands: An Open Platform for AI Software Developers as Generalist Agents. ICLR 2025; arXiv:2407.16741. https://arxiv.org/abs/2407.16741
- Hong, S., et al., Schmidhuber, J. (2023). MetaGPT: Meta Programming for a Multi-Agent Collaborative Framework. ICLR 2024 (secondary); arXiv:2308.00352. https://arxiv.org/abs/2308.00352
- Qian, C., et al. (2023). ChatDev: Communicative Agents for Software Development. ACL 2024; arXiv:2307.07924. https://arxiv.org/abs/2307.07924
- Islam, M. A., Ali, M. E., Parvez, M. R. (2024). MapCoder: Multi-Agent Code Generation for Competitive Problem Solving. ACL 2024 (secondary); arXiv:2405.11403. https://arxiv.org/abs/2405.11403
- Huang, D., Zhang, J. M., Luck, M., Bu, Q., Qing, Y., Cui, H. (2023). AgentCoder: Multi-Agent-based Code Generation with Iterative Testing and Optimisation. Preprint, arXiv:2312.13010. https://arxiv.org/abs/2312.13010
- Dong, Y., Jiang, X., Jin, Z., Li, G. (2023). Self-collaboration Code Generation via ChatGPT. ACM TOSEM; arXiv:2304.07590. https://arxiv.org/abs/2304.07590
- Tao, W., et al. (2024). MAGIS: LLM-Based Multi-Agent Framework for GitHub Issue Resolution. NeurIPS 2024 (secondary); arXiv:2403.17927. https://arxiv.org/abs/2403.17927
- Phan, H. N., Nguyen, T. N., Nguyen, P. X., Bui, N. D. Q. (2024). HyperAgent: Generalist Software Engineering Agents to Solve Coding Tasks at Scale. Preprint, arXiv:2409.16299. https://arxiv.org/abs/2409.16299
- Fourney, A., et al. (2024). Magentic-One: A Generalist Multi-Agent System for Solving Complex Tasks. Preprint, arXiv:2411.04468. https://arxiv.org/abs/2411.04468
- Antoniades, A., et al. (2024). SWE-Search: Enhancing Software Agents with Monte Carlo Tree Search and Iterative Refinement. ICLR 2025 (secondary); arXiv:2410.20285. https://arxiv.org/abs/2410.20285
- Ehrlich, R., et al. (2025). CodeMonkeys: Scaling Test-Time Compute for Software Engineering. Preprint, arXiv:2501.14723. https://arxiv.org/abs/2501.14723
- Trae Research Team, ByteDance (2025). Trae Agent: An LLM-based Agent for Software Engineering with Test-time Scaling. Technical report, arXiv:2507.23370. https://arxiv.org/abs/2507.23370
- Soni, A. B., Li, B., Wang, X., Chen, V., Neubig, G. (2025). Coding Agents with Multimodal Browsing are Generalist Problem Solvers. Preprint, arXiv:2506.03011. https://arxiv.org/abs/2506.03011
- Xia, C. S., Wang, Z., Yang, Y., Wei, Y., Zhang, L. (2025). Live-SWE-agent: Can Software Engineering Agents Self-Evolve on the Fly? Preprint, arXiv:2511.13646. https://arxiv.org/abs/2511.13646
- SWE-agent team (2025). mini-SWE-agent, README. https://github.com/SWE-agent/mini-swe-agent
- Liu, S., et al. (2025). An Empirical Study on Failures in Automated Issue Solving. Preprint, arXiv:2509.13941. https://arxiv.org/abs/2509.13941
- Martinez, M., Franch, X. (2025/2026). Dissecting the SWE-Bench Leaderboards: Profiling Submitters and Architectures of LLM- and Agent-Based Repair Systems. ICSE-SEIP 2026 (RQ1); arXiv:2506.17208. https://arxiv.org/abs/2506.17208
- Li, H., et al. (2025). SWE-Debate: Competitive Multi-Agent Debate for Software Issue Resolution. Preprint, arXiv:2507.23348. https://arxiv.org/abs/2507.23348
- Kumar, R., et al. (2026). AgentForge: Execution-Grounded Multi-Agent LLM Framework for Autonomous Software Engineering. Preprint, arXiv:2604.13120. https://arxiv.org/abs/2604.13120
- Li, J., Zhang, Q., Yu, Y., Fu, Q., Ye, D. (2024). More Agents Is All You Need. TMLR; arXiv:2402.05120. https://arxiv.org/abs/2402.05120
- Chen, D., et al. (2024). CodeR: Issue Resolving with Multi-Agent and Task Graphs. Preprint, arXiv:2406.01304. https://arxiv.org/abs/2406.01304
- Arora, D., et al. (2024). MASAI: Modular Architecture for Software-engineering AI Agents. Preprint, arXiv:2406.11638. https://arxiv.org/abs/2406.11638
- Anthropic (2025). How we built our multi-agent research system. Engineering post, 13 Jun 2025. https://www.anthropic.com/engineering/multi-agent-research-system
- Anthropic (2025). Effective harnesses for long-running agents. Engineering post, 26 Nov 2025. https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Anthropic (2024). Building effective agents. Engineering post, 19 Dec 2024. https://www.anthropic.com/engineering/building-effective-agents
- Anthropic (2026). Claude Code documentation: best practices; subagents. Read 2026-11-10. https://code.claude.com/docs/en/sub-agents
- Cursor (2026). Scaling long-running autonomous coding. Blog, January 2026. https://cursor.com/blog/scaling-agents
