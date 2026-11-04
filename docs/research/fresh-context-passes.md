# Can an orchestration pass start from a fresh context?

Research for [#12](https://github.com/shrujancheruku/claude-orchestrate/issues/12) (map [#9](https://github.com/shrujancheruku/claude-orchestrate/issues/9)). Sources are the Claude Code docs at `code.claude.com/docs/en/*` (fetched 2026-11-04, Claude Code 2.1.311 installed locally), Anthropic's API pricing page, and the Issho session transcript `44e00c3b`.

## Answer

Yes, but only by cutting the orchestrator off from the workers. Every way to get a fresh context (`/clear`, a new session or process, a Desktop or cloud scheduled task, a new `claude -p` run) starts a new conversation. Background subagents and `SendMessage` reachability belong to the conversation that spawned them. The run skill uses that reachability for three things: worker completions waking the orchestrator, review and CI fixes sent to the same worker, and resuming paused workers. A fresh pass would lose all three, and every in-flight ticket would be **stalled**, so the next pass would rescue and redispatch it.

Compaction is the one context bound that keeps workers reachable. Lowering the auto-compact window from the user's 400k to about 150–200k would have cut the orchestrator's input-side cost by about 25–30% in the Issho session (simulated below). It needs no change to how the plugin works, but the plugin can't set the window itself.

A real fresh-context orchestrator would need workers that live outside the orchestrator's conversation: background **sessions** (`claude --bg`) reached by cross-session messaging, not subagents. That would be a redesign, and agent view is a research preview.

## What the session did (local evidence)

From `~/.claude/projects/-Users-shrujan-code-issho/44e00c3b-….jsonl`, counting each assistant message ID once:

- 772 Opus 5.5 turns. Mean context 217k, max 367k. 165.6M cache-read tokens, 1.80M cache-write tokens, all at the 1-hour TTL (`ephemeral_1h_input_tokens`; zero 5-minute writes). 0.54M output tokens. Median gap between turns: 14 s.
- **What woke the orchestrator:** 112 worker hand-backs (`<agent-message …>`), 49 task notifications, 6 `/orchestrate:run` prompts, plus a few typed by the user. The orchestrator works as an event loop driven by worker completions; it rarely runs discrete scheduled passes. The tools it called were `SendMessage` 76 times and `Agent` 107 times.
- **4 compactions** (`compact_boundary`): three automatic at preTokens ≈ 367–368k, and one manual at 346k. Each brought the context down to 30–45k. The user's `modelSettings."claude-opus-5-5".autoCompactWindow` is `400000`. Compaction itself took about 20 ms (`precomputed: true`). The first turn after each compaction wrote 52–73k tokens to the cache and read only the ~28k system-prompt prefix.
- **One cold re-warm:** after a 6 h gap (22:36 → 04:47), the next turn wrote 340k tokens uncached. That is about $2.70 at Opus 1-hour write rates. Auto-compaction ran 10 s later. Idle compaction should have run during the gap; it requires the machine to stay awake, so the machine was probably asleep.
- The session's first turn read 28k from the cache and wrote 20k. The system prompt and tools layer was already warm from another session with the same prefix, so a fresh session pays to write only the project-context and conversation layers.

Opus 5.5 prices: input $4/MTok, 1-hour cache write $8, cache read $0.20 (0.05×), output $20 ([pricing](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). At those prices the session cost about $33 in cache reads, $14 in cache writes and $11 in output. Cache reads, which grow with context size × turns, are the largest share.

## Mechanisms, what each loses, and re-warm cost

| Mechanism | Bounds context? | Workers still reachable? | Re-warm cost per reset | Can the plugin trigger it? |
|---|---|---|---|---|
| Auto-compaction at a lower window (`/autocompact`, `autoCompactWindow`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`) | Yes, at the threshold | **Yes**: same session; background subagents keep running | ~50–70k cache write (~$0.45–0.60), plus a summary request that reads the warm prefix | No: user settings only |
| Manual `/compact` | Yes | Yes | Same | No: Claude can't run built-in commands |
| `/clear` | Yes, fully | **No**: new session ID; the `SendMessage` name check "resets on `/clear`"; whether running subagents survive is undocumented | ~20–50k write for the project layers, plus rebuilding state through the scout | No: same as `/compact` |
| New process or session (`claude`, `claude -p`, Agent SDK `query()` without resume) | Yes | **No**: subagents "work within a single session" | Same as `/clear` | Only from outside, by a wrapper or scheduler |
| `--resume` of the same session | No (restores the history) | Partly: completed subagents can be resumed, but background work that ended with the old process shows as unfinished | Full history re-warm if the cache has expired | n/a |
| `/loop`, `CronCreate`, `ScheduleWakeup` | **No**: they fire into the same conversation | Yes | None | Yes |
| Desktop scheduled task | Yes (a "fresh session" per run, minimum 1 min) | No | Same as a new session | No: set up by the user in the Desktop app |
| Cloud routine | Yes (a new session per run, minimum 1 h) | No; it also works on a fresh clone, without local worktrees or the state file | Same as a new session | No |
| Hooks (`SessionStart`, `PreCompact`, `PostCompact`, `Stop`) | No hook can clear or compact | n/a | n/a | They can re-inject state after compaction (`SessionStart` matcher `compact`) and block or allow compaction (`PreCompact`) |
| `/background` or `claude --bg` | No (the same conversation moves) | Yes: subagents and `/loop` tasks carry over | None | No |
| Workers as background **sessions**, orchestrator in a fresh session | Yes | **Yes**, through cross-session messaging by name, with `notify_when_idle` | The fresh session's project layers | Would need a redesign (see below) |

### Sources for each row

- **`/loop` and cron run in the same conversation.** "Session-scoped scheduled tasks let Claude re-run a prompt automatically on an interval within a Claude Code session." A fire "fires between your turns". `/clear` sent by a fire "reach[es] Claude as plain text instead of executing" ([scheduled-tasks](https://code.claude.com/docs/en/scheduled-tasks)). A loop therefore can't reset its own context.
- **Side finding: `disable-model-invocation: true` and `/loop`.** The same page says skills marked `disable-model-invocation: true` "reach Claude as plain text instead of executing" on a scheduled fire. `skills/run/SKILL.md` carries that flag. Under `/loop /orchestrate:run`, every fire after the first therefore arrives as text and relies on the skill body still being in context. After a compaction, invoked skill bodies are re-injected, capped at 5,000 tokens per skill and 25,000 in total, oldest dropped first ([context-window](https://code.claude.com/docs/en/context-window#what-survives-compaction)). SKILL.md is about 14 KB (roughly 3.5k tokens), so it fits under the cap. `brief.md` is read as a file rather than invoked as a skill, so it isn't re-injected.
- **Compaction keeps workers running.** "Background commands and background subagents: Keep running. Claude Code reminds Claude which ones are still running" ([context-window](https://code.claude.com/docs/en/context-window#what-survives-compaction)). "When the main conversation compacts, subagent transcripts are unaffected" ([sub-agents](https://code.claude.com/docs/en/sub-agents)).
- **Compaction cost.** It "invalidates the conversation layer"; the summary request "reads your prefix from the cache" while it's warm, and "the turn after compaction rebuilds the conversation cache for only the much shorter summary" ([prompt-caching](https://code.claude.com/docs/en/prompt-caching#compacting-the-conversation)). The summary request falls in the "everything else" bucket: 5-minute TTL ([prompt-caching](https://code.claude.com/docs/en/prompt-caching#which-ttl-each-request-gets)).
- **Auto-compact window.** Opus 4.7 and later run a native 1M window and compact at about 967K by default. The window can be set with `/autocompact <n>` (saved to user `modelSettings`), `autoCompactWindow`, `--autocompact`, or `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (100k–1M, which overrides the rest). `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` can only lower the threshold ([model-config](https://code.claude.com/docs/en/model-config#set-the-auto-compact-window), [env-vars](https://code.claude.com/docs/en/env-vars)). Agent frontmatter also takes `autoCompactWindow` ([sub-agents](https://code.claude.com/docs/en/sub-agents#subagent-auto-compact-window)); the docs don't say whether it applies when the agent runs as the main session.
- **The plugin can't set it.** A plugin's `settings.json` keeps only `agent` and `subagentStatusLine`; "other keys are dropped at load" ([plugins-reference](https://code.claude.com/docs/en/plugins-reference)).
- **Idle compaction.** On by default when the conversation is on the 1-hour TTL, its context is at least 200k (`CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`), and about an hour has passed with the computer awake ([settings-reference](https://code.claude.com/docs/en/settings-reference#idlecompaction)). It would have avoided the 340k cold re-warm, had the machine stayed awake.
- **`/clear`** "Start[s] a new conversation with empty context" ([commands](https://code.claude.com/docs/en/commands)). It "starts a new session" ([interactive-mode](https://code.claude.com/docs/en/interactive-mode)), and `CLAUDE_CODE_SESSION_ID` "is updated on `/clear`" ([env-vars](https://code.claude.com/docs/en/env-vars)). The `SendMessage` name check "is scoped to the current conversation and resets on `/clear`" ([sub-agents](https://code.claude.com/docs/en/sub-agents)). That `/clear` "costs nothing" applies to the clear itself ([costs](https://code.claude.com/docs/en/costs)). The docs don't say whether a running background subagent survives `/clear`; [tools-reference](https://code.claude.com/docs/en/tools-reference) hints that some work "can still spawn subagents [and] survives the clear, such as a running workflow".
- **Subagents are bound to their session.** "Subagents work within a single session." "You can resume a subagent after restarting Claude Code by resuming the same session" ([sub-agents](https://code.claude.com/docs/en/sub-agents)). On resume, "a background subagent … that ended with the previous process shows up in the resumed transcript as a note that it didn't finish" ([sessions](https://code.claude.com/docs/en/sessions)). `/list-agents` shows subagents as "agents running inside the current session" ([cross-session-messaging](https://code.claude.com/docs/en/cross-session-messaging)). This matches the run skill's rule that "an entry from another session can't be reached with SendMessage".
- **Headless `claude -p`.** A run waits for background subagents ("the run stays open until that work completes") and runs pending self-paced `/loop` wakeups ([headless](https://code.claude.com/docs/en/headless#background-tasks-at-exit)). A `-p` orchestrator per pass would therefore block until its workers finished, so passes couldn't overlap. `--resume <id>` reuses the session, and with it the context.
- **Agent SDK.** `query()` makes a fresh session unless `continue`, `resume` or `fork_session` is set ([agent-sdk/sessions](https://code.claude.com/docs/en/agent-sdk/sessions)). It has the same trade-off as `-p`.
- **Desktop and cloud schedules.** A Desktop task "starts a fresh session when a task is due" ([desktop-scheduled-tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)). A cloud routine runs "a new [session] for each run" on a fresh clone, at least an hour apart ([routines](https://code.claude.com/docs/en/routines), [scheduled-tasks](https://code.claude.com/docs/en/scheduled-tasks#compare-scheduling-options)).
- **Hooks.** `PreCompact` can block compaction (exit 2, or `"decision": "block"`). `PostCompact` reacts to it. `SessionStart` gets a `source` of `startup`, `resume`, `clear`, `compact` or `fork`, and its output is added to the context ([hooks](https://code.claude.com/docs/en/hooks)). No hook output starts a compaction or a clear.
- **A background agent owning the loop.** A subagent "that launches background subagents waits for their results before it finishes", and a nested background subagent "reports to your main conversation if it outlasts the spawning subagent" ([sub-agents](https://code.claude.com/docs/en/sub-agents)). An orchestrator subagent therefore doesn't take completions off the main session. It only moves the context growth into a subagent, which can have its own `autoCompactWindow`. Whether subagents get the cron tools is undocumented. `/background` moves the whole conversation, with its subagents and `/loop` tasks ([agent-view](https://code.claude.com/docs/en/agent-view#what-carries-over-when-you-background)), so it doesn't bound context.
- **Prompt-cache TTLs.** On a subscription within plan usage, the main conversation gets the 1-hour TTL and subagents and compaction get 5 minutes. Over the plan, the main conversation drops to 5 minutes. They can be changed with `promptCacheTtl` and `subagentPromptCacheTtl` ([prompt-caching](https://code.claude.com/docs/en/prompt-caching#which-ttl-each-request-gets)). Project context (CLAUDE.md, memory) is rebuilt "after `/clear` or `/compact`".

## Re-warm cost compared with bounded context

Simulation: replay the session's 771 per-turn context deltas, compact to a 42k floor whenever the next turn would cross the threshold, write each compaction's floor at the 1-hour rate, and charge each summary request a warm prefix read plus 8k output tokens. Calibration: at a 400k threshold the simulation gives $44.55; the session actually spent $47.5 on cache reads and writes, including the manual compaction and the cold re-warm.

| Threshold | Compactions | Mean context | Input-side cost |
|---|---|---|---|
| 400k (the Issho setting) | 3 | 211k | $44.6 |
| 300k | 5 | 168k | $38.9 |
| 200k | 8 | 118k | $32.8 |
| 150k | 12 | 93k | $31.0 |
| 100k (the minimum) | 22 | 70k | $32.5 |

Each reset costs about $0.45–0.60 to re-warm. Cache reads cost about $0.04 per 100k of context per turn. With about 770 turns, savings stop below roughly 150k. A fresh session per pass sits close to the 100k row (a 30–50k floor, but up to about 170 resets if every wake-up counted as a pass) and adds a reconcile scout call each time. That makes it no cheaper than compacting at 150–200k, and it gives up worker reachability.

## A design that would allow fresh passes

Workers as background sessions (`claude --agent orchestrate:worker --bg --name ticket-<n> "<brief>"` from the orchestrator's Bash), not as subagents:

- **What it gains.** Workers live outside the orchestrator's conversation and are reachable from any local session through `ListAgents` and `SendMessage` by name. The orchestrator can ask for one notice when a worker next goes idle (`notify_when_idle`, valid for 12 hours, main conversation only), and a fresh orchestrator session could ask again ([cross-session-messaging](https://code.claude.com/docs/en/cross-session-messaging)). Background sessions survive supervisor restarts, and their background work carries over ([agent-view](https://code.claude.com/docs/en/agent-view)).
- **What it costs.**
  - The hand-back channel changes: a worker's completion is no longer a task notification.
  - Agent view is a research preview, and background sessions edit in their own worktrees under `.claude/worktrees/`, which are deleted with the session.
  - The guard hook's `agent_type` detection for a worker run as a top-level session is untested.
  - Each worker's prompt is then a main conversation, on the 1-hour TTL, which charges 2× for cache writes rather than 1.25×.
  - The plugin would depend on agent view not being turned off.

## Open questions

1. Does a running background subagent keep running after `/clear`? Does its completion then reach the new conversation? (Undocumented; a 2-minute manual test would settle it.)
2. Does `autoCompactWindow` in an agent's frontmatter apply when the agent runs as the main session (`claude --agent …`)? If it does, an `orchestrate:orchestrator` agent could ship its own bound, with no user-settings edit.
3. Can a subagent call `CronCreate` or `ScheduleWakeup`? This decides whether a background orchestrator subagent could own the loop.
4. Does a scheduled `/loop /orchestrate:run` fire, which arrives as plain text because of `disable-model-invocation`, still run a full pass after several compactions? The Issho session had only 6 run prompts, so it doesn't show this.
