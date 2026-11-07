# A mechanical rule the prompts restate becomes a script

## Context

The plugin's behaviour lives in two kinds of module: prompts (skills and agent definitions) that agents read and apply, and scripts under `scripts/` that agents run. Some rules in the prompts need no judgement: the same inputs always give the same answer.

- **The overlap rule was restated six times in five files.** "Compare held paths by file, with any `:line` stripped" is written twice in `skills/run/SKILL.md` (The state lives outside you, and a pass's step 4) and once each in `skills/run/brief.md`, `agents/worker.md`, `agents/scout.md` and `GLOSSARY.md`. Three fixes each corrected a different copy: the orchestrator's check (#92), the worker's stop rule (#114) and the brief's held-paths line (#150).
- **`behind_by` was read in three places.** The scout's checklist item 3, `scripts/gate`, and the orchestrator's re-check before a merge (`skills/run/SKILL.md`, review loop step 6), after scouts called a behind branch up to date (#64, #96).
- **A prompt rule can only be tested by running an agent.** The behaviour harness is opt-in and capped (#122), so a regression in prose shows up in a trial run, not in CI (inferred from the fixes above, each found in a run).
- **The scripts that replaced prose held.** `scripts/gate` (#72), `scripts/merge` (#75) and `scripts/rebuild-state` (#69) each took a mechanical job out of the orchestrator's prompt and are pinned by CLI tests; so did the gate as the one check before review (#237).

## Decision

- **A rule becomes a script when both hold:** it needs no judgement (same inputs, same answer), and more than one prompt states it or more than one agent applies it.
- **The prompts call the script and pass its output on word for word.** They don't restate the rule. A brief line that a script fills is copied from the script's output.
- **The glossary names the script as the rule's source,** and the rule's tests are the script's CLI tests.
- **Judgement stays in the prompts:** predicting a touch map, deciding whether a stop is the user's decision, reviewing a diff, choosing what to tell a worker.
- **New scripts follow ADR-0003** for kind and invocation.

The first applications are the held-path script (`scripts/holds`), merge readiness moving into `scripts/merge`, and the state file carrying each worker's resolved worktree and touch map (the architecture review of 2026-11-05), then `scripts/worker-report`, which classifies every worker completion before the orchestrator acts (#260), the frontier (#239), and the merge rule's clauses as `merge`'s readiness (#237), and the closing of review threads as `scripts/threads`, with `merge` refusing an unresolved one (#241). A mechanical check of a stop needs a machine-readable stop list, so the worker's report carries `stop-reason`.

## Consequences

- Prompts shrink, and a rule's fix lands in one module and its tests.
- More scripts and more exit codes for the orchestrator to know. Each script's interface is written in its docstring and the one prompt line that calls it.
- A script bug reaches every pass at once. Its CLI tests are what stop that, so a new script ships with them.
- Behaviour tests check only that agents call the script and act on its output, not the rule itself.
- Rejected: one canonical prose copy that other prompts point to (agents still apply it by hand, and #150 was a correct rule copied wrong into the brief); a behaviour test per copy (opt-in and capped, so not a CI guard).
- Reopen when a rule moved to a script turns out to need judgement, or when the number of scripts makes ADR-0003's invocation rule a burden (inferred).
