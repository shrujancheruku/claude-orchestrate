# How the lead launches a background orchestrator session

Research for [#197](https://github.com/shrujancheruku/claude-orchestrate/issues/197) (map [#188](https://github.com/shrujancheruku/claude-orchestrate/issues/188)), following "Decide the channel between the orchestrator and the lead" (#192). Builds on `docs/research/session-messaging.md` (branch `research/session-messaging`).

Sources read 2026-11-07: Claude Code docs at code.claude.com (agent view, cross-session messaging, CLI reference, permission modes, env vars, errors, hooks, changelog through v2.1.319), and local checks against Claude Code v2.1.311. No background session was started and no message was sent while researching.

Labels: **source** = confirmed by a primary source; **local** = confirmed by a harmless local check; **unknown** = no source settles it.

## Answer

- **Launch command:** `claude --bg --name orchestrator "/orchestrate:run --greedy"` from the repo directory. `--bg` takes the prompt as its positional argument, accepts `--name`, prints `backgrounded · <id> · <name>` and returns at once. A slash command as that first prompt is very likely fine but not stated for `--bg` itself. Running it from the lead's Bash tool should work in a trusted directory, but two things are open: whether a `--bg` started from inside a session inherits the "nested session" marker (which turns off transcript saving and `claude agents` listing), and whether `--bg` has a non-TTY gate. Strip `CLAUDE_CODE_CHILD_SESSION`/`CLAUDECODE` (or set `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1`) when launching to be safe.
- **Permission mode:** a `--bg` session starts like a fresh `claude` in that directory: `--permission-mode` flag, else `permissions.defaultMode`, else the built-in default (`auto` on v2.1.283+). It does **not** inherit the lead's mode. The launcher can match it only by passing `--permission-mode <mode>` itself; no env var tells a Bash command the lead's mode (hooks get `permission_mode` in their JSON input). Bypass mode in a background session needs `skipDangerousModePermissionPrompt` in `~/.claude/settings.json`, or it silently starts in Manual.
- **Held messages with no terminal:** a held message's approval dialog stays open past `dialogExpiry` while nothing is attached; the countdown starts once someone attaches. The row in `claude agents` moves to `Needs input` showing `approve message from <sender>`. The sender's result says the message was held, not delivered (fix in v2.1.288; v2.1.271 for headless senders). Holding only happens when the two sessions are in different permission classes (bypass vs prompting), so launching the orchestrator in the lead's class, or setting `crossSessionInbound: "accept"` via `--settings`, avoids it.
- **Two live sessions with one name:** `ListAgents` adds a short identifier to each same-named row (and shows each one's working directory), and Claude addresses the message by identifier instead of name. An `@` mention of a shared name makes Claude ask the user which one. What a bare-name `SendMessage` returns when the name is ambiguous is **unknown**.
- **Exited background session:** no source says a cross-session message restarts it. An exited or idle-retired background session has no running process, so it binds no inbox socket and is not listed by `ListAgents`; a send by name should fail (inferred). Documented wake paths are an agent-view reply, `claude attach <id>`, `claude respawn <id>`, or `claude --resume <session-id> --bg "<prompt>"` (continues under the same ID). The supervisor retires a background session that has finished its turn and sat unattached for about an hour.

## Findings per question

### 1. Starting a named background session from a script or Bash tool

| Claim | Status | Source |
| :- | :- | :- |
| `claude --bg "<prompt>"` starts a background session and returns immediately; the prompt is the positional argument, not `-p` | source | [agent view, from your shell](https://code.claude.com/docs/en/agent-view#from-your-shell); [CLI reference `--bg`](https://code.claude.com/docs/en/cli-reference#cli-flags) |
| `--bg` with `-p`/`--print` is rejected | source | [errors: Conflict between `--bg` and `--print`](https://code.claude.com/docs/en/errors#conflict-between-bg-and-print) |
| `claude --bg --name "flaky-test-fix" "<prompt>"` sets the name; output is `backgrounded · 7c5dcf5d · flaky-test-fix` plus `claude attach/logs/stop <id>` lines | source | agent view; changelog 2.1.176 ("Fixed `claude --bg -cn <name>` not seeding the session name"), 2.1.144 ("`claude --bg --name <label>` now echoes the name") |
| Put `--bg` first; ambiguous positions exit 1 with `Couldn't tell whether --bg is a flag or text here` | source | agent view; [errors](https://code.claude.com/docs/en/errors#couldnt-tell-whether-bg-is-a-flag-or-text) |
| A slash command (skill or plugin command) as the first prompt: agent view's dispatch input sends "Skills, your own commands, and prompt-expanding built-ins such as `/init` ... to a new background session as its first prompt". For `claude --bg "/orchestrate:run --greedy"` the docs don't say | unknown (strongly implied) | agent view, dispatch input. Changelog 2.1.309 mentions "a session whose first prompt, such as `/code-review`"; CLI ref mentions "sessions whose first prompt was `/loop`" |
| In a script, `--bg` in an untrusted directory exits with `Workspace not trusted`; in a trusted directory it starts (scripts are an expected caller) | source | agent view; [errors: Workspace not trusted when dispatching](https://code.claude.com/docs/en/errors#workspace-not-trusted-when-dispatching-a-background-session) |
| The background session gets the dispatching shell's `PATH`, provider selection, model aliases and `CLAUDE_CODE_EXTRA_BODY` | source | agent view, from your shell |
| The lead's Bash tool runs with `CLAUDECODE=1`, `CLAUDE_CODE_CHILD_SESSION=1`, `CLAUDE_CODE_MESSAGING_SOCKET=...`, and no TTY on stdin/stdout | local | `env` and `[ -t 0 ]` inside this session's Bash tool |
| A nested interactive `claude` started from a Bash tool "is automatically excluded from `--resume`, `--continue`, up-arrow history, and the `claude agents` list", and saves no transcript. `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` overrides; the doc names "a background launcher first started by Claude Code's Bash tool" as a misclassification case | source | [env vars `CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE`](https://code.claude.com/docs/en/env-vars); [errors: Transcript saving is off](https://code.claude.com/docs/en/errors#transcript-saving-is-off-child-session-marker) |
| Whether a `--bg` session dispatched from a Bash tool inherits that marker (the session is hosted by the supervisor, which "starts from your home directory" and may itself be first started by that `--bg`) | unknown | no doc; no supervisor was running to inspect |
| Changelog 2.1.144: "`claude agents` / `--bg` rejection messages now name the specific gate (non-TTY, env var, or setting)". Whether `--bg` (not just `claude agents`) refuses a non-TTY caller | unknown | changelog; agent view's "such as in a script" wording suggests `--bg` works without a TTY |
| `CLAUDE_CODE_DISABLE_AGENT_VIEW` / `disableAgentView` turns off `--bg` entirely | source | env vars; [settings `disableAgentView`](https://code.claude.com/docs/en/settings-reference#disableagentview) |
| `claude agents --json` prints active sessions (interactive and background) for scripting, no TTY needed; `--all` adds completed background sessions | source + local | `claude agents --help`; local run shows fields `id`, `pid`, `cwd`, `kind`, `sessionId`, `name`, and `status` (`busy`) for live rows or `state` (`done`) for finished background rows |

Inside a session, `/bg` (moves the current conversation) and `/fork` (copies it into a new background session) also exist, but they are typed commands, not something a script or the Bash tool can call.

### 2. Permission mode of the launched session

| Claim | Status | Source |
| :- | :- | :- |
| "Dispatched from `claude agents` started in a shell, or with `claude --bg`: the new session starts the way a new `claude` session in that directory would" | source | [agent view, permission mode](https://code.claude.com/docs/en/agent-view#permission-mode) |
| Start order: `--permission-mode`/`--dangerously-skip-permissions`, then `permissions.defaultMode`, then the built-in default (`auto` in a terminal on v2.1.283+). `auto`/`bypassPermissions` in project `.claude/settings*.json` don't take effect | source | [permission modes, which mode a session starts in](https://code.claude.com/docs/en/permission-modes#which-mode-a-session-starts-in) |
| Only sessions dispatched from an agent view opened with `←`, or backgrounded with `/bg`/`←`, inherit the caller's mode. `--bg` does not | source | agent view, permission mode |
| `--permission-mode` is a top-level flag (`acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`) and combines with `--bg`; the mode persists across supervisor restarts | source + local | `claude --help`; CLI reference `--dangerously-skip-permissions` row ("For sessions started with `--bg`, the mode persists when the supervisor restarts the session"); agent view, what persists across restarts |
| Bypass in a background session needs `skipDangerousModePermissionPrompt: true` in `~/.claude/settings.json` or managed settings; otherwise it starts in Manual with `Bypass permissions was requested at launch and ignored` | source | [permission modes, bypass ignored in a background session](https://code.claude.com/docs/en/permission-modes#bypass-permissions-ignored-in-a-background-session) |
| No env var exposes the current permission mode to a Bash command; hooks receive `permission_mode` (Manual arrives as `"default"`) | source (absence: env-var list has none) | [env vars](https://code.claude.com/docs/en/env-vars); [hooks common input](https://code.claude.com/docs/en/hooks) |
| `CLAUDE_CODE_EVEN_MORE_DANGEROUSLY_SKIP_PERMISSIONS` has no effect in background sessions or in `claude` run from a Bash tool | source | permission modes, why the critical-path removal prompt stays on |

So the launcher can make the orchestrator's mode match its own only by passing it explicitly. The lead's Claude knows its own mode from context, or a hook could record `permission_mode` to a file for the launch script to read.

### 3. A held message on a background session with no terminal

| Claim | Status | Source |
| :- | :- | :- |
| With no `crossSessionInbound`, a prompting receiver holds only messages from a bypassing sender; a bypassing receiver holds everything except from another bypassing sender. Plan counts as bypassing in interactive sessions with bypass available; `auto`, `acceptEdits`, `dontAsk` count as prompting | source | [cross-session messaging, control inbound messages](https://code.claude.com/docs/en/cross-session-messaging#control-inbound-messages) |
| "While no terminal is attached to a background session, Claude Code leaves the dialog open past the deadline. After you attach, if the dialog stays unanswered for a full deadline period, Claude Code closes it and drops the message." At most 100 held messages, oldest dropped | source | same |
| The background session's row moves to `Needs input` and shows `approve message from` with the sender's address and claimed name | source | agent view version history, v2.1.257 |
| A reply typed in agent view doesn't answer a pending dialog; you must attach. v2.1.319 keeps such a reply in the box with a hint | source | agent view, peek and reply; changelog 2.1.319 |
| Sender side: v2.1.288 "Fixed Claude reporting a message to another session as delivered when that session held it"; v2.1.271 "headless senders now get a delivery notice, and `SendMessage` results no longer imply it was read" | source | [changelog](https://code.claude.com/docs/en/changelog) |
| Exact wording of the sender's held result | unknown | not documented |
| A later permission-mode class change on the receiver re-applies the rules and releases messages it now accepts; an explicit `crossSessionInbound: "accept"` (for example via `--settings`) delivers without a dialog | source | cross-session messaging |

A held message to an unattended orchestrator sits until someone attaches, so the orchestrator stalls on lead messages indefinitely. Launching in the same permission class as the lead, or with `--settings '{"crossSessionInbound":"accept"}'`, removes the hold.

### 4. `ListAgents` and `SendMessage` when two live sessions share a name

| Claim | Status | Source |
| :- | :- | :- |
| "Several sessions share the name, or Claude Code couldn't check everywhere your sessions run: Claude adds a short identifier to each row of its listing and uses the identifier in the address." One session with the name: delivered on the name alone | source | [cross-session messaging, see which sessions Claude can reach](https://code.claude.com/docs/en/cross-session-messaging#see-which-sessions-claude-can-reach) |
| `/list-agents` shows each local session's working directory so same-named sessions can be told apart (withheld while on Remote Control) | source | same |
| An `@` mention matching several live sessions makes Claude ask the user which one before sending | source | same, message another session |
| Names are not unique: `--name` and `/rename` don't refuse a taken name for interactive sessions. Backgrounding a conversation whose name is already a background row numbers the new row `my-session (2)` | source, implied: the docs describe same-named live sessions and "`/rename` followed by an unused name"; numbering is documented for `/bg` only | cross-session messaging; agent view v2.1.246 |
| What `claude --bg --name orchestrator` does when a live `orchestrator` already exists, and what a bare-name `SendMessage` returns when ambiguous | unknown | not documented |
| v2.1.260 fixed a backgrounded session appearing twice in `ListAgents` as a phantom interactive twin; v2.1.238 hid agent view's pre-warmed idle worker from `ListAgents` | source | changelog |

The lead's "is a live session named `orchestrator`?" check can be done from a script with `claude agents --json` (filter `name == "orchestrator"` and a live `pid`/`status`), before launching, so it doesn't make a duplicate.

### 5. Does a cross-session message restart an exited background session?

| Claim | Status | Source |
| :- | :- | :- |
| `ListAgents` lists local sessions only when they bind an inbox socket; the socket belongs to the running process | source | cross-session messaging; prior research |
| A background session that has finished or is waiting for its next message, and stays unattached for about an hour, has its process stopped by the supervisor. "The next time you attach or reply, the session resumes where it left off." Pinning (`Ctrl+T` in agent view) keeps the process running | source | [agent view, the supervisor process](https://code.claude.com/docs/en/agent-view#the-supervisor-process) |
| "When you reply or attach, Claude restarts it from its saved conversation" refers to agent view replies and `claude attach`, not `SendMessage` | source | agent view, status markers |
| `claude respawn <id>` restarts a running or stopped session from its saved conversation; `claude --resume <session-id> --bg "<prompt>"` continues that session under the same ID (or starts a copy with a `note:` if it is still running) | source | agent view, manage sessions from the shell; v2.1.257 |
| A crashed session is restarted by the supervisor; v2.1.311 fixed restart when "attached, pinned, or had a scheduled task or wake-up set" | source | agent view; changelog 2.1.311 |
| v2.1.265: "Fixed background (`--bg`) sessions occasionally being retired mid-turn when a message arrived just before the idle timeout" | source | changelog |
| Whether a `SendMessage` to a retired or stopped background session restarts it | unknown; docs silent, and the socket model implies it doesn't (the session isn't listed, so the send fails like any unknown name) | inference |
| `claude agents --json --all` shows finished background sessions with `state: "done"` and no `pid` | local | local run |
| Pinning is only documented as an agent-view keystroke; no CLI flag to pin at launch | unknown (none found) | `claude --help`, `claude agents --help` |

## Implications for the lead/orchestrator design

- The launch is one command, `claude --bg --name orchestrator --permission-mode <lead's mode> "/orchestrate:run --greedy"`, run in the repo directory. Strip `CLAUDE_CODE_CHILD_SESSION` and `CLAUDECODE` (`env -u ...`) or set `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` until the nested-marker question is settled.
- Check for an existing orchestrator with `claude agents --json`, not `ListAgents` alone, so the lead also sees a `done` background session to wake instead of starting a second one.
- To wake an orchestrator whose process was retired, the lead should run `claude --resume <session-id> --bg "<nudge>"` or `claude respawn <id>`, not rely on `SendMessage`.
- Keep the orchestrator's permission class equal to the lead's, or pass `--settings '{"crossSessionInbound":"accept"}'`, so lead messages are never held on an unattended session.

## Unknown

- `claude --bg` with a slash command as its positional prompt (implied to work, not stated).
- Whether a `--bg` dispatched from a Bash tool is treated as a nested session, and whether `--bg` has a non-TTY gate.
- Exact sender result text for a held message, and for a bare-name send when the name is ambiguous.
- What `--bg --name` does when the name is already live.
- Whether a cross-session message restarts a retired or stopped background session (assume no).
- Whether a pending scheduled wake-up or `/loop` keeps a background session from being retired.
