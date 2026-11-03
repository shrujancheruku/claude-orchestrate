# Slot count: default 2, suggested by each pass, changed only by the user

## Context

The config's **Slots** line caps how many tickets are in flight at once. Every extra slot costs:

- **Tokens:** each slot is a full worker context, plus a reviewer per round.
- **Usage limits:** agents share the user's plan, so a limit pauses every in-flight worker at once, and more slots mean more paused or stalled tickets to recover.
- **Orchestrator load:** every report, CI failure, review round and merge passes through the orchestrator one at a time. It saturates before the plan's limits do, and no plan tier raises it.
- **Overlap:** tickets run together only when their touch maps don't overlap, and touch maps are estimates. Extra slots sit idle behind held paths, or a missed overlap costs a rebase after merge.
- **The user's attention:** gates, handbacks, third review rounds and L-tier reads all come back to the user.

Wall-clock time is the only gain, and only when the backlog is wide and its tickets independent. Setup sees a repo before any pass has run; a pass sees real touch maps, held paths and usage limits.

## Decision

- **Setup recommends 2;** 1 when a parallel-worktree collision has no fix; 3 only after asking about the user's plan. Never more.
- **Each full pass may suggest a change of one slot**, from what that pass saw, within 1 to 5:
  - **Lower** when a usage limit hit, or a slot stayed free because every takeable ticket overlapped a held path.
  - **Raise** when every slot is full, no limit hit, and the next takeable ticket overlaps no held path.
  - A suggestion above 3 names the orchestrator's cost.
- **The count changes only when the user says yes.** The pass then edits the config; it never runs a different count on its own.

## Consequences

- A raise needs one extra scout touch map on passes where every slot is full.
- One pass is a noisy sample; the user is the filter, and declining costs nothing.
- Narrowed passes (`status`, one ticket, one parent) see part of the backlog and suggest nothing.
- `/orchestrate:run --profile` may recommend a change from a whole session's history. That stays advice beside the per-pass suggestion; neither changes the count without the user.
- Reopen when trial data shows the rules suggesting the wrong way, or the orchestrator coping with more than 5.
