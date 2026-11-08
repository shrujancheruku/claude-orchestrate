# An empty check rollup is green only when the head has no workflow files

## Context

`scripts/merge --approved-head` merges only when CI is green on the PR head (ADR-0004; `.claude/orchestration.md` **Orchestrator merges**). `ci_verdict` reads GitHub's status-check rollup: SUCCESS, SKIPPED and NEUTRAL pass, FAILURE and friends fail, and anything else, including an empty rollup, is pending. The L-tier review of #322 pointed out the consequence: a repo with no CI at all has an empty rollup forever, so the orchestrator can never merge there, and a run on such a repo waits on every PR.

An empty rollup is ambiguous. Right after a push it means "the checks have not started"; in a repo without workflows it means "there is nothing to wait for". Treating every empty rollup as green would merge PRs whose checks had not yet been scheduled, which is the failure the rule exists to prevent.

## Decision

- **An empty rollup counts as green only when the PR head cannot produce checks**: the head's tree has no workflow files under `.github/workflows/` (the contents read returns 404, or lists no `.yml`/`.yaml` entry). The read is made only when the rollup is empty, once per readiness check.
- **Any workflow file keeps the old rule.** An empty rollup with a workflow present is pending, however long it stays empty; `watch-ci` settles it when the checks report.
- **The verdict says which**: the readiness line reads `ci: none configured` for the first case and `ci: pending` for the second, so the pass table and the run log show why a PR merged without checks.
- Checks that come from outside Actions (a third-party app posting statuses) are not detected by this rule. A repo that relies on them and has no workflow files merges without waiting; that repo adds a workflow, or lists the app's check as required, which the merge then sees.

## Consequences

- A repo without CI can be run by the orchestrator; the reviewer and the gate remain its only checks there, and the config's **Verify** steps remain the worker's.
- One more `gh api` call per readiness check, only on the empty-rollup path.
- Rejected: treating every empty rollup as green (merges before checks start); a config switch (`CI: none`) that the developer must set (the plugin works out of the box and the facts are readable from the repo); counting an empty rollup as green after a timeout (hides a stuck CI).
