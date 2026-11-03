---
name: setup
description: Write this repo's .claude/orchestration.md (backlog, lanes, verify commands, gates, merge rule) so /orchestrate:run can drive it.
disable-model-invocation: true
---

You write the repo's **config**, `.claude/orchestration.md`, which `/orchestrate:run` and its agents read as their single source of rules. The facts come from the repo; the decisions come from the user.

## Steps

1. **Gather facts.** Send `orchestrate:scout` these questions in parallel calls:
   - The tracker: which host, how tickets block each other (native links, a body convention), and how a ticket gets claimed.
   - Where an ordered backlog lives or could live (a planning doc, milestones, labels), and whether tickets carry sizes.
   - The build, test, lint and codegen commands (Makefile, package scripts, CI workflows), and which CI checks gate merges.
   - The top-level layout, grouped into candidate lanes by directory and ownership.
   - Conventions in the instruction files (`CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING`): commit and PR format, docs every change must update, files many changes touch.
   - The default branch and the merge methods the host allows.
   - Anything that collides across parallel worktrees (fixed ports, one shared local database, container names), and the gitignored files the tests need.
   Done when each question has an answer or a `not found`.
2. **Draft** the config from [config-template.md](config-template.md): facts filled in, and a recommended answer written beside each decision.
3. **Decide with the user.** Put every open decision in one round, numbered, each with your recommendation: the backlog source and order, the lanes and any exclusive holds, the gates, whether the orchestrator merges, the models and review tiers, the shared-file rules, and the fix for each parallel-worktree collision. Done when the user has answered each one.
4. **Write** `.claude/orchestration.md`, plus a `.worktreeinclude` that lists the patterns of the gitignored files workers need (patterns only, never file contents). Suggest `.claude/worktrees/` for `.gitignore` if it's missing. Leave committing to the repo's normal flow, and tell the user which files to commit.
