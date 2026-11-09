# Orchestrator effort at launch

Research for #521 (map #513). Fetched 2026-11-09 from primary sources only.

## Answer

Yes. A main-thread session takes an effort level at launch, three ways, and all three work for the orchestrator's `claude --name orchestrator ...` line. Opus 5.5 and Sonnet 5.5 both support all five levels.

| Mechanism | Exact name | Scope | Source |
| :- | :- | :- | :- |
| CLI flag | `--effort <low\|medium\|high\|xhigh\|max\|ultracode>` | This session only, not persisted | [CLI reference](https://code.claude.com/docs/en/cli-reference) |
| Environment variable | `CLAUDE_CODE_EFFORT_LEVEL=<level>` (`auto` or `unset` makes Claude Code send no level) | The process | [Model configuration](https://code.claude.com/docs/en/model-config#adjust-effort-level) |
| `--settings` JSON key | `effortLevel` (`low`, `medium`, `high`, `xhigh`; not `max`) or `modelSettings.<model>.effortLevel` | This session; `--settings` can set any key a user settings file can | [Settings reference](https://code.claude.com/docs/en/settings-reference#effortlevel), [Settings](https://code.claude.com/docs/en/settings#change-a-setting-for-one-session) |

Notes per the docs:

- `--effort` "on the main conversation's model, overrides the `modelSettings` and `effortLevel` settings for this session and doesn't persist" (CLI reference).
- Precedence, first that applies (Model configuration): `CLAUDE_CODE_EFFORT_LEVEL`; a level picked in-session (`/effort`, `/model`); `--effort`; settings (`modelSettings` entry or `effortLevel`); the model's default.
- `max` is accepted by `--effort` and the env var, but not in `effortLevel` or `modelSettings`.
- Caveat for `effortLevel` in settings: in `~/.claude/settings.json` Opus 5.5 and later ignore the top-level key; in project, local, managed settings and with `--settings` it applies to every model (Settings reference). So the `--settings` route works for Opus 5.5, but `--effort` is simpler.
- A subagent with no `effort` of its own, on the main conversation's model, runs at the level picked in the session or set with `--effort`; on another model that level does not apply (Model configuration). The Agent tool's per-subagent `effort` (the reviewer's L tier) overrides it while it runs, unless `CLAUDE_CODE_EFFORT_LEVEL` is set. Implication: setting `CLAUDE_CODE_EFFORT_LEVEL` on the orchestrator would also pin every subagent and defeat the reviewer's `effort: high`; `--effort` does not.
- `maxEffortLevel` (v2.1.267+) caps any of the above, including subagent frontmatter.

## Supported models

Model configuration lists Opus 5.5, Sonnet 5.5, Haiku 5.5, Opus 5, Sonnet 5, Opus 4.8 and Opus 4.7 with `low`, `medium`, `high`, `xhigh`, `max`. Models not listed do not support effort. An unsupported level falls back to the highest supported level at or below it. The Claude API effort page lists `claude-opus-5-5` and `claude-sonnet-5-5` as supported.

## Defaults

- Claude Code: `medium` on Opus 5.5, Sonnet 5.5 and Haiku 5.5; `high` on other models except Opus 4.7 (`xhigh`) (Model configuration, Precedence item 5). Opus 5.5 starts at `medium` unless a listed source sets a level for it.
- Claude API with no `effort`: `medium` on Opus 5.5 and Haiku 5.5, `high` elsewhere. The API page also says Sonnet 5.5 "`high` is the default on the Claude API", which differs from Claude Code's `medium` for Sonnet 5.5. The two docs disagree on what Claude Code sends; the Claude Code page governs the launch line. Setting `--effort` explicitly removes the ambiguity.

## What effort changes

From the Claude API [effort page](https://platform.claude.com/docs/en/build-with-claude/effort):

- Effort "affects **all tokens** in the response", including text, tool calls and function arguments, and thinking when active. "Lower effort also means fewer and terser tool calls."
- `low`: "Most efficient. Significant token savings with some capability reduction", suited to "subagents". `medium`: "Balanced approach with moderate token savings", for "agentic tasks that require a balance of speed, cost, and performance". `high`: "as many tokens as the task needs".
- Tool use: lower effort tends to combine operations into fewer tool calls and make fewer calls; higher effort makes more tool calls.
- Opus 5.5: "Adaptive thinking is always on and can't be turned off, so effort is the primary control for how much the model reasons and what a request costs." Sonnet 5.5: levels are recalibrated versus Sonnet 5, so run a fresh effort sweep. Level names are calibrated per model, so the same name is not the same value across models (Model configuration).
- At `xhigh` and `max`, set a large `max_tokens`; `max` "may show diminishing returns and is prone to overthinking" (Model configuration).

From Claude Code [Model configuration](https://code.claude.com/docs/en/model-config#adjust-effort-level): effort controls adaptive reasoning; "Lower effort is faster and cheaper for straightforward tasks"; "You are charged for all thinking tokens generated, even when collapsed or redacted." Thinking cannot be turned off on Opus 5.5 or Sonnet 5.5.

## Cost implication for the orchestrator

- Cost is billed per output token (thinking included), and effort scales both thinking and tool-call volume, so it is the main per-model lever on the orchestrator's spend. The docs give no multiplier per level; "moderate" and "significant" savings are the only quantities stated. Measure on the orchestrator's own runs.
- The orchestrator is a long main thread of many short, mostly mechanical steps (scripts, tables, dispatch), so `low` or `medium` is plausible; the docs warn that low effort on long agent prompts can skip a check or stop early (Haiku 5.5 guidance; no equivalent statement was found for Opus 5.5 or Sonnet 5.5).
- Changing the model or effort mid-session affects the prompt cache; the settings page notes each model has its own cache and the first request after a model switch re-reads the conversation uncached. The API page says per-message effort changes preserve the cache (beta). A launch-time `--effort` avoids both.

## Not found

- No documented flag, key or variable sets effort for the orchestrator separately from its subagents other than relying on a subagent's own `effort`; with `CLAUDE_CODE_EFFORT_LEVEL` the env var wins over subagent effort.
- No published per-level token or price multiplier for Opus 5.5 or Sonnet 5.5.

## Sources

- https://code.claude.com/docs/en/cli-reference (`--effort`)
- https://code.claude.com/docs/en/model-config (levels, models, precedence, thinking)
- https://code.claude.com/docs/en/settings (`--settings`, one-session overrides)
- https://code.claude.com/docs/en/settings-reference (`effortLevel`, `modelSettings`, `maxEffortLevel`)
- https://platform.claude.com/docs/en/build-with-claude/effort (API effort parameter)
