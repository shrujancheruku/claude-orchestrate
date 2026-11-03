---
name: setup
description: Write this repo's .claude/orchestration.md (backlog, slots, verify commands, gates, merge rule) so /orchestrate:run can drive it.
disable-model-invocation: true
---

You write the repo's **config**, `.claude/orchestration.md`, which `/orchestrate:run` and its agents read as their single source of rules. The facts come from the repo; the decisions come from the user.

## Steps

1. **Gather facts.** Send `orchestrate:scout` these questions in parallel calls:
   - The tracker docs from `/setup-matt-pocock-skills`: whether `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md` and `docs/agents/domain.md` exist, the tracker each names, and whether every label string in `triage-labels.md` exists on the tracker. Only if the docs are missing: which host, how tickets block each other, how a ticket gets claimed, and where an ordered backlog lives or could live.
   - The build, test, lint and codegen commands (Makefile, package scripts, CI workflows), and which CI checks gate merges.
   - The top-level layout by directory and ownership: candidate exclusive holds, and the paths where a wrong change is costly (candidate risk paths).
   - Conventions in the instruction files (`CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING`): commit and PR format, docs every change must update, files many changes touch.
   - The default branch and the merge methods the host allows.
   - Anything that collides across parallel worktrees (fixed ports, one shared local database, container names), and the gitignored files the tests need.
   Done when each question has an answer or a `not found`.
2. **Draft** the config from [config-template.md](config-template.md): facts filled in, and a recommended answer written beside each decision.
3. **Decide with the user.** Put every open decision in one round, numbered, each with your recommendation: the backlog source (the tracker docs; if they're missing, recommend running `/setup-matt-pocock-skills` first, and otherwise settle the backlog and its order in the config; if labels are missing, recommend creating them), the slot count (recommend 2) and any exclusive holds, the gates, whether the orchestrator merges, the models, the review tiers and their thresholds, the risk paths, whether reviewers post their reviews on the PR (recommend yes), the shared-file rules, and the fix for each parallel-worktree collision. Done when the user has answered each one.
4. **Write** `.claude/orchestration.md`, plus a `.worktreeinclude` that lists the patterns of the gitignored files workers need (patterns only, never file contents). Suggest `.claude/worktrees/` for `.gitignore` if it's missing. Leave committing to the repo's normal flow, and tell the user which files to commit.
