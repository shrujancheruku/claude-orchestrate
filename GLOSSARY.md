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

**Parent**:
An issue with sub-issues, open or closed. Its sub-issues are the tickets; the parent itself is never taken. A parent whose sub-issues have all closed is reported to the user to close. Source: `scripts/frontier`.
_Avoid_: epic, umbrella, spec (a spec may be a parent; the word is for the issue itself)

**Takeable**:
A ticket a worker may take now: open, `ready-for-agent`, unassigned, with no open blocker, not a **Parent**, and carrying neither a `wayfinder:` label nor `lead`. Source: `scripts/frontier` (its docstring holds the clause list).
_Avoid_: ready, available, unblocked (blocking is only one clause)

**Frontier**:
The takeable tickets, in ascending issue number. Source: `scripts/frontier` (was `agents/scout.md`).
_Avoid_: queue, ready list

**Slot**:
One place in the config's cap on how many claimed tickets are open at once; a free slot can take one ticket. Source: `skills/run/SKILL.md` (State lives outside you), `docs/adr/0001-slot-count.md`.
_Avoid_: lane, worker limit, concurrency

**Greedy**:
A session started with `/orchestrate:run --greedy[=N]`: each pass fills slots with every takeable ticket that overlaps no held path, up to the cap N (5 by default), without editing the config's slot count. Source: `skills/run/SKILL.md` (Greedy slots), `docs/adr/0007-greedy-slots.md`.
_Avoid_: auto slots, unlimited, max parallelism

**Claimed**:
A ticket assigned to the user; it holds a slot until it merges or is handed back to the tracker. On a claimed stop it may carry `needs-lead` beside `ready-for-agent`. Source: `skills/run/SKILL.md`, `docs/agents/triage-labels.md`.
_Avoid_: taken, locked, in progress

**Claim**:
A claimed ticket together with what decides its state: its branch, worktree and lock, its PR, and the worker the state file records for it. The state is one of **Building**, **Stalled**, **Elsewhere**, **In review**, with the lead, or done. `scripts/claims` is the one source: it reports each claim, and the pass table and every script that needs a claimed ticket's state read it. Source: `scripts/claims`, `skills/run/SKILL.md`.
_Avoid_: ticket record, worker state, claim state

**Building**:
A claimed ticket with no PR yet and a worker from this session on it. Source: `scripts/claims`, `skills/run/SKILL.md`.
_Avoid_: running, active

**Paused**:
A claimed ticket whose worker ended on an API or usage-limit error. Source: `skills/run/SKILL.md` (Running out of usage).
_Avoid_: suspended, rate-limited

**Stalled**:
A claimed ticket with no PR and no live worker in any session: none this session knows of, and none that is **Elsewhere**. It goes through the **Rescue** (`scripts/rescue`) before a new worker continues its branch. Source: `scripts/claims`, `scripts/rescue`, `skills/run/SKILL.md`.
_Avoid_: stuck, orphaned, abandoned

**Elsewhere**:
A claimed ticket with no PR whose worker belongs to another live session. This session doesn't resume, rescue (`scripts/rescue` refuses its live lock) or redispatch it. Source: `scripts/claims`, `skills/run/SKILL.md`.
_Avoid_: foreign, orphaned

**Rescue**:
Saving a worktree's work to its branch and removing the worktree, so nothing is lost when its worker is replaced or stopped: a **Stalled** ticket before a new worker continues the branch, or a worker this session stopped. It refuses an **Elsewhere** ticket's worktree, and a held path among the saved changes holds the redispatch, never the save. `scripts/rescue` is the one source. Source: `scripts/rescue`, `skills/run/SKILL.md`.
_Avoid_: cleanup, salvage, sweep (that is the merge's)

**In review**:
A claimed ticket whose PR is open. Source: `scripts/claims`, `skills/run/SKILL.md`.
_Avoid_: under review, open

**Waiting**:
A claimed ticket blocked on the user: a gate, a question or a third review round. With a lead live it waits on the lead instead and carries `needs-lead`. Source: `skills/run/SKILL.md`, `docs/adr/0008-lead-above-orchestrator.md`.
_Avoid_: blocked, on hold

**State file**:
`.claude/orchestrate-state.json`, gitignored: for each ticket this session dispatched a worker for, the agent ID, worktree path (null once gone), worker model, time of its last report and the brief's touch map. Rebuilt by `scripts/rebuild-state`, never written by the orchestrator, and a cache that the tracker overrules. `scripts/claims` is its one reader. Source: `scripts/rebuild-state`, `scripts/claims`, `skills/run/SKILL.md`.
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

**Worker report**:
The text a worker's completion carries: `status: pr-opened | stopped`, the PR, branch, verify result and, on a stop, `stop-reason`. `scripts/worker-report` classifies it before the orchestrator acts, and logs each verdict as a `worker-report` event. Source: `agents/worker.md` (Report), `scripts/worker-report`.
_Avoid_: hand-back (that is the sweep's `HANDBACK` line), result, completion (the harness's notice, which may carry no report)

### Overlap

**Touch map**:
The files a ticket is expected to change, as a prediction rather than a fence. Source: `agents/scout.md`, `skills/run/brief.md`.
_Avoid_: file list, change set

**Held path**:
A file that another ticket holds: in an open PR's diff, in a building ticket's touch map, in the changed files of an **Elsewhere** ticket's live worktree, or under an exclusive hold. A ticket may not change one. `scripts/holds` is the one source of the rule: it decides what is held and whether a ticket overlaps it, and the brief's `Held paths:` line is its output. Source: `scripts/holds`, `skills/run/SKILL.md`.
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

**Approved head**:
The PR head commit the reviewer approved. `scripts/merge --approved-head` measures the worker's later change against it (the post-approval risk guard), ignoring merges of the default branch. Source: `scripts/merge`, `skills/run/SKILL.md`.
_Avoid_: approved SHA, reviewed commit, last-reviewed head

**Hand-back**:
A swept branch the script could not update and left untouched, reported as a `HANDBACK PR #n <branch>: <path> (<why>)` line (merge exit 4). Not the same as handing a ticket back to the tracker, not a reviewer's result, and not a **Worker report**. Source: `skills/run/SKILL.md`, `agents/worker.md`.
_Avoid_: bounce, kick-back

**Conflict list**:
The `HANDBACK` lines passed to the worker whose branch was handed back. Source: `agents/worker.md` (Follow-ups).
_Avoid_: conflict report

### Control points

**Gate**:
A point where the user decides: a start gate holds a ticket before a worker starts, a merge gate holds an approved PR before it merges. Both come from the config. The **Script gate** and **Merge readiness** are mechanical checks that ask nobody, so they are not gates. Source: `skills/setup/config-template.md`, `skills/run/SKILL.md`.
_Avoid_: approval, checkpoint, hold

**Script gate**:
The mechanical checks a PR passes before any reviewer reads it: mergeability with its base, its title against Conventional Commits, overlap with held paths, dependencies merged, no secrets, lockfiles with their manifests, and the config's repo-specific `run:` commands. Everything in it is something the worker alone can fix. Not a **Gate**: it asks nobody. Source: `scripts/gate` (every check, calling `scripts/holds` for held paths), `skills/run/SKILL.md` (The review loop).
_Avoid_: pre-merge check, pre-flight, gate (bare, for the script)

**Merge readiness**:
Whether an approved PR may merge now, decided at merge time: CI green on the head, every dependency merged, branch up to date, every **Review thread** resolved, and nothing since the **Approved head** that needs re-review. Source: `scripts/merge`.
_Avoid_: merge check, merge rule

**Tier**:
The size class of a PR, S, M or L, from its diff and the config's thresholds (or a `size:` label); it sets the review depth and, with the ticket's size, the worker and reviewer models. Source: `agents/reviewer.md`, `skills/setup/config-template.md`.
_Avoid_: size class, level, review level

**Round**:
One review of a PR, numbered from 1; after three the ticket waits for the user. A thread reopened in a round is a blocking finding of that round. Source: `skills/run/SKILL.md`, `agents/reviewer.md`.
_Avoid_: iteration, pass (a pass is one orchestrator run)

**Review thread**:
A reviewer's finding or a human's inline comment on a PR, which stays open until whoever acts on it closes it with a reply saying fixed or left, and which the reviewer may reopen; the unit the merge checks before merging. Source: `scripts/threads`, `scripts/merge`, `agents/reviewer.md`, `agents/worker.md`.
_Avoid_: comment, conversation, finding (a finding is what the reviewer writes; the thread is what it becomes on the PR)

### The lead

**Lead**:
The session above one orchestrator: it triages discovered work, writes ADRs and answers `needs-lead` stops, and never merges. Source: `docs/adr/0008-lead-above-orchestrator.md`.
_Avoid_: supervisor, manager, parent orchestrator

**Lead mode**:
Whether a lead session is live above the orchestrator. Detected, never configured. Source: `docs/adr/0008-lead-above-orchestrator.md`.
_Avoid_: lead flag, lead setting

**Discovered work**:
A problem a worker or reviewer finds outside its ticket's scope, filed as a draft ticket (`needs-triage` + `lead`) for the lead to triage. Not a finding, which is a reviewer's comment on the PR under review. Source: `docs/adr/0008-lead-above-orchestrator.md`, `agents/reviewer.md`.
_Avoid_: follow-up, spin-off, finding

**Stop comment**:
The comment a worker's question is posted as when its stop goes to the lead: its first line is `> *Stopped for the lead.*`, and `scripts/lead-route` reads it for the stop's time. Source: `scripts/lead-route`, `skills/run/SKILL.md`.
_Avoid_: stop note, question comment

**Lead watermark**:
`.claude/orchestrate-lead.json`, gitignored: how far the lead has read the tracker, so a restarted lead resumes there. Source: `docs/adr/0008-lead-above-orchestrator.md`.
_Avoid_: checkpoint, cursor, lead state

**Risk note**:
A line from a worker, the orchestrator or an L-tier reviewer (`path:line-range — what to look at`) on a decision or PR the lead should weigh, saying what is risky and why. `scripts/lead-brief` reads them from the posted review or the fallback PR comment. Source: `docs/adr/0008-lead-above-orchestrator.md`.
_Avoid_: warning, caveat, flag
