---
name: start
description: Start the orchestrator in the background from any Claude session in this repo, and print its session id and resume command.
disable-model-invocation: true
argument-hint: "[--greedy=N | <ticket>] [--print]"
---

Start the **orchestrator** without leaving this session. `scripts/launch` owns the launch line (name, permission mode, `--settings`, model, `--plugin-dir`), so this skill quotes it and restates none of it.

1. Run `"${CLAUDE_SKILL_DIR}/../../scripts/launch" --bg $ARGUMENTS`, plain (no wrapper, no `&`). With `--print` in the arguments, drop `--bg`: it prints the command and runs nothing.
2. If it exits 2, say its one line (a live `orchestrator` session already exists, most often) and stop. Launch nothing else.
3. Otherwise print the session id it reports and the resume command, `"${CLAUDE_SKILL_DIR}/../../scripts/launch" --resume <sessionId>`, then stop.

Never run the orchestrator in this session, and never start a second one.
