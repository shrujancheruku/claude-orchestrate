# Verdicts carry an action list the run skill executes

Extends [ADR-0014](0014-libraries-raise-clis-decide.md) and [ADR-0017](0017-prompt-budgets.md).

## Context

A `next:` word says what happened; what to do about it was still prose in `skills/run/SKILL.md`. Most `lead-route` verbs had a paragraph spelling out a fixed label swap, a fixed SendMessage or a fixed script call with no judgement in it, and every new word added another. The scripts took over the rules while the prompt kept growing (ADR-0017).

## Decision

- **No judgement means it becomes an action.** After its `next:` line a script may print one `action: <verb> <args>` line per step, the args shlex-quoted (`common.action_line` is the one writer). The vocabulary is closed: `dispatch <ticket>`, `send <msg-id> <to> [<args>]`, `run <script> <args>`, `watch <script> <args>`, `decide <question-id>` (the only place judgement is called for) and `ask <decision-file>`.
- **ADR-0014 still holds.** There is still exactly one `next:` line and the exit codes are unchanged; the action lines come after it, so a reader that ignores them sees today's output.
- **Message texts live in one file**, `scripts/messages.txt`, by id. A `send` names an id in that file (the script refuses to print one that is not there), and prompts quote the id, never the text.
- **`run` names only scripts the guard approves for the orchestrator.** A test builds each printed `run` call and checks it against `ORCHESTRATOR_FORMS` in `hooks/guard.py`.
- **Prototype scope.** Only `lead-route` prints actions: `RESUME` (`send`), `FALLBACK` (`run`) and `RESEND` (`send`, then `run`). `merge` and `watch-ci` wait for one live run of this prototype.
- **The run skill executes the lines in one short section** and loses the paragraph of each verb that became an action list.

## Consequences

- A new mechanical verdict is a line in the script and, for a message, a line in `scripts/messages.txt`; the prompt does not change.
- The verbs that stay prose, and why: `REDISPATCH` (a brief to write), `ESCALATE` (a model choice), `ABANDON` and `HANDBACK` (a worker to stop and its report to wait for), `HOLD-PR`, `ASK-DEV`, `LEAD-TICKET`, `LEAD-PR` (a table row or a merge to skip), `ELSEWHERE` and `UNCAP` (nothing to do).
- Rejected: printing the action lines before `next:` (the closing line would stop being the last); a free-form shell command per action (the guard's forms are the allow-list, so `run` names a script, not a command line).
