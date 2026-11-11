# Orchestration config

<!-- Read by /orchestrate:run and its agents. One source per rule: point at files that already hold a fact (Makefile, CI, instruction files), and write here only what lives nowhere else. -->

## Backlog

- **Tracker docs:** `docs/agents/issue-tracker.md` (operations, blocking, claiming) and `docs/agents/triage-labels.md` (label strings), from `/setup-matt-pocock-skills`. Takeable tickets are what `scripts/frontier` lists, in ascending issue number. Claim = assign to the user.
- **Domain docs:** `docs/agents/domain.md` (the glossary and ADRs workers and reviewers follow).

<!-- Only when docs/agents/ doesn't exist, replace the two lines above with:
- **Backlog in config:** yes
- **Sequence:** <where the order lives: a file and section, a milestone, or a label and sort>
- **Tracker:** <host; how blocking is expressed; claim = assign to the user; the label that marks a ticket ready>
-->

<!-- Optional extra trusted authors. Only text from an OWNER, MEMBER or COLLABORATOR of the repo steers the run (comments, reviews, review threads: ADR-0015); list other GitHub logins you want heard, backticked, on one line (a login matches exactly: list a bot with its suffix, e.g. `dependabot[bot]`):
- **Trusted authors:** `<login>`, `<login>`
-->

<!-- Optional Projects board. Setup writes the line itself when you opt in; `scripts/board` syncs it every pass and does nothing without it:
- **Board:** https://github.com/users/<owner>/projects/<n>
-->

## Slots

- **Slots:** <n, default 2>. How many tickets are in flight at once. Parallel workers multiply token use, so this is the throttle. `/orchestrate:run` suggests changes of one; it changes this line only when the user says yes.
- **Exclusive holds:** <e.g. "while #42 is open, nothing else touches `db/`" | none>

Tickets run in parallel only when their touch maps don't overlap outside the shared files.

## Velocity

Optional; delete the section for the defaults below. `scripts/lead-brief --watch` reads these to wake the lead when a slot sits idle.

- **Idle window:** 10m. How long a slot may sit free with a dispatchable ticket waiting before the lead is woken.
- **Expensive hold:** 3. How many takeable tickets one held PR must block before the lead's digest asks the developer about it.
- **Velocity watch:** on. `off` stops the idle-slot wake.

## Verify

Commands every worker runs before opening a PR. `verify --config` reads them from here and runs each exactly as written, so nothing else restates them. Write each command as a top-level bullet that opens with one backticked span; text after the span is a remark, and any other line is prose. Commands in a group are independent. A bullet that is just `` `--then` `` starts a group that depends on the ones above it (codegen or a build first), skipped if one of them fails.

- `<command>` <optional remark>

## Shared files

A **shared file** is one many PRs edit. The `merge` script resolves collisions in the files listed here by replaying each PR's lines onto main, so nobody merges them by hand (paths match case-sensitively); a conflict in any other file is handed to the worker.

- **Shared files:** one nested bullet per file (a path or glob in backticks), with the only change each PR may make in it.
  - `<path>`: <per-PR rule, e.g. "add one line under Unreleased">
- **Generated:** one nested bullet per generated glob (in backticks), with the command that regenerates it (in backticks). On a conflict the `merge` script takes main's side and runs the command against the PR's sources in the sweep worktree (a file that is also listed as shared is treated as shared; the command may only change files matching generated globs); if it fails the conflict goes back to the worker. Write `none` if there is no generated code.
  - `<glob>`: `<regenerate command, e.g. npm run codegen>`
- **Orchestrator-owned edits:** <status updates only the orchestrator makes, e.g. "tick the roadmap box after merge" | none>

## PRs and merging

- **Branch:** `<number>-<slug>`
- **Commits:** <convention>
- **PR title:** Conventional Commits, ending in ` (#<n>)`, where `<n>` is the ticket the PR closes (`scripts/gate` checks the ending against the body's closing reference; drop the suffix clause to turn that check off). **Body:** <closing reference, e.g. "Closes #<n>">
- **Method:** <squash | merge | rebase>
- **Merge:** up to date: <when overlapping | required>. `when overlapping` (the default, also when the line is missing) lets a behind PR the host calls mergeable merge without an update when none of its files changed on the default branch since the merge base and none is a Risk path; `required` always updates first.
- **Orchestrator merges:** <yes | no>. Rule: decided by `scripts/merge`: CI green, every dependency merged, branch up to date (see **Merge**), review threads resolved, reviewer approved.

## Gates

- **Start gates:** <tickets or labels that wait for the user before a worker starts>
- **Merge gates:** <path globs in backticks, in the first sentence; a PR touching one is merged by the developer, not the orchestrator>. `scripts/merge` reads the globs through `config.merge_gate_for` and ends `next: ask-user gate <glob>` for such a PR; tickets and labels are not read.

## Velocity

Optional: a missing section, or a missing line, keeps the default shown here.

- **Idle window:** 10m. How long a slot may sit free with a takeable ticket waiting before the lead is woken.
- **Expensive hold:** 3. How many takeable tickets one held PR must block before the digest asks the developer about it.
- **Velocity watch:** on. `off` stops the `idle-slot:` wake.

## Developer waits

Optional: a missing section is the defaults shown here. The lead prints this footer after every turn (`scripts/waiting`).

- enabled: yes
- sections: ready-for-human, needs-triage, lead-asks
- colours: ready-for-human red, needs-triage yellow, lead-asks blue
- orchestrator pass reports: no

`sections` is ordered: list order is print order. Optional sources, off until listed: `merge-gates`, `release-prs`, `parked`. Colours are black, red, green, yellow, blue, magenta, cyan or white. `orchestrator pass reports: yes` puts the footer in the orchestrator's pass report too.

## Human-only steps

<Actions only the user performs; workers stop and report when they reach one.>

## Environment

<Worktree setup commands; parallel-worktree collisions and their fix; what .worktreeinclude carries.>

## Models

| Role | Model | Effort |
|---|---|---|
| Lead | opus (used when scripts/launch --lead starts it; a hand-started session keeps its own) | |
| Orchestrator | opus (used when the lead launches it; a hand-started session keeps its own) | |
| Worker | sonnet; haiku for S tickets (at most <3> files, the **Tiers** file limit) | medium |
| Scout | haiku | low |
| Reviewer | S haiku · M sonnet · L sonnet (high effort) | medium |

## Review

- **Instruction files:** <paths the reviewer checks against>
- **Risk paths** (any PR touching them is reviewed as L): <globs>
- **Tiers:** S is at most <50> changed lines in at most <3> files, none on a risk path; L touches a risk path or changes more than <400> lines; M is everything else. A `size:` label only raises the tier; a label below the computed tier is ignored.
- **Repo-specific checks:** <what the reviewer judges, e.g. "docs updated">. An entry may carry a command, marked `run:` and backticked, e.g. "codegen is up to date (run: `make codegen && git diff --exit-code`)"; plain backticked text is never executed. A `run:` with no backticked command (e.g. "must run: the tests") fails the gate as malformed. The script gate runs each marked command before any reviewer, so mechanical failures cost no review round.
- **Post reviews:** <yes | no>. When yes, the reviewer posts each round on the PR itself.
- **CI wait limit:** <minutes>. Optional; delete the line for the default of 10. `scripts/ci-times` reports CI as over the limit when the p90 push-to-green of the last 10 merged PRs exceeds it.
