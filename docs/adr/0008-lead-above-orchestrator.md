# A lead session sits above one orchestrator

## Context

The orchestrator runs one backlog: dispatching workers, reviewing, merging. Decisions beyond a ticket (triaging discovered work, writing ADRs, resolving an ADR conflict) have had no owner but the user.

- **Subagents can't nest** (ADR-0005): a worker has no `Agent` tool, so an orchestrator cannot host a lead as a subagent, nor a lead an orchestrator.
- **Merging belongs to the orchestrator** (ADR-0004).

## Decision

- **A lead session sits above one orchestrator.** Each layer is its own session, because subagents cannot nest.
- **Sessions coordinate through the tracker as the record,** with nudges by fixed session name. A nudge only says to look; the tracker holds what to do.
- **Lead mode is detected, not configured.** A live lead is what turns it on; there is no config field.
- **Two labels carry the hand-offs:** `needs-lead` (waiting on the lead; may sit beside `ready-for-agent` on a claimed stop, or alone on an ADR-conflict ticket whose PR is held) and `lead` (a ticket the lead owns; never with `ready-for-agent`).
- **The lead is the only ADR writer while live,** and never merges: ADR-0004 holds.
- **The lead answers the orchestrator's asks, or bubbles them up.** While a lead is live, every ask the orchestrator would put to the developer (a third review round, a hand-back, a config gate, a parent to close, a slot suggestion) goes to the lead, once, and the lead's reply is the answer. The lead decides what it answers itself (judgment calls inside the build loop) and what it relays to the developer (config gates, Human-only steps, permissions, anything it is unsure of), with a recommendation; it never answers a relayed ask on the developer's behalf. The orchestrator asks the developer directly only for a permission the lead cannot grant, or when the lead cannot be reached. (Amended 2026-11-08; before, the lead relayed every ask without deciding it.)
- **The Orchestrator Models row** is used when the lead launches the orchestrator; a hand-started session keeps its own model.
- **The lead watermark** (`.claude/orchestrate-lead.json`, gitignored) records how far the lead has read the tracker.

## Consequences

- Two sessions to run instead of one; the cost is context isolation per layer.
- A stale nudge is harmless: the tracker is re-read.
- Rejected: a lead as a subagent (cannot nest, ADR-0005); a config switch for lead mode (it could disagree with whether a live lead exists).
