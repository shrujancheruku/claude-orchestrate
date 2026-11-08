---
name: start
description: Start the orchestrator in the background from any Claude session in this repo, and print its session id and resume command.
disable-model-invocation: true
argument-hint: "[--greedy=N | <ticket>] [--print]"
---

Start the **orchestrator** without leaving this session. `scripts/launch` owns the launch line (name, permission mode, `--settings`, model, `--plugin-dir`), so this skill quotes it and restates none of it.

1. **Arguments.** The arguments are `$ARGUMENTS`. Accept only these forms, each at most once: `--greedy`, `--greedy=<digits>`, `<digits>` (a ticket number, not together with `--greedy`) and `--print`. If anything else is there (`--lead`, `--resume`, `--permission-mode`, any other flag, any shell character), refuse in one line and launch nothing. Never paste the raw arguments into the command: build it from the accepted tokens only.
2. **Permission mode.** Pass this session's own: `--permission-mode <your own permission mode>`, never a broader one. If you cannot tell what it is, ask the developer once; if they don't answer, use `default`.
3. Run `"${CLAUDE_SKILL_DIR}/../../scripts/launch" --bg --permission-mode <mode> [<accepted tokens>]`, plain (no wrapper, no `&`). With `--print`, drop `--bg`: it prints the command and runs nothing.
4. If it exits 2, say its one line (a live `orchestrator` session already exists, most often) and stop. Launch nothing else.
5. Otherwise print the session id it reports and the resume command, `"${CLAUDE_SKILL_DIR}/../../scripts/launch" --resume <sessionId> --bg --permission-mode <mode>`, then stop.

Never run the orchestrator in this session, and never start a second one.
