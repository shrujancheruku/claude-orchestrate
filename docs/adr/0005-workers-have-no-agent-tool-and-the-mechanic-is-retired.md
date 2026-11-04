# Workers have no Agent tool, and the mechanic agent is retired

## Context

A worker builds one ticket end to end. It used to delegate: searches to the scout and mechanical edits (renames, moves, a pattern repeated across files) to a cheaper `mechanic` agent, then read the mechanic's diff before keeping it (`agents/worker.md` before #101, commit 11fab65).

- **Workers get cheaper through fewer, smaller turns rather than delegation** (issue #43): independent calls in one turn, chained verify steps, `grep -n` then line-range reads, bounded output.

## Decision

- **A worker has no `Agent` tool** (`disallowedTools: Agent` in `agents/worker.md`). It searches and edits with its own tools and cannot delegate. The orchestrator still dispatches the scout and the reviewer.
- **The mechanic agent is retired.** `agents/mechanic.md`, its guard rules, its config-template Models row and its README entries are gone.
- **Turn rules replace delegation,** in the worker definition: make independent calls in one turn, chain verify steps, `grep -n` then line-range reads, never re-read an unchanged file, keep Bash output bounded. The report is a fixed short block.
- **A stale `orchestrate:mechanic` is an unknown role,** which the guard treats as read-only, like a scout (ADR-0002).
- **`tests/test_worker_turn_rules.py` pins it:** the worker has no `Agent` tool, `agents/mechanic.md` does not exist, and no skill, agent, hook, the README or the config template mentions the mechanic.

## Consequences

- A large mechanical change is now made by the worker's own edits.
- Configs with a mechanic Models row should drop it. The change shipped as a breaking one.
- Rejected: keeping the mechanic, which #43 retired; leaving `Agent` on and asking workers not to use it (inferred: a prompt rule a worker can forget).
- Reopen when trial data shows workers spending more on mechanical edits than delegation cost (inferred).
