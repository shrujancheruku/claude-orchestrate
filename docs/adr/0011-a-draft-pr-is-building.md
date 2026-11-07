# A draft PR is building, and ready means in review

## Context

A worker used to open its PR only at the end, so the developer saw nothing until the work was done. A draft PR opened at the start lets them follow it. But every script that asks "is there an open PR?" meant "this ticket is in review", and a draft must not enter the review loop.

## Decision

- **A worker opens a draft PR at the end of Orient**, after an empty commit `chore: start #<n>` (a branch with no commit ahead of the default branch can't have a PR) and a push. If the branch already has an open PR it reuses it. If `gh pr create --draft` fails for any reason (drafts unsupported on the plan, the network, a permission denial) it carries on and opens the PR at Ship, as before; the report gets no extra line.
- **A draft is building; ready means in review.** `claims()` is the one place that decides it (`lead-route`'s own PR map, used for `HOLD-PR`/`ASK-DEV`, ignores a non-lead ticket's draft too): an open non-draft PR is `in-review`, a draft decides nothing (the ticket is building, stalled or elsewhere, so `lead-route` can still REDISPATCH it) and `Claim.pr` still names it so the pass table can link it. `board` shows it as Building. `watch-ci` doesn't settle a draft. `worker-report` doesn't accept a draft as a `pr-opened` PR.
- **`merge`'s sweep skips drafts**: their worker is still pushing, and a draft joins the sweep once it is marked ready.
- **`holds` holds a building ticket's touch map and its draft's diff** (a draft is an open PR, so the diff was already held).
- **Ship** is `gh pr edit --body-file` then `gh pr ready`. ABANDON closes a draft like any open PR. Lead PRs don't change.

## Consequences

- GitHub Actions' `pull_request` trigger runs CI on every push to a draft. A repo that wants to skip that adds `if: ${{ !github.event.pull_request.draft }}` to its workflow, and must then also list `types: [opened, synchronize, reopened, ready_for_review]` under `pull_request`; otherwise marking ready runs no CI and the skipped jobs read as a pass in `watch-ci`. The plugin doesn't edit workflows.
- `ready_for_review` isn't in `pull_request`'s default event types, so marking ready runs no new CI: `watch-ci` settles on the last draft push's checks.
- The empty start commit adds one `chore:` commit to the branch; the squash merge hides it.
- Rejected: opening the PR only at Ship (nothing to follow); treating a draft as in review (CI, gate and review on half-built code).
