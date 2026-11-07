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

<!-- Optional Projects board. Setup writes the line itself when you opt in; `scripts/board` syncs it every pass and does nothing without it:
- **Board:** <project URL>
-->

## Slots

- **Slots:** <n, default 2>. How many tickets are in flight at once. Parallel workers multiply token use, so this is the throttle. `/orchestrate:run` suggests changes of one; it changes this line only when the user says yes.
- **Exclusive holds:** <e.g. "while #42 is open, nothing else touches `db/`" | none>

Tickets run in parallel only when their touch maps don't overlap outside the shared files.

## Verify

Commands every worker runs before opening a PR. `verify --config` reads them from here and runs each exactly as written, so nothing else restates them. Write each command as a top-level bullet that opens with one backticked span; text after the span is a remark, and any other line is prose. Commands in a group are independent. A bullet that is just `` `--then` `` starts a group that depends on the ones above it (codegen or a build first), skipped if one of them fails.

- `<command>` <optional remark>

## Shared files

A **shared file** is one many PRs edit. The `merge` script resolves collisions in the files listed here by replaying each PR's lines onto main, so nobody merges them by hand; a conflict in any other file is handed to the worker.

- **Shared files:** one nested bullet per file (a path or glob in backticks), with the only change each PR may make in it.
  - `<path>`: <per-PR rule, e.g. "add one line under Unreleased">
- **Generated:** one nested bullet per generated glob (in backticks), with the command that regenerates it (in backticks). On a conflict the `merge` script takes main's side and runs the command against the PR's sources in the sweep worktree (a file that is also listed as shared is treated as shared; the command may only change files matching generated globs); if it fails the conflict goes back to the worker. Write `none` if there is no generated code.
  - `<glob>`: `<regenerate command, e.g. npm run codegen>`
- **Orchestrator-owned edits:** <status updates only the orchestrator makes, e.g. "tick the roadmap box after merge" | none>

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
| Orchestrator | opus (used when the lead launches it; a hand-started session keeps its own) | |
| Worker | sonnet; haiku for S tickets (at most <3> files, the **Tiers** file limit) | medium |
| Scout | haiku | low |
| Reviewer | S haiku · M sonnet · L sonnet (high effort) | medium |

## Review

- **Instruction files:** <paths the reviewer checks against>
- **Risk paths** (any PR touching them is reviewed as L): <globs>
- **Tiers:** S is at most <50> changed lines in at most <3> files, none on a risk path; L touches a risk path or changes more than <400> lines; M is everything else. A `size:S|M|L` label on the ticket overrides.
- **Repo-specific checks:** <what the reviewer judges, e.g. "docs updated">. An entry may carry a command, marked `run:` and backticked, e.g. "codegen is up to date (run: `make codegen && git diff --exit-code`)"; plain backticked text is never executed. A `run:` with no backticked command (e.g. "must run: the tests") fails the gate as malformed. The script gate runs each marked command before any reviewer, so mechanical failures cost no review round.
- **Post reviews:** <yes | no>. When yes, the reviewer posts each round on the PR itself.
- **CI wait limit:** <minutes>. Optional; delete the line for the default of 10. `scripts/ci-times` reports CI as over the limit when the p90 push-to-green of the last 10 merged PRs exceeds it.
