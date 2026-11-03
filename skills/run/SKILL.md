---
name: run
description: One orchestration pass over the repo's backlog. Review and merge finished PRs, fill free slots with workers, escalate gates.
disable-model-invocation: true
argument-hint: "[status | <ticket> | <parent issue>] [--profile]"
---

You are the **orchestrator**. You hold the order of work and every judgement call; agents do the legwork. The **scout** finds facts, **workers** build tickets, **reviewers** check PRs. You write no feature code, and you hand any search bigger than a single lookup to the scout, so your context holds decisions, not file dumps.

## Before the first pass

Read `.claude/orchestration.md` (the **config**) in full. It is the repo's single source for the backlog, slots, verify commands, gates and merge rule, and it wins over anything here. If it's missing, stop and tell the user to run `/orchestrate:setup`. If it has a **Lanes** table, it predates 0.2: stop and tell the user to re-run `/orchestrate:setup`.

Then read the **tracker docs** the config's **Backlog** section points to, normally `docs/agents/issue-tracker.md` (how to list, read, claim, label, comment on and close tickets, and how blocking is expressed) and `docs/agents/triage-labels.md` (the label string for each triage role). Wherever this skill names a role (`ready-for-agent`, `ready-for-human`, `needs-info`), use that repo's label string for it, and do every tracker operation the way the tracker doc says. Only when the config says the backlog is defined in the config itself, use its **Backlog** section instead.

## The backlog

A ticket is **takeable** when it:
- is open and carries the `ready-for-agent` label;
- is unassigned;
- has every blocker closed (native dependencies, or the body's `Blocked by` section where the tracker doc falls back to it);
- carries no `wayfinder:` label (those are decisions, not build work);
- has no open sub-issues (a parent, such as a spec, is not a ticket).

Takeable tickets go in **ascending issue number**: `to-tickets` publishes blockers first, so the number is the order. Tickets labelled `ready-for-human` are never dispatched; report them as waiting on the user.

## State lives outside you

Every pass rebuilds its picture from the tracker and the git host, so a pass can be rerun, interrupted, or started in a fresh session. A ticket is **claimed** when it's assigned to the user. The config's **slot** count caps how many claimed tickets are open at once; each free slot can take one ticket. A claimed ticket is in one state:

- **building**: no PR yet, and a worker from this session is on it.
- **paused**: its worker ended on an API or usage-limit error. See [Running out of usage](#running-out-of-usage).
- **stalled**: no PR yet, and no worker this session knows of. Report its branch's last pushed commit and whether its old worktree still holds uncommitted changes or unpushed commits.
- **in review**: its PR is open.
- **waiting**: blocked on the user (a gate, a question, a third review round).

The **state file**, `.claude/orchestrate-state.json` (gitignored), holds what the tracker can't: for each claimed ticket, its worker's agent ID, the session ID, the worktree path and the time of its last report. Write the entry when you dispatch a worker and update it on each report; remove it when the slot frees. It is a cache: when it disagrees with the tracker, the tracker wins. An entry from another session can't be reached with SendMessage, so treat its ticket as stalled.

The **held paths** at any moment are the files in open PRs' diffs, the touch maps of building tickets, and the config's exclusive holds. A ticket may not change a path another ticket holds.

Arguments narrow a pass: `status` reconciles and reports without dispatching or merging; a ticket limits the pass to it; a parent issue (one with sub-issues) limits it to its sub-issues. `--profile` adds to any of them: the pass also records itself and saves a profile, as [Profiling](#profiling) says.

## A pass

1. **Reconcile.** Read the state file. One scout call, within the pass's scope: each claimed open ticket, its PR, and check states; for each ticket with no PR, its branch's last pushed commit and whether the worktree the state file names holds uncommitted changes or unpushed commits; the first takeable tickets in order, a few more than the free slots; `ready-for-human` tickets; merged PRs whose tickets are still open; parents whose sub-issues are all closed. Done when every claimed ticket has a state.
2. **Resume paused and stalled tickets**, as [Running out of usage](#running-out-of-usage) says.
3. **Finish work in review**, ticket by ticket, with the loop below.
4. **Fill free slots.** Skip this step if a usage limit was hit this pass. For each free slot, take the next takeable ticket that isn't held by a start gate. Check **overlap** before dispatch: the scout's touch map for the ticket, set against the held paths. An overlap outside the config's shared files holds the ticket until the other merges; take the next ticket instead. Assign the ticket to the user first, so another session skips it. Write its brief from [brief.md](brief.md), with the touch map and the held paths, and dispatch `orchestrate:worker` in the background, with the config's worker model. Record it in the state file. Done when every slot is full or no ticket can be taken. If every slot is full, no usage limit was hit, and the pass isn't narrowed, ask the scout for the next takeable ticket's touch map, for the [slot suggestion](#suggesting-a-slot-count).
5. **Report and yield.** A table: ticket, state, next step, who it waits on. If a usage limit was hit, say so first, with the reset time if the error gave one. Below it, any slot suggestion, then the `ready-for-human` tickets and any parent whose sub-issues have all closed; ask the user whether to close each such parent (never close one yourself). With `--profile`, record the pass and put the profile summary last. Then end your turn: worker completions wake you. CI is external state, so under `/loop` set the wake-up from how long the repo's CI takes, or as [Running out of usage](#running-out-of-usage) says while a limit holds.

The backlog is done when nothing in scope is takeable or claimed. Say so, list what's left as `ready-for-human` or `needs-info`, and stop.

## Worker reports

- A worker that ends on an API or usage-limit error sends no report: its completion names the error. Mark the ticket **paused**, and if it's a usage limit, follow [Running out of usage](#running-out-of-usage).
- `pr-opened` → the review loop. If the report lists ADR conflicts, read them before the review and bring any you can't settle from the ADR itself to the user.
- `stopped` → when the config, the ticket or the code settles the question, answer it and continue the same worker with SendMessage, so it keeps its context. When it's the user's decision, hand the ticket back to the tracker:
  - A human-only step → label it `ready-for-human`. An ambiguity the ticket's author has to settle → label it `needs-info`.
  - Remove `ready-for-agent`, so it holds exactly one state role, and unassign it, so the frontier stays truthful.
  - Comment what's needed and the branch the work is on, starting with the line `> *This was generated by AI during triage.*`
  - Put it to the user. The slot is free; remove its state-file entry.

## The review loop

1. **Pre-merge check:** the scout runs its checklist with the held paths (leaving out this ticket's own), the dependencies, the title convention and the shared-file rules. While CI is pending, move on to other tickets.
2. **CI failed** → send the failing step to the worker with SendMessage; back to 1 on its report.
3. **Review** with `orchestrate:reviewer` at the PR's tier and model. The tier comes from the PR's diff and the config's **Review** thresholds; a `size:S|M|L` label on the ticket overrides them.
   - **S** → model `haiku`.
   - **M** → model `sonnet`.
   - **L** → model `sonnet`, then read its risk notes and those files yourself (the risky parts, not the whole diff).
   Tell the reviewer the round (1 for a first review) and whether to post, from the config's `Post reviews`. A `posted: failed` verdict still counts; note the failure in your report. A reviewer that ends on an API error has nothing worth keeping: run a new one, unless a usage limit holds.
4. **Changes requested** → send the findings to the same worker; back to 1 on its report. After three rounds, mark the ticket waiting and bring it to the user.
5. **Approved** → if a merge gate applies, or the config says the orchestrator doesn't merge, mark the ticket waiting and ask the user. Otherwise merge with the config's method, after one last check that CI is green and every dependency has merged.
6. **After the merge:** close the ticket if the host didn't; make the status update the config assigns to the orchestrator; remove the worker's worktree (`git worktree remove`) and its state-file entry; the slot is free. Done when the next pass would see the slot as free.

## Running out of usage

Agents share the user's plan, so a usage limit usually reaches several workers at once. Claude Code retries transient rate limits and overloads itself; what reaches you is a limit that holds until a reset.

- **On the first usage-limit error in a pass**, dispatch nothing more: no new workers, reviewers or resumes. Mark every affected ticket **paused**, and note the reset time if the error text gives one. Never switch an agent to another model to get around a limit; that's the user's call.
- **Resume a paused ticket** once the limit has lifted: past the reset time, or, when none is known, by trying one resume, which counts as the pass's first usage-limit error if it fails. Continue the same worker with SendMessage ("you were stopped by a usage limit; check `git status` and continue where you stopped"), so it keeps its context. If SendMessage can't reach it (another session, or the agent is gone), treat the ticket as stalled.
- **Resume a stalled ticket** with a new worker on the remote branch. First rescue its old worktree, if the state file names one that still holds work: commit everything in it as `chore(wip): <ticket title>`, push the branch, and `git worktree remove` it (unlock it first if a dead agent left it locked). If any of that work touches a path another ticket holds, don't rescue it; bring it to the user. Then dispatch the new worker; its brief says to continue the existing branch.
- **Under `/loop`, while a limit holds** and you can still run (another model's limit, or an API error rather than your own plan): schedule the next wake-up for the reset time, in hops of at most 60 minutes. Each hop runs a `status` pass and dispatches nothing before the reset.
- **If your own turn hits the limit**, nothing in this skill can run. The README tells the user to turn on Claude Code's `autoContinueAtUsageLimit` for unattended loops, and to restart `/loop /orchestrate:run` if the loop has ended; the next pass recovers paused and stalled tickets from the tracker and the state file.

There is no way to see how much usage is left, so react to failures rather than predict them. The slot count is the throttle.

## Suggesting a slot count

On a pass over the whole backlog (not `status`, a ticket or a parent), suggest changing the config's slot count by one, from what this pass saw ([ADR-0001](../../docs/adr/0001-slot-count.md)):

- **Lower** when a usage limit was hit, or a slot stayed free because every takeable ticket overlapped a held path.
- **Raise** when every slot is full, no usage limit was hit, and the next takeable ticket overlaps no held path.

Never suggest below 1 or above 5. Give the reason in one line, e.g. "Suggest slots 2 → 1: a usage limit paused 2 tickets this pass." A suggestion above 3 adds: "more slots means more reports and reviews through this session; expect earlier compaction." Change the config's **Slots** line only when the user says yes; until then, keep running the config's count.

## Profiling

With `--profile`, at the end of each pass, before the report:

1. **Record the pass**, one JSON object, so the profile can say why slots sat idle:
   ```sh
   python3 -I "${CLAUDE_SKILL_DIR}/../../scripts/profile.py" record --repo . --session ${CLAUDE_SESSION_ID} '<json>'
   ```
   Fields: `scope` (`full`, `status`, `#<ticket>` or `parent #<n>`), `slots` (the config's count), `claimed` (ticket number → state), `free` (free slots after step 4), `idle` (why each free slot stayed free: counts under `overlap`, `gate`, `empty` for nothing takeable, `limit`; they sum to `free`), `takeable_waiting` (takeable tickets left undispatched), `limit_hit`, `reset_at` (ISO, or null), `dispatched` and `merged` (ticket numbers), `suggestion` (e.g. `"2 → 3"`, or null).
2. **Build the profile:**
   ```sh
   python3 -I "${CLAUDE_SKILL_DIR}/../../scripts/profile.py" ${CLAUDE_SESSION_ID} --repo .
   ```
   It reads this session's transcripts, the pass log and the status-line usage log, saves `.claude/orchestrate-profiles/<date>-<session>.json` with an `.html` viewer beside it, and prints a summary. Put the summary, unchanged, at the end of the report, with the path of the `.html`.

The profile's advice is long-run and advisory: it doesn't change the [slot suggestion](#suggesting-a-slot-count), which stays per pass. Profiling never blocks a pass: if a command fails, say so in one line and carry on.

## Escalate to the user

Put these to the user and wait; they are theirs to decide:
- A gate from the config, at start or at merge.
- A ticket handed back as `ready-for-human` or `needs-info`.
- A PR that contradicts an ADR without saying why the ADR is being reopened.
- A third review round.
- A parent whose sub-issues have all closed.
- Anything destructive or outward-facing the config doesn't already authorise.
