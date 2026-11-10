# A draft PR is building, and ready means in review

## Context

A worker used to open its PR only at the end, so the developer saw nothing until the work was done. A draft PR opened at the start lets them follow it. But every script that asks "is there an open PR?" meant "this ticket is in review", and a draft must not enter the review loop.

## Decision

- **A worker opens a draft PR at the end of Orient**, after an empty commit `chore: start #<n>` (a branch with no commit ahead of the default branch can't have a PR) and a push. If the branch already has an open PR it reuses it. If `gh pr create --draft` fails for any reason (drafts unsupported on the plan, the network, a permission denial) it retries once, then opens the PR ready at Ship with a first body line `Draft unavailable: <reason>` and reports it under Notes (see the 2026-11-08 note below).
- **A draft is building; ready means in review.** `claims()` is the one place that decides it (`lead-route`'s own PR map, used for `HOLD-PR`/`ASK-DEV`, ignores a non-lead ticket's draft too): an open non-draft PR is `in-review`, a draft decides nothing (the ticket is building, stalled or elsewhere, so `lead-route` can still REDISPATCH it) and `Claim.pr` still names it so the pass table can link it. `board` shows it as Building. `watch-ci` doesn't settle a draft. `worker-report` doesn't accept a draft as a `pr-opened` PR.
- **`merge`'s sweep skips drafts**: their worker is still pushing, and a draft joins the sweep once it is marked ready.
- **`holds` holds a building ticket's touch map and its draft's diff** (a draft is an open PR, so the diff was already held).
- **Ship** is `gh pr edit --body-file` then `gh pr ready`. ABANDON closes a draft like any open PR. Lead PRs don't change.

## Consequences

- GitHub Actions' `pull_request` trigger runs CI on every push to a draft. A repo that wants to skip that adds `if: ${{ !github.event.pull_request.draft }}` to its workflow, and must then also list `types: [opened, synchronize, reopened, ready_for_review]` under `pull_request`; otherwise marking ready runs no CI and the skipped jobs leave the head with no real check: `watch-ci` holds an all-skipped rollup pending and ends in `no-run` (`merge` holds it pending too, ADR-0012). The plugin doesn't edit workflows.
- `ready_for_review` isn't in `pull_request`'s default event types, so marking ready runs no new CI: `watch-ci` settles on the last draft push's checks.
- The empty start commit adds one `chore:` commit to the branch; the squash merge hides it.
- Rejected: opening the PR only at Ship (nothing to follow); treating a draft as in review (CI, gate and review on half-built code).

## Note (2026-11-10): a parked PR is closed, and its ticket is `parked`

`scripts/park-pr` closes an open PR that waits on a newer PR's fix, keeping its branch and the ticket's claim (#708). A closed PR is not in the open set `holds` and the gate read, so it holds nothing; `claims` reads the newest `park` run-log event and calls the ticket `parked` (not building, stalled or in review), so no rescue or redispatch touches it. `park-pr <pr> --unpark` reopens it and the ticket is in review again.

## Note (2026-11-08): the guard enforces the draft

A worker's `gh pr create` without `--draft` (or `-d`) is refused by the guard (#355), because workers skipped the step. The fallback stays, with a marker: the guard allows a non-draft create only when its body (`--body` or `--body-file`) has a line starting `Draft unavailable:` followed by the reason. A PR opened that way is ready from creation, so it counts as in review at once; the worker reports it under Notes.
