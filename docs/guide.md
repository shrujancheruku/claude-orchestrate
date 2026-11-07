# orchestrate: the full guide

Everything about installing, configuring and running the plugin. For the overview, see the [README](../README.md); for the terms, the [glossary](../GLOSSARY.md).

## What it is

A Claude Code plugin that works through a backlog of GitHub issues in parallel, picking up the tickets that [Matt Pocock's skills](https://github.com/mattpocock/skills) (`to-tickets`, `to-spec`, `triage`) leave `ready-for-agent`. Your main session acts as the **orchestrator**: it decides the order of work, hands each ticket to a **worker** in its own git worktree, pushes searches down to a cheaper agent, reviews each PR at a depth sized to it, and merges by your repo's rule. Anything that is yours to decide waits for you.

```
orchestrator (your session, e.g. Opus)     order, briefs, verdicts, merges, gates
 ├─ worker        Sonnet, own worktree      one ticket → one PR
 ├─ reviewer      Haiku (S) / Sonnet (M, L high effort) one PR against its ticket, read-only, posts its review
 └─ scout         Haiku, read-only          tracker, CI and pre-merge checks
```

## Install

```sh
/plugin marketplace add shrujancheruku/claude-orchestrate
/plugin install orchestrate@claude-orchestrate
```

From a local clone, use the clone's path in place of `shrujancheruku/claude-orchestrate`. While developing, load it without installing: `claude --plugin-dir /path/to/claude-orchestrate`.

Needs `git` 2.38 or later (the post-merge sweep checks conflicts with `git merge-tree --write-tree`; on older git it says so and skips the sweep), `gh` (signed in), and `python3` 3.9 or later (plugin scripts and hooks: Python 3.9+, stdlib only). Run `/setup-matt-pocock-skills` in the repo first: orchestrate reads the tracker and domain docs it writes to `docs/agents/`. Without them, setup falls back to a backlog defined in the config.

## Use it in a repo

1. **`/orchestrate:setup`** scouts the repo (tracker, commands, layout, conventions, anything that would collide across parallel worktrees) and asks you the decisions in one round: backlog source, slots, gates, merge authority, models, review tiers. It infers shared files and their per-PR rules from your docs and merge history, advises on risk paths, and prints a recommendation if `autoCompactWindow` is above 200K (it never edits your settings). It writes `.claude/orchestration.md` (the **config**) and a `.worktreeinclude`.
2. **`/orchestrate:run`** runs one **pass**. It rebuilds state from the tracker, reviews and merges finished PRs, fills free slots with workers, and reports a table of tickets. Passes are safe to repeat and to start in a fresh session, because the state lives in the tracker, not in the conversation.
   - `/orchestrate:run status` reports without dispatching or merging.
   - `/orchestrate:run <ticket>` narrows a pass to one ticket; `/orchestrate:run <parent issue>` to its sub-issues.
   - Plain `/orchestrate:run` is the only supported flow. After its pass the orchestrator ends its turn and wakes only on worker and reviewer completions, on `watch-ci` lines (CI settling) and on its own usage-limit cron hops. It reads the config and tracker docs once per session, and again after a compaction summary, keeps every command's output bounded, and ends a wake with one line, or nothing when nothing needs you. It shows the full table only when a pass ends or something needs you, and a pass that ends with nothing in flight says so and stops.
   - **Discovered work.** A worker or reviewer that finds work the backlog is missing (a pre-existing bug in untouched code, a needed refactor, an unplanned dependency) records it in a `## Discovered work` section on its PR body or posted review, one line per item (`- <what is missing> — <where> — <why it matters>`), instead of fixing it or raising a `[minor]` finding. It never blocks the PR. Their reports carry `discovered: <n> — <gist>`, and the end-of-run summary lists the PR URLs that carry any, so you can triage them.
   - Add `--greedy[=N]` to a full or parent pass to size the slot count from how many tickets can run at once, up to N (5 by default): see [Choosing a slot count](#choosing-a-slot-count).
   - Add `--profile` to any of these to profile the session as it runs: see [Profiling](#profiling).
   - The two combine: `/orchestrate:run --greedy --profile` runs greedy and profiles it, which is how to find out whether greedy paid off on your backlog.

### Concepts

- **Ticket:** a GitHub issue that is open, `ready-for-agent`, unassigned and unblocked, with no open sub-issues. Tickets are taken in ascending issue number, the order `to-tickets` publishes them in. The tracker operations and label strings come from `docs/agents/`. A ticket a worker can't finish goes back to the tracker as `ready-for-human` or `needs-info`.
- **Slot:** one ticket in flight. The config sets how many. Two tickets run at once only when their **touch maps** (the files the scout expects each to change) don't overlap outside the shared files; an **exclusive hold** keeps a path to one ticket until it merges.
- **Domain docs:** workers and reviewers follow the glossary and ADRs that `docs/agents/domain.md` points to. A PR that contradicts an ADR without saying why is a blocking finding.
- **Gate:** a ticket, label or path that waits for your go-ahead to start or to merge.
- **Tier:** review depth from the PR's diff and the config's thresholds (a `size:` label on the ticket overrides). S is a Haiku checklist review; M is a Sonnet correctness review; L is a Sonnet review at high effort whose risk notes go in the posted review for humans; the orchestrator reads no risky files itself. A round-1 `approve` with minors gives the worker the minors to take or leave, and CI alone gates the merge. Later rounds continue the same reviewer, and a push after approval goes back to it only when it touches a risk path or exceeds the S thresholds. With `Post reviews: yes`, the reviewer posts each round on the PR itself as a comment review (verdict in the body, findings inline) and hands back only the verdict, counts and the review URL; the worker reads the findings from the PR. With `no`, or when a post fails, the reviewer returns the full findings. It's always a comment, because GitHub won't let your account approve its own PR.
- **Brief:** the pointer-only prompt a worker gets (see `skills/run/brief.md`). The ticket stays the spec.

## Cost

- Each role runs on the cheapest model that does it well. Set them per repo in the config's **Models** table.
- Searches and log reads run in Haiku contexts, so the orchestrator's and workers' contexts stay small.
- **Script gate:** before any reviewer is dispatched, `scripts/gate <pr>` checks the PR's mergeability with its base (conflicts and how far behind it is), its title against Conventional Commits, and each command a **Repo-specific check** in the config marks with ``run: `cmd` `` (plain backticked text is never run). It prints one line per check like `verify`, appends a `gate` event, and a failure goes straight back to the worker without counting as a review round.
- Review fixes go back to the worker that wrote the PR, so its context is reused instead of a new agent starting.
- Briefs share a fixed preamble, so workers share a prompt-cache prefix.
- Parallel agents multiply token use. Fewer slots cost less; the slot count is your throttle. See [Choosing a slot count](#choosing-a-slot-count).

## Choosing a slot count

Setup recommends 2. Each extra slot adds a worker's tokens and its reviews, raises the odds that a usage limit pauses several tickets at once, and adds reports and reviews to your main session, which handles them one at a time and is usually the first thing to saturate. The gain is wall-clock time, and only when the backlog's tickets are independent.

Every full pass suggests a change of one slot, between 1 and 5: lower after a usage limit or when overlaps left a slot idle, higher when every slot is full and the next ticket wouldn't overlap. It changes the config only when you say yes. The reasoning is in [ADR-0001](adr/0001-slot-count.md).

`/orchestrate:run --greedy[=N]` sizes the count for you instead. Each pass dispatches every takeable ticket whose touch map overlaps no held path, up to a cap of N (5 by default, 1 to 8), and never stops a worker in flight. It holds for the session, never edits the config, drops back to the config's count after a usage limit until the reset, and makes no per-pass suggestion. Each report starts `Greedy · cap N · running M`. The reasoning is in [ADR-0007](adr/0007-greedy-slots.md).

Use greedy when the backlog has several independent tickets and you have plan headroom to spend on finishing sooner: a batch of small fixes, or the leaves of a spec once their blockers have merged. Keep the config's count when tickets mostly touch the same files (overlap holds them at any count, so greedy gains nothing) or when usage is tight, since every running ticket adds a worker and its reviews. `--greedy=N` sets a lower or higher cap for one session without touching the config.

## The guard hook

`hooks/guard.py` runs before every Bash call and acts only on this plugin's agents (`agent_type` `orchestrate:<role>`):

| Role | Refused |
|---|---|
| scout, reviewer | git writes, `gh` writes, file writes and redirects, builds and installs. The reviewer may post one comment review on a PR (`gh api .../reviews` or `gh pr review --comment`, after an optional plain `cd <path> &&`), in the shapes `agents/reviewer.md` shows, and only when the config says `Post reviews: yes` |
| worker | `gh pr merge`, `scripts/merge`, force-pushes, pushes to `main`/`master` |
| any other `orchestrate:<role>` (retired, from another version, a typo) | the same as scout: the guard treats a role it doesn't know as read-only, and its refusals say so |

Every role also refuses `scripts/merge` (the orchestrator's merge script, which re-checks the PR until it is MERGED, then deletes the branch, closes the ticket and removes the worktree), run as a command.

After the cleanup, `scripts/merge` sweeps the other open PRs: each whose branch now conflicts with the default branch is updated and pushed to by the script itself (the orchestrator, not a worker, so the worker rule above doesn't apply). Under squash or merge it merges main into the branch with a plain push; under rebase it rebases and pushes with `--force-with-lease`. Conflicts in the files the config's **Shared files** bullet lists are resolved by replaying the PR's lines onto main (a word-level 3-way merge for a line changed on both sides); a conflicting file matching a glob under **Generated:** (nested bullets of the form `` - `glob`: `command` ``) takes main's side and the command is run in the sweep worktree against the PR's sources, its output committed; anything else, a failing or hanging command, a command that changes a file outside the generated globs, or overlapping words, is handed back: the branch is left untouched, the script prints `HANDBACK PR #n <branch>: <path> (<why>)` lines and exits 4, and the run skill resumes the worker with them. That sweep may rebase under the rebase method, but a worker never does: resumed with a conflict list, it always merges the default branch and pushes without force (`agents/worker.md`, Follow-ups). Each file's outcome (`line-replay`, `word-merge`, `regenerated`, `handed-back`) is logged as a `replay` event. A PR that is merely behind is not touched.

It splits the command into shell words (quotes, escapes, `$(...)`, heredocs, `sh -c`), decides on those, and refuses a command it can't parse. It's a guardrail against mistakes, not a sandbox. Your permission mode and sandbox settings still apply on top.

## Push and fetch under the sandbox

On macOS, `git fetch`, `pull` and `push` over an SSH remote fail inside the sandbox even when the host is allowed, because plain ssh ignores the sandbox proxy ([docs](https://code.claude.com/docs/en/sandboxing)); agents see `Permission denied (publickey)`. On Linux and WSL2, git's SSH is tunnelled through the sandbox proxy once the host is in `sandbox.network.allowedDomains`, so that alone is enough. For an SSH remote, `/orchestrate:setup` offers, with your yes, to run `scripts/sandbox-git`, which adds `git fetch *`, `git pull *` and `git push *` to `sandbox.excludedCommands` and the remote's host to `sandbox.network.allowedDomains` in `.claude/settings.local.json` (gitignored; leave it uncommitted; nothing under `~/.claude` is touched). Those three git commands then run **unsandboxed** and can reach any host. `excludedCommands` matches the command text, so a command run from inside a script or wrapper isn't matched, and workers push with plain `git push` ([docs](https://code.claude.com/docs/en/sandboxing)). Without that, a worker pushes over HTTPS through `gh`'s credential helper for one call (`git -c credential.helper='!gh auth git-credential' push https://github.com/<owner>/<repo>.git <branch>`), which needs github.com reachable from the sandbox. A worker never turns the sandbox off to push.

## Measuring CI wait

`scripts/ci-times [--since <time or PR>]` tells you whether CI wait is over the limit and whether the cause looks like too few runners or one slow job. It reads merged PRs only, works with lead mode off, and prints the median and p90 push-to-green, the slowest checks with queued and running time, and `over: yes|no`.

- **Push-to-green** runs from a head's commit time (not the push time, which `gh` does not give per commit, so a late push or a rebase skews it) to its last check going green. Heads that never went green are left out; a fix push counts as its own head.
- **Window:** the last 10 merged PRs, or every merged PR since a time or PR with `--since`.
- **Limit:** over means a p90 above 10 minutes. Set `- **CI wait limit:** <minutes>` in the config's Review section to change it.
- **Cause:** a hint, not a diagnosis. When over, `runners` if the slowest check (by median) waited longer than it ran, else `slow job`. Queued is measured from the commit, so a job that `needs:` another shows the upstream job's runtime as queued and can read as `runners`.

## Running out of usage

Every agent draws on your plan, so a usage limit tends to stop several workers at once.

- The first usage-limit error in a pass stops all new dispatches. The affected tickets show as **paused**, with the reset time when the error gives one.
- The next pass after the reset continues each paused worker where it stopped, with its context. A worker it can't reach (another session) is replaced by a new one on the same branch; first the orchestrator commits and pushes any work left in the old worktree as `chore(wip): …`.
- Workers push after every commit whose checks pass, so little is lost.
- `.claude/orchestrate-state.json` (gitignored) remembers each ticket's worker, worktree, model and last report between passes. `scripts/rebuild-state` rebuilds it from the session's transcripts at the start of each pass, joining workers to tickets by the `#<ticket>` in their dispatch description (`Build #<ticket>`); the orchestrator never writes it. A worker's worktree is the one its completion notice reports (on a branch `worktree-agent-<id>`), else its agent's directory under the harness's worktrees, else the checkout of its ticket's branch; the file records the first that exists, with the touch map the brief names. The tracker stays the source of truth.

While a limit holds and the orchestrator can still run, it schedules a one-shot cron at the reset time, in hops of at most 60 minutes; each hop runs a `status` pass. These cron jobs are session-only and fire only while the REPL is idle.

For unattended runs, turn on **Continue automatically at usage limit** in `/config` (`autoContinueAtUsageLimit`). Without it, a session whose own turn hits the limit ends after one retry about 20 minutes later; run `/orchestrate:run` again and the pass picks up where things stopped. A fallback model doesn't help: rate-limit errors never trigger one, and the orchestrator never switches models on its own.

## Profiling

`/orchestrate:run --profile` runs normal passes, and at the end of each one records the pass and saves a profile of the session so far. Each pass also records why free slots stayed idle (`overlap`, `gate`, `empty` for nothing takeable, `limit`), so with `--greedy` the profile shows whether a higher cap would have run more, or whether overlap and an empty backlog were what held it back:

```
Profile · session 346d1ea2 · 2026-11-03T16:01Z → 2026-11-03T16:22Z (21m) · 2 slots
proxy $ $3.09 per merged ticket (2 merged): opus $1.52 · sonnet $1.35 · haiku $0.22 · orchestrator $1.52
slots   occupied 59%  (building 37%, review/merge 22%)  idle: overlap 5m, empty 7m
plan    5h window 22% → 51%, peak 51%, 14.5 pts per merged ticket (7d 2.0) · 0 limit hits
saved   .claude/orchestrate-profiles/2026-11-03-346d1ea2.json  (+ .html)
```

- **Slot efficiency:** how much of the session's slot time held a ticket, split into building and review or waiting to merge, and why idle slots were idle (overlap, gate, nothing takeable, usage limit).
- **Proxy $ per merged ticket:** the whole session's cost (the orchestrator and every agent, priced per request at API list, by model and token type) divided by tickets merged, split by model and by role, with a per-ticket table of worker, reviewer and other cost. The prices are a dated constant at the top of `scripts/profile.py`; they are a proxy for plan usage, not your bill.
- **Usage efficiency:** tokens by role and model (input, output, cache read, cache write), tokens per merged ticket, the orchestrator's share, cache hits.
- **Review quality floor:** from the reviewers' own transcripts, so it works with posting on or off: rounds per PR, round-1 approvals and the round-1 "changes" rate, approved-with-minors PRs that merged with no round 2, and later rounds by the same reviewer (continued) versus a new one (fresh).
- **Workers by model:** each worker's model comes from its metadata. Tickets whose workers ran Haiku and then something stronger count as escalations. Usage, requests, median and p90 context per request, tool-output tokens (estimated at 4 characters each), rounds and round-1 approvals are split by worker model; a ticket counts under the model that finished it, except round 1, which is credited to the model that started it.
- **Depth-2 agents:** any agent that a dispatched agent spawned (joined to it by `toolUseId`) is listed as a defect, and the profile warns.
- **Orchestrator turns by job:** each of the orchestrator's turns is put in one job, by the tool or plugin script it called first (`watch-ci` and Monitor are CI waits, `gate` is review posting, `rebuild-state` is the state file, `merge` is merge and cleanup, Agent is dispatch), with a few command patterns as the fallback for hand-run commands. A turn with no tool call is end-of-wake text if it says anything, else no tool call. The profile reports turns and proxy $ per job per merged ticket, so you can see which jobs left Opus.
- **Context over time:** the orchestrator's context size at each turn, plotted with its compactions (trigger and `preTokens`); the effective window (the median `preTokens` of auto compactions); hand-back tokens per role, the orchestrator's own text and Bash output (all estimated at 4 characters a token); and idle wakes, the passes that dispatched, reviewed and merged nothing.
- **Merged tickets:** counted from the merge script's `merge` events (outcome `merged`); a session with no events log falls back to merge commands in the transcript and reports those merges as unverified. Slot efficiency is measured against each pass's own slot count, and the header shows `3→2 slots` when the count changed.
- **Script events:** read from the run log's `events.jsonl` in the main checkout, beside the pass log: script-gate runs (pass, fail or unknown; failures are counted apart from review rounds, and per ticket in the table), CI runs, merges and the shared-file hand-back rate. Without the log, the viewer says so.
- **Headroom:** usage-limit hits and reset times, from transcripts and the pass log. Plan usage (5-hour and 7-day percentages over the session, 5-hour windows that reset under 50% (`--threshold`) while every slot was full and tickets waited, and points used per merged ticket, unweighted) appears only when a legacy `usage.jsonl` exists; otherwise the profile says headroom isn't measured. A profile recommends one more or one fewer slot when the history supports it; the per-pass [slot suggestion](#choosing-a-slot-count) is separate.

Profiles live in `.claude/orchestrate-profiles/` (gitignore it). Open the `.html` beside each one, a copy of `viewer/profile.html` with the profile embedded: proxy $ per merged ticket, the orchestrator's turns by job, context per turn and script events, the review quality floor and workers by model, a slot timeline, tokens by role, plan usage over time, and tables behind each. Drop several profile JSON files on any copy to compare sessions.

**Plan headroom is not measured.** Claude Code gives plan usage only to the status line, and a plugin cannot add to or wrap the user's status line (a plugin's `settings.json` honors only `agent` and `subagentStatusLine`; checked against Claude Code 2.1.311), so the profile has no 5-hour or 7-day figures and says so. Limit hits and reset times still come from transcripts and the pass log. If you added the old `usage-tap.py` line to your status-line script, you can remove it; if `~/.claude/orchestrate/usage.jsonl` still has samples in the session, the profile reads them.

A profile prints the span it covers and warns when a transcript has records later than its end, which means it is partial (a session still running). The JSON is `schema` 2; the viewer reads only that.

Any past session can be profiled after the fact, without `--profile`, by its ID or transcript path:

```sh
python3 -I ~/.claude/plugins/cache/claude-orchestrate/orchestrate/<version>/scripts/profile.py <session id> [--repo PATH] [--slots N] [--threshold 50]
```

Without a pass log, idle reasons and whether slots were full with work waiting show as unknown.

To judge a trial against a baseline, profile both sessions with the same version of the script, then:

```sh
python3 -I ~/.claude/plugins/cache/claude-orchestrate/orchestrate/<version>/scripts/profile.py compare <baseline.json> <trial.json> [--max-cost USD] [--max-orchestrator-cost USD]
```

It prints the headline figures side by side with deltas (merged tickets, proxy $ and orchestrator $ per merged ticket, the round-1 "changes" rate, outright and with-minors round-1 approvals, rounds per PR), the L-tier share and each tier's $ per merged ticket (a PR's tier is the one its first reviewer was dispatched at). A second table gives the gating figures as measured and again mix-adjusted: each tier's figure in the trial reweighted by the baseline's tier mix, so a trial that drew more L tickets isn't judged dearer for that alone. The orchestrator's $ isn't attributed to tiers, so it isn't adjusted. The two $ targets aren't in the script; pass them from the spec (`--max-cost`, `--max-orchestrator-cost`) or those rows go unjudged. The review-quality rows are judged against the baseline: no more "changes" or rounds per PR, and as many round-1 approvals outright or counting approve-with-minors. The trial is flagged **INCONCLUSIVE** when it has fewer than 20 merged tickets, when its L share differs from the baseline's by more than 25 points, or when the mix adjustment flips a gating verdict.

To trial the orchestrator on a backlog:

1. Run plain `/orchestrate:run` (add `--profile` if you want per-pass records) and let it work until a pass ends with nothing in flight.
2. Note the session ID, then profile the finished session by hand:

```sh
python3 -I ~/.claude/plugins/cache/claude-orchestrate/orchestrate/<version>/scripts/profile.py <session>
```

## Development

Tests: `python3 -m unittest discover tests` (Python 3.9 or later, stdlib only).

**Behavioural tier (opt-in).** `ORCH_BEHAVIOUR=1 python3 -m unittest discover tests/behaviour` runs real headless `claude -p` agents against a temp repo with a fake `gh`, 3 runs per case (3/3 pass, 2/3 flaky, less fail), and prints a table with cost. Each run is capped at $1 and 40 turns, so a case costs at most $3; it is skipped without the variable, and CI never sets it. `ORCH_BEHAVIOUR_RUNS=1` cuts it to one run per case while you iterate. The cases (`tests/behaviour/test_*.py`) replace the phrase tests they match: the worker writes its PR body with the `:pr` skill (with and without the stub), cites a source for an external fact, and stops on a held path; the scout reports a PR one commit behind as behind; the reviewer posts one accepted review and returns its URL; and an S ticket is dispatched to a haiku worker.

**Exit codes of the scripts under `scripts/`.** One convention, so a broken environment never reads as a verdict:

- `0` ok.
- `1` a verdict: a check failed, or the action was refused (a worker is sent the output).
- `2` could not run: a missing tool (`gh`, `git`), bad input or a missing directory, or output the script could not read. Nothing about the code under test was decided; fix the environment and run it again.

A script may add its own codes above 2 for a specific state (`gate` 3 and `merge` 3/4 are documented in their docstrings). `common.run` reports a missing binary as code 127 instead of raising, and `gate`, `merge`, `watch-ci` and `verify` turn that into 2. `verify --config` (what every brief's `Verify:` line runs) reads the commands from the config's **Verify** section: each top-level bullet opening with a backticked span is one command, run exactly as written (no caller shell expands it), a bullet that is just `` `--then` `` starts a dependent group, and any other line is prose. A missing config, a missing section or one with no commands exits 2; `--config` with `CMD` arguments does too. Commands run by `verify` and `gate` get `--timeout` seconds (default 1800); one that outlives it is killed with its process group and fails with "timed out after Ns".

**How scripts under `scripts/` are found and run:** see [ADR-0003](adr/0003-script-invocation.md), checked by `tests/test_invocation.py`.

**Guard regression corpus.** `tests/fixtures/guard-corpus.jsonl` holds every distinct Bash call the plugin's subagents made in real sessions, one `{role, command, expected}` line each, and `tests/test_guard_corpus.py` runs `hooks/guard.py` over all of them, so a guard change that flips a decision shows up in the PR diff of the corpus. To add sessions (rerunnable, no duplicates, existing labels untouched), run the CLI from the repo root:

```
scripts/guard-corpus <session id or transcript path> [...] [--projects-dir ~/.claude/projects] [--dry-run]
```

It reads `<session>.jsonl` and `<session>/subagents/*.jsonl` and writes nothing under `~/.claude`. Commands are normalised before they are stored: home and repo paths, emails, token-shaped strings, heredoc bodies and long quoted text are replaced, and no tool output is kept. A line whose quoted text became `TEXT` (a `bash -c "..."`, `python3 -c "..."` or `gh api "..."` call) is a shape check, not the original command. Lines marked `"source": "hand"` are written by hand to pin refusals no session exercised. A new line's `expected` is a snapshot of the guard's decision (run in a directory whose config turns review posting on); read the new lines, especially the write-shaped ones (`git push`, PR merge, create or review, `gh api` with a method or `-f`/`-F`, `gh issue` edit, close or comment). If the guard is wrong, set the right `expected` and add `"gap": "#<ticket>"` for a ticket against the guard; the test then requires the guard to still be wrong there until the fix removes the `gap`.

## Limits

- Workers branch from the default branch (Claude Code's worktree default), so a ticket starts only after its dependencies have merged.
- Agents from a plugin ignore `permissionMode`, so workers follow your session's permission mode.
- The orchestrator stops for anything destructive or outward-facing the config doesn't authorise.
