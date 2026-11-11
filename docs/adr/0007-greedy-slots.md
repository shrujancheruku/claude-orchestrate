# Greedy slots: an opt-in flag sizes the slot count from independent tickets

## Context

[ADR-0001](0001-slot-count.md) fixes the slot count per session: the config's **Slots** line, changed by one only when the user accepts a pass's suggestion, and a pass "never runs a different count on its own". On a wide backlog of independent tickets that leaves slots idle for a pass or more while the suggestion climbs one at a time, and the user has no way to say "run as many as can safely run at once".

The costs ADR-0001 lists still hold: tokens, usage limits that pause every worker at once, orchestrator load, overlap and the user's attention. Of these, only overlap is something a pass can measure before dispatch, from touch maps against held paths.

## Decision

- **`/orchestrate:run --greedy[=N]` is an explicit opt-in** to automatic counts. Without it, ADR-0001 stands unchanged.
- **Fill to independence.** Each greedy pass dispatches every takeable ticket whose touch map overlaps no held path, up to a cap. A ticket dispatched earlier in the pass holds its touch map, so later picks are checked against it. In-flight workers are never stopped; the count shrinks as fewer independent tickets remain.
- **A cap bounds it:** 5 by default (ADR-0001's ceiling), `N` from 1 to 8. Above 3 the pass names the orchestrator's cost, as ADR-0001's suggestion does.
- **The config is never edited.** Its **Slots** line stays the user's default for passes without the flag.
- **It holds for the session.** Every wake keeps greedy until a plain `/orchestrate:run`. Each greedy report starts `Greedy · cap N · running M`, so a compaction summary carries it.
- **A usage limit drops back** to the config's count until the reset.
- **Narrowed passes:** a parent pass is greedy; `status` and a single ticket ignore the flag.
- **No ±1 suggestion on greedy passes.** A pass that reached the cap with independent tickets waiting says so in one line.

## Consequences

- The takeable tickets come from `scripts/frontier` (since #310), not the scout, which maps only the touch maps of the tickets a pass dispatches, so scout cost scales with the cap, not the backlog. (Amended 2026-11-08; before, the scout mapped up to two more takeable tickets than the free slots.)
- Touch maps are estimates: more tickets at once means more missed overlaps and more rebases after merge. The merge sweep and hand-backs absorb these.
- Greedy was in-session state with no file behind it; the report line was what survived a compaction. (Amended 2026-11-11, #657: `scripts/launch` writes a `greedy` run-log event, `set` with the cap or `off`, and `claims --slots` and `frontier --slots` read the newest back, so a compaction loses nothing for a run started through `launch`; `launch` is also the one place the 1 to 8 range is checked. A run not started through `launch` still relies on the report line.)
- Profiles measure a greedy pass's occupancy against its cap, so idle reasons show why it ran below it (#170).
- Reopen when trial data shows greedy sessions costing more per merged ticket than a fixed count, or the orchestrator coping with more than 8.
