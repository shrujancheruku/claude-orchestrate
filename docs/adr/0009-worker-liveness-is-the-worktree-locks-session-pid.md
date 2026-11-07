# Worker liveness is the worktree lock's session pid

## Context

The state file is per session, so a claimed ticket with no PR and no worker in it may be **Stalled** (its worker is dead) or **Elsewhere** (another live session's worker is still building). Treating both as stalled starts a second worker on a live worker's branch.

## Decision

- **A ticket's old worker is live when the worktree on its branch is locked and the lock's session pid is running with the start time the lock records.** The harness writes the reason `claude agent <id> (pid <n> start <time>)`; the pid is the owning session's, so an idle worker that session still holds counts as live.
- **A dead pid, or the pid running with another start time (reused), is a dead lock;** no lock is no live worker. Either way the ticket is stalled and goes through the rescue.
- **A lock whose reason doesn't parse counts as live,** and the pass reports it for the user to unlock if it is stale. Starting a second worker costs more than a ticket left waiting.
- **A lock whose pid is an ancestor of the process checking is this session's own.** The worker is ours, and the session knows whether it has stopped it, so a rescue it asks for unlocks that lock and goes on; only another process's live pid refuses a rescue. No flag asserts "stopped": a prompt could pass one on an Elsewhere ticket.

## Consequences

- The signal depends on a lock-reason format the harness owns; if it changes, every lock reads as unparseable and so live, which fails safe and shows in the pass table.
- Rejected: the lock alone (a dead session's lock would hold the ticket forever); the branch's last push time (a live worker can go a long time between pushes, and a dead one may have pushed a minute ago).
