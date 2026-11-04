# Orchestration config

<!-- Read by /orchestrate:run and its agents. One source per rule: point at files that already hold a fact (Makefile, CI, instruction files), and write here only what lives nowhere else. -->

## Backlog

- **Tracker docs:** `docs/agents/issue-tracker.md` (operations, blocking, claiming) and `docs/agents/triage-labels.md` (label strings), from `/setup-matt-pocock-skills`. Takeable tickets are open, `ready-for-agent`, unassigned and unblocked, with no open sub-issues and no `wayfinder:` label, taken in ascending issue number. Claim = assign to the user.
- **Domain docs:** `docs/agents/domain.md` (the glossary and ADRs workers and reviewers follow).

<!-- Only when docs/agents/ doesn't exist, replace the two lines above with:
- **Backlog in config:** yes
- **Sequence:** <where the order lives: a file and section, a milestone, or a label and sort>
- **Tracker:** <host; how blocking is expressed; claim = assign to the user; the label that marks a ticket ready>
-->

## Slots

- **Slots:** <n, default 2>. How many tickets are in flight at once. Parallel workers multiply token use, so this is the throttle. `/orchestrate:run` suggests changes of one; it changes this line only when the user says yes.
- **Exclusive holds:** <e.g. "while #42 is open, nothing else touches `db/`" | none>

Tickets run in parallel only when their touch maps don't overlap outside the shared files.

## Verify

<Commands every worker runs before opening a PR, per directory if they differ.>

## Shared files

<Files many PRs touch, and the only change each PR may make in them. Name the status updates the orchestrator owns. Generated code counts: say how a worker regenerates it after a rebase, so it never merges generated files by hand.>

## PRs and merging

- **Branch:** `<number>-<slug>`
- **Commits:** <convention>
- **PR title:** <convention>. **Body:** <closing reference, e.g. "Closes #<n>">
- **Method:** <squash | merge | rebase>
- **Orchestrator merges:** <yes | no>. Rule: CI green, every dependency merged, branch up to date, reviewer approved.

## Gates

- **Start gates:** <tickets or labels that wait for the user before a worker starts>
- **Merge gates:** <tickets, labels or paths that wait for the user's go-ahead to merge>

## Human-only steps

<Actions only the user performs; workers stop and report when they reach one.>

## Environment

<Worktree setup commands; parallel-worktree collisions and their fix; what .worktreeinclude carries.>

## Models

| Role | Model | Effort |
|---|---|---|
| Orchestrator | the session's model | |
| Worker | sonnet | medium |
| Scout, mechanic | haiku | low |
| Reviewer | S haiku · M sonnet · L sonnet, then an orchestrator read | medium |

## Review

- **Instruction files:** <paths the reviewer checks against>
- **Risk paths** (any PR touching them is reviewed as L): <globs>
- **Tiers:** S is at most <50> changed lines in at most <3> files, none on a risk path; L touches a risk path or changes more than <400> lines; M is everything else. A `size:S|M|L` label on the ticket overrides.
- **Repo-specific checks:** <e.g. "codegen is up to date", "docs updated">
- **Post reviews:** <yes | no>. When yes, the reviewer posts each round on the PR itself.
