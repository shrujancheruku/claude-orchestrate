# Can the orchestrator bound its own context without user settings?

Research for [#21](https://github.com/shrujancheruku/claude-orchestrate/issues/21) (map [#9](https://github.com/shrujancheruku/claude-orchestrate/issues/9)). It follows [fresh-context-passes.md](https://github.com/shrujancheruku/claude-orchestrate/blob/research/fresh-context-passes/docs/research/fresh-context-passes.md) ([#12](https://github.com/shrujancheruku/claude-orchestrate/issues/12)). Sources are the Claude Code docs at `code.claude.com/docs/en/*` and the changelog, fetched 2026-11-04. Claude Code 2.1.311 is installed locally; the docs describe up to 2.1.318. Where the docs say nothing, I ran throwaway probes: Haiku 5.5 sessions in a scratch git repo with a test agent and a test skill, driven through `claude -p` or interactive `claude` inside tmux. No user settings or plugin code were touched.

## Answer

**Not for the main session.** The plugin can't ship a context bound for the orchestrator while it runs as the main conversation:

- Agent frontmatter `autoCompactWindow` has no effect under `--agent` or the `agent` setting. The docs say so explicitly.
- A plugin's `settings.json` drops every key except `agent` and `subagentStatusLine`.
- Every other way to set the window is a user setting, a launch flag or an environment variable.

**What the plugin can ship is an orchestrator that runs as a background subagent.** Frontmatter `autoCompactWindow` *is* honoured for subagents, plugin subagents included. A background subagent keeps `Agent`, `SendMessage`, `Monitor` and `Skill`. A probe showed it is woken by the completions of the subagents it spawns. It does not get `CronCreate` or `ScheduleWakeup`, so the main session would keep the `/loop`. This is a redesign with costs (see [Option B](#option-b-orchestrator-as-a-background-subagent)), not a setting.

The other three questions:

| Question | Finding | Source |
|---|---|---|
| Frontmatter `autoCompactWindow` under `claude --agent`? | **No effect.** Documented. | Docs |
| Does a running background subagent survive `/clear`, and can it still be reached? | **It survives.** Its completion reaches the new conversation, and `ListAgents` still lists it. `SendMessage` **by name fails** but **by agent ID works**. Claude can't run `/clear` itself. | Changelog 2.1.72, plus a probe |
| Can a subagent use `CronCreate` / `ScheduleWakeup`? | `ScheduleWakeup`: **never.** `CronCreate`/`CronList`/`CronDelete`: **foreground subagents only**, not background ones. | Docs, confirmed by a probe |
| With `disable-model-invocation: true`, do plain-text `/loop` fires still run full passes after compactions? | **Yes, if the skill was invoked by the user at least once** in the conversation: the skill body is re-attached on each compaction, and the fires kept following it through 3 compactions. **No, if `/loop /orchestrate:run` is the first thing typed**, which is what the README says to do. Then the `/loop` skill's immediate run is refused by the Skill tool ("Do not replicate this skill's workflow by other means"), and the fires find no skill body to follow. | Docs, plus two probes |

That last row is a **live bug** in the current README flow, separate from context bounding. See [Q4](#q4-disable-model-invocation-and-plain-text-loop-fires).

## Q1: `autoCompactWindow` when an agent runs as the main session

- The frontmatter field: "Lower the auto-compact window for this subagent to a token count from `100000` to `1000000`. Requires Claude Code v2.1.296 or later" ([sub-agents, frontmatter table](https://code.claude.com/docs/en/sub-agents#supported-frontmatter-fields)).
- Under "Set the auto-compact window for one subagent", the troubleshooting list includes: "**You run the whole session as this subagent with `--agent` or the `agent` setting**: the field has no effect there. To set the session's window, see Set the auto-compact window" ([sub-agents](https://code.claude.com/docs/en/sub-agents#subagent-auto-compact-window)).
- `CLAUDE_CODE_AUTO_COMPACT_WINDOW` overrides the frontmatter field for subagents too (same section).
- The ways to set the session's window are `/autocompact` (saved to user `modelSettings`), `autoCompactWindow` in a settings file, `--autocompact` for one launch, and `CLAUDE_CODE_AUTO_COMPACT_WINDOW` ([model-config](https://code.claude.com/docs/en/model-config#set-the-auto-compact-window)). `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` can only lower the threshold ([env-vars](https://code.claude.com/docs/en/env-vars)).
- A plugin's `settings.json`: "Only `agent` and `subagentStatusLine` take effect; other keys are dropped at load" ([plugins-reference](https://code.claude.com/docs/en/plugins-reference)). A plugin *can* make its agent the main-session agent, but that agent's `autoCompactWindow` is then ignored.
- Hooks can't set it either. `CLAUDE_ENV_FILE` from a `SessionStart` hook persists variables "for the shell commands Claude runs later in the session", not for the Claude Code process ([hooks](https://code.claude.com/docs/en/hooks)).
- **Nearest automatic option, which is still a config edit.** `autoCompactWindow` has scope "Any file" ([settings-reference](https://code.claude.com/docs/en/settings-reference#autocompactwindow)), so `/orchestrate:setup` could write it to the repo's `.claude/settings.local.json` with the user's consent. Whether a project-level top-level `autoCompactWindow` beats a user-level `modelSettings.<model>.autoCompactWindow` (the Issho user has `400000` there) is **not documented**. For `effortLevel` the docs say the highest-precedence file that sets either form decides, which suggests the project file would win, but they don't say this for the window. This option also goes against the "no manual edits to user config" constraint in spirit, so I list it only as the fallback.

## Q2: Background subagents and `/clear`

The docs are silent on whether running subagents survive `/clear`. The [sub-agents](https://code.claude.com/docs/en/sub-agents) page says only that the `SendMessage` name check "is scoped to the current conversation and resets on `/clear`". The changelog settles survival. In 2.1.72: "Fixed `/clear` killing background agent/bash tasks — only foreground tasks are now cleared". [tools-reference](https://code.claude.com/docs/en/tools-reference) also mentions work that "survives the clear".

Probe (interactive, tmux). The main session spawned a background `general-purpose` subagent that waits on a background `sleep`. The probe then ran `/clear` and, in the new conversation:

1. **The completion arrived.** About 90 s after the clear, the subagent's hand-back (with its marker token) and the `Agent "sleeper" finished` task notification landed in the *new* conversation. Claude there had no record of spawning it.
2. **`ListAgents`** in the new conversation still listed the subagent by ID (`a5e3fda0c7ffb5156 · general-purpose · completed`).
3. **`SendMessage` to the name** returned `{"success":false,"message":"No agent named 'waiter' is reachable. ..."}`.
4. **`SendMessage` to the agent ID** returned `{"success":true,"message":"Resuming agent a5e3fda", ...}`, and the agent's reply reached the new conversation.

What this means for orchestrate:

- A user-typed `/clear` is a full context reset that **keeps workers running and reachable**, provided the orchestrator addresses them by agent ID. The state file already stores agent IDs.
- The run skill currently says "An entry from another session can't be reached with SendMessage, so treat its ticket as stalled". `/clear` changes the session ID (`CLAUDE_CODE_SESSION_ID` "is updated on `/clear`", [env-vars](https://code.claude.com/docs/en/env-vars)), so after a `/clear` the skill would wrongly treat every in-flight ticket as stalled. Before relying on `/clear`, the rule should be "try `SendMessage` by agent ID; stalled only if that fails".
- **Claude still can't trigger `/clear`.** A built-in command sent by a scheduled fire "reach[es] Claude as plain text instead of executing" ([scheduled-tasks](https://code.claude.com/docs/en/scheduled-tasks)), and no hook output clears ([hooks](https://code.claude.com/docs/en/hooks)). `/clear` is therefore a bound the user can apply by hand, not one the plugin applies itself.
- Not tested: a worker that is *mid-turn* (not idle-waiting) during `/clear`, and its permission prompts afterwards. The changelog says only foreground tasks are cleared, so I'd expect it to survive.

## Q3: Cron and wake-up tools in subagents

From [sub-agents, "Available tools"](https://code.claude.com/docs/en/sub-agents#available-tools):

- The first filter removes these from **every** subagent: `AskUserQuestion`, `EndConversation`, `EnterPlanMode`, `ExitPlanMode` (unless plan mode), **`ScheduleWakeup`**, `WaitForMcpServers`, `Workflow`, and `Agent` at the depth limit.
- The second filter applies to **background** subagents, which keep only `Read`, `Grep`, `Glob`, `LSP`, `Bash`, `PowerShell`, `Edit`, `Write`, `NotebookEdit`, `WebFetch`, `WebSearch`, `TodoWrite`, `Skill`, `ToolSearch`, `EnterWorktree`, `ExitWorktree`, `Monitor`, `TaskStop`, `SendMessage`, `Artifact` (+ `SubagentHandback`), plus `Agent` per the first filter and every MCP tool. **No cron tools.**
- Only agent-team teammates "additionally keep ... `CronCreate`, `CronDelete`, and `CronList`".

Probe (`claude -p`, a tool-listing test agent spawned both ways):

- The foreground run listed `CronCreate`, `CronList`, `CronDelete`, `Agent`, `SendMessage` and `Monitor`, but not `ScheduleWakeup`.
- The background run listed `Agent`, `SendMessage` and `Monitor`, but no cron tools and no `ScheduleWakeup`.

This matches the docs. A foreground subagent blocks the main conversation, so in practice an orchestrator subagent would have no scheduler. It would have to wait with `Monitor` (an until-loop on `gh pr checks`) instead of a cron wake-up. The docs don't say where a foreground subagent's `CronCreate` fire is delivered; I didn't test it, since it doesn't help.

## Q4: `disable-model-invocation` and plain-text `/loop` fires

Docs:

- "A scheduled fire only runs skills that Claude is allowed to invoke on its own." Skills marked `disable-model-invocation: true` "reach Claude as plain text instead of executing" ([scheduled-tasks](https://code.claude.com/docs/en/scheduled-tasks#run-a-prompt-repeatedly-with-loop)).
- On compaction, Claude Code "re-attaches the most recent invocation of each skill after the summary, keeping the first 5,000 tokens of each", with a 25,000-token shared budget filled from the most recently invoked skill ([skills, "Skill content lifecycle"](https://code.claude.com/docs/en/skills#skill-content-lifecycle); [context-window](https://code.claude.com/docs/en/context-window#what-survives-compaction)).
- `skills/run/SKILL.md` is 13.9 KB, roughly 3.5k tokens, so it fits under the per-skill cap.

Probe A: the skill typed once, then looped. A test skill `ping` with `disable-model-invocation: true` replies `PONG <n> skill-body-present`, where only the skill body tells Claude that phrase.

1. Typed `/ping`, which replied `PONG 1`.
2. Ran `/loop 1m /ping`. `CronCreate` succeeded. The loop's immediate run called `Skill(ping)` and was refused: *"Skill ping cannot be used with Skill tool due to disable-model-invocation. Ask the user to run /ping themselves … Do not replicate this skill's workflow by other means"*.
3. Ran `/compact` three times between fires. Each compaction printed `Skills restored (loop, ping)`.
4. The fires after compactions 1, 2 and 3 replied `PONG 2` … `PONG 5 skill-body-present`. **The plain-text fires kept following the re-attached body through 3 compactions.**

Probe B: `/loop` first, as the README says.

1. In a fresh session, ran `/loop 1m /ping`. Same refusal on the immediate run; Claude said the loop "will do nothing useful".
2. Fire 1: "I don't see /ping expanded in this turn, so I can't tell whether the skill ran … I won't reproduce its steps by hand."
3. Fire 2: same.
4. Fire 3: Claude read `.claude/skills/ping/SKILL.md` off disk on its own initiative and answered. That is unreliable, and it contradicts the refusal message.

What this means for orchestrate:

- Under `/loop /orchestrate:run` typed cold, the first pass is refused and later fires are no-ops or improvised. The README (step 2: "`/loop /orchestrate:run` keeps going") leads users straight into this. The Issho session worked because the user had typed `/orchestrate:run` directly (6 run prompts).
- Once `/orchestrate:run` has been typed, fires keep working across compactions, as long as the run skill is among the most recently invoked skills within the 25k budget. The orchestrator invokes few other skills, so that should hold. A large real SKILL.md under Opus wasn't tested, only the mechanism.
- Fixes that degrade gracefully:
  - **(a) Drop `disable-model-invocation` from `run`.** Fires and the loop's first run would then invoke it properly. Re-invoking identical content adds only "a short note that the skill is already loaded" ([skills](https://code.claude.com/docs/en/skills#skill-content-lifecycle)), so fires get cheaper too. The cost is that Claude may invoke `run` unprompted; a description such as "Only when the user or a /loop asks for an orchestration pass" mitigates that.
  - **(b) Keep the flag** and change the README to "run `/orchestrate:run` once, then `/loop /orchestrate:run`". That is weaker: it relies on the body staying re-attached.

## Options for a self-shipped bound

### Option A: no plugin change; document the bound

Tell users to launch the orchestrator with `claude --autocompact 200k`, or to run `/autocompact 200k`. The earlier simulation estimated a 25–30% input-side saving in the Issho session ([fresh-context-passes.md](https://github.com/shrujancheruku/claude-orchestrate/blob/research/fresh-context-passes/docs/research/fresh-context-passes.md#re-warm-cost-compared-with-bounded-context)). It works out of the box at today's cost, and the bound is opt-in.

### Option B: orchestrator as a background subagent

`/orchestrate:run` in the main session would spawn (or `SendMessage`-resume, by agent ID) one plugin agent `orchestrate:orchestrator` with `autoCompactWindow: 200000` and `background: true`. That agent runs the pass loop. The main session stays thin and keeps the `/loop`.

Evidence it can work:

- The field is honoured for subagents, and plugin subagents ignore only `permissionMode`, `mcpServers`, `hooks` and `initialPrompt` ([sub-agents](https://code.claude.com/docs/en/sub-agents#supported-frontmatter-fields)). Subagents auto-compact on their own, logged as `compact_boundary` in their transcripts (same page, "Auto-compaction").
- A background subagent keeps `Agent`, `SendMessage`, `Monitor` and `Skill` (Q3).
- "A subagent that launches background subagents waits for their results before it finishes" ([sub-agents, nested](https://code.claude.com/docs/en/sub-agents#let-subagents-spawn-their-own-subagents)).
- Probe: a background `boss` subagent spawned two nested background children in one turn and logged each completion separately, at 12:22:43 and 12:23:01, as wake events. It finished only after both were done.

Costs and unknowns:

- **Depth.** The limit is three layers below main. Orchestrator (1) → worker (2) → scout or mechanic (3) is at the limit, so a worker's helpers can't nest further.
- **No scheduler** in the subagent. CI waits move to `Monitor`, and usage-limit hops stay with the main session's `/loop`.
- **No `AskUserQuestion`.** Gates and `ready-for-human` questions go back to main as a report.
- **Prompt cache.** Subagent requests default to the 5-minute TTL ([prompt-caching](https://code.claude.com/docs/en/prompt-caching#which-ttl-each-request-gets)), so a 200k orchestrator idling on workers would re-warm often. Frontmatter `experimental.cacheTtl: 1h` can pin it, but it is experimental and ignored on usage credits.
- **Permission prompts** from nested workers still surface in main, named by subagent.
- **Untested:** a long-running orchestrator subagent across many completions and its own compactions; whether nested completions still reach it after it compacts; what happens to nested workers if the orchestrator subagent ends or is stopped (the docs say a nested background subagent "reports to your main conversation if it outlasts the spawning subagent", which is a graceful fallback).

### Option C: user `/clear` as a manual reset

After the run-skill change in Q2 (reach workers by agent ID; don't treat a new session ID as stalled), the user can `/clear` and type `/orchestrate:run` again without losing workers. It's cheap to add and degrades to today's behaviour. It isn't automatic.

## Open questions

1. Does Option B hold up under a real multi-hour backlog: completions after the orchestrator subagent's own compaction, the 5-minute cache churn, and recovery when it ends? This needs a prototype.
2. Does a project-scoped `autoCompactWindow` beat a user-level `modelSettings.<model>.autoCompactWindow`? (Undocumented. Observable via `compact_boundary.preTokens`, but slow to test.)
3. Would dropping `disable-model-invocation` from `run` cause unwanted auto-invocation in practice?
