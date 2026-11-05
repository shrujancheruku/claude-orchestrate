# Orchestrate

A Claude Code plugin that runs a repo's ticket backlog: an orchestrator dispatches workers, one per ticket, and has reviewers check their PRs. The terms below are the ones its prompts, scripts and docs share. Each entry names its source and the words it is easily confused with.

## Language

### Work and state

**Config**:
The repo's `.claude/orchestration.md`: the single source for the backlog, slots, verify commands, gates and merge rule, and it wins over the skills. Source: `skills/run/SKILL.md` (Before the first pass), `skills/setup/config-template.md`.
_Avoid_: settings, setup file

**Ticket**:
One unit of build work in the backlog: a GitHub issue that a worker turns into a PR.
_Avoid_: task, story, item

**Takeable**:
A ticket that is open, `ready-for-agent`, unassigned, has every blocker closed, no `wayfinder:` label and no open sub-issues. Source: `skills/run/SKILL.md` (The backlog).
_Avoid_: ready, available, unblocked (blocking is only one clause)

**Frontier**:
The takeable tickets, in ascending issue number. Source: `agents/scout.md`.
_Avoid_: queue, ready list

**Slot**:
One place in the config's cap on how many claimed tickets are open at once; a free slot can take one ticket. Source: `skills/run/SKILL.md` (State lives outside you), `docs/adr/0001-slot-count.md`.
_Avoid_: lane, worker limit, concurrency

**Greedy**:
A session started with `/orchestrate:run --greedy[=N]`: each pass fills slots with every takeable ticket that overlaps no held path, up to the cap N (5 by default), without editing the config's slot count. Source: `skills/run/SKILL.md` (Greedy slots), `docs/adr/0007-greedy-slots.md`.
_Avoid_: auto slots, unlimited, max parallelism

**Claimed**:
A ticket assigned to the user; it holds a slot until it merges or is handed back to the tracker. Source: `skills/run/SKILL.md`.
_Avoid_: taken, locked, in progress

**Building**:
A claimed ticket with no PR yet and a worker from this session on it. Source: `skills/run/SKILL.md`.
_Avoid_: running, active

**Paused**:
A claimed ticket whose worker ended on an API or usage-limit error. Source: `skills/run/SKILL.md` (Running out of usage).
_Avoid_: suspended, rate-limited

**Stalled**:
A claimed ticket with no PR and no worker this session knows of. Source: `skills/run/SKILL.md`.
_Avoid_: stuck, orphaned, abandoned

**In review**:
A claimed ticket whose PR is open. Source: `skills/run/SKILL.md`.
_Avoid_: under review, open

**Waiting**:
A claimed ticket blocked on the user: a gate, a question or a third review round. Source: `skills/run/SKILL.md`.
_Avoid_: blocked, on hold

**State file**:
`.claude/orchestrate-state.json`, gitignored: for each ticket this session dispatched a worker for, the agent ID, worktree path (null once gone), worker model, time of its last report and the brief's touch map. Rebuilt by `scripts/rebuild-state`, never written by the orchestrator, and a cache that the tracker overrules. Source: `skills/run/SKILL.md`.
_Avoid_: status file, session file

**Pass**:
One run of `/orchestrate:run`: reconcile, resume, review, fill slots, report. Source: `skills/run/SKILL.md` (A pass).
_Avoid_: iteration, cycle

**Run log**:
The events log (`events.jsonl`: one line per gate, CI, merge or replay) plus the pass log (`passes.jsonl`), both in `.claude/orchestrate-profiles/` of the main checkout, whichever subdirectory or linked worktree a script or the profile runs from. Owned by `scripts/runlog.py`; the profile reads it. Logs that landed in a worktree before this are not migrated.
_Avoid_: events file, pass file, profile data

**Brief**:
The pointer-only message that hands a ticket to a worker: ticket, touch map, held paths, branch, verify command and rules. Source: `skills/run/brief.md`.
_Avoid_: prompt, spec (the ticket is the spec)

### Overlap

**Touch map**:
The files a ticket is expected to change, as a prediction rather than a fence. Source: `agents/scout.md`, `skills/run/brief.md`.
_Avoid_: file list, change set

**Held path**:
A file that another ticket holds: in an open PR's diff, in a building ticket's touch map, or under an exclusive hold. A ticket may not change one; overlap compares file paths with any `:line` stripped, and the brief writes held paths as plain file paths. Source: `skills/run/SKILL.md`.
_Avoid_: locked file, reserved path, blocked path

**Exclusive hold**:
A config rule that, while a ticket is open, nothing else touches a path. Source: `skills/setup/config-template.md`.
_Avoid_: lock, freeze

**Shared file**:
A file many PRs edit, listed in the config with the only change each PR may make; the merge sweep resolves collisions in it by replaying the PR's lines onto main. Source: `skills/setup/config-template.md`.
_Avoid_: hot file, common file

### Merging

**Sweep**:
What `scripts/merge` does after a merge: updating every other open PR whose branch now conflicts with the default branch. Source: `docs/guide.md` (The guard hook), `skills/run/SKILL.md`.
_Avoid_: refresh, rebase-all

**Hand-back**:
A swept branch the script could not update and left untouched, reported as a `HANDBACK PR #n <branch>: <path> (<why>)` line (merge exit 4). Not the same as handing a ticket back to the tracker, and not a reviewer's result. Source: `skills/run/SKILL.md`, `agents/worker.md`.
_Avoid_: bounce, kick-back

**Conflict list**:
The `HANDBACK` lines passed to the worker whose branch was handed back. Source: `agents/worker.md` (Follow-ups).
_Avoid_: conflict report

### Control points

**Gate**:
A point where the user decides: a start gate holds a ticket before a worker starts, a merge gate holds an approved PR before it merges. Both come from the config. Source: `skills/setup/config-template.md`, `skills/run/SKILL.md`.
_Avoid_: approval, checkpoint, hold

**Pre-merge check**:
The scout's numbered checklist on a PR (CI, held paths, dependencies, title, shared-file rules), reported pass, fail or pending. Not a gate: it asks nobody. The `gate` script is a separate check that runs before review. Source: `agents/scout.md`, `skills/run/SKILL.md` (The review loop).
_Avoid_: merge gate, pre-flight

**Tier**:
The size class of a PR, S, M or L, from its diff and the config's thresholds (or a `size:` label); it sets the review depth and, with the ticket's size, the worker and reviewer models. Source: `agents/reviewer.md`, `skills/setup/config-template.md`.
_Avoid_: size class, level, review level

**Round**:
One review of a PR, numbered from 1; after three the ticket waits for the user. Source: `skills/run/SKILL.md`, `agents/reviewer.md`.
_Avoid_: iteration, pass (a pass is one orchestrator run)
