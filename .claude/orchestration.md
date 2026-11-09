# Orchestration config

<!-- Read by /orchestrate:run and its agents. One source per rule: point at files that already hold a fact (Makefile, CI, instruction files), and write here only what lives nowhere else. -->

## Backlog

- **Tracker docs:** `docs/agents/issue-tracker.md` (operations, blocking, claiming) and `docs/agents/triage-labels.md` (label strings), from `/setup-matt-pocock-skills`. Takeable tickets are what `scripts/frontier` lists, in ascending issue number. Claim = assign to the user.
- **Domain docs:** `docs/agents/domain.md` (the glossary and ADRs workers and reviewers follow).
- **Board:** https://github.com/users/shrujancheruku/projects/1

## Slots

- **Slots:** 2. How many tickets are in flight at once. Parallel workers multiply token use, so this is the throttle. `/orchestrate:run` suggests changes of one; it changes this line only when the user says yes.
- **Exclusive holds:** none

Tickets run in parallel only when their touch maps don't overlap outside the shared files.

## Verify

From the repo root, the same steps as `.github/workflows/ci.yml`:

- `python3 -m py_compile hooks/*.py scripts/*.py $(grep -lE '^#!.*python' scripts/*)`
- `python3 tests/run_parallel.py`
- `claude plugin validate .` (errors fail; warnings don't)

Code must stay Python 3.9 compatible; CI runs 3.9 and 3.x. No lint or codegen step.

## Shared files

A **shared file** is one many PRs edit. The `merge` script resolves collisions in the files listed here by replaying each PR's lines onto main, so nobody merges them by hand; a conflict in any other file is handed to the worker.

- **Shared files:**
  - `README.md`: edit only the section for the feature the PR changes.
  - `docs/guide.md`: edit only the section for the feature the PR changes.
  - `skills/run/SKILL.md`: edit only the section for the step the PR changes.
- **Generated:** none
- **Orchestrator-owned edits:** none

`CHANGELOG.md`, `version.txt`, `.release-please-manifest.json` and the `version` field of `.claude-plugin/plugin.json` belong to release-please. PRs never edit them.

## PRs and merging

- **Branch:** `<number>-<slug>`
- **Commits:** Conventional Commits (`type(scope): description`, `!` or a `BREAKING CHANGE:` footer for breaking changes). No attribution lines or trailers.
- **PR title:** Conventional Commits, same as commits (checked by `scripts/gate`). **Body:** `Closes #<n>`; no attribution lines.
- **Method:** squash, deleting the branch in the merge command (the repo doesn't delete merged branches on its own)
- **Orchestrator merges:** yes. Rule: decided by `scripts/merge`: CI green, every dependency merged, branch up to date, review threads resolved, reviewer approved. `main`'s ruleset requires a PR (squash or rebase) but no status checks, so this rule is the only CI gate: never merge with CI pending or red.

## Gates

- **Start gates:** none beyond the label filter.
- **Merge gates:** PRs touching `hooks/**`, `.claude-plugin/**`, `.github/workflows/**`, `release-please-config.json` or `.release-please-manifest.json` wait for the user's go-ahead. release-please's `chore(main): release` PRs are never merged by the orchestrator.

## Human-only steps

- Merging release PRs.
- Changing anything under `~/.claude` (settings, status line).
- Repo settings: branch protection, labels, secrets.

## Environment

- No worktree setup commands.
- No parallel-worktree collisions: no fixed ports, databases or containers; tests write only to temp dirs (`CLAUDE_CONFIG_DIR` and `ORCHESTRATE_USAGE_LOG` overridden).
- `.worktreeinclude` carries nothing; tests need no gitignored files.

## Models

| Role | Model | Effort |
|---|---|---|
| Lead | opus (used when scripts/launch --lead starts it; a hand-started session keeps its own) | |
| Orchestrator | opus (used when the lead launches it; a hand-started session keeps its own) | |
| Worker | sonnet; haiku for S tickets (at most 3 files, the **Tiers** file limit) | medium |
| Scout | haiku | low |
| Reviewer | S haiku · M sonnet · L sonnet (high effort) | medium |

## Review

- **Instruction files:** `CLAUDE.md`, `docs/agents/*.md`, `docs/adr/`
- **Risk paths** (any PR touching them is reviewed as L): `.claude-plugin/**`, `hooks/**`, `.github/workflows/**`, `release-please-config.json`, `.release-please-manifest.json`, `version.txt`, `CHANGELOG.md`, `scripts/merge`, `scripts/gate`, `scripts/sandbox-git`, `scripts/github.py`, `scripts/replay.py`, `scripts/rescue`
- **Tiers:** S is at most 50 changed lines in at most 3 files, none on a risk path; L touches a risk path or changes more than 400 lines; M is everything else. A `size:S|M|L` label on the ticket overrides.
- **Repo-specific checks:**
  - behaviour changes come with tests; Python 3.9 compatible
  - user-facing changes update `docs/guide.md`, and `README.md` only where the showcase covers the feature
  - release-please files untouched
  - plugin features work out of the box, with no manual edits to the user's config scripts (degrade instead)
  - a mechanical rule moved into a script ships with CLI tests, and the prompts call the script rather than restate the rule (ADR-0006)
  - a new script declares its kind in `tests/test_invocation.py` (ADR-0003)
  - a guard behaviour change updates `tests/fixtures/guard-corpus.jsonl`
- **Post reviews:** yes. The reviewer posts each round on the PR as a comment review.
