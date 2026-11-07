# How one Claude Code session can message and wake another

Research for [#189](https://github.com/shrujancheruku/claude-orchestrate/issues/189) (map [#188](https://github.com/shrujancheruku/claude-orchestrate/issues/188)). Sources read 2026-11-07 against Claude Code v2.1.311 (local) and docs/changelog through v2.1.319.

## Answer

- **Use cross-session messaging (`SendMessage` + `ListAgents`) between two local sessions.** It is the only option built for two separate top-level sessions. It is on by default from v2.1.224 (macOS/Linux) with no flag. A message to an idle session starts a new turn there, so it wakes a session that has ended its turn. The sender's tool result reports refusals and drops; it does not say the message was read.
- **It does not survive a restart of the receiving session as a queue.** The inbox is a socket owned by the live process, named by PID. Messages wait in that process's memory. A restarted session gets a new socket. The sender finds it again by session name. Whether undelivered messages survive the restart is **unconfirmed**; assume they are lost. Compaction is a different case: a delivered message is part of the conversation, so compaction summarizes it like any other prompt.
- **Agent teams don't fit.** A team belongs to one lead session and can't be shared across sessions. It needs `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, and in-process teammates are not restored on `/resume`.
- **Remote Control is a transport, not a separate channel.** It carries cross-session messages to sessions on other machines or in the cloud, and queues messages to an `offline` peer until it reconnects. On one machine it adds nothing.
- **Headless `claude -p --resume <id>` is a fallback**, not a way to message a live session. It starts a new process on the saved conversation. The caller gets the reply on stdout, so delivery is certain. A long-running `-p` worker can also receive `SendMessage`.
- **For the lead/orchestrator pair:** both sessions should be interactive (or background) sessions with fixed names (`--name lead`, `--name orchestrator`). They pass items with `SendMessage`, and keep durable state in a file or GitHub issue, so a restart loses nothing. Each side should treat a message as a nudge to re-read that state. Use `notify_when_idle` to learn when the other side finishes a turn.

## Comparison

| Mechanism | Wakes an idle session? | Survives restart / compaction? | Flag or setting? | Sender knows it was delivered? |
| :- | :- | :- | :- | :- |
| Cross-session `SendMessage` (local) | Yes: "When the receiving session is idle, Claude Code starts a new turn with the message." | Restart: undelivered queue is per-process (**unconfirmed**, likely lost); name-based address works again after restart. Compaction: delivered text is summarized like any prompt | None (v2.1.224+). `crossSessionInbound` can hold/refuse; default holds if receiver bypasses permissions and sender doesn't | Partly. Refusals (`Not sent`, too large, burst, refused, held, dropped at inbox) come back to the sender. Success means written to the inbox, not read |
| `ListAgents` | n/a (discovery only) | Lists live sessions only | None | n/a |
| `notify_when_idle` (on `SendMessage`) | Yes, wakes the asking session with the notice | Expires after 12 h; per-process (**unconfirmed** across restart) | v2.1.236+ both sides; main conversation only; local only | The notice is the signal |
| Agent teams | Yes, within the team (automatic delivery; idle notifications to lead) | No `/resume` restore of in-process teammates | `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, interactive only | Yes for mailbox write: "reports a message as sent only when the write to the recipient's mailbox file succeeds" |
| Remote Control | Yes (same delivery as cross-session) | Messages to an `offline` peer queue until it reconnects | `--remote-control` / `/remote-control` / `remoteControlAtStartup`, claude.ai login | Result says queued for offline peer (v2.1.261+) |
| Headless `claude -p --resume` | Starts a fresh process on the saved transcript, not a live wake | Transcript on disk survives restart | None | Yes, synchronous output |

## What the primary sources say

### Cross-session messaging

[Message your other Claude Code sessions](https://code.claude.com/docs/en/cross-session-messaging)

- Requirements: "Cross-session messaging requires Claude Code v2.1.224 or later on macOS and Linux ... When a session meets the requirements, messaging is on with nothing to enable." Check with `/list-agents` (alias `/peers`). `/status` shows the `Peer address`.
- Discovery and send: "Claude discovers the target with `ListAgents` and sends with `SendMessage`." A session "answers to the name you set with the `/rename` command or the `--name` flag." If one live session has the name, "Claude Code delivers the message on the name alone."
- **Wake:** "The receiving Claude reads the message between tool calls during an active turn, so a running tool is never interrupted. When the receiving session is idle, Claude Code starts a new turn with the message."
- Receiver outcomes: **Delivered**, **Held** (reaches Claude only on approval or a later mode/settings change), **Refused** (dropped).
- Default inbound policy without `crossSessionInbound`: a receiver that prompts for permissions delivers, unless the sender bypasses permissions. A receiver that bypasses permissions holds each message unless the sender also bypasses. A held message's dialog expires after `dialogExpiry` (5 min default). With no terminal attached to a background session, the dialog stays open. **Implication:** if the orchestrator runs with `--dangerously-skip-permissions` and the lead doesn't, set `crossSessionInbound: "accept"` on the orchestrator, or the lead's messages wait for approval.
- At most 100 held messages; the receiver queues at most 50 accepted messages for Claude to read. Rate limits and loop cutting apply.
- Incoming messages carry no user authority: they "can't approve anything", "can't change configuration", and commands in them "arrive as plain text".
- Non-interactive: "Claude Code binds an inbox socket for a `claude -p` session like an interactive one, so a long-running `-p` worker can receive messages". Bare mode binds no socket. To take messages unattended, start the worker with `crossSessionInbound: "accept"` in `--settings`.
- Socket: per-session Unix socket, exported as `CLAUDE_CODE_MESSAGING_SOCKET` (and `CLAUDE_CODE_MESSAGING_TOKEN`) to hooks and Bash, so a script can post into its own session.
- Limits: plain text only, about 1M characters per message.

[Errors reference](https://code.claude.com/docs/en/errors) (sender-side results):

- `Message too large for cross-session delivery` and `Too many messages to this session just now` appear "in the sending session's tool result". The receiver got nothing.
- `Cross-session message was dropped at the recipient session's inbox` (queue full, too fast, repeat, relay loop): "Before v2.1.238, the sending session got no report".

[Changelog](https://code.claude.com/docs/en/changelog), delivery reporting:

- 2.1.238: "sending to a session on this machine that refuses inbound messages ... now reports 'refused' to the sender instead of a silent success"; dropped messages now reported.
- 2.1.239: "`SendMessage` to your own name says so instead of 'no agent named …'". This is the error when no live session has the name.
- 2.1.271: "headless senders now get a delivery notice, and `SendMessage` results no longer imply it was read".
- 2.1.288: "Fixed Claude reporting a message to another session as delivered when that session held it".
- 2.1.236: "Added `notify_when_idle` ... opt-in, one-shot, no polling".

### Restart and compaction (inference plus observation)

- Observed locally: this session's socket is `/tmp/cc-socks/8872.sock`, and 8872 is the parent `claude` process's PID. The address is tied to the process, so a restarted or resumed session binds a new one. Name-based addressing still finds it, because `ListAgents` lists live sessions by name, and `/rename`/`--name` names persist (agent-view docs: "keeps a name you set with `/rename` ... across that restart").
- **Unconfirmed:** whether messages queued but not yet read (the 50-message queue, held messages) are written to disk and survive a receiver restart. No doc says they are. Assume they are lost, and keep durable state outside the message.
- **Unconfirmed:** what the sender sees when the target is closed (no live socket). The 2.1.239 entry implies a "no agent named …" result. Either way the sender is told it failed, not told it succeeded.
- Compaction: a delivered message becomes a turn in the conversation, so [compaction](https://code.claude.com/docs/en/context-window) summarizes it with the rest. No doc says delivered peer messages get special treatment.
- Background sessions ([agent view](https://code.claude.com/docs/en/agent-view)) are restarted by a supervisor if they crash, and resumed from the saved conversation when replied to. That makes a background session a good host for the orchestrator. The agent-view page doesn't say whether a cross-session message restarts an exited background session (**unconfirmed**). Changelog 2.1.311 fixes "a background session that crashed between turns not being restarted while it was attached, pinned, or had a scheduled task or wake-up set".

### Agent teams

[Orchestrate teams of Claude Code sessions](https://code.claude.com/docs/en/agent-teams)

- "Agent teams are experimental and disabled by default. Enable them by setting `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`". Spawning teammates "requires an interactive session".
- Delivery: "when teammates send messages, they're delivered automatically to recipients." Idle notifications: when a teammate stops, "it automatically notifies the lead and includes its final answer".
- Mailbox: a JSON file at `~/.claude/teams/{team-name}/inboxes/{agent-name}.json`. "Claude Code reports a message as sent only when the write to the recipient's mailbox file succeeds".
- Limits that rule it out here: "One team per session ... You can't create additional named teams or share a team across sessions." "No session resumption with in-process teammates: `/resume` and `/rewind` do not restore in-process teammates." "No nested teams". "No background subagents from in-process teammates". A teammate can't run `/orchestrate:run`'s background workers.

### Remote Control

[Remote Control](https://code.claude.com/docs/en/remote-control)

- Activation: `claude remote-control`, `claude --remote-control`, `/remote-control`, or `remoteControlAtStartup: true`. Requires a claude.ai login.
- "with cross-session messaging, the same connection carries messages between your own sessions on different machines." Remote Control does not open a second messaging channel.
- Cross-session doc: a message to an `offline` peer "goes through, but arrives only after that session's machine reconnects". Changelog 2.1.261: the result "now says delivery is queued until that machine reconnects".
- "Local process must keep running ... the session goes offline until you bring it back."

### Headless `claude -p`

[Run Claude Code programmatically](https://code.claude.com/docs/en/headless)

- `claude -p "<prompt>" --resume "$session_id"` continues a saved conversation in a new process. On v2.1.257+, `--continue` "opens a background session that has finished, but not one that is still running."
- **Unconfirmed:** what happens if you `--resume` a session that is still live in another process. Don't use this for a live peer.

### Channels (adjacent)

[Channels](https://code.claude.com/docs/en/channels) push MCP-server events into a running session, need `--channels`, and are in research preview. "Events only arrive while the session is open". They are meant for external systems, not for one session talking to another.

## Unconfirmed

- Undelivered or held messages surviving a receiver restart.
- Exact sender result when the named target has no live session.
- Whether a cross-session message restarts an exited background session.
- `-p --resume` against a session that is live in another process.
