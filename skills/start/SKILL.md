---
name: start
description: Start the orchestrator in the background from any Claude session in this repo, and print its session id and resume command.
disable-model-invocation: true
argument-hint: "[--greedy=N | <ticket>] [--profile] [--print]"
---

Start the **orchestrator** without leaving this session. `scripts/launch` owns the launch line (name, permission mode, `--settings`, model, `--plugin-dir`), so this skill quotes it and restates none of it.

1. **Arguments.** The arguments are `$ARGUMENTS`. Accept only these forms, each at most once: `--greedy`, `--greedy=<digits>`, `<digits>` (a ticket number, not together with `--greedy`), `--profile` and `--print`. If anything else is there (`--lead`, `--resume`, `--permission-mode`, any other flag, any shell character), refuse in one line and launch nothing. Never paste the raw arguments into the command: build it from the accepted tokens only.
2. **Permission mode.** Pass none: `scripts/launch` picks it (`acceptEdits` by default) and adds the per-session `disableAutoMode`, because workers inherit the orchestrator's mode and the auto-mode classifier skips their file edits (see Permission mode in `docs/guide.md`).
3. Run `"${CLAUDE_SKILL_DIR}/../../scripts/launch" --bg [<accepted tokens>]`, plain (no wrapper, no `&`). With `--print`, drop `--bg`: it prints the command and runs nothing.
4. If it exits 2, say its one line (a live `orchestrator` session already exists, most often) and stop. Launch nothing else.
5. Otherwise print the session id it reports and the resume command, `"${CLAUDE_SKILL_DIR}/../../scripts/launch" --resume <sessionId> --bg`, then stop.

Never run the orchestrator in this session, and never start a second one.
