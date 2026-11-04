# Can a plugin's agents delegate despite the harness's no-spawn guidance?

Research for [#10](https://github.com/shrujancheruku/claude-orchestrate/issues/10) (map [#9](https://github.com/shrujancheruku/claude-orchestrate/issues/9)); feeds [#16](https://github.com/shrujancheruku/claude-orchestrate/issues/16). Researched 2026-11-04 against Claude Code 2.1.311, the version the Issho run used.

## Answer

- **Workers could spawn. Nothing blocked them; they chose not to.** All 68 `orchestrate:worker` transcripts on this machine have the `Agent` tool and see `orchestrate:scout` and `orchestrate:mechanic` in their agent listing. All 68 made zero Agent calls, and so did all 38 `general-purpose` subagents, which also have the tool.
- **The worker's whole Agent-tool description carries the exception that matters:** "Only use this tool when the user explicitly says to use a subagent, or **names one of the available agent types**." The agent definition (`agents/worker.md`) reaches the worker as its *system prompt*, not as a user message. The brief is the worker's only user turn, and the brief template never names an agent type. So by the tool's own rule, nothing has "asked".
- **The orchestrator shows the other side.** It got the same guidance (13 copies in its transcript) and still made 107 Agent calls (47 reviewer, 45 worker, 15 scout). In its case the `run` skill names the types, and skill content arrives as a **user-role** message.
- **No supported switch turns the guidance off.** No frontmatter field, plugin setting or documented env var authorises delegation. The levers a plugin has are content in the user turn (the brief, or a skill), and tool lists that grant or withhold `Agent`.
- **Depth 2 is supported and on by default** (up to 3 layers), and it bills to the same plan. Plugin hooks fire inside subagents, with `agent_type` set. No depth-2 subagent has ever run on this machine (every one of 232 meta files has `spawnDepth: 1` or no field), so nested runs are documented but not yet observed here.

## Findings

### 1. What the guidance says, and what counts as "the user asks"

The worker's Agent tool, read from its recorded request shape (`api-request-shape` record, `shape.tools[name=Agent].description`, in `~/.claude/projects/-Users-shrujan-code-issho/44e00c3b-…/subagents/agent-a020e1dd8e3fb6b52.jsonl`):

> **Do not spawn agents unless the user asks.** Each spawn starts cold and re-derives context you already have — it's the expensive path on this plan. A task with "multiple angles," "thorough," or several parts is not a request to spawn; handle it inline with your own tools. Only use this tool when the user explicitly says to use a subagent, or names one of the available agent types.

- **Where it appears.** Every Agent-tool description recorded on this machine has this text: versions 2.1.304, 2.1.308, 2.1.309 and 2.1.311, in both main sessions and subagents (a scan of every `api-request-shape` record under `~/.claude/projects`). It isn't in the public docs or the [changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md), so I can't date it. "On this plan" hints that it depends on the subscription, but nothing documented confirms that.
- **What the worker sees as system prompt and as user turn.** The worker's system prompt is `"You are a Claude agent, built on Anthropic's Claude Agent SDK."`, then the body of `agents/worker.md`, then harness notes. Those notes include: "Messages from the agent that launched you — your task and any mid-task course corrections — direct your work." The first user message is the brief, which opens with "Your instructions are your agent definition; this brief is your ticket." (`skills/run/brief.md`). No brief names `orchestrate:scout` or `orchestrate:mechanic`. The orchestrator's line in the Issho transcript that carries the `run` skill is `type: user, isMeta: true, role: user` (line 10 of `44e00c3b-….jsonl`).
- **Inference, not tested yet:** the model reads "the user" as the user turn, so an agent-type name in the brief probably meets the stated exception, and the same name in the agent definition doesn't. It fits all 107 + 0 + 0 calls above. Worker thinking is redacted in the transcripts, and no worker text mentions scout, mechanic, delegation or spawning, so the transcripts can't confirm it directly.
- **The docs' own wording on invoking an agent:** "Name the subagent and Claude typically delegates" and "@-mention the subagent … This ensures that specific subagent runs", with plugin agents as `@agent-<plugin>:<name>` ([sub-agents](https://code.claude.com/docs/en/sub-agents)). The docs say "use proactively" in a description encourages *automatic* delegation. That phrase sits in the main thread's agent listing, though, and the no-spawn text competes with it.

### 2. Supported ways for a plugin to authorise delegation

| Lever | What the docs say | Use for orchestrate |
| :- | :- | :- |
| `tools` in agent frontmatter | "listing `Agent` in `tools` lets that subagent spawn subagents of its own while the depth limit allows it, but any type list inside the parentheses is ignored" ([sub-agents](https://code.claude.com/docs/en/sub-agents)). Omit `Agent`, or put it in `disallowedTools`, to stop one subagent from spawning. | `worker.md` has no `tools`, so it inherits `Agent` (confirmed in transcripts). `scout`, `mechanic` and `reviewer` list tools without `Agent` and can't nest (confirmed: 46 scout, 1 mechanic and 75 reviewer transcripts have no `Agent`). It grants the ability, not the will. |
| `Agent(type, …)` allowlist | "applies only to an agent running as the main thread with `claude --agent`" | Doesn't apply to workers. |
| `hooks`, `mcpServers`, `permissionMode`, `initialPrompt` frontmatter | "ignored when loading agents from a plugin" | Not available. Ship hooks in `hooks/hooks.json` instead. |
| Plugin `settings.json` / manifest `settings` | "Only `agent` and `subagentStatusLine` take effect; other keys are dropped at load" ([plugins reference](https://code.claude.com/docs/en/plugins-reference)) | A plugin can't set `env` or permissions. |
| `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` | Caps the nesting depth (default 3; `1` turns nesting off) | It's a cap, not an authorisation, and a plugin can't set it anyway. |
| `SubagentStart` hook `additionalContext` | Injected "at the start of the conversation, before the first prompt", wrapped in a system reminder. Write it "as factual statements rather than imperative system instructions" or it may trip prompt-injection defences ([hooks](https://code.claude.com/docs/en/hooks)) | A plugin hook could add per-role context. It's an untested and weaker channel than the brief: system reminders aren't "the user". |
| The brief (user turn) | The tool's stated exception: "names one of the available agent types" | The most direct lever, and it changes only the plugin's own template ([[plugin-features-work-out-of-the-box]]). |

### 3. Depth-2 subagents: do they run, share usage and get recorded?

- **They run, by default.** "By default, a subagent can spawn subagents of its own, up to three layers below the main conversation. At the depth limit, Claude Code withholds the `Agent` tool…" ([sub-agents](https://code.claude.com/docs/en/sub-agents)). In the changelog, 2.1.217 turned nesting off by default, then 2.1.219 says: "Subagents can now spawn nested subagents up to depth 3 by default (was 1); set CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1 to disable nesting". 2.1.187 fixed depth tracking ("resumed subagents now restore their original spawn depth"). There is also a per-session cap of 200 spawns (`CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION`, 2.1.212). Orchestrate's chain, orchestrator → worker → scout or mechanic, is depth 2, inside the default.
- **They bill to the same plan.** A subagent's requests "count toward the same usage limits as your main conversation" ([sub-agents](https://code.claude.com/docs/en/sub-agents)). Nothing exempts nested ones.
- **Transcripts.** The docs give `~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl` with a `.meta.json` beside each. They don't document `spawnDepth`, but the meta files carry it (`"spawnDepth":1`, plus `toolUseId`, `requestShape` and `model`). 2.1.219 added "subagents spawned at depth-2+ now appear when `--forward-subagent-text` is set, keyed by their spawning Agent `tool_use` id", so the harness tracks depth 2 and up as its own case. **Observed:** 232 meta files under `~/.claude/projects`, 231 with `spawnDepth: 1` and one older file with no field (`look-together/b0fd3e9c…/agent-a0238c9a5cceec3a8.meta.json`, `{"agentType":"general-purpose","stoppedByUser":true}`). None has depth 2 or more, and no subagent transcript holds an Agent `tool_use`. Whether a depth-2 transcript sits flat in the session's `subagents/` folder with `spawnDepth: 2` is likely but unconfirmed. The profiler should not assume a nested folder.

### 4. Do depth-2 subagents inherit the guard hook and `agent_type`?

- **The docs say hooks fire inside every subagent:** "Hooks from settings files, managed policy settings, and plugins also run inside subagents. When a subagent calls a tool, tool events such as `PreToolUse` … fire the same configured hooks … and the input carries the `agent_id` and `agent_type`" ([hooks](https://code.claude.com/docs/en/hooks)). `agent_type` is "Present when … the hook fires inside a subagent. For subagents, the subagent's type takes precedence over the session's `--agent` value." The docs don't single out nested subagents, but nothing limits the rule to depth 1. A depth-2 scout should therefore get `agent_type: "orchestrate:scout"`, which `hooks/guard.py` (`role_of`) maps to the read-only rules.
- **Observed at depth 1:** guard denials appear in 51 reviewer, 16 scout and 1 mechanic transcripts, for example "orchestrate guard: the scout role can't run a command that changes git state" in Issho's `agent-a9f27003b0b21f020.jsonl`. So plugin hooks reach subagents, and `agent_type` carries the plugin-scoped name.
- **The worktree caveat:** a mechanic spawned by a worker needs the worker's worktree as its cwd. The guard reads the config from `cwd` up (`posting_enabled`), which only matters for reviewers.

## Open, testable in a minute

1. **Does a brief that names the types make a Sonnet worker delegate?** Dispatch one worker with an extra brief line naming `orchestrate:scout` and `orchestrate:mechanic` for a concrete step. Count its Agent calls.
2. **Depth-2 recording:** after (1), check that the scout's meta file has `spawnDepth: 2` and where the file lands, and that a scout write attempt at depth 2 gets the guard's denial.
3. **Does the no-spawn text vary by plan or auth type** (subscription vs API key)? Compare the recorded Agent descriptions from an API-key session.

## Sources

- Claude Code docs: [Subagents](https://code.claude.com/docs/en/sub-agents) (nesting, `tools`/`Agent(...)`, plugin-ignored fields, usage limits, transcripts, invoking by name); [Hooks](https://code.claude.com/docs/en/hooks) (subagent hooks, `agent_type`, `SubagentStart`, `additionalContext`); [Plugins reference](https://code.claude.com/docs/en/plugins-reference) (`settings` keys).
- [Claude Code changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md): 2.1.69, 2.1.187, 2.1.212, 2.1.217, 2.1.219, 2.1.288.
- Local transcripts, Claude Code 2.1.311: `~/.claude/projects/-Users-shrujan-code-issho/44e00c3b-cbe9-4e49-9ee9-aeb62b793f8b.jsonl` (orchestrator) and its `subagents/*.jsonl` and `*.meta.json` (45 workers in this session); every `*.meta.json` under `~/.claude/projects` (232).
- This repo: `agents/worker.md`, `agents/scout.md`, `agents/mechanic.md`, `skills/run/SKILL.md`, `skills/run/brief.md`, `hooks/hooks.json`, `hooks/guard.py`.
