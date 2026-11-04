# orchestrate

A Claude Code plugin that works through a backlog of GitHub issues in parallel, picking up the tickets that [Matt Pocock's skills](https://github.com/mattpocock/skills) (`to-tickets`, `to-spec`, `triage`) leave `ready-for-agent`. Your main session acts as the **orchestrator**: it decides the order of work, hands each ticket to a **worker** in its own git worktree, pushes searches and mechanical edits down to cheaper agents, reviews each PR at a depth sized to it, and merges by your repo's rule. Anything that is yours to decide waits for you.

```
orchestrator (your session, e.g. Opus)     order, briefs, verdicts, merges, gates
 ├─ worker        Sonnet, own worktree      one ticket → one PR
 │   ├─ scout     Haiku, read-only          searches, callers, CI logs
 │   └─ mechanic  Haiku                     mechanical edits in the worker's worktree
 ├─ reviewer      Haiku (S) / Sonnet (M, L) one PR against its ticket, read-only, posts its review
 └─ scout         Haiku, read-only          tracker, CI and pre-merge checks
```

## Install

```sh
/plugin marketplace add shrujancheruku/claude-orchestrate
/plugin install orchestrate@claude-orchestrate
```

From a local clone, use the clone's path in place of `shrujancheruku/claude-orchestrate`. While developing, load it without installing: `claude --plugin-dir /path/to/claude-orchestrate`.

Needs `git`, `gh` (signed in), and `python3` 3.9 or later for the guard hook. Run `/setup-matt-pocock-skills` in the repo first: orchestrate reads the tracker and domain docs it writes to `docs/agents/`. Without them, setup falls back to a backlog defined in the config.

## Use it in a repo

1. **`/orchestrate:setup`** scouts the repo (tracker, commands, layout, conventions, anything that would collide across parallel worktrees) and asks you the decisions in one round: backlog source, slots, gates, merge authority, models, review tiers. It writes `.claude/orchestration.md` (the **config**) and a `.worktreeinclude`.
2. **`/orchestrate:run`** runs one **pass**. It rebuilds state from the tracker, reviews and merges finished PRs, fills free slots with workers, and reports a table of tickets. Passes are safe to repeat and to start in a fresh session, because the state lives in the tracker, not in the conversation.
   - `/orchestrate:run status` reports without dispatching or merging.
   - `/orchestrate:run <ticket>` narrows a pass to one ticket; `/orchestrate:run <parent issue>` to its sub-issues.
   - `/loop /orchestrate:run` keeps going: worker completions wake the orchestrator, and it schedules its own checks on CI.
   - Add `--profile` to any of these to profile the session as it runs: see [Profiling](#profiling).

### Concepts

- **Ticket:** a GitHub issue that is open, `ready-for-agent`, unassigned and unblocked, with no open sub-issues. Tickets are taken in ascending issue number, the order `to-tickets` publishes them in. The tracker operations and label strings come from `docs/agents/`. A ticket a worker can't finish goes back to the tracker as `ready-for-human` or `needs-info`.
- **Slot:** one ticket in flight. The config sets how many. Two tickets run at once only when their **touch maps** (the files the scout expects each to change) don't overlap outside the shared files; an **exclusive hold** keeps a path to one ticket until it merges.
- **Domain docs:** workers and reviewers follow the glossary and ADRs that `docs/agents/domain.md` points to. A PR that contradicts an ADR without saying why is a blocking finding.
- **Gate:** a ticket, label or path that waits for your go-ahead to start or to merge.
- **Tier:** review depth from the PR's diff and the config's thresholds (a `size:` label on the ticket overrides). S is a Haiku checklist review; M is a Sonnet correctness review; L is a Sonnet review plus the orchestrator's own read of the risky files. With `Post reviews: yes`, the reviewer posts each round on the PR itself as a comment review (verdict in the body, findings inline) and hands back only the verdict, counts and the review URL; the worker reads the findings from the PR. With `no`, or when a post fails, the reviewer returns the full findings. It's always a comment, because GitHub won't let your account approve its own PR.
- **Brief:** the pointer-only prompt a worker gets (see `skills/run/brief.md`). The ticket stays the spec.

## Cost

- Each role runs on the cheapest model that does it well. Set them per repo in the config's **Models** table.
- Searches and log reads run in Haiku contexts, so the orchestrator's and workers' contexts stay small.
- Review fixes go back to the worker that wrote the PR, so its context is reused instead of a new agent starting.
- Briefs share a fixed preamble, so workers share a prompt-cache prefix.
- Parallel agents multiply token use. Fewer slots cost less; the slot count is your throttle. See [Choosing a slot count](#choosing-a-slot-count).

## Choosing a slot count

Setup recommends 2. Each extra slot adds a worker's tokens and its reviews, raises the odds that a usage limit pauses several tickets at once, and adds reports and reviews to your main session, which handles them one at a time and is usually the first thing to saturate. The gain is wall-clock time, and only when the backlog's tickets are independent.

Every full pass suggests a change of one slot, between 1 and 5: lower after a usage limit or when overlaps left a slot idle, higher when every slot is full and the next ticket wouldn't overlap. It changes the config only when you say yes. The reasoning is in [ADR-0001](docs/adr/0001-slot-count.md).

## The guard hook

`hooks/guard.py` runs before every Bash call and acts only on this plugin's agents (`agent_type` `orchestrate:<role>`):

| Role | Refused |
|---|---|
| scout, reviewer | git writes, `gh` writes, file writes and redirects, builds and installs. The reviewer may post one comment review on a PR (`gh api .../reviews` or `gh pr review --comment`, after an optional plain `cd <path> &&`), in the shapes `agents/reviewer.md` shows, and only when the config says `Post reviews: yes` |
| mechanic | git writes (staging and commits are the worker's), `gh` writes |
| worker | `gh pr merge`, force-pushes, pushes to `main`/`master` |

It reads the command text, so it's a guardrail against mistakes, not a sandbox. Your permission mode and sandbox settings still apply on top. Tests: `python3 -m unittest discover tests`.

## Running out of usage

Every agent draws on your plan, so a usage limit tends to stop several workers at once.

- The first usage-limit error in a pass stops all new dispatches. The affected tickets show as **paused**, with the reset time when the error gives one.
- The next pass after the reset continues each paused worker where it stopped, with its context. A worker it can't reach (another session) is replaced by a new one on the same branch; first the orchestrator commits and pushes any work left in the old worktree as `chore(wip): …`.
- Workers push after every commit whose checks pass, so little is lost.
- `.claude/orchestrate-state.json` (gitignored) remembers each ticket's worker and worktree between passes. The tracker stays the source of truth.

For unattended `/loop /orchestrate:run`, turn on **Continue automatically at usage limit** in `/config` (`autoContinueAtUsageLimit`). Without it, a loop whose own turn hits the limit ends after one retry about 20 minutes later; restart it with `/loop /orchestrate:run` and the next pass picks up where things stopped. A fallback model doesn't help: rate-limit errors never trigger one, and the orchestrator never switches models on its own.

## Profiling

`/orchestrate:run --profile` (or `/loop /orchestrate:run --profile`) runs normal passes, and at the end of each one records the pass and saves a profile of the session so far:

```
Profile · session 346d1ea2 · 21m · 2 slots
slots   occupied 59%  (building 37%, review/merge 22%)  idle: overlap 5m, empty 7m
tokens  orchestrator 4.8M (33%) · workers 8.3M · reviewers 1.0M · scouts 433k · cache hits 96%
tickets 2 merged, 4.8M each
plan    5h window 22% → 51%, peak 51% · 0 limit hits
saved   .claude/orchestrate-profiles/2026-11-03-346d1ea2.json  (+ .html)
```

- **Slot efficiency:** how much of the session's slot time held a ticket, split into building and review or waiting to merge, and why idle slots were idle (overlap, gate, nothing takeable, usage limit).
- **Usage efficiency:** tokens by role and model (input, output, cache read, cache write), tokens per merged ticket, the orchestrator's share, cache hits.
- **Headroom:** your plan's 5-hour and 7-day usage over the session; 5-hour windows that reset under 50% (`--threshold`) while every slot was full and tickets waited; usage-limit hits. A profile recommends one more or one fewer slot when the history supports it; the per-pass [slot suggestion](#choosing-a-slot-count) is separate.

Profiles live in `.claude/orchestrate-profiles/` (gitignore it). Open the `.html` beside each one, a copy of `viewer/profile.html` with the profile embedded: a slot timeline, tokens by role, plan usage over time, and tables behind each. Drop several profile JSON files on any copy to compare sessions.

**Plan usage needs the status-line tap.** Claude Code shows plan usage only to the status line, so `/orchestrate:setup` offers to pipe yours through `scripts/usage-tap.py`, which logs changes to `~/.claude/orchestrate/usage.jsonl` and prints nothing. By hand: copy it to `~/.claude/orchestrate/usage-tap.py` and add `echo "$input" | python3 -I ~/.claude/orchestrate/usage-tap.py` to your status-line script. Only subscribers get usage figures.

Any past session can be profiled after the fact, without `--profile`, by its ID or transcript path:

```sh
python3 scripts/profile.py <session id> [--repo PATH] [--slots N] [--threshold 50]
```

Without a pass log, idle reasons and whether slots were full with work waiting show as unknown.

## Limits

- Workers branch from the default branch (Claude Code's worktree default), so a ticket starts only after its dependencies have merged.
- Agents from a plugin ignore `permissionMode`, so workers follow your session's permission mode.
- The orchestrator stops for anything destructive or outward-facing the config doesn't authorise.
