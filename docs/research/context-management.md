# Context management: evidence for orchestrating AI coding agents

Research for [#605](https://github.com/shrujancheruku/claude-orchestrate/issues/605) (map [#588](https://github.com/shrujancheruku/claude-orchestrate/issues/588)). Sources fetched 2026-11-10 from arXiv, venue pages, vendor engineering posts and the Claude Code docs. Area prefix `CX`.

Long context degrades performance on every measurement that varies length alone: code repair falls from 29% to 3% between 32K and 256K tokens (LongCodeBench), latent-association recall halves by 32K for 11 of 13 models (NoLiMa), and a 100-document answer in the middle of the window scores below the closed-book baseline (Liu et al.). Trimming or masking old tool observations is the best-supported context control: it halves agent cost at an equal or better solve rate, and an LLM summary does no better than masking (Lindenbauer et al., SWE-agent ablation, OpenHands). Repository instruction files are the clearest negative: three independent 2026 studies find no significant success-rate gain from AGENTS.md (−2 to +2.4 pp, p > 0.2), 20% higher cost in the one study that measured it, and a 20 to 29% speed gain in another; agents do follow the instructions, so files should carry non-standard conventions, not overviews. Continuing a long session carries a measurable penalty (−39% on sharded multi-turn tasks, unreliability +112%; self-conditioning on earlier errors), which favours fresh contexts with written handoffs, though no study isolates fresh-vs-continued sessions for coding agents. Cross-session memory is thin and mixed: oracle-selected summaries lift resolution 26% to 34%, free retrieval of the wrong summary drops it to 22%, and the best field system gains +2.7 pp (not significant) while hurting the strongest model.

## Sources

### 1. Gloaguen, Mündler-Sasahara, Müller, Raychev, Vechev (2026). Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?

- Citation: arXiv:2602.11988 (v3, 29 Sep 2026), ETH Zurich SRI lab. https://arxiv.org/abs/2602.11988. Listed on the ICLR 2026 virtual site (secondary).
- Design: controlled benchmark with stratified permutation and Cochran-Mantel-Haenszel tests; three conditions (no file, LLM-generated file, developer-committed file). Grade A.
- Population: Claude Code (Sonnet 4.5, Haiku 4.5 subagents), Codex CLI (GPT-5.2, GPT-5.1 mini), Qwen Code (Qwen3-30B). SWE-bench Lite (300 tasks, 11 Python repos, no committed context files) and CTXbench (138 issues from 12 repos that have developer-committed context files, built from 5,694 PRs).
- Results: LLM-generated files change success by about −0.5 pp on SWE-bench (p = 0.87) and −2 pp on CTXbench (p = 0.37); developer files +2.4 pp on CTXbench (p = 0.21); developer vs LLM file is significant (p = 0.038). Cost rises significantly: +20% (SWE-bench) and +23% (CTXbench) with LLM files, up to +19% with developer files; steps +2.45 to +3.92 per task. Per-model CTXbench success, none / LLM / dev: Sonnet 4.5 73.2 / 65.2 / 70.3; GPT-5.2 65.2 / 68.1 / 68.1; GPT-5.1 mini 54.3 / 50.7 / 55.8; Qwen3-30B 45.7 / 47.1 / 53.6 (± 4 SE). Agents follow the file: `uv` used 1.6× per instance when mentioned vs < 0.01 otherwise; repo-specific tools 2.5× vs < 0.05. Overviews did not shorten the path to the files the reference patch changes. With all repository documentation deleted, LLM-generated files improved success 2.7% on average.
- Limitations: Python only; task resolution only; Claude Code excluded from the no-docs appendix for cost.
- For orchestrate: the plugin's own `CLAUDE.md`, `.claude/orchestration.md` and the briefs it writes are context files. The evidence says to keep them to non-standard conventions and commands the agent would otherwise get wrong, drop repository overviews, and expect a cost rise of about 20% per added file, not a success gain.

### 2. Lindenbauer, Slinko, Felder, Bogomolov, Zharov (2025). The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management

- Citation: arXiv:2508.21433 (v3, 27 Oct 2025), JetBrains Research and TUM; camera-ready for the DL4C workshop at NeurIPS 2025. https://arxiv.org/abs/2508.21433. Blog: https://blog.jetbrains.com/research/2025/12/efficient-context-management/.
- Design: ablation of three context strategies in one scaffold; no confidence intervals. Grade B.
- Population: SWE-agent on SWE-bench Verified, 500 instances per configuration, five model configurations (Qwen3-32B with and without thinking, Qwen3-Coder 480B, Gemini 2.5 Flash with and without thinking); OpenHands probe on a 50-instance slice.
- Results, solve rate and cost per instance (raw / masking / LLM summary): Qwen3-Coder 480B 53.4% $1.29 / 54.8% $0.61 / 53.8% $0.64; Gemini 2.5 Flash 32.8% $0.41 / 35.6% $0.18 / 36.0% $0.24; Gemini 2.5 Flash thinking 40.4% $0.56 / 36.4% $0.24 / 31.4% $0.25; Qwen3-32B 17.0% $1.12 / 15.0% $0.55 / 16.0% $0.50; Qwen3-32B thinking 23.0% $0.51 / 24.6% $0.46 / 24.8% $0.51. Masking cuts cost 50 to 57% on four of five configurations. Observation tokens are about 84% of an average SWE-agent turn. A hybrid (mask first, summarise only on very long trajectories) cuts cost a further 7% vs masking and 11% vs summary on a 50-instance subset, +2.6 pp over the raw agent. On OpenHands the masking window had to be re-tuned; reusing the SWE-agent value degraded results sharply.
- Limitations: one domain, fixed non-adaptive triggers, OpenHands evidence is initial.
- For orchestrate: Claude Code's compaction is LLM summarisation; the cheaper control it already exposes is tool-result clearing. Workers should prefer tools that return bounded output and the plugin should measure the observation share of worker turns before adding any summarisation of its own.

### 3. Liu, Lin, Hewitt, Paranjape, Bevilacqua, Petroni, Liang (2024). Lost in the Middle: How Language Models Use Long Contexts

- Citation: TACL 12, 2024; arXiv:2307.03172. https://arxiv.org/abs/2307.03172.
- Design: controlled position experiment, peer-reviewed. Grade A.
- Population: GPT-3.5-Turbo (4K and 16K), Claude-1.3 (and 100K), MPT-30B-Instruct, LongChat-13B-16K; multi-document QA with 10, 20 or 30 documents, one containing the answer; synthetic key-value retrieval.
- Results: GPT-3.5-Turbo, 20 documents: 75.8% with the answer first, 53.8% at position 10, 63.2% at position 20; closed-book 56.1%, oracle 88.3%. The middle position scores below the closed-book baseline. Extended-context variants perform nearly identically to the base model when the input fits both. Query-aware contextualisation (query before and after the documents) brings key-value retrieval to near-perfect.
- Limitations: 2023 model generation; QA not code.
- For orchestrate: anything the orchestrator or a worker must act on belongs at the start or the end of the prompt: ticket, acceptance criteria and the instruction at the ends, bulk reference material in the middle. Briefs that bury the acceptance criteria among file dumps are the failure mode this measures.

### 4. Laban, Hayashi, Zhou, Neville (2025). LLMs Get Lost in Multi-Turn Conversation

- Citation: arXiv:2505.06120, Microsoft Research and Salesforce Research. https://arxiv.org/abs/2505.06120.
- Design: controlled simulation with 200,000+ conversations; effect sizes reported. Grade A.
- Population: 15 LLMs from eight families; six tasks including Code, text-to-SQL and API calling; 600 instructions, each delivered whole (single turn) or in shards over turns.
- Results: average performance drops 39% from single-turn to sharded multi-turn (35% in the Figure 1 caption). Decomposition: aptitude (90th percentile) −16%, unreliability (best-minus-worst gap) +112%. Concatenating the shards into one turn recovers 95.1% of the full-spec score, so the loss is from the turn structure, not lost information. Snowball recap recovers 15 to 20% of the gap (GPT-4o-mini: sharded 50.4, snowball 61.8, full 86.8). Temperature 0 for both parties leaves unreliability near 30%. Claude 3.7 Sonnet and GPT-4.1 "conserve performance well on Code".
- Limitations: simulated user (GPT-4o-mini); analytical tasks; the authors call the drop an underestimate.
- For orchestrate: a worker that receives the task in one complete brief outperforms one that gathers it through follow-up messages. Review rounds delivered as additional turns are the sharded setting; restating the complete task in each round is the "concat" remedy. It also supports starting a fresh worker for a rework rather than continuing a long conversation.

### 5. Lulla, Mohsenimofidi, Galster, Zhang, Baltes, Treude (2026). On the Impact of AGENTS.md Files on the Efficiency of AI Coding Agents

- Citation: arXiv:2601.20404 (v2, 30 Mar 2026); Journal Ahead Workshop (JAWs) 2026. https://arxiv.org/abs/2601.20404.
- Design: paired within-task comparison, Wilcoxon signed-rank; efficiency only. Grade A for efficiency, no correctness outcome.
- Population: Codex CLI with gpt-5.2-codex; 124 merged PRs (≤ 100 LoC, ≤ 5 files) from 10 repositories that carry a root AGENTS.md; same snapshot and issue text with and without the file.
- Results: wall-clock 162.9 s → 129.9 s mean (−20.3%), 98.6 s → 70.3 s median (−28.6%), significant; output tokens 5,745 → 4,591 mean (−20.1%), 2,925 → 2,440 median (−16.6%), significant; total tokens −9.9% mean but −1.3% median, not significant. Success rate not reported ("comparable" after a 50-task manual check).
- Limitations: one agent and model, small PRs, no correctness evaluation, presence/absence only.
- For orchestrate: the one measured benefit of an instruction file is fewer output tokens and shorter runs, consistent with Gloaguen's finding that agents obey the file. The two studies conflict only on cost: Gloaguen counts the added input of a longer prompt and more steps; Lulla counts output and time.

### 6. Modarressi, Deilamsalehy, Dernoncourt, Bui, Rossi, Yoon, Schütze (2025). NoLiMa: Long-Context Evaluation Beyond Literal Matching

- Citation: ICML 2025, PMLR 267; arXiv:2502.05167. https://arxiv.org/abs/2502.05167.
- Design: peer-reviewed controlled length sweep, 7,540 tests per length. Grade A.
- Population: 13 models (GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro and Flash, Gemini 2.0 Flash, Llama 3.1 and 3.3, Mistral Large 2, Command R+, Jamba 1.5 Mini, GPT-4o mini); needles with no lexical overlap with the question.
- Results: at 32K, 11 of 13 models score at or below half their short-context base. GPT-4o 99.3 → 69.7 (effective length 8K); Claude 3.5 Sonnet 87.5 → 29.8 (effective 4K); Gemini 1.5 Pro 92.6 → 48.2 (effective 2K); Llama 3.1 8B 76.7 → 14.2. Chain-of-thought helps but not beyond about 16K. Literal matches make the task much easier; distracting literal matches hurt badly.
- Limitations: 58 question-needle pairs; model snapshot from early 2025.
- For orchestrate: a worker asked to connect a ticket's wording to code that uses different names is the no-literal-match case; at 32K of accumulated context its recall is already at half strength. The scout's job (turn a question into exact paths and symbols) is the mitigation the paper's "literal match" result supports.

### 7. Rando, Kyuragi, Romani, Sampieri, Franco, Galasso, Yang, Hashimoto (2025). LongCodeBench: Evaluating Coding LLMs at 1M Context Windows

- Citation: arXiv:2505.07897 (v3, 22 Oct 2025); COLM 2025 (secondary). https://arxiv.org/abs/2505.07897.
- Design: benchmark across six context brackets; no controls or CIs. Grade B.
- Population: LongCodeQA (443 MCQ from 98 Python repos) and LongSWE-Bench (600 repair tasks, 100 per bracket, 61 repos); the context always contains the ground-truth files and is filled with random files to the bracket.
- Results, LongSWE-Bench % solved at 32K / 64K / 128K / 256K / 512K / 1M: Claude 3.5 Sonnet 29 / 19 / 15 / 3 / – / –; Gemini 2.5 Pro 23 / 25 / 22 / 24 / 12 / 7; GPT-4o 11 / 6 / 5; Claude 3.5 Sonnet with RAG over the same files 23 / 23 / 22 / 21 / 16 / 22. LongCodeQA: Qwen2.5 14B 70.2 at 512K → 40.0 at 1M; GPT-4.1 72.6 → 80.0 (no decline). Performance at short lengths does not predict long lengths.
- Limitations: random filler files; binary pass/fail; RAG setup described by the authors as a narrow view; 2025 model generation.
- For orchestrate: whole-repo context is the wrong default for a repair task; retrieval over the same files is flat across length and beats whole-context by 18 pp at 256K. Workers should load files by path on demand (which Claude Code does) and never be handed a repository dump.

### 8. Yang, Jimenez, Wettig, Lieret, Yao, Narasimhan, Press (2024). SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering

- Citation: NeurIPS 2024; arXiv:2405.15793 (v3, 11 Nov 2024). https://arxiv.org/abs/2405.15793.
- Design: component ablation on SWE-bench Lite, GPT-4 Turbo; no CIs. Grade B.
- Results (% resolved, full system 18.0): keep full history 15.0 vs collapse observations older than the last 5 turns 18.0 (−3.0); file viewer showing the full file 12.7 vs 100-line window 18.0 (−5.3) vs 30-line window 14.3; iterative search 12.0 vs summarised search 18.0 (−6.0); shell-only 11.0; RAG baseline 2.67; no demonstration 16.3.
- Limitations: 300 tasks, one model, 2024 generation.
- For orchestrate: the earliest controlled evidence that showing less (bounded file windows, collapsed old observations, summarised search results) resolves more. It is the same direction as source 2 and the opposite of "give the agent everything".

### 9. Khatri (2026). Do Context Files Help Coding Agents? A Two-Agent Ablation Study on Real Repositories

- Citation: arXiv:2607.27250 (28 Jul 2026), independent. https://arxiv.org/abs/2607.27250.
- Design: within-task permutation tests, three conditions × three repeats; underpowered (minimum detectable effect > 30 pp). Grade B.
- Population: Claude Code (claude-sonnet-4-6) and Codex CLI (gpt-5.5); 17 tasks from three Python repos (pdm, firebase-admin-python, opshin); 288 evaluated runs; hidden gold tests.
- Results: Claude pass rate none 53.3%, always-on AGENTS.md 55.6%, selective wiki 55.6% (omnibus p = 1.00); Codex 58.8 / 56.9 / 52.9% (p = 0.66). On the four Codex-borderline tasks, context lowered the pass rate (58% → 42%). Claude's selective condition used significantly fewer cache-creation tokens (11/11 tasks, Holm p = 0.012); opshin wall-clock fell about 24% with context (not significant, n = 5). Codex efficiency flat (32 tool calls in every condition). Near-miss failures were implementation errors, not missing repository facts. Detecting a 10 pp effect at 80% power needs 120 to 200 tasks.
- Limitations: tiny sample, three repos, injection channel differs by agent.
- For orchestrate: confirms the null of source 1 with a different model generation and adds a power calculation: any in-house A/B of brief or config wording needs on the order of 150 tasks to see a 10 pp effect, so single-run anecdotes about prompt tweaks are noise.

### 10. Xu, Saghir, Wu, Côté, Wang, Lakkaraju, Pei, Zhang (2026). The Devil Is in the Interface: Evaluating How Tool Architecture Shapes Coding Agent Behavior

- Citation: arXiv:2608.11386 (13 Aug 2026), Purdue, Microsoft Research, University of Chicago. https://arxiv.org/abs/2608.11386.
- Design: six tool architectures with the same underlying capability, 10 rollouts per instance; pass^k consistency; correlational mechanism claims. Grade B.
- Population: Qwen3Coder-30B, Kimi K2.5, Claude Sonnet 4.5; 65 SWE-bench Live instances; 11,700 trajectories; generalisation on SWE-bench Verified and Pro with the two open models.
- Results: resolve rates broadly similar across architectures. pass^5, BashOnly → Atomic (bash plus bounded search/view/replace tools): Qwen 0.046 → 0.106, Kimi 0.290 → 0.304, Sonnet 0.296 → 0.313; Atomic is the only setup that raises pass^5, pass^7 and pass^9 for all three. Python code blocks cut steps 77 → 46 (Qwen), 65 → 47 (Kimi), 80 → 55 (Sonnet), abstract: −41.6% steps, −56.3% tokens. Atomic lowered Qwen's input tokens 980K → 662K but raised Kimi's 950K → 1,141K and Sonnet's 2,140K → 2,576K. A grep-backed natural-language search subagent with no embeddings raised read diversity and high-relevance recall (+0.046 to +0.064) at a precision cost (−0.046 to −0.104). Hypothesis-tracking and scratchpad tools had little effect.
- Limitations: 65 instances; mechanisms untested causally.
- For orchestrate: how a capability is exposed changes consistency even when it adds no information; bounded view and search tools (what Claude Code's Read/Grep already are) are the architecture with the most consistent gain. The scout as a natural-language search layer widens exploration but lowers precision, so its answers should be paths and symbols, not prose.

### 11. Zhu, Hu, Wu (2026). SWE Context Bench: A Benchmark for Context Learning in Coding

- Citation: arXiv:2602.08316 (9 Feb 2026), independent and University of Oxford. https://arxiv.org/abs/2602.08316.
- Design: benchmark with oracle and free retrieval settings; single run, no variance. Grade B.
- Population: Claude Code with Sonnet 4.5; 300 experience tasks from SWE-bench Lite producing a trajectory pool; 99 related tasks from 12 repos; experience given as raw trajectories (about 24,765 words) or summaries (about 205 words).
- Results (resolution on related tasks): no experience 26.26% ($0.79); free raw-trajectory reuse 26.26% ($0.98); oracle raw trajectory 27.27%; free summary reuse 22.22% ($0.85); oracle summary reuse 34.34% ($0.77), with runtime down over 60% on the slowest tasks. Oracle summary is 27.3% cheaper than free raw reuse.
- Limitations: one model, one run, oracle settings are an upper bound, several repos contribute one to three tasks.
- For orchestrate: memory helps only when the right short summary is selected; a wrong summary costs 4 pp, and raw trajectories cost money for nothing. Any run-log or worker-report reuse should be a short, keyed summary chosen deterministically (by file path or ticket link), never a free-text search over past transcripts.

### 12. Chen, Herrero-Vidal, Chen, Yu, Liu, Song, Xu, Khan, Chen (2026). REM: What to Store, When to Recall, What to Forget in Experience Memory for Coding Agents

- Citation: arXiv:2610.27377 (19 Oct 2026), Amazon and Yale; CLEA workshop. https://arxiv.org/abs/2610.27377.
- Design: three replicates for main conditions, paired McNemar; ablations. Grade B.
- Population: Strands agent, Claude Sonnet 4.5 primary plus four other models; SWE-ContextBench Lite (99 tasks, 300-trajectory pool).
- Results: 27.6% resolved with REM vs 24.9% without (+2.7 pp; 20 wins vs 12 losses over 297 paired runs, McNemar p = 0.22). Tokens per task −17% (452K → 374K), turns 70.4 → 64.6, wall-clock 11.9 → 8.8 min. Summaries or trajectories injected at the start of a run did not raise resolution (oracle summary 23.2%, equal to no memory). Retrieval match@1: REM 68.7%, LangMem 55.6%, Mem0 25.3%, Supermemory 23.2%, self-search over raw trajectories 22.2%. Memory helped weaker models (Haiku 4.5 +4.0, Opus 4.6 +4.1) and hurt the strongest (Opus 4.8 47.5% → 39.4%).
- Limitations: most secondary conditions ran once; single hand-written struggle trigger; staleness not measured.
- For orchestrate: recall on a struggle signal (first failed verification) beats loading memory up front, and memory can hurt a strong model. Any cross-session memory must be gated, measured per model, and switchable off.

### 13. Sinha, Arun, Goel, Staab, Geiping (2025). The Illusion of Diminishing Returns: Measuring Long Horizon Execution in LLMs

- Citation: arXiv:2509.09677 (v3, 13 Mar 2026), MPI-IS, ELLIS Tübingen and others. https://arxiv.org/abs/2509.09677.
- Design: controlled synthetic task with injected error histories; figures mostly in plots. Grade B.
- Results: accuracy at turn 100 falls as the injected rate of the model's own earlier errors rises ("self-conditioning"), including for Kimi-K2, DeepSeek-V3 and Qwen3-235B; scaling model size does not remove it. Thinking models (Qwen3 thinking) did not self-condition, and their prior thinking traces are dropped between turns. Single-turn execution length: GPT-5 2,176 steps, Claude 4 Sonnet 432, Grok 4 384, Gemini 2.5 Pro 120; non-thinking models fail past about six steps without chain-of-thought.
- Limitations: synthetic retrieve-and-add task; no self-correction credit.
- For orchestrate: a context that contains the agent's own failed attempts makes the next attempt worse. A worker that has failed verification twice should hand off to a fresh worker with a written summary of what was tried, not keep going in the same context; this is also the mechanism behind the plugin's third-round stop.

### 14. Li, Li, Zhang, Mei, Bendersky (2024). Retrieval Augmented Generation or Long-Context LLMs? A Comprehensive Study and Hybrid Approach

- Citation: EMNLP 2024 Industry Track; arXiv:2407.16833. https://arxiv.org/abs/2407.16833.
- Design: benchmark comparison with a routing method; no CIs. Grade B.
- Population: Gemini 1.5 Pro, GPT-4o, GPT-3.5-Turbo on seven LongBench and two ∞Bench QA datasets (text, not code).
- Results: long context beats RAG on average (Gemini 1.5 Pro 49.70 vs 37.33; GPT-4o 48.67 vs 32.60; GPT-3.5 32.07 vs 30.33). RAG and LC give identical answers on 63% of queries. Self-Route (answer from retrieval, escalate to full context only when the model says the chunks are insufficient) matches LC at 65% (Gemini) and 39% (GPT-4o) lower cost.
- Limitations: text QA; 2024 models; the opposite direction from source 7 on code.
- For orchestrate: the contradiction with LongCodeBench is informative: when the task is answering from prose, full context wins; when it is repairing code, retrieval wins. Routing (retrieve first, widen only on an explicit "insufficient" signal) is the shape that worked in both.

### 15. Liu, Xu, McAuley (2024). RepoBench: Benchmarking Repository-Level Code Auto-Completion Systems

- Citation: ICLR 2024; arXiv:2306.03091. https://arxiv.org/abs/2306.03091.
- Design: benchmark across retrieval settings. Grade B.
- Results (RepoBench-P, Codex, Python exact match / edit similarity): no cross-file context 33.15 / 72.20; random cross-file snippet 34.15 / 72.72; gold snippet 35.79 / 73.59; UniXcoder-retrieved snippets 37.11 / 74.32. Java 40.40 → 41.62 (gold). Placing the most similar snippets adjacent to the completion point improves results.
- Limitations: next-line completion, 2023 models; effects of 2 to 4 pp.
- For orchestrate: relevant cross-file context helps completion, and placement next to the point of use matters (consistent with source 3). The effect is small compared with the harm from over-long context in sources 6 to 8, so retrieval should be targeted rather than generous.

### 16. Chintalapati, Raskar, Acharya, Willard, Emami, Horawalavithana (2026). Evaluating Memory Condensation Strategies for Coding Agents in Data-Driven Scientific Discovery

- Citation: arXiv:2605.18854 (13 May 2026). https://arxiv.org/abs/2605.18854.
- Design: eight OpenHands condensers on 60 DiscoveryBench tasks with GPT-4o; 480 evaluations. Grade B.
- Results: LLM-based condensers increased total token use by 24 to 94% (the summarisation calls cost more than they save); masking tool-call outputs gave an 8.6% net saving; no condenser significantly changed hypothesis quality; the best condenser varied by domain and task length.
- Limitations: one model; data-science tasks, not SWE-bench; abstract-level figures.
- For orchestrate: a second independent result that LLM summarisation can cost more tokens than it saves, and that masking tool output is the only strategy with a reliable saving.

### 17. Kumar (2026). SWE-PRBench: Benchmarking AI Code Review Quality Against Pull Request Feedback

- Citation: arXiv:2603.26130 (27 Mar 2026). https://arxiv.org/abs/2603.26130. Cross-reference to the reviewer-framing area; cited here for its context result only.
- Design: benchmark with three frozen context configurations; LLM judge (κ = 0.75). Grade B.
- Results: 350 PRs, 8 frontier models; diff-only detection of human-flagged issues 15 to 31%; all 8 models degrade monotonically from diff-only to diff-plus-files to full context, even with AST-extracted and import-graph context; a 2,000-token diff-with-summary prompt beat a 2,500-token full-context prompt for all 8 models.
- Limitations: abstract-level figures; judge-based scoring.
- For orchestrate: reviewer context should be the diff plus a short summary, widened only by the reviewer's own targeted reads. It argues against the S/M/L tiers adding whole-file context as the tier grows.

### 18. Hong, Troynikov, Huber (2025). Context Rot: How Increasing Input Tokens Impacts LLM Performance

- Citation: Chroma technical report, 14 Jul 2025. https://www.trychroma.com/research/context-rot.
- Design: industry measurement across 18 models (5 Anthropic, 7 OpenAI, 3 Google, 3 Qwen), 194,480 calls; figures mostly in charts. Grade C.
- Results: LongMemEval, 306 prompts at about 113K tokens (full) vs about 300 tokens (only the relevant turns): the focused input scores significantly higher for every model, with the largest gap in the Claude family, mainly from abstentions; one distractor lowers accuracy and four lower it further; needle position has no notable effect but needle-question semantic similarity does; shuffled haystacks beat coherent ones for all 18 models; refusals 0.035% of calls.
- Limitations: simple tasks, two haystack topics, exact drops only in charts.
- For orchestrate: the same question answered from a 300-token focused context beats a 113K context that contains the same facts. It is the strongest cross-vendor argument for the orchestrator holding decisions only and asking the scout for facts rather than keeping facts in its own context.

### 19. Anthropic Applied AI (2025). Effective context engineering for AI agents

- Citation: Rajasekaran, Dixon, Ryan, Hadfield; Anthropic engineering post, 29 Sep 2025. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents. Grade C.
- Content: recall from context decreases as the window grows, in every model, as a gradient not a cliff. Recommendations: system prompts at the "right altitude" with a minimal but complete set of instructions; few canonical examples over edge-case lists; lightweight identifiers (paths, queries) with just-in-time loading over pre-loaded data (Claude Code pre-loads CLAUDE.md and retrieves with glob and grep); tool-result clearing as the lightest compaction; Claude Code's compaction keeps architectural decisions, unresolved bugs and implementation details, drops redundant tool outputs, and carries the five most recently accessed files; structured note-taking outside the window (Claude Code's to-do list, a NOTES.md, the Pokémon agent re-reading its notes after resets); sub-agents work in clean windows and return 1,000 to 2,000 token summaries.
- For orchestrate: the vendor's own description of what survives compaction is what a worker's draft PR and run-log report must duplicate, because compaction is lossy by design.

### 20. Anthropic (2025). How we built our multi-agent research system

- Citation: Anthropic engineering post, 13 Jun 2025. https://www.anthropic.com/engineering/multi-agent-research-system. Grade C.
- Content: Opus 4 lead with Sonnet 4 subagents beat single-agent Opus 4 by 90.2% on an internal research eval; on BrowseComp three factors explain 95% of performance variance and token usage alone 80%; agents use about 4× chat tokens and multi-agent systems about 15×; the lead saves its plan to memory because the window is truncated past 200K; when limits approach, agents "spawn fresh subagents with clean contexts while maintaining continuity through careful handoffs"; domains where every agent needs the same context, or with many inter-agent dependencies, are a poor fit.
- For orchestrate: parallel workers with their own windows are the fit case the post names (independent tickets); the "handoff on limit" pattern is the fresh-worker paradigm.

### 21. Anthropic (2025). Writing effective tools for agents, and Code execution with MCP

- Citation: Anthropic engineering posts, 11 Sep 2025, https://www.anthropic.com/engineering/writing-tools-for-agents; and Jones and Kelly, 4 Nov 2025, https://www.anthropic.com/engineering/code-execution-with-mcp. Grade C.
- Content: Claude Code truncates tool responses at 25,000 tokens by default; a concise Slack response was 72 tokens against 206 detailed; tool-description refinements took Sonnet 3.5 to state of the art on SWE-bench Verified (no figure given); namespacing reduces confusion with many tools and prefix-vs-suffix effects vary by model. MCP as code: loading only needed tool definitions and processing intermediate results outside the model cut one workflow from 150,000 to 2,000 tokens (98.7%).
- For orchestrate: the plugin's scripts are already the "code, not tool calls" pattern; every script's output should be bounded and summarised at the source rather than paged through the model.

### 22. Claude Code docs: memory, costs, prompt caching, agent skills

- Citation: https://code.claude.com/docs/en/memory, https://code.claude.com/docs/en/costs, https://code.claude.com/docs/en/prompt-caching (fetched 2026-11-10); Anthropic, Equipping agents for the real world with Agent Skills, 16 Oct 2025, https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills. Grade C.
- Content: CLAUDE.md "target under 200 lines ... Longer files consume more context and reduce adherence"; imports do not reduce cost; auto memory loads the first 200 lines or 25 KB every session; CLAUDE.md edits do not apply mid-session until `/clear`, `/compact` or restart; nested CLAUDE.md and path-scoped rules load on demand; skills load name and description only until invoked; MCP tool definitions are deferred by default; `/usage` recommends "Trim CLAUDE.md" when it costs tens of thousands of tokens per request (example: 49k); compaction replaces history and rebuilds only the conversation cache layer, the summarisation call reads the warm prefix; subagents get a 5-minute cache TTL by default; agent teams use about 7× the tokens of a standard session; hooks can filter tool output before the model sees it.
- For orchestrate: these are the levers the plugin can actually pull: short CLAUDE.md, instructions in skills, bounded script output, hooks that filter, and a restart after any instruction-file edit.

### 23. Smith (2025). OpenHands Context Condensation for More Efficient AI Agents

- Citation: OpenHands blog, 9 Apr 2025. https://www.openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents. Grade C.
- Content: an LLM condenser that summarises older events when the context reaches a size threshold, keeping goals, progress, remaining work, critical files and failing tests. On a SWE-bench Verified subset: 54% resolved with condensation vs 53% without; per-turn cost "less than half" the baseline once condensation starts; turn counts rise. Subset size and exact savings not given.
- For orchestrate: the production summariser that source 2 shows is no better than masking; its own numbers show parity on resolve rate and a cost cut, which is the honest claim for compaction.

### 24. Yan (2025). Don't Build Multi-Agents

- Citation: Cognition blog, 12 Jun 2025. https://cognition.com/blog/dont-build-multi-agents. Grade C (position piece, no measurement).
- Content: share full agent traces, not summaries, because actions carry implicit decisions and conflicting decisions produce bad results; for long tasks use a dedicated fine-tuned model to compress history into key events and decisions; the author says this does not remove the limit.
- For orchestrate: the counter-position to source 20. The two agree on one thing: whatever crosses a context boundary must carry decisions, not just facts. orchestrate's worker report and draft PR are that record.

### 25. Chatlatanagulchai et al. (2025) and Jiang and Nam (2026): what context files contain

- Citation: Chatlatanagulchai, Li, Kashiwa, Reid, Thonglek, Leelaprute, Rungsawang, Manaskasemsak, Adams, Hassan, Iida. Agent READMEs: An Empirical Study of Context Files for Agentic Coding. arXiv:2511.12884 (v2, 9 Aug 2026), https://arxiv.org/abs/2511.12884. Jiang, Nam. Beyond the Prompt: An Empirical Study of Cursor Rules. MSR 2026; arXiv:2512.18925, https://arxiv.org/abs/2512.18925.
- Design: descriptive mining studies, 2,303 files from 1,925 repos and 401 repos respectively; no outcome measured. Grade B (field datasets) for content, no effect sizes.
- Results: context files cover test procedures (75.9%), implementation details (70.8%) and architecture (68.1%); security 14.8% and performance 14.5%; they evolve through frequent small additions. Cursor rules fall into five themes (conventions, guidelines, project information, LLM directives, examples), with many copied from templates.
- For orchestrate: developers mostly write the overview and architecture content that source 1 found useless and omit the non-standard constraints it found useful. Setup should ask for commands, conventions and prohibitions, not a repository tour.

### 26. Internal: fresh-context passes and the Issho session (2026)

- Citation: docs/research/fresh-context-passes.md on branch `research/fresh-context-passes` (https://github.com/shrujancheruku/claude-orchestrate/tree/research/fresh-context-passes), from the Issho orchestrator transcript `44e00c3b`. Grade C (single system's field observation).
- Content: 772 orchestrator turns, mean context 217K, max 367K; four compactions at about 367K each dropping to 30 to 45K; one cold re-warm wrote 340K uncached tokens after a 6 h gap; a replay simulation shows lowering the auto-compact window from 400K to 150 to 200K would have cut input-side cost 25 to 30% with no change to worker reachability, while a fresh session per pass would have been no cheaper and would have lost the workers.
- For orchestrate: the local measurement that connects the benchmark evidence to this plugin's cost line.

## Implementable findings

| id | finding | grade | source | for orchestrate |
|---|---|---|---|---|
| CX-01 | Repository context files (AGENTS.md/CLAUDE.md) do not raise task success: −0.5 to −2 pp for LLM-written, +2.4 pp for developer-written (p ≥ 0.21), across 3 agents and 4 models on 438 tasks. | A | Gloaguen 2026 | reject: do not claim or expect a success gain from the plugin's CLAUDE.md or orchestration.md content; justify every line by a measured failure it prevents |
| CX-02 | Context files raise cost: +20 to 23% inference cost and +2.5 to 3.9 steps per task (permutation p ≤ 0.0126). | A | Gloaguen 2026 | measure: `--profile` should attribute per-request prompt size to CLAUDE.md and config so the cost of each instruction file is visible |
| CX-03 | Agents follow context-file instructions (named tool used 1.6× per task when mentioned vs < 0.01 otherwise), but repository overviews do not shorten the path to the right files. | A | Gloaguen 2026 | default: setup writes commands, conventions and prohibitions into orchestration.md and omits repository overviews; the scout answers "where" questions at run time |
| CX-04 | With AGENTS.md present, Codex runs were 20% faster (mean) and used 20% fewer output tokens (Wilcoxon p < 0.05) on 124 paired PR tasks; total tokens unchanged (median −1.3%, n.s.). | A | Lulla 2026 | measure: log worker wall-clock and output tokens per ticket with and without the config, since speed, not success, is the measurable benefit |
| CX-05 | Developer-written files beat LLM-generated files (p = 0.038); LLM-generated files help only when the repository has no documentation (+2.7%). | A | Gloaguen 2026 | rule: setup must not auto-generate a repository overview into CLAUDE.md; it may only propose entries the developer confirms |
| CX-06 | Masking old tool observations halves agent cost (−50 to −57% on 4 of 5 model configurations) at equal or better solve rate (e.g. 53.4% → 54.8%). | B | Lindenbauer 2025 | default: workers run with tool-result clearing on and prefer bounded-output tools; measure the observation share of worker turns |
| CX-07 | LLM summarisation is not better than observation masking (within ±1 pp on 4 of 5 configurations, −5 pp on Gemini 2.5 Flash thinking) and in another setting costs 24 to 94% more tokens than it saves. | B | Lindenbauer 2025; Chintalapati 2026 | reject: do not add a plugin-level LLM summariser of worker or orchestrator history; rely on masking, hand-offs and written state |
| CX-08 | Keeping the full interaction history lowers SWE-bench Lite resolution from 18.0% to 15.0%; showing whole files instead of a 100-line window lowers it to 12.7%. | B | Yang 2024 | rule: worker and reviewer prompts never inline whole files; they name paths and let the agent read windows |
| CX-09 | Code repair collapses with context length (Claude 3.5 Sonnet 29% at 32K → 3% at 256K) while retrieval over the same files stays flat (23 → 21%) and beats whole-context by 18 pp at 256K. | B | Rando 2025 | default: workers get the ticket, paths and acceptance criteria, never a repository dump; a whole-repo context mode is a reject |
| CX-10 | Full context beats retrieval on prose QA (49.70 vs 37.33 for Gemini 1.5 Pro) and a retrieve-first, escalate-on-"insufficient" router matches it at 39 to 65% lower cost. | B | Li 2024 | flag: a scout mode that answers from targeted reads first and widens to larger reads only on an explicit insufficiency signal |
| CX-11 | Information in the middle of a long prompt is used worst: 53.8% at position 10 of 20 vs 75.8% first and 63.2% last, below the 56.1% closed-book baseline. | A | Liu 2024 | rule: brief and review templates put the task and acceptance criteria first and the instruction last, with reference material between |
| CX-12 | Latent-association recall halves by 32K tokens for 11 of 13 models (Claude 3.5 Sonnet 87.5 → 29.8, effective length 4K); literal matches rescue it. | A | Modarressi 2025 | default: the scout returns exact paths, symbols and line ranges so the worker's task becomes literal matching |
| CX-13 | The same facts in a 300-token focused prompt beat a 113K-token prompt containing them, for all 18 models tested; shuffled haystacks beat coherent ones. | C | Hong 2025 | default: the orchestrator keeps decisions only and re-derives facts through scripts and the scout each pass (current design) |
| CX-14 | Delivering a task over several turns instead of one complete message drops performance 39% on average and raises unreliability 112%; concatenating the pieces recovers 95% of the single-turn score. | A | Laban 2025 | default: a review round re-sends the complete task (ticket, criteria, all open review comments) in one message rather than an incremental note |
| CX-15 | A context containing the model's own earlier errors lowers later accuracy (self-conditioning), and scaling does not remove it; thinking models are largely immune. | B | Sinha 2026 | rule: after a second failed verification the worker is replaced by a fresh worker with a written summary; the plugin's third-round stop is the same mechanism |
| CX-16 | Fresh sub-agents with clean contexts and written handoffs are the vendor's pattern when limits approach; token usage explains 80% of performance variance on BrowseComp. | C | Anthropic 2025 (multi-agent) | flag: fresh-worker handoff on a context threshold, with the draft PR and run-log report as the handoff record |
| CX-17 | Sharing full traces rather than summaries across agents is the counter-position; conflicting implicit decisions between agents produce bad results. | C | Yan 2025 | measure: track rework caused by a worker contradicting an earlier decision; both positions agree decisions must cross the boundary explicitly |
| CX-18 | Bounded search/view/replace tools over bash raise run-to-run consistency for all three models (pass^5 0.046 → 0.106 for Qwen3Coder-30B) while adding no information; they raised Sonnet 4.5's tokens 2,140K → 2,576K. | B | Xu 2026 | measure: compare worker pass^k and tokens with Claude Code's built-in tools vs bash-heavy use before restricting either |
| CX-19 | A grep-backed natural-language search sub-agent widens exploration (recall +0.05 to +0.06) at a precision cost (−0.05 to −0.10). | B | Xu 2026 | rule: scout answers are paths and symbols with a confidence note, not narrative, and the worker verifies before editing |
| CX-20 | Cross-session memory helps only with the right short summary: oracle summary 34.34% vs 26.26% baseline; a wrongly retrieved summary drops to 22.22%; raw trajectories add cost for no gain. | B | Zhu 2026 | flag: ticket-keyed reuse of prior worker reports for the same files, selected deterministically, never free-text retrieval over transcripts |
| CX-21 | A gated experience memory gained +2.7 pp (not significant, p = 0.22) and −17% tokens on Sonnet 4.5, helped weaker models by +4 pp, and hurt the strongest (47.5% → 39.4%); loading memory up front did nothing. | B | Chen 2026 | rule: any memory feature recalls on a struggle signal, is measured per model, and defaults off for the strongest models |
| CX-22 | More review context lowers defect detection monotonically for all 8 models; a 2,000-token diff-plus-summary beat a 2,500-token full-context prompt. | B | Kumar 2026 | rule: reviewer tiers scale the reviewer's effort and model, not the amount of pre-loaded context; the diff plus a summary is the prompt at every tier |
| CX-23 | Claude Code loads CLAUDE.md and the first 200 lines or 25 KB of auto memory every session; the docs set a 200-line target because longer files "reduce adherence"; edits apply only after `/clear`, `/compact` or restart. | C | Claude Code docs | rule: the plugin's CLAUDE.md and setup-written files stay under 200 lines; detailed procedure lives in skills; a config edit mid-run is announced as taking effect at the next session |
| CX-24 | Tool and skill definitions cost context only when loaded: skills expose name and description until invoked, MCP tools are deferred, and moving tool definitions and intermediate results out of the model cut one workflow 150,000 → 2,000 tokens. | C | Anthropic 2025 (skills, MCP); Claude Code docs | default: orchestrate logic lives in scripts that return bounded summaries; no new MCP servers; skills carry the long instructions |
| CX-25 | Compaction summarises lossily (keeps decisions, open bugs, recent files; drops tool outputs) and rebuilds the conversation cache; a production condenser scored 54% vs 53% at under half the per-turn cost. | C | Anthropic 2025 (context engineering); Smith 2025 | default: every fact the orchestrator will need after compaction is already in GitHub or the run log before compaction can fire |
| CX-26 | Lowering the orchestrator's auto-compact window from 400K to 150 to 200K would have cut input-side cost 25 to 30% in a 772-turn session with no loss of worker reachability; a fresh session per pass would not have been cheaper. | C | internal (fresh-context-passes) | flag: recommend a 150 to 200K auto-compact window in setup output; fresh-session passes are a reject until workers live outside the orchestrator's conversation |
| CX-27 | Detecting a 10 pp effect of a prompt or instruction-file change at 80% power needs 120 to 200 tasks; 17 tasks × 3 repeats cannot see less than 30 pp. | B | Khatri 2026 | measure: in-house A/Bs of brief or config wording are reported with a power statement or not reported as effects |
| CX-28 | Developers fill context files with architecture (68.1%) and implementation detail (70.8%) and rarely with constraints such as security (14.8%), the inverse of what source 1 found useful. | B | Chatlatanagulchai 2026 | default: setup's questions ask for prohibitions, risk paths and non-standard commands, and the template has no architecture section |

## Candidate paradigms

- **Observation masking (compaction-lite).** Drop or truncate tool outputs older than a window; keep the model's own messages. Backed by CX-06, CX-07, CX-08, CX-25. Best grade B.
- **LLM-summary compaction at a threshold.** Claude Code's default; parity on success, cost cut from the shorter window, no advantage over masking. Backed by CX-07, CX-25, CX-26. Best grade B (as a non-superior option).
- **Fresh-context handoff.** Replace a worker after a failure count or context threshold with a new one briefed from the draft PR and run-log report. Backed by CX-14, CX-15, CX-16, CX-26. Best grade A (CX-14) for the mechanism, C for the handoff design.
- **Single shared trace.** Keep one long-lived worker and compress with a dedicated model; the Cognition position. Backed only by CX-17 (C) and contradicted by CX-14 and CX-15.
- **Just-in-time retrieval over pre-loading.** Workers get identifiers and read on demand; no repository dumps. Backed by CX-09, CX-11, CX-12, CX-13. Best grade A.
- **Retrieve-first router.** Answer from targeted reads, widen only on an explicit insufficiency signal. Backed by CX-10 (B).
- **Minimal instruction files.** Conventions, commands and prohibitions only; no overviews; under 200 lines; developer-confirmed. Backed by CX-01, CX-03, CX-05, CX-23, CX-28. Best grade A.
- **Progressive disclosure of tools and skills.** Long instructions in skills, logic in scripts with bounded output, no upfront tool definitions. Backed by CX-24 (C) and CX-06 (B).
- **Diff-bounded review context.** Reviewer prompt is the diff plus a summary at every tier; the reviewer reads more only on its own initiative. Backed by CX-22, CX-13. Best grade B.
- **Bounded atomic tools.** Prefer Read/Grep/Edit-style tools over free bash for consistency; measure before enforcing. Backed by CX-18, CX-19 (B).
- **Keyed experience memory.** Deterministically selected short summaries of prior work on the same files, recalled on a struggle signal, off for the strongest models. Backed by CX-20, CX-21 (B).
- **Externalised state (decisions in GitHub, facts re-derived).** The orchestrator's current design. Backed by CX-13, CX-25, CX-17. Best grade C for the pattern, A for the degradation it avoids (CX-11, CX-12).

## Open questions

1. No study isolates fresh-vs-continued sessions for coding agents on the same task set; CX-14 and CX-15 are mechanisms, not a direct measurement. A paired run of "rework in the same worker" vs "new worker from the draft PR" is the missing experiment.
2. Instruction-file studies cover Python repositories and 2025 to 2026 model snapshots; whether orchestration-specific config (slots, gates, merge rules) behaves like a repository overview or like a convention is untested.
3. The observation-masking window must be re-tuned per scaffold (source 2); the right window for a Claude Code worker is unknown, and Claude Code does not expose it to a plugin.
4. Memory results are from one benchmark lineage (SWE-ContextBench) with one or three runs; whether the same-model harm (REM on Opus 4.8) generalises is unknown.
5. Atomic tools raised consistency but also raised Sonnet 4.5's tokens by 20%; the cost-vs-consistency trade for current Claude models is unmeasured.
6. Long-context degradation figures are from 2024 to 2025 models; GPT-4.1 showed no LongCodeQA decline to 1M, so the curve is moving and should be re-checked per model generation.
7. The 80%-of-variance token-usage result is a research task, not coding; whether worker tokens predict worker success on this backlog is a `--profile` question.

## Sources list

- Gloaguen, T., Mündler-Sasahara, N., Müller, M. N., Raychev, V., & Vechev, M. (2026). Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents? arXiv:2602.11988. https://arxiv.org/abs/2602.11988
- Lindenbauer, T., Slinko, I., Felder, L., Bogomolov, E., & Zharov, Y. (2025). The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management. DL4C workshop, NeurIPS 2025; arXiv:2508.21433. https://arxiv.org/abs/2508.21433
- Liu, N. F., Lin, K., Hewitt, J., Paranjape, A., Bevilacqua, M., Petroni, F., & Liang, P. (2024). Lost in the Middle: How Language Models Use Long Contexts. TACL 12; arXiv:2307.03172. https://arxiv.org/abs/2307.03172
- Laban, P., Hayashi, H., Zhou, Y., & Neville, J. (2025). LLMs Get Lost In Multi-Turn Conversation. arXiv:2505.06120. https://arxiv.org/abs/2505.06120
- Lulla, J. L., Mohsenimofidi, S., Galster, M., Zhang, J. M., Baltes, S., & Treude, C. (2026). On the Impact of AGENTS.md Files on the Efficiency of AI Coding Agents. JAWs 2026; arXiv:2601.20404. https://arxiv.org/abs/2601.20404
- Modarressi, A., Deilamsalehy, H., Dernoncourt, F., Bui, T., Rossi, R., Yoon, S., & Schütze, H. (2025). NoLiMa: Long-Context Evaluation Beyond Literal Matching. ICML 2025, PMLR 267; arXiv:2502.05167. https://arxiv.org/abs/2502.05167
- Rando, S., Kyuragi, Y., Romani, L., Sampieri, A., Franco, L., Galasso, F., Yang, J., & Hashimoto, T. (2025). LongCodeBench: Evaluating Coding LLMs at 1M Context Windows. arXiv:2505.07897. https://arxiv.org/abs/2505.07897
- Yang, J., Jimenez, C. E., Wettig, A., Lieret, K., Yao, S., Narasimhan, K., & Press, O. (2024). SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering. NeurIPS 2024; arXiv:2405.15793. https://arxiv.org/abs/2405.15793
- Khatri, P. (2026). Do Context Files Help Coding Agents? A Two-Agent Ablation Study on Real Repositories. arXiv:2607.27250. https://arxiv.org/abs/2607.27250
- Xu, X., Saghir, H., Wu, Q., Côté, M.-A., Wang, T., Lakkaraju, K., Pei, K., & Zhang, X. (2026). The Devil Is in the Interface: Evaluating How Tool Architecture Shapes Coding Agent Behavior. arXiv:2608.11386. https://arxiv.org/abs/2608.11386
- Zhu, J., Hu, M., & Wu, J. (2026). SWE Context Bench: A Benchmark for Context Learning in Coding. arXiv:2602.08316. https://arxiv.org/abs/2602.08316
- Chen, X. L., Herrero-Vidal, P., Chen, X., Yu, X., Liu, C., Song, B., Xu, B., Khan, S. A., & Chen, Y.-L. (2026). REM: What to Store, When to Recall, What to Forget in Experience Memory for Coding Agents. CLEA workshop; arXiv:2610.27377. https://arxiv.org/abs/2610.27377
- Sinha, A., Arun, A., Goel, S., Staab, S., & Geiping, J. (2026). The Illusion of Diminishing Returns: Measuring Long Horizon Execution in LLMs. arXiv:2509.09677. https://arxiv.org/abs/2509.09677
- Li, Z., Li, C., Zhang, M., Mei, Q., & Bendersky, M. (2024). Retrieval Augmented Generation or Long-Context LLMs? A Comprehensive Study and Hybrid Approach. EMNLP 2024 Industry Track; arXiv:2407.16833. https://arxiv.org/abs/2407.16833
- Liu, T., Xu, C., & McAuley, J. (2024). RepoBench: Benchmarking Repository-Level Code Auto-Completion Systems. ICLR 2024; arXiv:2306.03091. https://arxiv.org/abs/2306.03091
- Chintalapati, R., Raskar, S., Acharya, A., Willard, J., Emami, P., & Horawalavithana, S. (2026). Evaluating Memory Condensation Strategies for Coding Agents in Data-Driven Scientific Discovery. arXiv:2605.18854. https://arxiv.org/abs/2605.18854
- Kumar, D. (2026). SWE-PRBench: Benchmarking AI Code Review Quality Against Pull Request Feedback. arXiv:2603.26130. https://arxiv.org/abs/2603.26130
- Hong, K., Troynikov, A., & Huber, J. (2025). Context Rot: How Increasing Input Tokens Impacts LLM Performance. Chroma technical report. https://www.trychroma.com/research/context-rot
- Rajasekaran, P., Dixon, E., Ryan, C., & Hadfield, J. (2025). Effective context engineering for AI agents. Anthropic engineering. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic (2025). How we built our multi-agent research system. Anthropic engineering. https://www.anthropic.com/engineering/multi-agent-research-system
- Anthropic (2025). Writing effective tools for agents. Anthropic engineering. https://www.anthropic.com/engineering/writing-tools-for-agents
- Jones, A., & Kelly, C. (2025). Code execution with MCP: Building more efficient agents. Anthropic engineering. https://www.anthropic.com/engineering/code-execution-with-mcp
- Anthropic (2025). Equipping agents for the real world with Agent Skills. Anthropic engineering. https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Anthropic. Claude Code documentation: How Claude remembers your project; Manage costs effectively; How Claude Code uses prompt caching. https://code.claude.com/docs/en/memory, https://code.claude.com/docs/en/costs, https://code.claude.com/docs/en/prompt-caching (fetched 2026-11-10)
- Smith, C. (2025). OpenHands Context Condensation for More Efficient AI Agents. OpenHands blog. https://www.openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents
- Yan, W. (2025). Don't Build Multi-Agents. Cognition blog. https://cognition.com/blog/dont-build-multi-agents
- Chatlatanagulchai, W., Li, H., Kashiwa, Y., Reid, B., Thonglek, K., Leelaprute, P., Rungsawang, A., Manaskasemsak, B., Adams, B., Hassan, A. E., & Iida, H. (2026). Agent READMEs: An Empirical Study of Context Files for Agentic Coding. arXiv:2511.12884. https://arxiv.org/abs/2511.12884
- Jiang, S., & Nam, D. (2026). Beyond the Prompt: An Empirical Study of Cursor Rules. MSR 2026; arXiv:2512.18925. https://arxiv.org/abs/2512.18925
- Cheruku, S. (2026). Can an orchestration pass start from a fresh context? docs/research/fresh-context-passes.md, branch research/fresh-context-passes. https://github.com/shrujancheruku/claude-orchestrate/tree/research/fresh-context-passes
- Thurlow, S. (2026). Human Oversight System (HOS), documentation and research notes, https://github.com/thurlow-research/HumanOversightSystem (software archived at https://doi.org/10.5281/zenodo.21347272), docs CC BY 4.0, code MIT. Consulted for its index of review-context papers (the SWE-PRBench pointer above); no HOS text is reproduced here.
