# Orchestration config

<!-- Read by /orchestrate:run and its agents. One source per rule: point at files that already hold a fact (Makefile, CI, instruction files), and write here only what lives nowhere else. -->

## Backlog

- **Sequence:** <where the order lives: a file and section, a milestone, or a label and sort>
- **Tracker:** <host; how blocking is expressed; claim = assign to the user>
- **Sizes:** <where each ticket's S/M/L lives>. A ticket with no size is M.

## Lanes

| Lane | Paths | Notes |
|---|---|---|
| <name> | <globs> | <e.g. "exclusive while X is open: nothing else touches Y"> |

At most one open PR per lane.

## Verify

<Commands every worker runs before opening a PR, per lane if they differ.>

## Shared files

<Files many PRs touch, and the only change each PR may make in them. Name the status updates the orchestrator owns. Generated code counts: say how a worker regenerates it after a rebase, so it never merges generated files by hand.>

## PRs and merging

- **Branch:** `<lane>/<number>-<slug>`
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
- **Repo-specific checks:** <e.g. "codegen is up to date", "docs updated">
