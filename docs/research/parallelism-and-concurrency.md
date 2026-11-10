# Parallelism and concurrency: evidence for orchestrating AI coding agents

Ticket #610, part of the #588 evidence map. Area prefix `PR`. Compiled 2026-11-10 from primary sources (papers, vendor engineering posts, vendor docs). What orchestrate does today, for reference: workers build in their own git worktrees; a ticket starts only when its scout-predicted touch map overlaps no open PR diff, no building ticket's touch map, no live worktree of another session and no exclusive hold (`scripts/holds`); merges are serialised by the orchestrator and followed by a sweep that updates the other open PRs, replaying config-listed shared files and handing any other conflict back to the PR's own worker; the slot count (`--greedy`) throttles concurrency; there is no sampling-then-select and no inter-worker messaging.

**Summary.** (1) Parallel agents on one repository do collide: 27.7% of 107k agentic PRs conflict with their base at merge time, 19.8% of co-active same-agent PR pairs and 41.7% of cross-agent pairs conflict textually, and on contradictory tasks two unaware parallel agents drop from 62% to 16% success where a sequential hand-off recovers 54% at the same token cost; this supports orchestrate's hold-and-serialise rule over any "let them talk" design. (2) File-level touch maps are necessary but not sufficient: 47.3% of textual conflicts in one 715-pair study arose between PRs with disjoint authored file sets, and interface changes that pass alone fail together in 97% of constructed cases, so the gate should replay a real merge (`git merge-tree`, median 0.02 s) and the post-merge sweep should tell affected workers what changed. (3) Sampling-then-select is the best-evidenced lever this area has that orchestrate does not use: coverage grows log-linearly with samples, and execution-based or critic selection recovers most of the oracle gap (CodeMonkeys 45.8% random, 57.4% selected, 69.8% oracle; 67.0% to 76.4% against a 79.0% best-of-8 ceiling; vendors report 72.5% to 79.4%), at k times the cost. (4) Topology evidence favours what orchestrate already is: centralised coordination with independent workers contains error amplification to 4.4x against 17.2x for uncoordinated agents, independent agents degrade SWE-bench Verified by 14.9%, and multi-agent setups cost 1.6 to 6.2x the tokens; leaderless swarms with locks collapsed to the throughput of two or three agents at Cursor before a planner/worker/judge hierarchy fixed it. (5) The human is the bottleneck the field measures most consistently and this repo instruments least: high-AI-adoption teams merge 98% more PRs while review time rises 91% and bugs per developer 9%, 38% of rejected agentic PRs died with no human engagement at all and 23% were duplicates, and two-fifths of agent-PR resolution-time variance is the repository rather than the PR. Thin or contradictory: no controlled study compares hold-based scheduling against optimistic merge-and-repair; Cursor's "hundreds of workers, minimal conflicts" claim is unmeasured; and no primary source quantifies how many concurrent PRs one human can absorb.

## Sources

### 1. Kim et al. (2025), Towards a Science of Scaling Agent Systems

Yubin Kim, Ken Gu, Chanwoo Park, Chunjong Park, Samuel Schmidgall, A. Ali Heydari, Yao Yan, Zhihan Zhang, Yuchen Zhuang, Yun Liu, Mark Malhotra, Paul Pu Liang, Hae Won Park, Yuzhe Yang, Xuhai Xu, Yilun Du, Shwetak Patel, Tim Althoff, Daniel McDuff, Xin Liu. 2025 (v1 9 Dec 2025, v3 8 Apr 2026). Preprint, arXiv:2512.08296. https://arxiv.org/abs/2512.08296. Google Research and MIT.
**Design:** controlled evaluation of 260 configurations: five architectures (single agent; independent, centralised, decentralised and hybrid multi-agent) across three LLM families and six agentic benchmarks (including SWE-bench Verified, Terminal-Bench, BrowseComp-Plus, Finance-Agent, PlanCraft), with a fitted predictive model (cross-validated R² 0.373, 0.413 with a task-grounded capability metric). **Grade A.**
**Population:** OpenAI, Google and Anthropic model families; agentic benchmarks, not developers.
**Headline results:** relative change against single agent ranges from +80.8% (decomposable financial reasoning under centralised coordination) to −70.0% (sequential planning under independent coordination); every multi-agent variant degrades sequential-constraint tasks, −39% to −70% on PlanCraft. Independent systems amplify trace-level errors 17.2x; centralised coordination contains this to 4.4x. Independent coordination scores −14.9% on SWE-bench Verified; centralised −19.2% on Terminal-Bench. Tasks where the single agent already exceeds about 45% accuracy get negative returns from more agents. Coordination overhead against single agent: 58% (independent), 263% (decentralised), 285% (centralised), 515% (hybrid), described as 1.6 to 6.2x token budgets. The framework picks the best architecture for 87% of held-out configurations.
**Limitations:** benchmarks, not repositories; one task per run, so it measures agents splitting a task rather than a backlog split across agents; model generation moves fast.
**For orchestrate:** supports the shape orchestrate has: one centralised decision-maker, workers that never coordinate with each other, verification at a central point (gate, reviewer, merge script). It argues against adding worker-to-worker messaging (decentralised, 263% overhead) and against splitting one ticket across several agents when a single Sonnet or Opus worker already solves most of that class.

### 2. Mediratta et al. (2026), Decentralized Multi-Agent Coordination and Communication in Shared Codebases (ParallACT)

Rishabh Mediratta, Lingyun Xiao, Peter Stone, Joydeep Biswas, Elias Stengel-Eskin. 2026 (v1 30 Oct 2026). Preprint, arXiv:2611.00314. https://arxiv.org/abs/2611.00314.
**Design:** controlled benchmark, 155 instances (85 contradictory, 70 complementary task pairs) from 11 repositories in four languages, comparing a single-agent oracle against two parallel agents under varied awareness, messaging, synchronisation, ordering and visibility; seven harness/model configurations. **Grade A** (controlled conditions with significance tests).
**Population:** Claude Code with Claude Opus 4.8; Claude Code and Codex each with Qwen3.8 Flash Next, DeepSeek V4 Flash and GLM 5.3 Flash.
**Headline results (Opus 4.8, pass rate):** single-agent oracle 62% contradictory / 86% complementary; two parallel unaware agents 16% / 57%; telling each agent a peer exists 26% / 60% ("does not help significantly"); file messaging 20% / 63%; file synchronisation 22% / 71%; Redis synchronisation 32% / 77%, every messaging variant costing more tokens than unaware parallel; a sequential A→B hand-off 54% / 77% at 1.18x the oracle's tokens; a re-entrant ABAB schedule 50% / 80% at 1.88x. Open-weight mean over six configurations: oracle 60 / 72, parallel unaware 19 / 53, sequential 60 / 71. Parallel unaware runs cost 1.14 to 1.17x the oracle.
**Limitations:** two agents only; synthetic pairs (27 seed, 128 generated); the "contradictory" set is designed to collide.
**For orchestrate:** the strongest single result for the hold rule. When two tickets touch the same thing, running them one after the other recovers almost all of the single-agent score at near-oracle cost, while any communication channel recovers only a few points for more tokens. Holding an overlapping ticket until the other merges is the sequential hand-off; it is what the evidence says to do.

### 3. Xu, Subramanian and Karthik (2026), AI Agent Pull Requests on GitHub: Frequency, Structure, and Merge Conflict Rates

George Xu, Arjun Subramanian, Nithilan Karthik. 2026 (v2 7 Jul 2026). Preprint, arXiv:2607.04697. https://arxiv.org/abs/2607.04697.
**Design:** field dataset, AIDev-pop (33,596 agentic PRs, 2,807 repositories), temporal co-activity analysis plus three-way merge replay on 747 co-active pairs (one per repository), with 95% confidence intervals. **Grade A.**
**Population:** PRs by OpenAI Codex, Devin, GitHub Copilot, Cursor and Claude Code on public GitHub.
**Headline results:** 40.2% of repositories have co-active agent PR pairs under exact temporal overlap, 53.4% under a one-week window; 79.4% of agent PRs are part of a co-active pair (95.0% under the week window). Cross-agent pairs are 0.5% of co-active pairs, in 122 repositories. Replayed textual conflict rate: 41.7% for cross-agent pairs against 19.8% for intra-agent pairs, non-overlapping 95% CIs. 84.4% of conflicted files are source code, not manifests; nearly 42% of conflicts are structural (modify/delete or add/add).
**Limitations:** co-activity is a proxy for concurrency; one pair per repository in the replay; no human baseline.
**For orchestrate:** concurrency is the normal case, not the exception, and one in five same-agent co-active pairs conflicts textually. The 42% structural share (file added or deleted on one side) is the kind a line-level touch map cannot express; the sweep's handback path, not shared-file replay, covers those.

### 4. Ogenrwot and Businge (2026), AgenticFlict

Daniel Ogenrwot, John Businge. 2026 (v1 4 Apr 2026, v2 12 May 2026). AIware 2026. arXiv:2604.03551. https://arxiv.org/abs/2604.03551.
**Design:** field dataset; 142k+ agentic PRs from 59k+ repositories, 107,026 run through deterministic merge simulation (`git merge --no-commit --no-ff <head>` on the PR's base commit; closed PRs reconstructed as of closure); 336k+ conflict regions. **Grade A** (large field dataset).
**Population:** PRs by Copilot, Cursor, Devin, Claude Code and OpenAI Codex.
**Headline results:** 29,609 of 107,026 simulated PRs conflict, 27.67%. Per agent (Table 2): Copilot 15.24% (2,583/16,954), Cursor 19.75% (1,421/7,196), Devin 22.85% (1,883/8,241), Claude Code 25.93% (202/779), OpenAI Codex 31.85% (23,520/73,856). The paper's body text gives slightly different per-agent figures (15.43%, 20.06%, 23.04%, 26.86%, 32.31%); the table's rates match its own counts.
**Limitations:** conflict with the base at simulation time, which includes staleness as well as concurrency; no human-PR baseline (the authors cite Ghiotto et al.'s "nearly 20%" of merges in 2,731 Java projects needing manual intervention as context only).
**For orchestrate:** a base rate. Roughly a quarter of agentic PRs will not merge cleanly if left alone, which is what the post-merge sweep exists for. The per-agent spread (15% to 32%) suggests the rate tracks how long PRs sit open, so time-to-merge is a conflict-rate lever.

### 5. Ehsani et al. (2026), Where Do AI Coding Agents Fail?

Ramtin Ehsani, Sakshi Pathak, Shriya Rawal, Abdullah Al Mujahid, Mia Mohammad Imran, Preetha Chatterjee. 2026 (21 Jan 2026). MSR 2026. arXiv:2601.15195. https://arxiv.org/abs/2601.15195.
**Design:** field dataset of 33,596 agentic PRs (merged against unmerged, Cliff's delta effect sizes) plus a qualitative taxonomy from 600 rejected PRs (562 categorised). **Grade A.**
**Population:** OpenAI Codex (21,799 PRs), Devin (4,827), Copilot (4,970), Cursor (1,541), Claude Code (459).
**Headline results:** overall merge rate 71.48% (24,014); per agent Codex 82.59%, Cursor 65.22%, Claude Code 59.04%, Devin 53.76%, Copilot 43.04%. Rejection taxonomy: reviewer level 38% (228), nearly all "abandoned / not reviewed: closed with no meaningful human interaction"; PR level 31% (188), of which duplicate PR 23% (142), unwanted feature 4%; code level 22% (133), of which CI/test failure 17% (99); agentic level 2%. Unmerged PRs are larger (δ 0.17 on LOC), touch more files (δ 0.10) and fail more checks (δ 0.24); each additional failed check cuts the odds of merging by about 15%.
**Limitations:** public OSS only; agents' own repositories mixed with third-party ones; rejection labels from closed PRs.
**For orchestrate:** when many agentic PRs arrive, the largest failure mode is that nobody reads them, and the second is that they duplicate each other. Both are scheduling facts: cap what waits on the human, and check for an existing PR or merged fix before dispatch (orchestrate's "does main already meet the criteria" step covers half of this; it does not check open PRs for the same change).

### 6. Russo (2026), For Coding Agents, the Repository Matters

Daniel Russo. 2026 (v1 26 Jun 2026, v2 20 Oct 2026). Preprint, arXiv:2606.28235. https://arxiv.org/abs/2606.28235.
**Design:** multilevel (hierarchical) model of agent against human PRs in the same 280 repositories, AIDev data, with confidence intervals. **Grade A.**
**Population:** agentic and human PRs on public GitHub.
**Headline results:** about two-fifths of the variation in agent-PR resolution time lies between repositories after controlling for the PR, the agent and observable repository attributes; between-repository variance is 1.76x as large for agent PRs as for human PRs (95% CI 1.41 to 2.18); a repository one standard deviation slower multiplies a multi-hour wait by about 2.8 for agent PRs against 2.2 for human PRs; rejection shows the same pattern; review rounds and comments do not.
**Limitations:** observational; resolution time conflates review latency with CI and author response.
**For orchestrate:** the human queue is a per-repository property. A slot count or review WIP limit should be configured per repo and measured there, not carried between repos; `--greedy` sizing from "how many tickets can run at once" ignores how fast this repo's human absorbs PRs.

### 7. Ehrlich et al. (2025), CodeMonkeys: Scaling Test-Time Compute for Software Engineering

Ryan Ehrlich, Bradley Brown, Jordan Juravsky, Ronald Clark, Christopher Ré, Azalia Mirhoseini. 2025 (v1 24 Jan 2025, v2 3 Feb 2025). Preprint, arXiv:2501.14723. https://arxiv.org/abs/2501.14723. Code and all samples released at https://scalingintelligence.stanford.edu/pubs/codemonkeys.
**Design:** benchmark with ablations on SWE-bench Verified: 10 parallel testing/editing state machines per issue, up to 8 serial iterations each, selection by model-written tests, majority voting to a top 3, then a selection state machine (up to 10 iterations). **Grade B.**
**Population:** Claude 3.5 Sonnet (claude-3-5-sonnet-20241022) for generation and selection; Qwen2.5-Coder-32B-Instruct for the relevance scan; DeepSeek-V3 in an appendix.
**Headline results:** 57.4% resolved at $2,291.90 total; coverage (some candidate correct) 69.8%; random selection among candidates would score 45.8%, so selection closes about half of the oracle gap. "Barrel of Monkeys": selecting over an ensemble of edits from existing top leaderboard submissions scores 66.2%, above the best member alone (Blackbox AI Agent, 62.8%).
**Limitations:** 2024-generation model; SWE-bench Verified contamination risk; cost per issue about $4.60.
**For orchestrate:** sampling plus execution-based selection is worth roughly 12 points over single-sample at 10x the compute, and selection across heterogeneous sources beats the best source. Orchestrate has the verify command and a reviewer; it lacks a "run k workers, keep the one that passes" mode.

### 8. Brown et al. (2024), Large Language Monkeys: Scaling Inference Compute with Repeated Sampling

Bradley Brown, Jordan Juravsky, Ryan Ehrlich, Ronald Clark, Quoc V. Le, Christopher Ré, Azalia Mirhoseini. 2024 (v1 31 Jul 2024, v3 30 Dec 2024). Preprint, arXiv:2407.21787. https://arxiv.org/abs/2407.21787.
**Design:** benchmark study of coverage against sample count across tasks and models, four orders of magnitude of samples. **Grade B.**
**Population:** DeepSeek-Coder-V2-Instruct, Llama 3 and Gemma models; SWE-bench Lite, GSM8K, MATH, MiniF2F, CodeContests.
**Headline results:** on SWE-bench Lite, issues solved by at least one sample rise from 15.9% with one sample to 56% with 250 samples (single-sample state of the art then 43%); coverage is often log-linear in samples (exponentiated power law). Where no automatic verifier exists, majority voting and reward models plateau beyond several hundred samples.
**Limitations:** coverage is an oracle number; the paper does not deliver selection.
**For orchestrate:** the ceiling for sampling is high and the slope is known; the whole value depends on a verifier, which in a real repo is the test suite and the gate. Tickets with a strong verify command are where sampling pays.

### 9. Sunkaraneni et al. (2026), Inference-Time Amplification of Weak Reasoning Models

Varun Sunkaraneni, Pierfrancesco Beneventano, Riccardo Neumarker, Tomaso Poggio, Tomer Galanti. 2026 (v1 13 May 2026, v2 4 Oct 2026). Preprint, arXiv:2605.14163. https://arxiv.org/abs/2605.14163.
**Design:** benchmark with a critic-comparator selector over k sampled trajectories, separating coverage from identifiability. **Grade B.**
**Population:** GPT-5.4 nano as proposer on SWE-bench Verified; also MATH L4-5, GSM-Plus, competitive programming, formal proof.
**Headline results:** single trajectory 67.0%; oracle best-of-8 79.0%; critic-comparator selection 76.4%, recovering 78.3% of the oracle-exposed gain; adding repository information to the selector improves recovery at fixed coverage; amplification fails when the selection signal is weak or misaligned.
**Limitations:** one proposer model on SWE-bench; cost multipliers not reported on the abstract page.
**For orchestrate:** a cheap worker sampled eight times with a good selector reaches a stronger model's single-shot score. That is a different escalation path from orchestrate's Haiku-to-Sonnet rule: sample the cheap model before escalating.

### 10. Kwok et al. (2026), LLM-as-a-Verifier: A General-Purpose Verification Framework

Jacky Kwok, Shulu Li, Pranav Atreya, Yuejiang Liu, Yixing Jiang, Chelsea Finn, Marco Pavone, Ion Stoica, Azalia Mirhoseini. 2026 (v2 7 Jul 2026). Preprint, arXiv:2607.05391. https://arxiv.org/abs/2607.05391.
**Design:** benchmark; a training-free verifier that scores candidates from the expectation over scoring-token logits, with a ranking algorithm for choosing among candidates. **Grade B.**
**Population:** SWE-bench Verified, Terminal-Bench V2, RoboRewardBench, MedAgentBench.
**Headline results:** 78.2% on SWE-bench Verified; secondary coverage says this used a heterogeneous pool of three candidates from different models scored with mini-swe-agent and beat each model alone (secondary; not confirmed on the abstract page). 86.5% Terminal-Bench V2.
**Limitations:** the pool composition and cost are not on the abstract page.
**For orchestrate:** a model-based verifier can select among candidates where tests are thin; the reviewer agent already exists and could rank k PRs instead of reviewing one.

### 11. Subramanian, Xu and Karthik (2026), Asymmetric Repository Lineage Modeling and Verifier-Guided Coordination in Concurrent AI Coding Agents (MERGEGYM)

Arjun Subramanian, George Xu, Nithilan Karthik. 2026 (3 Oct 2026). Preprint, arXiv:2610.04779. https://arxiv.org/abs/2610.04779.
**Design:** benchmark on field data; 715 stratified lineage pairs of concurrent agent PRs; three tracks (scope forecasting, conflict resolution, scheduling); logistic regression and random forest forecasters; executable merge replay as the checker. **Grade B.**
**Population:** agentic PR pairs from the AIDev lineage.
**Headline results:** 167 of 715 pairs conflict textually; 79 of those 167 (47.3%) conflict although the two PRs' authored file sets are disjoint, a proxy mismatch between PR diffs measured against PR-specific bases and the checker's common-merge-base comparison. Forecasters recover 81.2% (logistic) and 82.9% (random forest) of conflicts at a 33.3% replay budget (AUROC 0.882 / 0.902). Local `git merge-tree` replay costs a median 0.02 s, so "when exact replay is cheap, verify everything". A decision-time gate de-overlaps a median 91.7% of labelled scope collisions at 65.0% makespan inflation under frozen-label replay. 48 of the 79 zero-overlap conflicts became clean after patch reconstruction against the common base.
**Limitations:** textual mergeability only; frozen-label scheduling target; same group as source 3.
**For orchestrate:** two concrete things. First, file-set overlap (the touch map and `holds`) misses almost half of real conflicts, which come from base drift rather than shared files; the fix is to make the gate and the sweep run a real `git merge-tree` against main and against the other open PR heads, which is nearly free. Second, holding on predicted overlap buys a 92% collision cut for a 65% makespan cost: that trade is worth a flag and a measurement, not a silent default.

### 12. Xia, Wu and Park (2026), Passes Alone, Fails Together (stale)

Haocheng Xia, Eugene Wu, Yongjoo Park. 2026 (21 Sep 2026). EXPRESS 2026 workshop. arXiv:2609.25396. https://arxiv.org/abs/2609.25396.
**Design:** benchmark with three tiers: synthetic interface changes, 417 mined pairs of merged Django PRs (834 runs), and constructed tasks over 12 real Django helpers; the same tests run on each patch alone and on the combination, counting only failures the combination introduces. **Grade B.**
**Population:** Django; models not named on the abstract page.
**Headline results:** among 834 runs on mined, already-reviewed PR pairs, only one showed interference after grading was corrected; on constructed tasks where one agent changes an interface the other depends on, interference occurred in 97% of runs; a message describing the completed concurrent change recovered 82% of runs. The authors say constructed rates do not estimate real-world frequency.
**Limitations:** one framework; small constructed set; the message's baseline is not on the abstract page.
**For orchestrate:** semantic conflicts concentrate in interface changes and are near-absent in pairs that reached a reviewer. The cheap mitigation is already named in the paper: when a merge lands, tell the still-running workers what changed. Orchestrate's sweep rebases silently; it should hand the resumed worker the merged PR's summary, not only a conflict list.

### 13. Cemri et al. (2025), Why Do Multi-Agent LLM Systems Fail? (MAST)

Mert Cemri, Melissa Z. Pan, Shuyi Yang, Lakshya A. Agrawal, Bhavya Chopra, Rishabh Tiwari, Kurt Keutzer, Aditya Parameswaran, Dan Klein, Kannan Ramchandran, Matei Zaharia, Joseph E. Gonzalez, Ion Stoica. 2025 (v3 26 Oct 2025). NeurIPS 2025 Datasets and Benchmarks (secondary for the venue). arXiv:2503.13657. https://arxiv.org/abs/2503.13657.
**Design:** taxonomy from 150 annotated traces (Cohen's kappa 0.88), MAST-Data of 1,600+ traces across seven frameworks, an LLM annotator, and two intervention case studies without controls. **Grade B.**
**Population:** ChatDev, MetaGPT, HyperAgent, AppWorld, AG2, Magentic-One, OpenManus with GPT-4o, Claude 3, Qwen2.5, CodeLlama; coding, math and general agent tasks.
**Headline results:** failure rates of 41% to 86.7% across the evaluated systems (not comparable across benchmarks). Fourteen modes in three categories: system design 44.2% (step repetition 15.7%, unaware of termination 12.4%, disobey task spec 11.8%), inter-agent misalignment 32.4% (reasoning-action mismatch 13.2%, task derailment 7.4%), task verification 23.5% (incorrect verification 9.1%, no or incomplete verification 8.2%, premature termination 6.2%). Interventions on ChatDev: +9.4% from role specification and a final-say rule, +15.6% from a task-objective verification step.
**Limitations:** 2024-era frameworks; interventions uncontrolled.
**For orchestrate:** a quarter of multi-agent failures are verification failures, which is the case for script-owned verdicts and a gate that asks nobody; 12.4% "unaware of termination" is the case for a hard stop rule in the worker's report format.

### 14. Yang, He and Zhou (2026), To Ban or not to Ban? How Open Source Projects Govern GenAI Contributions

Wenhao Yang, Runzhi He, Minghui Zhou. 2026 (v1 27 Mar 2026, v2 30 Jul 2026). ASE 2026. arXiv:2603.26487. https://arxiv.org/abs/2603.26487.
**Design:** qualitative analysis of governance documents from 67 highly visible OSS projects; three orientations, 12 strategies. **Grade B.**
**Population:** OSS maintainers' written policy.
**Headline results:** disclosure of AI assistance required by roughly 38 of 67 projects (~57%, derived from the paper's per-orientation percentages); outright bans in roughly 30 of 67 (~45%); "Capacity and Queue Control" (limiting input rate and concurrency) in about 3% of the main orientation, the example being Jaeger, which limits simultaneous open PRs for new contributors. No AI-specific rate limit was found.
**Limitations:** counts derived from a figure; policy, not behaviour.
**For orchestrate:** when orchestrate pushes PRs to a repository it does not own, the receiving project may cap concurrent open PRs per author and will likely require disclosure; a per-repo "max open PRs by this bot" setting is the only portable answer.

### 15. Baltes, Cheong and Treude (2026), "An Endless Stream of AI Slop"

Sebastian Baltes, Marc Cheong, Christoph Treude. 2026 (v1 28 Mar 2026, v4 28 Aug 2026). Preprint, arXiv:2603.27249. https://arxiv.org/abs/2603.27249.
**Design:** qualitative coding of 1,154 Reddit and Hacker News posts (978 coded, 1,603 codings, 15 codes in three clusters). **Grade B** for the study; the figures below are single-team reports (**C**).
**Population:** developers discussing AI-assisted contributions.
**Headline results:** "One team reported receiving 30 PRs per day across 6 reviewers" (R07); a team that will not review above 500 LOC per PR (R05); reviewers feeling like "the first human being to ever lay eyes on this code"; mitigations named: PR size caps, self-review before peer review, synchronous walkthroughs, dual review with an outside team, explicit ownership. The authors frame the dynamic as a tragedy of the commons shifting cost to reviewers.
**Limitations:** self-report; no measurement of volume beyond anecdotes.
**For orchestrate:** the field's own mitigations are size caps and an ownership rule. Orchestrate's size tiers and "review fixes go back to the worker that wrote the code" match; a per-PR LOC cap at dispatch (split before build) does not exist yet.

### 16. Li, Zhang and Hassan (2025), The Rise of AI Teammates in Software Engineering 3.0 (AIDev)

Hao Li, Haoxiang Zhang, Ahmed E. Hassan. 2025 (20 Jul 2025). Preprint, arXiv:2507.15003. https://arxiv.org/abs/2507.15003.
**Design:** dataset paper; 456,000+ agentic PRs from 61,000 repositories and 47,000 developers (later releases 932,791 PRs; the curated AIDev-pop subset 33,596). **Grade B.**
**Population:** OpenAI Codex, Devin, GitHub Copilot, Cursor, Claude Code.
**Headline results:** agents work faster than humans but have a lower PR acceptance rate (a "trust and utility gap"); agent PRs are structurally simpler by complexity metrics. Exact rates are in the full paper, not the abstract page; a derivative study on AIDev-pop reports 76.80% agentic against 82.82% human merge rate (secondary).
**Limitations:** public OSS; the dataset underlies sources 3, 5, 6 and 11, so those are not independent samples.
**For orchestrate:** the substrate every field number in this document stands on.

### 17. Anthropic (2025), Claude Opus 4 and Claude Sonnet 4 announcement, SWE-bench "high compute" footnote

Anthropic. 2025 (22 May 2025). Vendor announcement. https://www.anthropic.com/news/claude-4.
**Design:** vendor benchmark run with and without parallel test-time compute. **Grade C** (the sample count is undisclosed, so the configuration is not reproducible).
**Population:** Claude Opus 4 and Sonnet 4 on SWE-bench Verified with a two-tool scaffold (bash and a string-replacement editor).
**Headline results:** standard 72.5% (Opus 4) and 72.7% (Sonnet 4); high compute 79.4% and 80.2%. Method, quoted: "We sample multiple parallel attempts", "We discard patches that break the visible regression tests in the repository" (citing Agentless), "note no hidden test information is used", "We then use an internal scoring model to select the best candidate from the remaining attempts."
**Limitations:** no sample count, no cost, no selector details.
**For orchestrate:** the vendor's own recipe is exactly the two-stage selection orchestrate could run with its verify command (discard what fails) and reviewer (score the rest): about seven points on the hardest benchmark the vendor reports.

### 18. Carlini (2026), Building a C compiler with a team of parallel Claudes

Nicholas Carlini, Anthropic. 2026 (5 Feb 2026). Vendor engineering post. https://www.anthropic.com/engineering/building-c-compiler.
**Design:** single-system case study. **Grade C.**
**Population:** 16 Claude Opus 4.6 agents, each in its own Docker container, sharing one git repository; nearly 2,000 Claude Code sessions, 2 billion input and 140 million output tokens, just under $20,000, about two weeks; output a ~100,000-line Rust C compiler that builds Linux 6.9 on x86, ARM and RISC-V.
**Headline results:** no orchestrator; each agent claims a task by writing a file under `current_tasks/` in the repo, and git's synchronisation forces the second claimant to pick another; after finishing, an agent pulls, merges others' changes, pushes and removes its lock. "Merge conflicts are frequent, but Claude is smart enough to figure that out." Sixteen agents did not help while the kernel build was one task: they hit the same bug and overwrote each other's fixes; the fix was a GCC-as-oracle harness compiling most files with GCC and a random subset with Claude's compiler, so agents worked on different bugs in different files; delta debugging found file pairs that failed together. A CI pipeline with stricter enforcement was added near the end to stop new commits breaking existing code.
**Limitations:** one project with a perfect oracle (GCC) and a verifiable goal; no conflict rate; human designed the harness, tests and CI.
**For orchestrate:** parallelism only paid once tasks were made independent, which is what a touch map tries to do up front. The worker resolving its own merge conflicts, and CI tightening as the swarm grows, both match orchestrate's handback and gate.

### 19. Lin (2026), Scaling long-running autonomous coding (Cursor)

Wilson Lin, Cursor. 2026 (14 Jan 2026). Vendor engineering post. https://cursor.com/blog/scaling-agents.
**Design:** single-system case study. **Grade C.**
**Population:** "hundreds of concurrent agents" on one project; a browser (over 1M lines across 1,000 files in close to a week); a Solid-to-React migration in Cursor's codebase (+266K/−193K over three weeks); Java LSP, Windows 7 emulator, Excel clone; "trillions of tokens".
**Headline results:** the first design (equal agents self-coordinating through a shared file with locks) failed: locks held too long or never released, "Twenty agents would slow down to the effective throughput of two or three, with most time spent waiting"; agents failed while holding locks or updated the file without locking. Optimistic concurrency control was simpler and more robust but agents without hierarchy became risk-averse and churned. The working design is planners (explore, create tasks, spawn sub-planners), workers (take a task, finish without coordinating with other workers, push) and a judge (decide per cycle whether to continue, next iteration starts fresh). Workers push to the same branch "with minimal conflicts"; an integrator role for conflict resolution "created more bottlenecks than it solved" because workers "were already capable of handling conflicts themselves". The migration "still needs careful review".
**Limitations:** no conflict rate, no quality metric, no cost; projects with a strong oracle.
**For orchestrate:** pessimistic locking among peers does not scale; a hierarchy with non-coordinating workers does. Orchestrate's orchestrator is the planner and judge, and its workers do not coordinate. The "minimal conflicts" claim contradicts sources 3 and 4 and is unmeasured; the most likely reconciliation is that planners sized tasks to be independent, which is the touch map's job.

### 20. Cursor (2025), Introducing Cursor 2.0 and Composer

Cursor. 2025 (29 Oct 2025). Vendor announcement. https://cursor.com/blog/2-0.
**Design:** product claim. **Grade C.**
**Headline results:** Cursor 2.0 can "run many agents in parallel without them interfering with one another, powered by git worktrees or remote machines"; running several models on the same problem and "picking the best result significantly improves the final output", "especially for harder tasks". No figures.
**For orchestrate:** two vendors (with source 17) ship sampling-then-select to users; neither publishes the lift in production.

### 21. Yan (2025, 2026), Don't Build Multi-Agents; Multi-Agents: What's Actually Working (Cognition)

Walden Yan, Cognition. 12 Jun 2025, https://cognition.com/blog/dont-build-multi-agents; 22 Apr 2026, https://cognition.com/blog/multi-agents-working. Vendor engineering posts. **Grade C.**
**Design:** practitioner principles with product observations.
**Headline results (2025):** "Share context, and share full agent traces, not just individual messages"; "Actions carry implicit decisions, and conflicting decisions carry bad results"; parallel subagents writing in parallel produce inconsistent work; "running multiple agents in collaboration only results in fragile systems"; recommendation: single-threaded agent plus a context-compression model. No measurements. **(2026):** what works is read-only subagents (code search, Deepwiki), an author/reviewer loop ("we make Devin and Devin Review natively iterate against one another"; Devin Review catches an average of 2 bugs per PR, roughly 58% severe), and manager Devins spawning child Devins over an internal MCP; what still does not see adoption is parallel writers; "decision-making can get quite fragmented in a multi-agent world where multiple agents are taking write actions"; unstructured swarms are "mostly a distraction", favour "map-reduce-and-manage"; the showcase swarm results (a 200k LOC browser, a 100k LOC compiler) share "a simple, verifiable success criterion" that most real software lacks.
**Limitations:** no controlled numbers; vendor interest.
**For orchestrate:** writes single-threaded per ticket, reads fanned out (scout, reviewer), a manager that delegates whole units: this is orchestrate's shape. It also names the caveat for every swarm demo cited here: a verifiable oracle.

### 22. Anthropic (2025), How we built our multi-agent research system

Anthropic. 2025 (13 Jun 2025). Vendor engineering post. https://www.anthropic.com/engineering/multi-agent-research-system.
**Design:** internal evaluation reported without method detail. **Grade C.**
**Headline results:** an Opus 4 lead with Sonnet 4 subagents "outperformed single-agent Claude Opus 4 by 90.2% on our internal research eval"; on BrowseComp three factors explain 95% of variance and token usage alone 80%; agents use about 4x the tokens of chat, multi-agent systems about 15x; parallel subagents (3 to 5 at once) and parallel tool calls "cut research time by up to 90% for complex queries"; failures: "spawning 50 subagents for simple queries", duplicated work from vague instructions, agents distracting each other with updates; domains with many dependencies between agents "are not a good fit for multi-agent systems today", and most coding tasks have fewer truly parallelisable parts than research.
**Limitations:** internal eval; research, not coding.
**For orchestrate:** the vendor's own position is that coding is a weak fit for intra-task parallelism, and that explicit effort-scaling rules (how many subagents, how many calls) are needed to stop over-spawning: the slot count and the scout's narrow briefs are those rules.

### 23. Anthropic, Claude Code documentation: agent teams; parallel sessions with worktrees

Anthropic. 2026 (live docs, read 2026-11-10). https://code.claude.com/docs/en/agent-teams; https://code.claude.com/docs/en/common-workflows#run-parallel-sessions-with-worktrees. Vendor docs. **Grade C.**
**Headline statements:** agent teams are experimental (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`); teammates share a task list and "Task claiming uses file locking to prevent race conditions"; "Two teammates editing the same file leads to overwrites. Break the work so each teammate owns a different set of files"; "For sequential tasks, same-file edits, or work with many dependencies, a single session or subagents are more effective"; "Start with 3-5 teammates"; "Having 5-6 tasks per teammate keeps everyone productive"; token costs scale linearly with teammates; no nested teams; `claude --worktree <name>` gives each session its own checkout "without the edits colliding".
**For orchestrate:** the harness vendor's guidance is file ownership per agent, a shared claimed-task list, and small teams; orchestrate implements ownership as touch maps plus holds, claims as tracker assignment, and the team size as the slot count.

### 24. OpenAI, Codex documentation: Git worktrees

OpenAI. 2026 (live docs, read 2026-11-10). https://developers.openai.com/codex/app/worktrees (redirects to https://learn.chatgpt.com/docs/environments/git-worktrees). Vendor docs. **Grade C.**
**Headline statements:** "Worktrees let Codex run multiple independent chats in the same project without interfering with each other"; scheduled tasks "run on dedicated background worktrees so they don't conflict with your ongoing work"; each worktree duplicates dependencies and build caches, so many worktrees use significant disk space. The May 2025 Codex launch was framed as an agent that "can work on many tasks in parallel" in separate sandboxes (secondary).
**For orchestrate:** a third vendor converging on worktree isolation per task; the disk and dependency duplication is a real cost orchestrate's `--greedy` does not account for.

### 25. GitHub documentation, Managing a merge queue

GitHub. Live docs, read 2026-11-10. https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue. Vendor docs. **Grade C.**
**Headline statements:** the queue "creates temporary branches" and merges PRs "in a first-in-first-out order where the required checks are always satisfied"; it batches by minimum and maximum group size with a wait-time timeout; a PR whose required check fails "will be removed from the queue" and the queue continues without it; build concurrency is capped at 1 to 100 `merge_group` webhooks.
**For orchestrate:** the platform's own answer to many PRs landing at once is serialisation with re-validation against the combined state, and dropping the failing entry rather than stalling. Orchestrate's merge script (update branch, wait for CI on the new head, then merge, then sweep) is a hand-rolled merge queue; the one thing it lacks is batching.

### 26. Thurlow (2026), Human Oversight System: two field findings on batch merges

Thurlow, S. (2026). Human Oversight System (HOS), research notes `research/findings/dismiss-stale-reviews-cascade-in-batch-merges.md` and `research/findings/validation-stamp-merge-conflict-cascade.md`. Single-system field observations. **Grade C.**
**Headline results:** with `dismiss_stale_reviews_on_push` on, merging PR A advanced the base and GitHub dismissed the overseer's approvals on B and C, stalling an overnight batch merge (2026-06-16, issue #322); the fix was to serialise merges, re-check approval and re-run the tier CI check before each one, never two merges against one base in a cycle, and not to disable stale-review dismissal. Separately, a tracked validation-stamp file every PR wrote caused "~15 rounds of manual conflict resolution" across 8 simultaneously open PRs (>2 hours), fixed by content-hash-named stamp files so identical content yields identical filenames and no conflict.
**For orchestrate:** two concrete anti-patterns for a system that merges many PRs: an approval that a base advance invalidates, and a tracked file every PR writes. Orchestrate's `--approved-head` check and shared-file replay cover both today; the content-addressed file is a cheaper design than replay where the repo controls the file.

### 27. Faros AI (2025), The AI Productivity Paradox Report 2025

Faros AI. 2025 (23 Jul 2025; data as of June 2025). Industry report. https://www.faros.ai/blog/ai-productivity-paradox-report-2025.
**Design:** vendor telemetry from over 10,000 developers across 1,255 teams, up to two years of history aggregated by quarter; per-team comparison of the two lowest-adoption quarters against the two highest; only metrics with data from at least six companies and p < 0.05 reported. **Grade C** (industry report; no independent replication).
**Headline results:** high-adoption teams "complete 21% more tasks and merge 98% more pull requests"; "PR review time increases 91%"; "a 154% increase in average PR size"; "a 9% increase in bugs per developer"; "we observed no significant correlation between AI adoption and improvements at the company level." Faros's 2026 follow-up (about 22,000 developers) reports median time to first review up 156.6% and PRs merged with no review up 31.3% (secondary).
**Limitations:** correlational; vendor telemetry; adoption measured, not agents specifically.
**For orchestrate:** the clearest number on the human bottleneck: PR volume doubles and review time nearly doubles with it, while quality slips. An orchestrator that only maximises tickets per hour reproduces this; it should measure review latency and defect escapes beside throughput.

## Implementable findings

| id | finding | grade | source | for orchestrate |
|---|---|---|---|---|
| PR-01 | Two unaware parallel agents on contradictory tasks fall from 62% to 16% success; a sequential A→B hand-off recovers 54% at 1.18x the oracle's tokens | A | 2 | `default`: keep holding an overlapping ticket until the other merges; never start both |
| PR-02 | Peer awareness (+10 points, not significant) and messaging or sync channels (20% to 32%) recover only a few points over unaware parallel (16%) and always cost more tokens | A | 2 | `reject`: do not add worker-to-worker messaging or a shared "what I am editing" channel as a substitute for holds |
| PR-03 | On complementary (non-conflicting) tasks parallel agents still lose 29 points (86% to 57%) against one agent doing both, recovering to 77% with synchronisation | A | 2 | `measure`: log, per pass, how many dispatched pairs share a module even when their touch maps are disjoint, and their rework rate |
| PR-04 | 19.8% of co-active same-agent PR pairs and 41.7% of cross-agent pairs conflict textually on replay (non-overlapping 95% CIs); 42% of conflicts are add/add or modify/delete | A | 3 | `rule`: `holds` treats a path either side adds or deletes as held, not only paths both change; mixed-model runs (#513/#514) need the same holds, not looser ones |
| PR-05 | 27.67% of 107k agentic PRs conflict with their base when merge is simulated; per agent 15.2% to 31.9% | A | 4 | `measure`: record the conflict rate of orchestrate's own PRs at sweep time (clean / shared-file replay / handback) as a run-log metric |
| PR-06 | 47.3% of textual conflicts between concurrent agent PRs occurred with disjoint authored file sets; `git merge-tree` replay costs a median 0.02 s, so "verify everything" | B | 11 | `rule`: the gate and `holds --pr` run `git merge-tree` of the PR head against main and against every other open PR head, not only a path-overlap check |
| PR-07 | A decision-time overlap gate de-overlaps a median 91.7% of scope collisions at 65.0% makespan inflation | B | 11 | `flag`: an `--optimistic` mode that dispatches on predicted overlap and relies on sweep plus handback, so the hold's throughput cost can be measured per repo |
| PR-08 | Forecasters recover 81 to 83% of conflicts at a one-third replay budget (AUROC 0.88 to 0.90) | B | 11 | `measure`: score the scout's touch map against the PR's actual diff and against sweep conflicts; a map that misses is the first thing to tune |
| PR-09 | Interface changes that pass alone fail together in 97% of constructed runs but in 1 of 834 reviewed PR pairs; a message describing the completed concurrent change recovered 82% of runs | B | 12 | `default`: when the sweep updates or hands back a PR, the resumed worker's message carries the merged PR's title, touched files and changed signatures, not only a conflict list |
| PR-10 | Centralised coordination contains error amplification to 4.4x against 17.2x for independent agents; independent agents score −14.9% on SWE-bench Verified | A | 1 | `default`: keep one orchestrator owning dispatch and merge and a gate every PR passes; no worker merges, no worker spawns workers |
| PR-11 | Multi-agent variants degrade sequential-constraint tasks by 39 to 70%, and more agents give negative returns once the single agent exceeds about 45% | A | 1 | `reject`: do not split one ticket across several workers; a ticket is the unit, and splitting is the lead's job before dispatch |
| PR-12 | Multi-agent coordination costs 1.6 to 6.2x the tokens of a single agent (decentralised 263%, hybrid 515%) | A | 1 | `measure`: `--profile` reports orchestration overhead (orchestrator, scout, reviewer tokens) as a ratio to worker tokens per merged PR |
| PR-13 | Coverage on SWE-bench Lite rises from 15.9% (1 sample) to 56% (250 samples), log-linearly; without a verifier, voting and reward models plateau | B | 8 | `flag`: sampling only for tickets whose verify command is a real test run; a ticket with no verify command is not a sampling candidate |
| PR-14 | Selection recovers about half the oracle gap (random 45.8%, selected 57.4%, oracle 69.8%) with 10 parallel trajectories at ~$4.60 per issue | B | 7 | `flag`: `--samples=k` for L tickets or risk paths: k workers in k worktrees on one ticket, keep the PR that passes verify, reviewer ranks ties, close the rest |
| PR-15 | A critic-comparator over 8 samples lifts a weak model from 67.0% to 76.4% against a 79.0% oracle, recovering 78.3% of the gain | B | 9 | `flag`: before escalating a failed Haiku worker to Sonnet, try k Haiku samples with test-based selection; compare cost per merged PR against escalation |
| PR-16 | Vendor high-compute recipe: sample in parallel, discard patches failing visible regression tests, score the rest with a model; 72.5% to 79.4% | C | 17 | `flag`: the selection order for PR-14 is verify first, reviewer second; never reviewer alone |
| PR-17 | Selecting across a heterogeneous ensemble beats its best member (66.2% against 62.8%; 78.2% with a three-model pool) | B | 7, 10 | `flag`: when sampling, draw the k workers from two model families, not k of one; ties go to the reviewer |
| PR-18 | Peer agents self-coordinating with locks collapsed "twenty agents" to "the effective throughput of two or three"; an integrator role for conflicts "created more bottlenecks than it solved" | C | 19 | `reject`: no shared lock file among workers and no separate conflict-resolver agent; the PR's own worker resolves its conflicts (today's handback) |
| PR-19 | Sixteen agents on one shared goal overwrote each other's fixes until the harness split the goal into independent files | C | 18 | `rule`: `frontier` skips a parent whose sub-issues share a touch map; the lead splits by files before dispatch |
| PR-20 | Vendor guidance: 3 to 5 teammates, 5 to 6 tasks each, one file owner per file, same-file edits are for a single session; task claims use file locking | C | 23 | `default`: `--greedy` caps at the number of non-overlapping tickets and at a config ceiling (default 4 or 5); claims stay in the tracker, which is the lock |
| PR-21 | Swarm successes (compiler, browser) all had "a simple, verifiable success criterion"; most real software lacks one | C | 21, 18, 19 | `rule`: the scout reports whether a ticket has an executable oracle; tickets without one get neither sampling nor a Haiku worker |
| PR-22 | High-AI-adoption teams merge 98% more PRs, review time rises 91%, PR size 154%, bugs per developer 9%, with no company-level gain | C | 27 | `measure`: per run, report PRs merged, median time from ready to merge-gate decision, and reopened or reverted PRs within 7 days; throughput alone is not a success metric |
| PR-23 | 38% of rejected agentic PRs were closed with no human engagement; unmerged PRs are larger (δ 0.17 LOC) and each failed check cuts merge odds ~15% | A | 5 | `rule`: a PR reaches a human merge gate only green and only inside the S/M thresholds; a WIP limit on PRs waiting at a human gate pauses dispatch (default: 3) |
| PR-24 | 23% of rejected agentic PRs were duplicates | A | 5 | `rule`: before dispatch, `frontier` checks open PRs and recently merged PRs for the ticket's reference or title, and the brief names any near-duplicate PR |
| PR-25 | Two-fifths of agent-PR resolution-time variance is between repositories; the between-repo variance is 1.76x that of human PRs | A | 6 | `measure`: the review WIP limit (PR-23) and slot count are per-repo config, tuned from this repo's observed merge-gate latency, never a global default copied between repos |
| PR-26 | OSS projects cap simultaneous open PRs for new contributors (Jaeger) and ~57% require AI disclosure; one team reported 30 PRs/day for 6 reviewers and a 500-LOC review ceiling | B/C | 14, 15 | `rule`: config `max-open-prs` per repo enforced by `frontier`; PR bodies disclose the agent; a worker whose diff exceeds the config LOC cap stops and asks for a split |
| PR-27 | Base advance dismisses stale approvals and a tracked file every PR writes causes a conflict cascade (~15 resolution rounds for 8 PRs); fix is serialised merge with re-check plus content-addressed files | C | 26, 25 | `default`: keep one merge at a time with `--approved-head`; recommend in setup that generated artefacts every PR writes be content-addressed or untracked rather than listed as shared files |
| PR-28 | A merge queue re-validates each PR against the combined state in FIFO order and drops a failing entry instead of stalling | C | 25 | `flag`: `--merge-queue` delegates serialisation to GitHub's queue where the repo enables it, and the sweep runs only on queue eviction |
| PR-29 | 23.5% of multi-agent failures are verification failures; 12.4% are agents unaware of termination conditions | B | 13 | `default`: script-owned verdicts and a fixed worker report format with a hard stop are the verification layer; keep them out of the model's hands |
| PR-30 | Contradiction: Cursor reports "hundreds of workers" pushing to one branch with "minimal conflicts" (no rate) while field data shows 20 to 28% of agentic PRs conflict | C vs A | 19 vs 3, 4 | `measure`: PR-05's conflict metric is what settles this for orchestrate; do not loosen holds on the vendor claim |
| PR-31 | Contradiction: a 90.2% gain from parallel subagents on research against vendor and practitioner statements that coding has few parallelisable parts and parallel writers are fragile | C | 22, 21 | `default`: fan out reads (scout, reviewers), never writes; one writer per ticket |
| PR-32 | Each worktree duplicates dependencies and build caches | C | 24 | `measure`: the scout's environment check reports per-worktree setup cost; `--greedy` sizing considers it |

## Candidate paradigms

1. **Isolated workers with predictive holds** (what orchestrate runs): one worktree per ticket, dispatch only on disjoint predicted touch, serialised merge, sweep after merge. Backed by PR-01, PR-04, PR-10, PR-18, PR-20. Best grade A.
2. **Sequential hand-off on overlap**: an overlapping ticket waits and its worker is told what merged. Backed by PR-01, PR-09. Best grade A.
3. **Optimistic dispatch with verified repair**: dispatch regardless, run `merge-tree` continuously, hand conflicts to the PR's worker; trades a 92% collision cut for 65% makespan. Backed by PR-06, PR-07, PR-18. Best grade B.
4. **Sampling-then-select (best-of-k)**: k workers on one ticket, keep the one that passes verify, reviewer ranks the rest. Backed by PR-13 to PR-16. Best grade B.
5. **Heterogeneous-model ensemble selection**: the k samples come from different model families. Backed by PR-17. Best grade B.
6. **Sample-before-escalate**: k cheap samples with test selection before moving a ticket to a bigger model. Backed by PR-15. Best grade B.
7. **Planner / worker / judge hierarchy**: planners mint independent tasks, workers never coordinate, a judge ends the cycle. Backed by PR-18, PR-19. Best grade C.
8. **Leaderless swarm with git-level claims**: lock files in the repo, each agent merges its own way in; needs an executable oracle. Backed by PR-19, PR-21. Best grade C.
9. **Agent teams with a shared task list**: a lead, 3 to 5 teammates, file ownership per teammate, locked task claims. Backed by PR-20. Best grade C.
10. **Single writer, fanned-out readers**: one writing agent per unit of work; scouts and reviewers in parallel. Backed by PR-10, PR-31. Best grade A.
11. **Review WIP limit**: dispatch pauses when more than N PRs wait on a human, N per repo. Backed by PR-22, PR-23, PR-25, PR-26. Best grade A.
12. **Platform merge queue**: GitHub serialises and re-validates; the orchestrator only enqueues. Backed by PR-27, PR-28. Best grade C.
13. **Duplicate-aware dispatch**: check open and merged PRs before a ticket starts. Backed by PR-24. Best grade A.

## Open questions

- No controlled study compares hold-based scheduling (paradigm 1) with optimistic dispatch and repair (paradigm 3) on throughput, conflict cost and defect rate; PR-07's 65% makespan figure comes from frozen-label replay, not live agents.
- How many PRs per day one human reviewer absorbs before quality drops is unmeasured in any primary source here; the 30 PRs/6 reviewers figure is one team's self-report, and Kim and Yegge's claim that coordination cost rises more than tenfold from two to four concurrent agents (secondary, via the HOS literature review; book, no study design given) is not something this document could verify.
- The lift from sampling-then-select in production (Cursor 2.0, Anthropic high compute) is reported only on benchmarks or not at all; whether it holds on tickets with weak test suites is unknown, and PR-13 says it should not.
- Whether a line-level touch map adds anything over a file-level one is untested; every conflict study here works at file granularity.
- The mix of co-active PRs that are the same bot on the same backlog (orchestrate's case) against unrelated bots (most of the AIDev data) changes the base rate; PR-05 is the only way to learn orchestrate's own.
- Semantic conflicts are near-zero in reviewed PR pairs (stale) but the study is Django-only; a typed language with wider interfaces may differ.
- ParallACT tests two agents; nothing here measures three to eight concurrent writers on one repository under any coordination rule except the Cursor and Carlini case studies.

## Sources list

1. Kim, Y., Gu, K., Park, C., Park, C., Schmidgall, S., Heydari, A. A., Yan, Y., Zhang, Z., Zhuang, Y., Liu, Y., Malhotra, M., Liang, P. P., Park, H. W., Yang, Y., Xu, X., Du, Y., Patel, S., Althoff, T., McDuff, D., & Liu, X. (2025). Towards a Science of Scaling Agent Systems. Preprint, arXiv:2512.08296. https://arxiv.org/abs/2512.08296
2. Mediratta, R., Xiao, L., Stone, P., Biswas, J., & Stengel-Eskin, E. (2026). Decentralized Multi-Agent Coordination and Communication in Shared Codebases. Preprint, arXiv:2611.00314. https://arxiv.org/abs/2611.00314
3. Xu, G., Subramanian, A., & Karthik, N. (2026). AI Agent Pull Requests on GitHub: Frequency, Structure, and Merge Conflict Rates. Preprint, arXiv:2607.04697. https://arxiv.org/abs/2607.04697
4. Ogenrwot, D., & Businge, J. (2026). AgenticFlict: A Large-Scale Dataset of Merge Conflicts in AI Coding Agent Pull Requests on GitHub. AIware 2026. arXiv:2604.03551. https://arxiv.org/abs/2604.03551
5. Ehsani, R., Pathak, S., Rawal, S., Mujahid, A. A., Imran, M. M., & Chatterjee, P. (2026). Where Do AI Coding Agents Fail? An Empirical Study of Failed Agentic Pull Requests in GitHub. MSR 2026. arXiv:2601.15195. https://arxiv.org/abs/2601.15195
6. Russo, D. (2026). For Coding Agents, the Repository Matters. Preprint, arXiv:2606.28235. https://arxiv.org/abs/2606.28235
7. Ehrlich, R., Brown, B., Juravsky, J., Clark, R., Ré, C., & Mirhoseini, A. (2025). CodeMonkeys: Scaling Test-Time Compute for Software Engineering. Preprint, arXiv:2501.14723. https://arxiv.org/abs/2501.14723
8. Brown, B., Juravsky, J., Ehrlich, R., Clark, R., Le, Q. V., Ré, C., & Mirhoseini, A. (2024). Large Language Monkeys: Scaling Inference Compute with Repeated Sampling. Preprint, arXiv:2407.21787. https://arxiv.org/abs/2407.21787
9. Sunkaraneni, V., Beneventano, P., Neumarker, R., Poggio, T., & Galanti, T. (2026). Inference-Time Amplification of Weak Reasoning Models. Preprint, arXiv:2605.14163. https://arxiv.org/abs/2605.14163
10. Kwok, J., Li, S., Atreya, P., Liu, Y., Jiang, Y., Finn, C., Pavone, M., Stoica, I., & Mirhoseini, A. (2026). LLM-as-a-Verifier: A General-Purpose Verification Framework. Preprint, arXiv:2607.05391. https://arxiv.org/abs/2607.05391
11. Subramanian, A., Xu, G., & Karthik, N. (2026). Asymmetric Repository Lineage Modeling and Verifier-Guided Coordination in Concurrent AI Coding Agents. Preprint, arXiv:2610.04779. https://arxiv.org/abs/2610.04779
12. Xia, H., Wu, E., & Park, Y. (2026). Passes Alone, Fails Together: Benchmarking Semantic Coordination in Parallel LLM-Agent Development. EXPRESS 2026. arXiv:2609.25396. https://arxiv.org/abs/2609.25396
13. Cemri, M., Pan, M. Z., Yang, S., Agrawal, L. A., Chopra, B., Tiwari, R., Keutzer, K., Parameswaran, A., Klein, D., Ramchandran, K., Zaharia, M., Gonzalez, J. E., & Stoica, I. (2025). Why Do Multi-Agent LLM Systems Fail? Preprint, arXiv:2503.13657 (NeurIPS 2025 Datasets and Benchmarks, secondary). https://arxiv.org/abs/2503.13657
14. Yang, W., He, R., & Zhou, M. (2026). To Ban or not to Ban? How Open Source Projects Govern GenAI Contributions. ASE 2026. arXiv:2603.26487. https://arxiv.org/abs/2603.26487
15. Baltes, S., Cheong, M., & Treude, C. (2026). "An Endless Stream of AI Slop": How Developers Discuss the Burden of AI-Assisted Software Development. Preprint, arXiv:2603.27249. https://arxiv.org/abs/2603.27249
16. Li, H., Zhang, H., & Hassan, A. E. (2025). The Rise of AI Teammates in Software Engineering (SE) 3.0: How Autonomous Coding Agents Are Reshaping Software Engineering. Preprint, arXiv:2507.15003. https://arxiv.org/abs/2507.15003
17. Anthropic. (2025, May 22). Introducing Claude 4. Vendor announcement. https://www.anthropic.com/news/claude-4
18. Carlini, N. (2026, February 5). Building a C compiler with a team of parallel Claudes. Anthropic Engineering. https://www.anthropic.com/engineering/building-c-compiler
19. Lin, W. (2026, January 14). Scaling long-running autonomous coding. Cursor blog. https://cursor.com/blog/scaling-agents
20. Cursor. (2025, October 29). Introducing Cursor 2.0 and Composer. https://cursor.com/blog/2-0
21. Yan, W. (2025, June 12). Don't Build Multi-Agents. Cognition. https://cognition.com/blog/dont-build-multi-agents; Yan, W. (2026, April 22). Multi-Agents: What's Actually Working. Cognition. https://cognition.com/blog/multi-agents-working
22. Anthropic. (2025, June 13). How we built our multi-agent research system. Anthropic Engineering. https://www.anthropic.com/engineering/multi-agent-research-system
23. Anthropic. (2026). Claude Code documentation: Orchestrate teams of Claude Code sessions; Common workflows: Run parallel sessions with worktrees. https://code.claude.com/docs/en/agent-teams; https://code.claude.com/docs/en/common-workflows
24. OpenAI. (2026). Codex documentation: Worktrees. https://developers.openai.com/codex/app/worktrees
25. GitHub. (2026). Managing a merge queue. GitHub Docs. https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
26. Thurlow, S. (2026). Human Oversight System (HOS), documentation and research notes, https://github.com/thurlow-research/HumanOversightSystem (software archived at https://doi.org/10.5281/zenodo.21347272), docs CC BY 4.0, code MIT. Findings used: `research/findings/dismiss-stale-reviews-cascade-in-batch-merges.md`, `research/findings/validation-stamp-merge-conflict-cascade.md`; the Kim and Yegge pointer comes from `docs/planning/SLR-Derived-Improvements.md`.
27. Faros AI. (2025, July 23). The AI Productivity Paradox Report 2025. Industry report. https://www.faros.ai/blog/ai-productivity-paradox-report-2025
