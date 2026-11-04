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
3. **Decide with the user.** Put every open decision in one round, numbered, each with your recommendation: the backlog source (the tracker docs; if they're missing, recommend running `/setup-matt-pocock-skills` first, and otherwise settle the backlog and its order in the config; if labels are missing, recommend creating them), the slot count (recommend 2; 1 when a parallel-worktree collision has no fix; 3 only after asking about the user's plan, never more; `/orchestrate:run` suggests changes from real passes) and any exclusive holds, the gates, whether the orchestrator merges, the models, the review tiers and their thresholds, the risk paths, whether reviewers post their reviews on the PR (recommend yes), the shared-file rules, and the fix for each parallel-worktree collision. Done when the user has answered each one.
4. **Write** `.claude/orchestration.md`, plus a `.worktreeinclude` that lists the patterns of the gitignored files workers need (patterns only, never file contents). Suggest `.claude/worktrees/`, `.claude/orchestrate-state.json` and `.claude/orchestrate-profiles/` for `.gitignore` if they're missing. Leave committing to the repo's normal flow, and tell the user which files to commit.
5. **Offer SSH under the sandbox**, only when `git remote get-url origin` is an SSH URL (`git@…` or `ssh://`). The sandbox hides the ssh-agent socket, so agents' `git push` and `git fetch` fail with `Permission denied (publickey)`. A plugin can't ship sandbox settings, so ask whether to merge this into the repo's `.claude/settings.json` (keep every other key; create the file if missing; never touch `~/.claude`): `{"sandbox": {"network": {"allowUnixSockets": ["<value of $SSH_AUTH_SOCK>"], "allowedDomains": ["github.com"]}}}`. Union with any existing `allowUnixSockets` and `allowedDomains` lists. If `$SSH_AUTH_SOCK` is empty, skip the socket entry and say so. The socket path can change after a reboot or re-login; tell the user to re-run setup if SSH starts failing. On a no, nothing is written: workers fall back to HTTPS through `gh` (see `agents/worker.md`). Tell the user to commit the file.
6. **Offer the usage tap** for `/orchestrate:run --profile`, unless the user's status-line command already pipes into `usage-tap.py`. It's outside the repo, so ask first. On a yes: copy `${CLAUDE_SKILL_DIR}/../../scripts/usage-tap.py` to `~/.claude/orchestrate/usage-tap.py` (a fixed path that survives plugin updates), and add `echo "$input" | python3 -I ~/.claude/orchestrate/usage-tap.py` to the status-line script, after it reads its input into `input`. With no status line, write a script that does only that, set it as `statusLine`, and say so.
