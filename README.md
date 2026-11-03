# orchestrate

A Claude Code plugin that works through a sequenced backlog in parallel. Your main session acts as the **orchestrator**: it decides the order of work, hands each ticket to a **worker** in its own git worktree, pushes searches and mechanical edits down to cheaper agents, reviews each PR at a depth sized to it, and merges by your repo's rule. Anything that is yours to decide waits for you.

```
orchestrator (your session, e.g. Opus)     order, briefs, verdicts, merges, gates
 ├─ worker        Sonnet, own worktree      one ticket → one PR
 │   ├─ scout     Haiku, read-only          searches, callers, CI logs
 │   └─ mechanic  Haiku                     mechanical edits in the worker's worktree
 ├─ reviewer      Haiku (S) / Sonnet (M, L) one PR against its ticket, read-only
 └─ scout         Haiku, read-only          tracker, CI and pre-merge checks
```

## Install

```sh
/plugin marketplace add shrujancheruku/claude-orchestrate
/plugin install orchestrate@claude-orchestrate
```

From a local clone, use the clone's path in place of `shrujancheruku/claude-orchestrate`. While developing, load it without installing: `claude --plugin-dir /path/to/claude-orchestrate`.

Needs `git`, `gh` (signed in), and `python3` 3.9 or later for the guard hook.

## Use it in a repo

1. **`/orchestrate:setup`** scouts the repo (tracker, commands, layout, conventions, anything that would collide across parallel worktrees) and asks you the decisions in one round: backlog source, lanes, gates, merge authority, models. It writes `.claude/orchestration.md` (the **config**) and a `.worktreeinclude`.
2. **`/orchestrate:run`** runs one **pass**. It rebuilds state from the tracker, reviews and merges finished PRs, fills free lanes with workers, and reports a table of lanes. Passes are safe to repeat and to start in a fresh session, because the state lives in the tracker, not in the conversation.
   - `/orchestrate:run status` reports without dispatching or merging.
   - `/orchestrate:run <lane>` or `/orchestrate:run <ticket>` narrows a pass.
   - `/loop /orchestrate:run` keeps going: worker completions wake the orchestrator, and it schedules its own checks on CI.

### Concepts

- **Lane:** a set of paths with at most one open PR at a time, so parallel PRs don't conflict. A lane can be **exclusive** (for example, a schema rewrite that nothing else may touch until it merges).
- **Gate:** a ticket, label or path that waits for your go-ahead to start or to merge.
- **Tier:** review depth from the ticket's size. S is a Haiku checklist review; M is a Sonnet correctness review; L is a Sonnet review plus the orchestrator's own read of the risky files.
- **Brief:** the pointer-only prompt a worker gets (see `skills/run/brief.md`). The ticket stays the spec.

## Cost

- Each role runs on the cheapest model that does it well. Set them per repo in the config's **Models** table.
- Searches and log reads run in Haiku contexts, so the orchestrator's and workers' contexts stay small.
- Review fixes go back to the worker that wrote the PR, so its context is reused instead of a new agent starting.
- Briefs share a fixed preamble, so workers share a prompt-cache prefix.
- Parallel agents multiply token use. Fewer lanes cost less; the lane count is your throttle.

## The guard hook

`hooks/guard.py` runs before every Bash call and acts only on this plugin's agents (`agent_type` `orchestrate:<role>`):

| Role | Refused |
|---|---|
| scout, reviewer | git writes, `gh` writes, file writes and redirects, builds and installs |
| mechanic | git writes (staging and commits are the worker's), `gh` writes |
| worker | `gh pr merge`, force-pushes, pushes to `main`/`master` |

It reads the command text, so it's a guardrail against mistakes, not a sandbox. Your permission mode and sandbox settings still apply on top. Tests: `python3 -m unittest discover tests`.

## Limits

- Workers branch from the default branch (Claude Code's worktree default), so a ticket starts only after its dependencies have merged.
- Agents from a plugin ignore `permissionMode`, so workers follow your session's permission mode.
- The orchestrator stops for anything destructive or outward-facing the config doesn't authorise.
