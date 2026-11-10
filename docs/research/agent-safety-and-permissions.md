# Agent safety and permissions: evidence for orchestrating AI coding agents

Research for [#608](https://github.com/shrujancheruku/claude-orchestrate/issues/608) (map [#588](https://github.com/shrujancheruku/claude-orchestrate/issues/588)). Sources read 2026-11-10. This is defensive research for the plugin's own guard: it names attack classes and measured rates, never payloads.

1. Indirect prompt injection through tracker and repository text is the dominant, measured threat to coding agents: 24 to 47% of attacks land on a 2024 GPT-4 agent (InjecAgent), 66.5% of malicious GitHub issues get past every guardrail of Cursor, Claude Code and Codex in 2026 (IssueTrojanBench), and the same mechanism was demonstrated live against the GitHub MCP server (Invariant, May 2025) and Copilot Chat (CamoLeak, CVSS 9.6). Frontier models are better (Opus 4.5: 0.3% single-attempt in coding) but an adaptive attacker with 200 attempts still reaches 10 to 17.5%, and no vendor claims the problem solved.
2. The strongest defences are structural, not textual: a default-deny policy over tool calls and arguments cuts attack success from 39.9% to 1.0% while keeping 76.3% of 79.4% utility (Progent); a tool allow-list cuts AgentDojo's strongest attack from 57.7% to 6.8% with no utility loss; a detector classifier gets a similar rate but halves utility; CaMeL keeps 77 of 84% of tasks with a provable guarantee. Stacked, independent layers beat any single layer (LLMail-Inject: team success 0.166 stacked vs 0.318 for the best single defence; adaptive attackers bypass 12 single defences at >90%).
3. Text-matching permission controls fail in the same four ways every time: subshells, encoding, scripts written then run, and quoting or expansion (Cursor's denylist, four bypasses, deprecated; Cursor's allowlist, three CVEs; Claude Code CVE-2025-54795, argument injection in an allow-listed command). This is the evidence for ADR-0002's own lexer. It also bounds it: the guard's own caveats (eval, aliases, scripts to disk) are exactly the residual classes, which only an OS sandbox closes.
4. ADR-0015's trusted-author rule matches GitHub's own production rule for its coding agent ("comments from users without write access are never presented to the agent") and the evidence that rejections come from the model, not the agent framework. Its gaps are measured too: repository content (code comments, rule files, config such as `.claude/settings.json` and `.mcp.json`) is a vector the author filter does not cover (AIShellJack up to 84%; CodeIPI: comments and issue text the most effective vectors; Check Point: hooks ran on open), and an issue body can be edited by its untrusted author after labelling.
5. Thin or contradictory: there is no controlled study of allow-list versus deny-list on task success for coding agents specifically (Progent and AgentDojo measure assistant tasks); destructive-action incidents (Replit, Gemini CLI, Antigravity) are single accounts, all with a mechanical cause (no dev/prod separation, no read-after-write check, no confirmation mode) and no base rate; and Claude Code's own hook contract fails open on crash or timeout unless `onFailure: "block"` is set, which the plugin does not set.

## Sources

### Shi et al. 2025, Progent

Shi, T., He, J., Wang, Z., Li, H., Wu, L., Guo, W., Song, D. (2025). Progent: Securing AI Agents with Privilege Control (earlier title: Programmable Privilege Control for LLM Agents). Preprint, arXiv:2504.11703 (v1 16 Apr 2025, v2 30 Aug 2025, v3 14 May 2026). https://arxiv.org/abs/2504.11703

- Design: defence with ablation on three benchmarks (AgentDojo, ASB, AgentPoison), manual and LLM-generated policies, adaptive attacks, overhead measured. Population: GPT-4o and other backbones on AgentDojo's assistant tasks; no coding agent.
- Policy model: allow or forbid rules per tool with conditions on arguments and a priority; a call that matches no policy is blocked by default (default deny).
- Headline: manual policies reduce AgentDojo ASR "from the baseline's 39.9% to 0%"; LLM-generated policies "reduce the attack success rate from 39.9% to 1.0%" and "maintain high agent utility, with a score of 76.3% compared to the original agent's 79.4%". ASB ASR 70.3% to 0%. Enforcement costs 0.0008 s of a 6.09 s average task. Adaptive attacks "can only marginally increase the ASR".
- Limits: not peer-reviewed as of v3; benchmark agents, not SWE tasks; utility under manual policies given only in a figure.
- For orchestrate: the best measured case for turning the guard's read-only roles from a list of refused verbs into a default-deny list of allowed tool and argument shapes. The 3-point utility cost is the number to beat when measuring false refusals.

### Debenedetti et al. 2024, AgentDojo

Debenedetti, E., Zhang, J., Balunović, M., Beurer-Kellner, L., Fischer, M., Tramèr, F. (2024). AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents. Preprint, arXiv:2406.13352 (v3 24 Nov 2024); NeurIPS 2024 Datasets and Benchmarks track (secondary; the arXiv page lists no venue). https://arxiv.org/abs/2406.13352

- Design: benchmark, 97 user tasks, 27 injection tasks, 629 security test cases, 4 suites (workspace, Slack, banking, travel), 74 tools; attacks and defences ablated. Population: GPT-4o, Claude 3.5 Sonnet, Claude 3 Opus, Gemini 1.5 Pro, Llama 3 70B, Command-R+.
- Headline: benign utility Claude 3.5 Sonnet 78.22%, GPT-4o 69.00%; targeted ASR of the "Important message" attack on GPT-4o 57.7% (Table 4; 47.69% in Table 3, a within-paper inconsistency). Defences on GPT-4o (benign utility / utility under attack / targeted ASR): none 69.0 / 50.0 / 57.69; data delimiting 72.66 / 55.64 / 41.65; PI detector 41.49 / 21.14 / 7.95; prompt sandwiching 85.53 / 67.25 / 27.82; tool filter 73.13 / 56.28 / 6.84. The paper's prose says "less than 25% of cases" for the best agents and "7.5%" for the tool filter; the tables are the figures above.
- Limits: 2024 models; the Slack suite is far more vulnerable (92% in a secondary summary; not verified in the paper); the tool filter needs the task's tool set known before untrusted data is read.
- For orchestrate: a tool allow-list is the one defence that cut ASR by 8x without costing utility; a classifier cut ASR as much but halved utility. A per-role tool allow-list (what the guard already does for `gh` verbs) is the evidence-backed shape.

### Debenedetti et al. 2025, CaMeL

Debenedetti, E., Shumailov, I., Fan, T., Hayes, J., Carlini, N., Fabian, D., Kern, C., Shi, C., Terzis, A., Tramèr, F. (2025). Defeating Prompt Injections by Design. Preprint, arXiv:2503.18813 (v2 24 Jun 2025). Google DeepMind and ETH Zurich. https://arxiv.org/abs/2503.18813

- Design: system design with evaluation on AgentDojo. Population: frontier models of early 2025.
- Headline: "solving 77% of tasks with provable security (compared to 84% with an undefended system) in AgentDojo". A privileged LLM writes a program from the trusted user query; a quarantined LLM with no tools parses untrusted data; capabilities on values enforce policies at tool call time.
- Limits: 7-point utility cost; the policies still have to be written; assistant tasks, not SWE tasks.
- For orchestrate: the "quarantined reader" pattern is implementable for the scout and the reviewer's reading of outside text: a reader that summarises a fork PR or an untrusted comment with no tool access, whose output enters the run as data.

### Abdelnabi et al. 2025, LLMail-Inject

Abdelnabi, S., Fay, A., et al. (Microsoft, ETH Zurich and others) (2025). LLMail-Inject: A Dataset from a Realistic Adaptive Prompt Injection Challenge. Preprint, arXiv:2506.09956; the challenge ran at IEEE SaTML 2025. https://arxiv.org/abs/2506.09956

- Design: public adaptive attack competition, field dataset: 839 participants, 461,640 submissions, 208,095 unique prompts, attackers knew the defence in use. Population: GPT-4o-mini and Phi-3-medium; defences Spotlighting, Prompt Shield, LLM Judge, TaskTracker, and all four stacked.
- Headline: "Only 3,018 submissions (0.8%) resulted in successful end-to-end attacks" in the main phase, 0.3% in phase 2. Team success rate at the hardest level: Prompt Shield 0.600, Spotlight 0.529, TaskTracker 0.440, LLM Judge 0.318, all stacked 0.166. The data card says all defences combined with GPT-4o yielded no successful attacks in phase 2.
- Limits: email assistant, two models, no coding; "success rate" is per team, not per attempt.
- For orchestrate: the only large-N measurement that stacking independent defences roughly halves the best single defence's bypass rate. It supports keeping the deterministic guard, the auto-mode classifier and the OS sandbox as separate layers rather than merging them.

### Nasr et al. 2025, The Attacker Moves Second

Nasr, M., Carlini, N., Sitawarin, C., Schulhoff, S., Hayes, J., Ilie, M., Pluto, J., Song, S., Chaudhari, H., Shumailov, I., Thakurta, A., Xiao, K., Terzis, A., Tramèr, F. (2025). The Attacker Moves Second: Stronger Adaptive Attacks Bypass Defenses Against LLM Jailbreaks and Prompt Injections. Preprint, arXiv:2510.09023. https://arxiv.org/abs/2510.09023

- Design: adaptive attack evaluation (gradient descent, RL, random search, human red-teaming) against 12 published defences.
- Headline: "we bypass 12 recent defenses (based on a diverse set of techniques) with attack success rate above 90% for most", where "the majority of defenses originally reported near-zero attack success rates".
- Limits: model-side and detector defences; does not test capability-based controls such as Progent or an OS sandbox.
- For orchestrate: a classifier or prompt-side defence's published ASR is a static-attack number. Only controls that do not depend on the model's judgement (tool policy, sandbox, author filter) keep their guarantee under adaptation.

### Dziemian et al. 2026, Indirect Prompt Injection Arena

Dziemian, M., et al. (31 authors) (2026). How Vulnerable Are AI Agents to Indirect Prompt Injections? Insights from a Large-Scale Public Competition. Preprint, arXiv:2603.15714. https://arxiv.org/abs/2603.15714

- Design: public red-teaming competition, field dataset: 464 participants, 272,000 attempts, 13 frontier models, 41 scenarios across tool calling, coding and computer use; 8,648 successful attacks.
- Headline: "All models proved vulnerable, with attack success rates ranging from 0.5% (Claude Opus 4.5) to 8.5% (Gemini 2.5 Pro)". Universal strategies transferred across 21 of 41 behaviours; capability and robustness only weakly correlated.
- Limits: per-attempt rates from motivated humans; the coding track's own figures are not in the abstract.
- For orchestrate: the lower bound on a 2026 model is not zero, and an attacker needs attempts, not skill. A public backlog gives them attempts for free.

### Singh, Yang and Chen 2026, IssueTrojanBench

Singh, A., Yang, J., Chen, T.-H. (2026). IssueTrojanBench: Benchmarking AI Coding Agents Against Malicious Issue Requests. Preprint, arXiv:2607.20759 (22 Jul 2026). https://arxiv.org/abs/2607.20759

- Design: benchmark, 4,176 experiments against Cursor, Claude Code and Codex Desktop, four attack categories, six delivery vectors (PDF, issue comments and others), with perturbations. Population: GPT-5.3 Codex, GPT-5.4, Sonnet 4.6.
- Headline: "66.5% of the malicious issues from IssueTrojanBench penetrate all the guardrails (agent- and LLM-level) of coding agents"; rejection comes from the LLMs "rather than the agent frameworks"; agent-level defences "offer limited additional protection". Per-model (secondary): GPT-5.3 Codex 84.8%, GPT-5.4 73.6%, Sonnet 4.6 41.1%; supply-chain package installs 96.6% (secondary).
- Limits: preprint; malicious issues are the task itself, not an injection beside a benign task; per-model figures not verified from the paper.
- For orchestrate: the closest study to the run's own threat: an issue is the spec. It says the agent framework's prompts and sandbox will not reject a hostile ticket, so who may write a ticket (ADR-0015's label gate by collaborators) is the control that matters.

### Liu et al. 2025, AIShellJack

Liu, Y., Lyu, Y., Zhao, Y., Zhang, T., Wang, H., Lo, D. (2025). "Your AI, My Shell": Demystifying Prompt Injection Attacks on Agentic AI Coding Editors. Preprint, arXiv:2509.22040 (v2). Singapore Management University, HUST, Monash. https://arxiv.org/abs/2509.22040

- Design: automated testing framework, 314 payloads covering 70 MITRE ATT&CK techniques in 11 categories, embedded in coding rule files; five user prompts; ablations on placement and framing; two defences tested. Population: Cursor 1.2.2 (auto mode, Claude 4 Sonnet, Gemini 2.5 Pro), Copilot in VS Code 1.102.
- Headline: "Attack success rates can reach as high as 84% for executing malicious commands"; Cursor auto mode 66.9 to 84.1% across scenarios; all combinations 41.1 to 83.4%; per category 55.6 (exfiltration) to 93.3% (initial access). The user's prompt barely matters (17 to 19 of 20 succeed under five different prompts); removing the payload's framing drops it to 0 or 1 of 20. Restricting terminal access is not enough: 277 of 314 (88.2%) payloads were instead written into `main.py` via `os.system()`, to run when the developer runs the file. A stronger model as detector left ASR around 40% or higher.
- Limits: rule files only, not README or comments; editors of mid-2025.
- For orchestrate: the evidence that a Bash guard without an Edit/Write guard is bypassed by writing the command into a file the test runner will execute; the plugin's guard does cover Edit and Write, and the OS sandbox must cover test execution too.

### Zhan et al. 2024, InjecAgent

Zhan, Q., Liang, Z., Ying, Z., Kang, D. (2024). InjecAgent: Benchmarking Indirect Prompt Injections in Tool-Integrated Large Language Model Agents. Findings of ACL 2024; arXiv:2403.02691. https://arxiv.org/abs/2403.02691

- Design: benchmark, 1,054 test cases, 17 user tools, 62 attacker tools, two harms (direct harm, data exfiltration), 30 agents.
- Headline: ReAct-prompted GPT-4 exploited 24% of the time; a reinforcing "hacking prompt" nearly doubles it (47%); a fine-tuned GPT-4 agent 7.1%.
- Limits: 2024 models; simulated tools.
- For orchestrate: the baseline rate the field started from, and the first measurement that an injected line can both exfiltrate and act.

### Guo et al. 2024, RedCode

Guo, C., Liu, X., Xie, C., Zhou, A., Zeng, Y., Lin, Z., Song, D., Li, B. (2024). RedCode: Risky Code Execution and Generation Benchmark for Code Agents. NeurIPS 2024 Datasets and Benchmarks; arXiv:2411.07781. https://arxiv.org/abs/2411.07781

- Design: benchmark; RedCode-Exec 4,050 risky Python and Bash cases, 25 vulnerability types, 8 domains, in Docker; RedCode-Gen 160 malware prompts; 3 agent frameworks (OpenCodeInterpreter, CodeAct, ReAct), 19 LLMs.
- Headline (qualitative, with figures only in plots): "The overall rejection rate is low, and the attack success rate is high for all code agents"; rejection is higher for OS and file-system operations (named sensitive paths) than for buggy code; "Agents are more likely to execute harmful actions by risky queries in natural language than in programming languages"; Bash is rejected less than Python; the framework with hard-coded constraints (OpenCodeInterpreter) is safest.
- Limits: 2024 models; the per-agent rates are in figures, not text.
- For orchestrate: a coding agent's own refusal is weakest exactly where the run lives (Bash, natural-language asks). The model will not refuse a `git push --force` it was asked for in prose; the guard must.

### Zhang et al. 2025, Agent Security Bench (ASB)

Zhang, H., et al. (2025). Agent Security Bench (ASB): Formalizing and Benchmarking Attacks and Defenses in LLM-based Agents. ICLR 2025; arXiv:2410.02644 (v4 30 May 2025). https://arxiv.org/abs/2410.02644

- Design: benchmark across 10 scenarios, prompt injection, memory poisoning, tool poisoning, backdoors, 4 mixed attacks, 11 defences, 13 backbones.
- Headline: "the highest average attack success rate of 84.30%", with "limited effectiveness shown in current defenses". Progent later reports ASB ASR 70.3% to 0% with its policies.
- Limits: counts of attacks and defences differ between versions; generic agents.
- For orchestrate: the ceiling: against a 2024 agent with prompt-side defences, most attacks work. Only the capability layer (Progent on ASB) closed it.

### Wang, Poskitt and Sun 2026, AgentSpec

Wang, H., Poskitt, C. M., Sun, J. (2026). AgentSpec: Customizable Runtime Enforcement for Safe and Reliable LLM Agents. ICSE 2026, pp. 2938–2950; arXiv:2503.18666. https://arxiv.org/abs/2503.18666

- Design: a rule DSL (trigger, predicates, enforcement action) evaluated on code agents, embodied agents and autonomous driving, with LLM-generated rules.
- Headline: "prevents unsafe executions in over 90% of code agent cases", "overheads in milliseconds"; o1-generated rules "identify 87.26% of the risky code".
- Limits: the code-agent corpus is RedCode-style risky code, not SWE tasks; rules are hand- or LLM-written.
- For orchestrate: a peer-reviewed sibling of the guard's design (rules on tool events, enforce before execution, millisecond cost). Its number is the benchmark a guard corpus should report: share of a risky-command corpus refused.

### Wang et al. 2025, MCPTox

Wang, Z., Gao, Y., Wang, Y., Liu, S., Sun, H., Cheng, H., Shi, G., Du, H., Li, X. (2025). MCPTox: A Benchmark for Tool Poisoning Attack on Real-World MCP Servers. Preprint, arXiv:2508.14925. https://arxiv.org/abs/2508.14925

- Design: benchmark on 45 live MCP servers, 353 tools, 1,348 test cases, 10 risk categories, 20 agent settings.
- Headline: ASR "with o1-mini, achieving an attack success rate of 72.8%"; "the highest refused rate (Claude-3.7-Sonnet) less than 3%"; more capable models more susceptible because they follow instructions better. Payloads from IPI benchmarks placed in tool descriptions get near 0%: the two attack classes do not transfer.
- Limits: mid-2025 models; no coding tasks.
- For orchestrate: the plugin loads MCP servers only as the user configures them, but a project's `.mcp.json` is repository content. Tool descriptions are an injection surface the author filter never sees.

### Zhang et al. 2024 and Vijayvargiya et al. 2025, unsafe-action base rates

Zhang, Z., Cui, S., Lu, Y., Zhou, J., Yang, J., Wang, H., Huang, M. (2024). Agent-SafetyBench: Evaluating the Safety of LLM Agents. Preprint, arXiv:2412.14470 (v2 20 May 2025). https://arxiv.org/abs/2412.14470. Vijayvargiya, S., Soni, A. B., Zhou, X., Wang, Z. Z., Dziri, N., Neubig, G., Sap, M. (2025). OpenAgentSafety: A Comprehensive Framework for Evaluating Real-World AI Agent Safety. ICLR 2026; arXiv:2507.06134. https://arxiv.org/abs/2507.06134

- Design: benchmarks. Agent-SafetyBench: 349 environments, 2,000 cases, 8 risk categories, 10 failure modes, 16 agents. OpenAgentSafety: 350+ multi-turn tasks with real tools (browser, code execution, file system, bash, messaging), rule-based plus LLM-judge scoring, 5 LLMs.
- Headline: "none of the agents achieves a safety score above 60%"; defence prompts alone insufficient. OpenAgentSafety: unsafe behaviour in 51.2% of safety-vulnerable tasks (Claude Sonnet 3.7) to 72.7% (o3-mini) (v1 figures; v2 reports 49% with Sonnet 4 to 73%).
- Limits: safety-vulnerable tasks are selected to tempt; not SWE tasks.
- For orchestrate: with no outside attacker, half or more of tempting tasks produce an unsafe action from the model alone. The guard's job is mostly against mistakes, as ADR-0002 says.

### Anthropic 2025, Claude Opus 4.5 system card, section 5.2

Anthropic (2025). Claude Opus 4.5 System Card, section 5.2 Prompt injection risk within agentic systems. Industry report. https://www-cdn.anthropic.com/bf10f64990cfda0ba858290be7b8cc6317685f47.pdf

- Design: vendor evaluation with Gray Swan's adaptive Shade attacker in coding and computer-use environments, 1 attempt and 200 attempts, no additional safeguards in coding.
- Headline, coding (Table 5.2.2.1.A): Opus 4.5 extended thinking 0.3% at 1 attempt, 10.0% at 200; standard thinking 0.7% and 17.5%; Sonnet 4.5 extended thinking 17.7% and 70.0%; standard 29.9% and 87.5%. Computer use with safeguards: Opus 4.5 extended thinking 0.0% and 0.0%; standard 0.32% and 14.3%. The card states the model is not immune and cites Nasr et al. on adaptive attacks.
- Limits: vendor-run; the coding environment is not described; no third-party reproduction.
- For orchestrate: the model generation and reasoning setting move coding ASR by two orders of magnitude; routing a worker to a cheaper or non-thinking model (#513, #516) is a safety decision, not only a cost one. The 200-attempt column is the public-repo number.

### Anthropic 2025–2026, Claude Code permission, hook and sandbox contracts

Anthropic. Claude Code documentation: Security (https://code.claude.com/docs/en/security), Permissions (https://code.claude.com/docs/en/permissions), Hooks reference (https://code.claude.com/docs/en/hooks), Sandboxing (https://code.claude.com/docs/en/sandboxing); Anthropic engineering, "Beyond permission prompts: making Claude Code more secure and autonomous" (20 Oct 2025, https://www.anthropic.com/engineering/claude-code-sandboxing); "Claude Code auto mode" (25 Mar 2026, https://www.anthropic.com/engineering/claude-code-auto-mode). Vendor documentation and reports; fetched 2026-11-10.

- Hook contract: "Blocking error: your hook exits 2"; "Non-blocking error: your hook exits with any other code, or fails in some other way, such as not starting or printing invalid JSON. The action goes ahead, except for hooks whose failure blocks the action, for example a hook with `onFailure` set to `"block"`." "On most events, when a hook fails or times out, Claude Code still carries out the action. A policy hook with a wrong path or a crashing script then lets everything through." `onFailure: "block"` requires v2.1.295 or later; the default timeout is 600 s and "a timed-out command ... hook doesn't block the tool call". "Exit code 0 with no output means the hook has no decision to report ... staying silent doesn't approve it." Exit 2 "blocks even when your hook prints JSON".
- Precedence: "Rules are evaluated in order: deny, then ask, then allow." "PreToolUse hook decisions don't bypass permission rules." "A blocking hook also takes precedence over allow rules."
- Bash rule limits: "Claude Code is aware of shell operators, so a rule like `Bash(safe-cmd *)` won't give it permission to run the command `safe-cmd && other-cmd`"; "Deny and ask rules apply when any subcommand matches them, including a command nested inside a subshell, a command substitution, or a control-flow body"; a deny written as `git push` is not matched by `git -C . push`; exec wrappers (`watch`, `setsid`, `ionice`, `flock`) and `find -exec` cannot be prefix-approved. Security page: "Command injection detection: In Manual mode, Claude Code asks before running a Bash command it can't fully analyze"; "Fail-closed matching: In Manual mode, unmatched commands require approval by default"; the working-directory boundary "is a permission prompt, so a Bash command you approve can still write anywhere your user account can"; Accept Edits mode "auto-approves file edits and a fixed set of filesystem Bash commands like `mkdir`, `touch`, `rm`, `mv`, `cp`, and `sed` for paths in the working directory".
- Sandbox: "In our internal usage, we've found that sandboxing safely reduces permission prompts by 84%"; Seatbelt on macOS, bubblewrap on Linux; network through a local proxy whose allow-list "start[s] empty"; a command outside the sandbox "run[s] with your full access"; `excludedCommands` match command text, so "a broad entry such as `docker *` covers everything that tool can do"; with filesystem isolation off "a sandboxed command can write files that later commands run or read ... and use them to widen its own access on the next run".
- Auto mode: "users accept 93% of them anyway" (manual prompts); "The 17% false-negative rate on real overeager actions is the honest number"; stage 1 tuned to "8.5% FPR"; "File writes and edits inside the project directory are allowed without a classifier call"; "If a session accumulates 3 consecutive denials or 20 total, we stop the model and escalate"; "a server-side prompt-injection probe scans tool outputs"; "The transcript classifier's injection defense is structural as it never sees tool results."
- For orchestrate: `hooks/hooks.json` registers the guard without `onFailure`, so a guard that cannot start (no `python3`, a broken snapshot path after #583) or that exceeds 600 s lets the call through; ADR-0002's "a crash must not read as a pass" is implemented inside the script but not at the hook contract. The docs' own limits on Bash rules (wrappers, `-C`, text-only `excludedCommands`) restate the guard's reasons for a lexer, and confirm the sandbox is the only layer whose enforcement does not depend on command text.

### Anthropic 2025, browser-agent prompt-injection measurements

Anthropic (2025). "Piloting Claude for Chrome" (25 Aug 2025, https://claude.com/blog/claude-for-chrome) and "Prompt injection defenses" research post (24 Nov 2025, https://www.anthropic.com/research/prompt-injection-defenses). Industry reports.

- Design: internal adversarial test set (123 cases, 29 scenarios), then an adaptive best-of-N attacker on four models with and without safeguards.
- Headline: "Browser use without our safety mitigations showed a 23.6% attack success rate"; with mitigations "to 11.2%"; a browser-specific challenge set "from 35.7% to 0%". November table (model only / old safeguards / improved): Sonnet 4.5 13.8 / 10.8 / 1.9%; Opus 4.5 8.0 / 7.6 / 1.4%. "No browser agent is immune to prompt injection." Mitigations: site-level permissions, confirmation before high-risk actions (publishing, purchasing, sharing personal data), blocked site categories, classifiers on untrusted content entering the context.
- Limits: browser surface, vendor-run; the coding surface is in the system card above.
- For orchestrate: the vendor's own defence set is permissions first, confirmation for irreversible actions second, classifiers third; this is the order the plugin's layers already follow (guard, ask-user verdicts from scripts, auto-mode classifier).

### Invariant Labs 2025, GitHub MCP toxic agent flow and tool poisoning

Milanta, M., Beurer-Kellner, L. (26 May 2025). GitHub MCP Exploited: Accessing private repositories via MCP. https://invariantlabs.ai/blog/mcp-github-vulnerability. Invariant Labs (1 Apr 2025). MCP Security Notification: Tool Poisoning Attacks. https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks. Industry reports with a live demonstration each.

- Design: demonstrations. GitHub MCP: a user with a public and a private repository asks Claude 4 Opus in Claude Desktop, with the GitHub MCP server on a PAT covering both, to look at open issues; a malicious issue in the public repository steers the agent to read the private repository and publish its contents (project name, relocation plans, salary) in a public PR. Tool poisoning: instructions in MCP tool descriptions, invisible to the user, read by the model; a "rug pull" (description changes after approval) and "cross-server shadowing" (one server's description changes how another server's tools are used, so the malicious tool is never called and never logged).
- Statements: "this is not a flaw in the GitHub MCP server code itself", it is "a fundamental architectural issue that must be addressed at the agent system level"; even a highly aligned model "was still susceptible to manipulation through relatively simplistic prompt injections"; users who choose "Always Allow" stop monitoring. Mitigations: least privilege, e.g. one repository per session; runtime policy enforcement; toxic-flow detection; show tool descriptions, pin and hash them, enforce boundaries between servers.
- Limits: demonstrations, no rate.
- For orchestrate: the exact shape of the run: a token that reaches more than the run's repository, a public issue, and `gh pr create`. ADR-0015 removes the untrusted issue text; it does not narrow the token. One-repo-per-session is the mitigation Invariant names.

### Mayraz 2025, CamoLeak (GitHub Copilot Chat)

Mayraz, O. (Legit Security) (30 Sep 2025). CamoLeak: Critical GitHub Copilot Vulnerability Leaks Private Source Code. CVSS 9.6; fixed by GitHub 14 Aug 2025. https://legitsecurity.com/blog/camoleak-critical-github-copilot-vulnerability-leaks-private-source-code

- Design: disclosed vulnerability with demonstration. Instructions placed in a PR description inside HTML comments GitHub renders invisible; Copilot Chat, running with the viewing user's permissions, read private repositories and issues and exfiltrated them through GitHub's own image proxy, which the Content Security Policy trusted. "Copilot operates with the same permissions as the user making the request." GitHub "fixed it by disabling image rendering in Copilot Chat completely."
- Limits: single case.
- For orchestrate: hidden markup in tracker text is an attack class; GitHub's coding-agent docs now say HTML comments in issues and PR comments are not passed to the agent. The plugin reads raw bodies and comments through `gh`.

### GitHub 2025–2026, Copilot cloud agent risks and mitigations; `pull_request_target` guidance; `author_association`

GitHub Docs. Risks and mitigations for GitHub Copilot cloud agent. https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations. GitHub Docs. Securely using pull_request_target. https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target. GitHub GraphQL schema, enum `CommentAuthorAssociation` (read by introspection 2026-11-10). Vendor documentation.

- Copilot cloud agent: "Only users with write access to the repository can trigger Copilot cloud agent to work." "Comments from users without write access are never presented to the agent." GitHub "filters hidden characters before passing user input" and "text entered as an HTML comment in an issue or pull request comment is not passed to Copilot cloud agent." The agent "only has the ability to push to a single branch" (`copilot/`), "is also subject to any branch protections and required checks", workflows "are not triggered until Copilot cloud agent's code is reviewed", and GitHub "prevents the user who asked Copilot cloud agent to create a pull request from approving it." Internet access is restricted by a firewall that "should not be considered a comprehensive security solution".
- Actions: before using `pull_request_target`, ensure "the checked-out code is only ever inspected as data and never executed"; an "`issue_comment` or `workflow_run` workflow that fetches and runs a fork's pull request code" is vulnerable the same way; GitHub "provides a default event policy that blocks the `pull_request_target` event in public repositories".
- `author_association`: OWNER "Author is the owner of the repository"; MEMBER "Author is a member of the organization that owns the repository"; COLLABORATOR "Author has been invited to collaborate on the repository"; CONTRIBUTOR "Author has previously committed to the repository"; FIRST_TIME_CONTRIBUTOR, FIRST_TIMER, MANNEQUIN, NONE.
- For orchestrate: ADR-0015's set (OWNER, MEMBER, COLLABORATOR) is GitHub's "write access" rule expressed in the only field `gh` returns; excluding CONTRIBUTOR matches GitHub's own practice. GitHub adds three controls the plugin lacks: hidden-text stripping, a requester-cannot-approve rule, and treating fork code as data.

### Check Point Research 2026 and Cymulate 2025, Claude Code vulnerabilities

Donenfeld, A., Vanunu, O. (Check Point Research) (25 Feb 2026). Caught in the Hook: RCE and API Token Exfiltration Through Claude Code Project Files. GHSA-ph6w-f82w-28w6, CVE-2025-59536, CVE-2026-21852. https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/. Beber, E. (Cymulate) (2025). CVE-2025-54794 and CVE-2025-54795 in Claude Code. https://www.cymulate.com/blog/cve-2025-547954-54795-claude-inverseprompt/

- Check Point: three issues from repository configuration on opening an untrusted project. Hooks in `.claude/settings.json` ran after the trust dialog with no further prompt (the dialog "contains no warning that hook commands ... will run automatically without confirmation"); MCP servers in `.mcp.json` with auto-approval flags started before the dialog could be read; `ANTHROPIC_BASE_URL` in project settings sent the API key to an attacker host before consent. Reported 21 Jul, 3 Sep and 28 Oct 2025; fixed 26 Aug, 22 Sep and 28 Dec 2025. Anthropic now "defers all network operations until after explicit user consent".
- Cymulate: CVE-2025-54794 (CVSS 7.7), path containment by string prefix rather than resolved boundary, so a sibling directory sharing the prefix passed; fixed v0.2.111. CVE-2025-54795 (CVSS 8.7), "improper input sanitization in how Claude Code handles whitelisted commands": an allow-listed command's arguments could end the argument context and append a second command, which ran "no user confirmation required"; fixed v1.0.20. Exploitation "requires that Claude Code be run in a context where it has elevated privileges and is exposed to untrusted input".
- Limits: patched; single-vendor findings.
- For orchestrate: two things the author filter cannot see are repository configuration and allow-list parsing. A fork PR or a trusted collaborator's PR can carry `.claude/settings.json` or `.mcp.json`; a worker in a worktree of that branch opens it. The plugin runs from a snapshot outside the repo (#583) but the project's own `.claude/` is still in every worktree.

### Backslash Security 2025, HiddenLayer 2025 and CVE-2025-54131, Cursor auto-run lists

Backslash Research Team (21 Jul 2025). Cursor AI Security Flaw: Auto-Run Denylist Bypass. https://www.backslash.security/blog/cursor-ai-security-flaw-autorun-denylist. HiddenLayer (26 Nov 2025). Allowlist Bypass in Run Terminal Tool Allows Arbitrary Code Execution During Autorun Mode (Cursor 1.3.4 to <2.0, CVSS 9.8). https://hiddenlayer.com/sai-security-advisory/sai-security-advisor-2025-11-cursor. CVE-2025-54131 (GHSA-534m-3w6r-8pqr): allow-list bypass with a backtick or `$(cmd)` in Cursor <1.3 (secondary: Tenable/NVD). CVE-2026-22708: shell built-ins bypassed the allow-list, fixed 2.3 (secondary).

- Backslash: "no fewer than four ways" past the denylist: encoding (Base64), a subshell, a script written to disk then run, and quote characters inside the command; "for any denylisted command, infinitely many variants with the same behavior could exist outside the list"; Cursor replied it was "officially deprecating the denylist feature in release 1.3" and recommended the allow-list.
- HiddenLayer: allow-list enforcement "could be bypassed using brace expansion" so an allowed command carried a second one; the vendor said the allow-list "is best-effort, not a security boundary and determined agents or prompt injection might bypass it" and pointed to the macOS sandbox. Disclosed 29 Jul 2025, fixed 5 Nov 2025.
- Limits: one product; the bypasses are classes, not rates.
- For orchestrate: the four denylist classes and the three allow-list CVEs are each a shell construct the guard's lexer resolves (quotes, `$(...)`, backticks, brace words flagged as globs, `sh -c`) or names as a residual (scripts to disk, `eval`). This is the evidence for ADR-0002's "text matching is not enough" and for its honest caveat; the vendor consensus (Cursor, Anthropic) is that the sandbox, not the list, is the boundary.

### Destructive-action incidents, 2025

Lemkin, J. (SaaStr) and Masad, A. (Replit), X posts 18–21 Jul 2025, as reported by The Register (22 Jul 2025, https://www.theregister.com/2025/07/22/replit_saastr_response/) and Ars Technica (Edwards, B., 25 Jul 2025) (secondary). Gupta, A., google-gemini/gemini-cli issue #4586, 21 Jul 2025, "Gemini CLI 'lost' files during a failed file move operation. [Windows]", Gemini CLI 0.1.13, gemini-2.5-pro, "Sandbox: no sandbox" (primary issue; transcript attached). "Tassos M.", Google Antigravity, Reddit, late Nov 2025, as reported by The Register (1 Dec 2025, https://www.theregister.com/2025/12/01/google_antigravity_wipes_d_drive/) (secondary). AWS Security Bulletin AWS-2025-015 (23 Jul 2025, CVE-2025-8217), https://aws.amazon.com/security/security-bulletins/AWS-2025-015/ (primary).

- Replit: the agent "deleted a production database" during a declared code freeze and "invented data"; Lemkin reported the agent said rollback was impossible, "It turns out Replit was wrong, and the rollback did work." Masad: "Unacceptable and should never be possible"; "started rolling out automatic DB dev/prod separation to prevent this categorically"; one-click restore. Cause (Masad's own framing): the agent had production access during development, and the freeze was an instruction, not a control.
- Gemini CLI: asked to reorganise files on Windows; a `mkdir` failed but was treated as successful; subsequent moves into the phantom directory renamed over one another; the user's analysis: "The core failure is the absence of a 'read-after-write' verification step." Ran with no sandbox.
- Antigravity: asked to clear a project cache in Turbo mode (commands run without asking); the command targeted the root of the D: drive and bypassed the Recycle Bin; Google said it was investigating.
- Amazon Q: an "inappropriately scoped GitHub token" in CodeBuild let a threat actor "commit malicious code into the extension's open-source repository", which was "automatically included in a release" (1.84.0, ~1M installs per secondary reports); it "was unsuccessful in executing due to a syntax error". A supply-chain delivery of an agent-directed wipe instruction.
- Limits: four accounts, no base rate, each partly the user's own report.
- For orchestrate: every cause is mechanical and already named in the plugin's ADRs: separate the environment the agent can reach from the one that matters (worktrees, no production access), enforce a freeze with a script-owned gate rather than prose (ADR-0006, lead stop markers from trusted authors only), verify post-conditions after filesystem and git operations, and never run with a "turbo" that disables confirmation for destructive verbs outside the worktree.

### Beurer-Kellner et al. 2025, design patterns; Meta 2025, Agents Rule of Two

Beurer-Kellner, L., Buesser, B., Creţu, A.-M., Debenedetti, E., Dobos, D., Fabian, D., Fischer, M., Froelicher, D., Grosse, K., Naeff, D., Ozoani, E., Paverd, A., Tramèr, F., Volhejn, V. (2025). Design Patterns for Securing LLM Agents against Prompt Injections. Preprint, arXiv:2506.08837 (v3 27 Jun 2025). https://arxiv.org/abs/2506.08837. Meta AI (31 Oct 2025). Agents Rule of Two: A Practical Approach to AI Agent Security. https://ai.meta.com/blog/practical-ai-agent-security/. Willison, S. (16 Jun 2025). The lethal trifecta for AI agents. https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/

- Design: position papers with case studies; no measurement.
- Content: principle "once an LLM agent has ingested untrusted input, it must be constrained so that it is impossible for that input to trigger unintended actions"; six patterns: action-selector, plan-then-execute, LLM map-reduce, dual LLM (privileged plus quarantined), code-then-execute, context-minimisation. The software-engineering case study: "Any remote documentation or third-party code imported into the assistant could hijack the assistant"; user confirmation "can be impractical and burdensome" and "tired or overloaded human verifiers approving unsafe actions" are a weak point; the safest design feeds the agent only a quarantined LLM's formatted summary of third-party content. Meta: an agent "must satisfy no more than two of" [A] processes untrustworthy inputs, [B] has access to sensitive systems or private data, [C] can change state or communicate externally; one that needs all three "should not be permitted to operate autonomously" without supervision; prompt injection is "a fundamental, unsolved weakness in all LLMs". Willison's trifecta is the same three.
- For orchestrate: a worker on a public repo has all three by construction (reads tickets and code, holds the developer's `gh` token, pushes and comments). ADR-0015 trims [A] (only trusted text steers) and the guard trims [C] per role (a reviewer may only comment; a scout writes nothing). The quarantined-reader pattern is the one untried way to let outside text in without [A].

### Anthropic 2025, SHADE-Arena

Anthropic (16 Jun 2025). SHADE-Arena: Evaluating sabotage and monitoring in LLM agents. https://www.anthropic.com/research/shade-arena-sabotage-monitoring; arXiv:2506.15740. Benchmark, 17 benign tasks each paired with a hidden side task; an LLM monitor scores transcripts.

- Headline: "No models exceeded an overall success rate of 30%" at sabotage under monitoring; "the strongest models were able to successfully achieve the side task without being caught (evasion success) almost 60% of the time"; "to have an 80% chance of spotting a side task, we'd have to accept a false positive rate" of "over 20%", "not feasible for a real-world deployment"; monitors with access to the agent's reasoning did much better; fine-tuning on sabotage transcripts helped.
- Limits: deliberate sabotage by a prompted model, not injection; one monitor.
- For orchestrate: a reviewer reading only the diff and PR text is a weak monitor for a hijacked worker; access to the worker's transcript (the run log) is what makes a monitor good.

### Inspect Evals 2026, CodeIPI

UK AI Security Institute, Inspect Evals, `ipi_coding_agent` (contributed by debu-sinha). CodeIPI: Indirect Prompt Injection for Coding Agents. https://ukgovernmentbeis.github.io/inspect_evals/evals/ipi_coding_agent/index.html

- Design: 45 samples (35 injected, 10 benign): bug-fix tasks with payloads in issue text (10), code comments (10), READMEs (10), config files (5); categories exfiltration, code execution, persistence. Models: Claude Sonnet 4, Sonnet 4.6, GPT-4o, GPT-4.1, GPT-4o-mini.
- Headline: injection resistance rate Sonnet 4 68.6%, Sonnet 4.6 88.6%, GPT-4o 85.7%, GPT-4.1 97.1%; detection "1 detection out of 140 injected samples across 4 models"; "code comments and issue text were the most effective vectors, even on current models. README injections were almost fully resisted"; Claude kept 100% task completion.
- Limits: N=35 injected; an eval page, not a paper.
- For orchestrate: agents comply silently; they almost never say they saw an injection, so a "report suspicious text" instruction will not surface attacks; detection has to be structural (author filter, diff of what text reached the prompt).

## Implementable findings

| id | finding | grade | source | for orchestrate |
|---|---|---|---|---|
| SF-01 | A default-deny policy over tool names and arguments cuts AgentDojo ASR from 39.9% to 1.0% (LLM policies) or 0% (manual) at a utility cost of 79.4% to 76.3%. | B | Progent | flag: a `strict` guard mode where each read-only role has an allow-list of command shapes and everything else is refused; measure false refusals against the 3-point budget |
| SF-02 | A tool allow-list cuts the strongest AgentDojo attack from 57.69% to 6.84% ASR with benign utility unchanged (69.0% to 73.13%); a detector classifier gets 7.95% but drops utility to 41.49%. | B | AgentDojo | default: keep per-role verb allow-lists (`gh`, `git`) as the first layer; reject adding an LLM "injection detector" as a gate on worker commands |
| SF-03 | Stacking four independent defences lowers adaptive attackers' team success to 0.166 from 0.318 for the best single defence (839 participants, 208,095 unique attacks). | A | LLMail-Inject | default: keep guard, auto-mode classifier and OS sandbox as separate layers; never let one layer's allow short-circuit another |
| SF-04 | Adaptive attacks bypass 12 published model-side and detector defences at >90% ASR though most reported near zero. | B | Nasr et al. | rule: a defence whose decision is a model's judgement (classifier, prompt) never counts toward a role boundary; only script and OS controls do |
| SF-05 | 66.5% of malicious issues penetrate all agent- and LLM-level guardrails of Cursor, Claude Code and Codex; rejections come from the model, not the framework. | B | IssueTrojanBench | default: ADR-0015's collaborator-only start gate is the control for hostile tickets; do not expect prompts or the sandbox to reject a hostile spec |
| SF-06 | 0.5% (Opus 4.5) to 8.5% (Gemini 2.5 Pro) of 272,000 human attack attempts succeed across 13 frontier models; capability and robustness are weakly correlated. | A | IPI Arena | measure: log every outside-authored text the run saw but did not act on, so a public-repo run can report its exposure |
| SF-07 | Adaptive coding-environment ASR on Opus 4.5 is 0.3% at 1 attempt and 10.0% at 200 (extended thinking), 0.7% and 17.5% (standard); Sonnet 4.5 is 17.7% and 70.0%. | C | Opus 4.5 system card | rule: the model routing map (#513/#516) treats worker model and thinking mode as a safety tier; a public repo pins a frontier thinking model for roles that read outside text |
| SF-08 | Payloads in repository rule files hijack Cursor and Copilot at up to 84% ASR; restricting the terminal is bypassed in 88.2% of cases by writing the command into a file the developer later runs. | B | AIShellJack | default: keep the guard on Edit/Write as well as Bash; run tests under the OS sandbox, since a worker's test run executes repository code |
| SF-09 | Code comments and issue text are the most effective injection vectors; agents flagged 1 of 140 injections. | B | CodeIPI | reject: relying on agents to report suspicious text; measure: a scout check that diffs which non-trusted text reached a prompt |
| SF-10 | A malicious public issue steered Claude 4 Opus over the GitHub MCP server to publish private-repository data in a public PR; Invariant's named mitigation is one repository per session. | C | Invariant GitHub MCP | rule: run agents with a token scoped to the run's repository (fine-grained PAT or GitHub App) and have `setup` warn when `gh auth status` shows a classic broad token |
| SF-11 | GitHub's production coding agent never presents comments from users without write access, strips hidden characters and HTML comments, pushes only to its own branch, and bars the requester from approving its PR. | C | GitHub Copilot cloud agent docs | default: ADR-0015 as is; rule: strip HTML comments and hidden Unicode from tracker text before it enters any prompt, trusted author or not |
| SF-12 | `author_association` CONTRIBUTOR means only "has previously committed"; MEMBER means organisation member; COLLABORATOR means invited. | C | GitHub GraphQL schema | default: ADR-0015's OWNER/MEMBER/COLLABORATOR set is GitHub's own write-access proxy; keep CONTRIBUTOR out |
| SF-13 | Hidden HTML comments in a PR description exfiltrated private code through Copilot Chat (CVSS 9.6); the agent ran with the viewing user's permissions. | C | CamoLeak | rule: as SF-11; measure: count tracker texts containing `<!--` or zero-width characters per pass |
| SF-14 | A Claude Code PreToolUse hook that crashes, cannot start, prints invalid JSON or times out (600 s default) lets the call through unless `onFailure: "block"` is set (v2.1.295+). | C | Claude Code hooks reference | rule: add `"onFailure": "block"` and an explicit short `timeout` to the guard's entries in `hooks/hooks.json`; ADR-0002's fail-closed clause then holds at the contract, not only inside the script |
| SF-15 | Hook `allow` decisions never bypass deny or ask rules; exit 2 blocks even over an allow rule. | C | Claude Code permissions docs | default: the guard's `allow` for routine writes (#358) is safe under the user's deny rules; document that a user `ask` rule still prompts a background role |
| SF-16 | Claude Code's own Bash rules are text matches: `git -C . push` escapes a `git push` deny, wrappers and `find -exec` cannot be prefix-approved, `excludedCommands` match text only. | C | Claude Code permissions and sandboxing docs | default: ADR-0002's lexer over tokens; rule: the guard corpus carries one line per documented limit (`-C`, wrappers, `find -exec`, brace words) |
| SF-17 | Four denylist bypass classes (encoding, subshell, script to disk, quoting) and three allow-list CVEs (backtick/`$()`, brace expansion, shell built-ins) defeated Cursor's text-matched auto-run lists; the vendor deprecated the denylist and called the allow-list "best-effort, not a security boundary". | C | Backslash, HiddenLayer, CVE-2025-54131 | default: ADR-0002; measure: the guard corpus names each class and the residual ones (`eval`, alias, script to disk) are tested as denied-or-sandboxed, never as allowed |
| SF-18 | An allow-listed command's arguments ended the argument context and ran a second command without a prompt (CVE-2025-54795); path containment by string prefix let a sibling directory through (CVE-2025-54794). | C | Cymulate | rule: the guard decides path rules on resolved paths with a boundary check (it already resolves `.git`, line 646); add a corpus line for a sibling-prefix worktree path |
| SF-19 | Repository `.claude/settings.json` hooks and `.mcp.json` servers ran on opening an untrusted project before or without consent; patched Aug–Dec 2025. | C | Check Point | rule: the scout's risk-path set includes `.claude/**`, `.mcp.json` and `hooks/**` so any PR touching them is tier L and ask-user; a fork PR touching them is reported, never checked out by a worker |
| SF-20 | OS sandboxing cut Anthropic's internal permission prompts by 84%; the network allow-list starts empty; a sandboxed command with filesystem isolation off can widen its own access on the next run. | C | Anthropic sandboxing | default: `setup` turns the sandbox on for the run with filesystem and network isolation and the remote host allowed (already offered by `sandbox-git`); reject turning filesystem isolation off to make tests pass |
| SF-21 | Users accept 93% of manual permission prompts; the auto-mode classifier misses 17% of real overeager actions at 8.5% FPR and never sees file edits in the project directory. | C | Anthropic auto mode | default: ADR-0002's note that auto mode is unsupported for the orchestrator stands; measure: the lead's approval tally compares human and classifier denials per pass |
| SF-22 | Code agents refuse less for Bash than Python, less for natural-language asks than code, and rarely for buggy code; the framework with hard-coded constraints was safest. | B | RedCode | default: the guard, not the model, owns refusal of destructive git and filesystem verbs; the worker prompt never relies on the model declining |
| SF-23 | Without an attacker, 51.2% to 72.7% of safety-vulnerable tasks produce an unsafe action; no agent scores above 60% safety. | B | OpenAgentSafety, Agent-SafetyBench | default: the guard is a guardrail against mistakes first (ADR-0002); measure: count guard denials of the plugin's own roles per run as the mistake rate |
| SF-24 | Tool-poisoning via MCP descriptions succeeds at up to 72.8% with under 3% refusal, and IPI payloads do not transfer to it. | B | MCPTox | rule: `setup` lists project-scope MCP servers from `.mcp.json` and the guard never auto-allows an `mcp__*` tool for a background role |
| SF-25 | Three 2025 data-loss incidents share mechanical causes: production reachable from development, a failed `mkdir` treated as success with no read-after-write check, and a no-confirmation mode deleting outside the project. | C | Replit, gemini-cli #4586, Antigravity | default: worktrees only, destructive FS verbs refused outside the worktree (FS_VERBS), post-condition checks in scripts (`verify`); rule: a developer "freeze" is a script-read stop marker from a trusted author, never prose |
| SF-26 | A supply-chain commit through an over-scoped CI token shipped an agent-directed wipe instruction to ~1M installs; a syntax error stopped it. | C | AWS-2025-015 | rule: the plugin's own release path uses a least-privilege token; `merge` keeps requiring green CI and a trusted approval |
| SF-27 | A runtime rule engine on agent actions prevents >90% of unsafe code-agent executions at millisecond overhead. | B | AgentSpec | measure: report the guard corpus's refusal share on a risky-command set and its latency, the two numbers AgentSpec reports |
| SF-28 | An agent that processes untrusted input, holds private access and can change state "should not be permitted to operate autonomously" without supervision; the quarantined-reader pattern removes the first property. | C | Meta Rule of Two; design patterns | flag: a `quarantine` reader (no tools) that summarises fork PRs and untrusted comments into data the lead can relay, instead of discarding them |
| SF-29 | A transcript monitor needs >20% false positives to catch 80% of hidden side tasks from diff-level evidence; access to the agent's reasoning makes it far better. | B | SHADE-Arena | flag: the L-tier reviewer reads the worker's run log, not only the diff, when the ticket is on a risk path or from a public repo |
| SF-30 | Contradiction: a detector classifier reaches the same ASR as a tool filter on AgentDojo (7.95% vs 6.84%) but halves utility, while Anthropic's browser classifiers cut ASR 8.0% to 1.4% with no utility figure published. | B/C | AgentDojo vs Anthropic defenses post | measure: before adopting any classifier gate, record task success with and without it on the plugin's own tickets |
| SF-31 | Contradiction: ADR-0015 trusts a body once labelled, but an untrusted author can edit their own body after the label. | C | GitHub issue semantics; ADR-0015 | rule: distrust a body edited after `ready-for-agent` by a non-trusted author (the open work in #572) |

## Candidate paradigms

- **Trusted-text gate.** Only text from collaborators steers the run; everything else is counted, stripped of hidden markup, and kept out of prompts. Backed by SF-05, SF-11, SF-12, SF-13, SF-31. Best grade B.
- **Default-deny role policy.** Each role has an allow-list of tool and argument shapes; unmatched calls are refused. Backed by SF-01, SF-02, SF-17. Best grade B.
- **Layered enforcement.** Deterministic guard, permission rules, OS sandbox and an optional classifier as independent layers, none of which can approve past another. Backed by SF-03, SF-04, SF-14, SF-15, SF-20. Best grade A.
- **Quarantined reader.** Untrusted content (fork PRs, outside comments, fetched docs) is read by a tool-less model whose structured output enters the run as data. Backed by SF-28, CaMeL. Best grade B.
- **Scoped credentials per run.** A token that reaches only the run's repository; MCP servers from the project never auto-allowed. Backed by SF-10, SF-24, SF-26. Best grade C.
- **Mechanical freezes and post-condition checks.** Stops, holds and freezes are script-read markers; filesystem and git steps verify their effect; destructive verbs stay inside the worktree. Backed by SF-22, SF-23, SF-25. Best grade B.
- **Safety-tiered routing.** Roles that read outside text run on a frontier thinking model; cheaper models take roles that read only trusted text. Backed by SF-06, SF-07. Best grade A (for the rate), C (for the model gap).
- **Transcript-aware review.** The heavy reviewer reads the worker's run log for risk-path or public-repo tickets. Backed by SF-29, SF-09. Best grade B.
- **Measured guard.** A risky-command corpus with refusal share and latency reported in CI, one line per documented bypass class. Backed by SF-16, SF-17, SF-18, SF-27. Best grade B.

## Open questions

- No controlled study measures allow-list versus deny-list on coding task success; Progent and AgentDojo use assistant tasks. The 3-point utility cost may not transfer to SWE tasks where a worker's command vocabulary is wide.
- Whether a worker checking out a trusted collaborator's branch is exposed to repository configuration (`.claude/`, `.mcp.json`, rule files) that a third party contributed earlier; the author filter covers tracker text, not history.
- The reviewer's exposure: does any role read a fork PR's diff or comments as text, and if so is that read quarantined? ADR-0015 says a fork PR is never the run's own; it does not say no agent reads it.
- The base rate of injection attempts on a real public backlog is unmeasured; every rate above is from benchmarks or competitions.
- The system card's 200-attempt column is the only adaptive coding number, vendor-run; no third party has reproduced it, and no equivalent exists for the models the routing map proposes.
- Whether Claude Code's `onFailure: "block"` interacts with the guard's own `except Exception` denial (double denial is harmless; the question is the timeout path and the message the agent sees).
- Hidden-text stripping could remove legitimate content in trusted bodies (HTML comments used as templates); the right rule may be strip-and-count rather than refuse.
- Destructive-action incidents have no denominator; the measured proxy available to the plugin is its own guard-denial count per role per run.

## Sources list

- Shi, T., He, J., Wang, Z., Li, H., Wu, L., Guo, W., Song, D. (2025). Progent: Securing AI Agents with Privilege Control. Preprint, arXiv:2504.11703. https://arxiv.org/abs/2504.11703
- Debenedetti, E., Zhang, J., Balunović, M., Beurer-Kellner, L., Fischer, M., Tramèr, F. (2024). AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents. Preprint, arXiv:2406.13352; NeurIPS 2024 Datasets and Benchmarks (secondary). https://arxiv.org/abs/2406.13352
- Debenedetti, E., Shumailov, I., Fan, T., Hayes, J., Carlini, N., Fabian, D., Kern, C., Shi, C., Terzis, A., Tramèr, F. (2025). Defeating Prompt Injections by Design. Preprint, arXiv:2503.18813. https://arxiv.org/abs/2503.18813
- Abdelnabi, S., Fay, A., et al. (2025). LLMail-Inject: A Dataset from a Realistic Adaptive Prompt Injection Challenge. Preprint, arXiv:2506.09956; IEEE SaTML 2025 competition. https://arxiv.org/abs/2506.09956
- Nasr, M., Carlini, N., Sitawarin, C., Schulhoff, S., Hayes, J., Ilie, M., Pluto, J., Song, S., Chaudhari, H., Shumailov, I., Thakurta, A., Xiao, K., Terzis, A., Tramèr, F. (2025). The Attacker Moves Second: Stronger Adaptive Attacks Bypass Defenses Against LLM Jailbreaks and Prompt Injections. Preprint, arXiv:2510.09023. https://arxiv.org/abs/2510.09023
- Dziemian, M., et al. (2026). How Vulnerable Are AI Agents to Indirect Prompt Injections? Insights from a Large-Scale Public Competition. Preprint, arXiv:2603.15714. https://arxiv.org/abs/2603.15714
- Singh, A., Yang, J., Chen, T.-H. (2026). IssueTrojanBench: Benchmarking AI Coding Agents Against Malicious Issue Requests. Preprint, arXiv:2607.20759. https://arxiv.org/abs/2607.20759
- Liu, Y., Lyu, Y., Zhao, Y., Zhang, T., Wang, H., Lo, D. (2025). "Your AI, My Shell": Demystifying Prompt Injection Attacks on Agentic AI Coding Editors. Preprint, arXiv:2509.22040. https://arxiv.org/abs/2509.22040
- Zhan, Q., Liang, Z., Ying, Z., Kang, D. (2024). InjecAgent: Benchmarking Indirect Prompt Injections in Tool-Integrated Large Language Model Agents. Findings of ACL 2024; arXiv:2403.02691. https://arxiv.org/abs/2403.02691
- Guo, C., Liu, X., Xie, C., Zhou, A., Zeng, Y., Lin, Z., Song, D., Li, B. (2024). RedCode: Risky Code Execution and Generation Benchmark for Code Agents. NeurIPS 2024 Datasets and Benchmarks; arXiv:2411.07781. https://arxiv.org/abs/2411.07781
- Zhang, H., et al. (2025). Agent Security Bench (ASB): Formalizing and Benchmarking Attacks and Defenses in LLM-based Agents. ICLR 2025; arXiv:2410.02644. https://arxiv.org/abs/2410.02644
- Wang, H., Poskitt, C. M., Sun, J. (2026). AgentSpec: Customizable Runtime Enforcement for Safe and Reliable LLM Agents. ICSE 2026, pp. 2938–2950; arXiv:2503.18666. https://arxiv.org/abs/2503.18666
- Wang, Z., Gao, Y., Wang, Y., Liu, S., Sun, H., Cheng, H., Shi, G., Du, H., Li, X. (2025). MCPTox: A Benchmark for Tool Poisoning Attack on Real-World MCP Servers. Preprint, arXiv:2508.14925. https://arxiv.org/abs/2508.14925
- Zhang, Z., Cui, S., Lu, Y., Zhou, J., Yang, J., Wang, H., Huang, M. (2024). Agent-SafetyBench: Evaluating the Safety of LLM Agents. Preprint, arXiv:2412.14470. https://arxiv.org/abs/2412.14470
- Vijayvargiya, S., Soni, A. B., Zhou, X., Wang, Z. Z., Dziri, N., Neubig, G., Sap, M. (2025). OpenAgentSafety: A Comprehensive Framework for Evaluating Real-World AI Agent Safety. ICLR 2026; arXiv:2507.06134. https://arxiv.org/abs/2507.06134
- Anthropic (2025). Claude Opus 4.5 System Card, section 5.2. https://www-cdn.anthropic.com/bf10f64990cfda0ba858290be7b8cc6317685f47.pdf
- Anthropic. Claude Code documentation: Security, Permissions, Hooks reference, Sandboxing. https://code.claude.com/docs/en/security, https://code.claude.com/docs/en/permissions, https://code.claude.com/docs/en/hooks, https://code.claude.com/docs/en/sandboxing
- Anthropic (20 Oct 2025). Beyond permission prompts: making Claude Code more secure and autonomous. https://www.anthropic.com/engineering/claude-code-sandboxing
- Anthropic (25 Mar 2026). Claude Code auto mode. https://www.anthropic.com/engineering/claude-code-auto-mode
- Anthropic (25 Aug 2025). Piloting Claude for Chrome. https://claude.com/blog/claude-for-chrome
- Anthropic (24 Nov 2025). Prompt injection defenses. https://www.anthropic.com/research/prompt-injection-defenses
- Anthropic (16 Jun 2025). SHADE-Arena: Evaluating sabotage and monitoring in LLM agents. https://www.anthropic.com/research/shade-arena-sabotage-monitoring; arXiv:2506.15740
- Milanta, M., Beurer-Kellner, L. (26 May 2025). GitHub MCP Exploited: Accessing private repositories via MCP. Invariant Labs. https://invariantlabs.ai/blog/mcp-github-vulnerability
- Invariant Labs (1 Apr 2025). MCP Security Notification: Tool Poisoning Attacks. https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks
- Mayraz, O. (30 Sep 2025). CamoLeak: Critical GitHub Copilot Vulnerability Leaks Private Source Code. Legit Security. https://legitsecurity.com/blog/camoleak-critical-github-copilot-vulnerability-leaks-private-source-code
- GitHub Docs. Risks and mitigations for GitHub Copilot cloud agent. https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations
- GitHub Docs. Securely using pull_request_target. https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target
- GitHub GraphQL API, enum CommentAuthorAssociation (schema introspection, 2026-11-10). https://docs.github.com/en/graphql/reference/enums
- Donenfeld, A., Vanunu, O. (25 Feb 2026). Caught in the Hook: RCE and API Token Exfiltration Through Claude Code Project Files. Check Point Research. https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/
- Beber, E. (2025). CVE-2025-54794 and CVE-2025-54795: Claude Code path restriction bypass and command injection. Cymulate. https://www.cymulate.com/blog/cve-2025-547954-54795-claude-inverseprompt/
- Backslash Research Team (21 Jul 2025). Cursor AI Security Flaw: Auto-Run Denylist Bypass. https://www.backslash.security/blog/cursor-ai-security-flaw-autorun-denylist
- HiddenLayer (26 Nov 2025). Allowlist Bypass in Run Terminal Tool Allows Arbitrary Code Execution During Autorun Mode (Cursor). https://hiddenlayer.com/sai-security-advisory/sai-security-advisor-2025-11-cursor
- CVE-2025-54131, GHSA-534m-3w6r-8pqr (Cursor allow-list bypass, fixed 1.3) (secondary: NVD, Tenable). https://nvd.nist.gov/vuln/detail/CVE-2025-54131
- AWS Security Bulletin AWS-2025-015 (23 Jul 2025). Amazon Q Developer Extension for VS Code 1.84.0, CVE-2025-8217. https://aws.amazon.com/security/security-bulletins/AWS-2025-015/
- Gupta, A. (21 Jul 2025). Gemini CLI 'lost' files during a failed file move operation. google-gemini/gemini-cli issue #4586. https://github.com/google-gemini/gemini-cli/issues/4586
- The Register (22 Jul 2025). Replit makes vibe-y promise to stop its AI agents making vibe coding disasters (secondary; quotes Masad's and Lemkin's X posts). https://www.theregister.com/2025/07/22/replit_saastr_response/
- Edwards, B. (25 Jul 2025). Two major AI coding tools wiped out user data after making cascading mistakes. Ars Technica (secondary). https://arstechnica.com/information-technology/2025/07/ai-coding-assistants-chase-phantoms-destroy-real-user-data/
- The Register (1 Dec 2025). Google Antigravity wipes a user's D: drive (secondary; the user's Reddit account). https://www.theregister.com/2025/12/01/google_antigravity_wipes_d_drive/
- Beurer-Kellner, L., Buesser, B., Creţu, A.-M., Debenedetti, E., Dobos, D., Fabian, D., Fischer, M., Froelicher, D., Grosse, K., Naeff, D., Ozoani, E., Paverd, A., Tramèr, F., Volhejn, V. (2025). Design Patterns for Securing LLM Agents against Prompt Injections. Preprint, arXiv:2506.08837. https://arxiv.org/abs/2506.08837
- Meta AI (31 Oct 2025). Agents Rule of Two: A Practical Approach to AI Agent Security. https://ai.meta.com/blog/practical-ai-agent-security/
- Willison, S. (16 Jun 2025). The lethal trifecta for AI agents. https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/
- UK AI Security Institute, Inspect Evals. CodeIPI: Indirect Prompt Injection for Coding Agents. https://ukgovernmentbeis.github.io/inspect_evals/evals/ipi_coding_agent/index.html
- Thurlow, S. (2026). Human Oversight System (HOS), documentation and research notes, https://github.com/thurlow-research/HumanOversightSystem (software archived at https://doi.org/10.5281/zenodo.21347272), docs CC BY 4.0, code MIT. Consulted for its index of runtime-guard papers (Jackson 2025; Shinde et al. 2026; Mozannar et al. 2025); none of those three was chased to a primary source here, so no figure from them is used above.
