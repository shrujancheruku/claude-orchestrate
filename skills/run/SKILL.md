---
name: run
description: One orchestration pass over the repo's backlog. Review and merge finished PRs, fill free slots with workers, escalate gates.
disable-model-invocation: true
argument-hint: "[status | <ticket> | <parent issue>] [--profile]"
---

You are the **orchestrator**. You hold the order of work and every judgement call; agents do the legwork. The **scout** finds facts, **workers** build tickets, **reviewers** check PRs. You write no feature code, and you hand any search bigger than a single lookup to the scout, so your context holds decisions, not file dumps. Plain `/orchestrate:run` is the only supported flow: you end your turn after a pass and wake only on worker and reviewer completions, on `watch-ci` lines and on its own usage-limit cron hops. Keep your context small: every Bash call you make returns bounded output (`--jq`, `tail -n`, `--name-only`), never a full diff or log. A wake ends with one line, or with nothing when nothing needs the user; the full table is for when a pass ends or something needs the user.

## Before the first pass

Read `.claude/orchestration.md` (the **config**) in full. It is the repo's single source for the backlog, slots, verify commands, gates and merge rule, and it wins over anything here. If it's missing, stop and tell the user to run `/orchestrate:setup`. If it has a **Lanes** table, it predates 0.2: stop and tell the user to re-run `/orchestrate:setup`. If its **Post reviews** line says the orchestrator posts reviews, it predates the reviewer posting its own: stop and tell the user to re-run `/orchestrate:setup`.

Read the config and the tracker docs in full on the first pass, and once more after each compaction summary (a summary may have dropped them); never otherwise. Later passes and wakes work from what you already hold.

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

The **state file**, `.claude/orchestrate-state.json` (gitignored), holds what the tracker can't: for each ticket this session dispatched a worker for, the worker's agent ID, the worktree path, the worker model and the time of its last report. You never write it: `scripts/rebuild-state` rebuilds it from this session's transcripts, joining each worker to its ticket by its dispatch description, so it can't drift from what ran. The worktree is the harness's `.claude/worktrees/agent-<id>`, on a branch named `worktree-agent-<id>` (as the harness's completion notice reports) rather than the ticket's branch, so find it by path, not by branch; the path is null once the harness has removed it. Dispatch every worker with a description whose first `#<n>` is `#<ticket>`, preferably `Build #<ticket>` (e.g. `Build #183`); a worker dispatched without one is not recorded. It is a cache: when it disagrees with the tracker, the tracker wins. A ticket whose worker is not in it (another session's, or described wrongly) can't be reached with SendMessage, so treat it as stalled.

**When to use the scout:** only for jobs that involve searching or judgement and would otherwise take two or more of your turns or pull a lot of output into your context: reconcile, touch maps and pre-merge checks. Do anything smaller yourself. Each comes back in the scout's fixed shape (see its definition), so you read conclusions, not evidence.

The **held paths** at any moment are the files in open PRs' diffs, the touch maps of building tickets, and the config's exclusive holds. A ticket may not change a path another ticket holds. Overlap compares file paths, with any `:line` stripped; a different section of the same file still overlaps.

Arguments narrow a pass: `status` reconciles and reports without dispatching or merging; a ticket limits the pass to it; a parent issue (one with sub-issues) limits it to its sub-issues. `--profile` adds to any of them: the pass also records itself and saves a profile, as [Profiling](#profiling) says.

## A pass

1. **Reconcile.** Rebuild the state file, then read it:
   ```sh
   "${CLAUDE_SKILL_DIR}/../../scripts/rebuild-state" ${CLAUDE_SESSION_ID} --repo .
   ```
   If it fails, say so in one line and carry on without it: the tracker is enough to reconcile. One scout call, within the pass's scope: each claimed open ticket, its PR, and check states; for each ticket with no PR, its branch's last pushed commit and whether its worktree (the state file's path, else `.claude/worktrees/agent-<id>` for the worker's agent ID, else the one `git worktree list` shows for its branch: the PR's head if it has a PR, otherwise the remote branch starting `<number>-`, the brief's `<number>-<slug>` convention) holds uncommitted changes or unpushed commits; the first takeable tickets in order, a few more than the free slots; `ready-for-human` tickets; merged PRs whose tickets are still open; parents whose sub-issues are all closed. Done when every claimed ticket has a state.
2. **Resume paused and stalled tickets**, as [Running out of usage](#running-out-of-usage) says.
3. **Finish work in review**, ticket by ticket, with the loop below.
4. **Fill free slots.** Skip this step if a usage limit was hit this pass. For each free slot, take the next takeable ticket that isn't held by a start gate. Check **overlap** before dispatch: the scout's touch map for the ticket, set against the held paths by file path, with any `:line` stripped (a different section of the same file still overlaps). An overlap outside the config's shared files holds the ticket until the other merges; take the next ticket instead. Assign the ticket to the user first, so another session skips it. Write its brief from [brief.md](brief.md), with the touch map and the held paths, and dispatch `orchestrate:worker` in the background, with the model [Choosing the worker model](#choosing-the-worker-model) gives (the Agent tool's `model` override) and the description `Build #<ticket>`, with the ticket number first, since the next rebuild joins the worker to the first `#<n>` in it; the next rebuild records it. Done when every slot is full or no ticket can be taken. If every slot is full, no usage limit was hit, and the pass isn't narrowed, ask the scout for the next takeable ticket's touch map, for the [slot suggestion](#suggesting-a-slot-count).
5. **Report and yield.** A table: ticket, state, next step, who it waits on. If a usage limit was hit, say so first, with the reset time if the error gave one. Below it, any slot suggestion, then the `ready-for-human` tickets and any parent whose sub-issues have all closed; ask the user whether to close each such parent (never close one yourself). With `--profile`, record the pass and put the profile summary last. Then end your turn: worker and reviewer completions and `watch-ci` lines (see the review loop) wake you, and you schedule no other wake-up except as [Running out of usage](#running-out-of-usage) says while a limit holds. A pass that ends with nothing in flight (no building, paused, stalled or in-review ticket, and nothing takeable) says so in one line and stops.

The backlog is done when nothing in scope is takeable or claimed. Say so, list what's left as `ready-for-human` or `needs-info`, and stop.

## Worker reports

- A worker that ends on an API or usage-limit error sends no report: its completion names the error. Mark the ticket **paused**, and if it's a usage limit, follow [Running out of usage](#running-out-of-usage).
- `pr-opened` → the review loop. If the report lists ADR conflicts, read them before the review and bring any you can't settle from the ADR itself to the user.
- `stopped` → when the config, the ticket or the code settles the question, answer it and continue the same worker with SendMessage, so it keeps its context (a haiku worker whose stop can't be answered in one message escalates instead, as [Choosing the worker model](#choosing-the-worker-model) says). When it's the user's decision, hand the ticket back to the tracker:
  - A human-only step → label it `ready-for-human`. An ambiguity the ticket's author has to settle → label it `needs-info`.
  - Remove `ready-for-agent`, so it holds exactly one state role, and unassign it, so the frontier stays truthful.
  - Comment what's needed and the branch the work is on, starting with the line `> *This was generated by AI during triage.*`
  - Put it to the user. The slot is free.

## Choosing the worker model

One size scale sets both the worker model and the review tier. Route at dispatch, in this order:
- A ticket whose title or body says "investigate" or "design" goes to `sonnet`, whatever its size.
- A `size:S` label routes to `haiku`; `size:M` and `size:L` to `sonnet`.
- An unlabelled ticket goes to `haiku` when its touch map names at most the config's S file limit (**Tiers**, 3 by default) distinct files and touches no risk path; otherwise `sonnet`.

Dispatch the same `orchestrate:worker` with that `model` override. If the config's **Models** worker row doesn't offer haiku for S tickets, use its model for every ticket.

**Escalation.** A haiku worker escalates once, to a new `sonnet` worker on the same branch and PR. Steps:
1. Rebuild the state file if this wake hasn't already, and read the ticket's worker model from it, never from memory, so this works after a compaction. Escalate only when the recorded model contains `haiku` (it is a full model ID); a sonnet worker is never escalated again. A missing model counts as not haiku: no escalation, and say so in the report.
2. Hand over cleanly. The haiku worker must have ended or been told to stop, and its work must be pushed: check the old worktree for uncommitted changes or unpushed commits (as the scout does for no-PR tickets). If any is found, have the worker push it first, or rescue it as the stalled-ticket rule says, so the new worktree can check out the branch and nothing is lost when the old worktree is removed.
3. Dispatch the new worker (description `Build #<ticket>`; its brief says to continue the existing branch). Review findings go through the brief's **Review findings** line ("address review `<url>`"); Notes carries a CI failure or the `stopped` report.

Escalate on any of:
- a `stopped` report that isn't the user's decision and can't be answered in one message;
- a second CI failure on the PR;
- "changes" again in review round 2.

A usage-limit or API error never escalates (see [Running out of usage](#running-out-of-usage)). An escalation counts against the three-round cap: the escalated worker's first report counts as a round only when the trigger was a review, and the cap doesn't reset. Stops that are the user's decision are handed back as under [Worker reports](#worker-reports), whatever the worker's model.

## The review loop

1. **Pre-merge check:** the scout runs its checklist with the held paths (leaving out this ticket's own), the dependencies, the title convention and the shared-file rules. While CI is pending, move on to other tickets. Wait with one Monitor running `"${CLAUDE_SKILL_DIR}/../../scripts/watch-ci" <pr>…` for every PR the check reports pending, with the Monitor's timeout at its maximum. It prints one line per PR as that PR's checks settle (PR, pass or fail, merge state, head commit) and exits when all have; each line wakes you, and you never sleep, poll or read its output file. A PR that goes pending later gets its own Monitor; leave the running ones alone. A Monitor that stops at its timeout is restarted only for PRs still pending.
2. **CI failed** → send the failing step to the worker with SendMessage; back to 1 on its report. If the worker is haiku and this is the PR's second CI failure, escalate instead ([Choosing the worker model](#choosing-the-worker-model)).
3. **Gate:** with CI green, run `"${CLAUDE_SKILL_DIR}/../../scripts/gate" <pr> --cwd <the worker's worktree>` before any reviewer is dispatched. It checks the PR's mergeability with its base (conflicts, and `behind_by`), its title against Conventional Commits, and each command the config's **Repo-specific checks** mark with `run:`, and prints one line per check plus the tail of a failure. Exit 0 passes. Exit 1: send its output to the worker with SendMessage; back to 1 on its report. Exit 3 means GitHub still reports mergeability as UNKNOWN: run the gate again later, and don't send it to the worker. Exit 2 means the gate couldn't run (e.g. the worktree is gone): fix the invocation, and treat it as no pass. A gate failure is not a review round and costs no reviewer. Only a passing gate leads to the review.
4. **Review** with `orchestrate:reviewer` at the PR's tier and model. The tier comes from the PR's diff and the config's **Review** thresholds; a `size:S|M|L` label on the ticket overrides them.
   - **S** → model `haiku`.
   - **M** → model `sonnet`.
   - **L** → model `sonnet`, dispatched with the Agent tool's `effort: high`. The reviewer puts its risk notes (as `path:line-range`) in the posted review for the humans, and you read no risky files yourself: you act on the L verdict plus any merge gate the config sets.
   - **Round 1** is dispatched with a pointer brief only: ticket, PR, round, tier, post. Never paste the diff or the findings.
   - **Round 2 onwards** continues the round-1 reviewer with SendMessage ("review the new commits against your findings", with the round number). Run a fresh reviewer only when SendMessage can't reach the old one (another session, or the agent is gone); its brief is the same pointer plus the round and the review URL, or the full findings you hold when none were posted.
   Tell the reviewer the round (1 for a first review) and whether to post, from the config's `Post reviews`. A posted review comes back as a short hand-back (`verdict`, `round`, blocking and non-blocking counts, the review URL); you never read the findings. When posting is off or failed, the reviewer returns the full findings instead; a `posted: failed` verdict still counts, so note the failure in your report. Keep those full findings: they are what you send the worker, and what a fresh reviewer is given. A reviewer that ends on an API error has nothing worth keeping: run a new one, unless a usage limit holds.
5. **Changes requested** → with a posted review, send the worker "address review `<url>`" and nothing more: it reads the findings from the PR. Without one, send the full findings. Send it to the same worker either way, except that a haiku worker whose round-2 review asks for changes again escalates instead ([Choosing the worker model](#choosing-the-worker-model)); back to 1 on its report, and the next review is round 2 or later, continued as step 4 says. After three rounds, mark the ticket waiting and bring it to the user.
   - **Approved with minors** (an `approve` in any round with non-blocking findings): tell the worker to take or leave the minors, giving the review URL or, without one, the minor findings. The worker decides on them with no extra round. CI alone gates the merge, with no re-review: merge (step 6) after the worker reports and CI is green on that head.
   - **Post-approval risk guard:** if the worker pushes after an approval, compare the worker's own commits since the approved head, cumulatively, ignoring merges of main and rebases that bring in nothing new from the worker (the merge script's sweep makes these, and they must not trigger a re-review). When that cumulative change touches one of the config's **Risk paths** or exceeds its S thresholds (measured against the approved head, so several small pushes can't slip past), send it to the same reviewer as the next round (step 4) before merging. Otherwise the approval stands and CI alone gates the merge (the merge script's `--match-head-commit` still needs the new head, so go back to 1 for CI).
6. **Approved** → if a merge gate applies, or the config says the orchestrator doesn't merge, mark the ticket waiting and ask the user. Otherwise, after one last check of the config's merge rule (every clause, e.g. CI green, every dependency merged, branch up to date, reviewer approved), merge. For "branch up to date", read `behind_by` again now, with the same compare call the gate uses, because another PR may have merged since the gate ran: if it is above 0, run `gh pr update-branch <pr>`, go back to 1 to wait for CI, and don't merge. If `update-branch` fails (a conflict, or a fork branch it can't push to), treat it as a failed check: send the worker the output, or ask the user for a fork. A main-only update needs no re-review. Then merge with `"${CLAUDE_SKILL_DIR}/../../scripts/merge" <pr> --method <the config's method> --worktree <the worker's worktree>` (if the state file's worktree is null, or `git worktree list` no longer shows it, omit `--worktree`: the script then skips removal). It merges with `--match-head-commit`, so a push after the review makes it a refusal (exit 1) rather than merging unreviewed code. It never deletes the default branch, `main`/`master` or a fork's branch. Never run `gh pr merge` yourself: its exit can lie (a 502 or "merge already in progress" while the merge lands). The script re-checks the PR until it is MERGED, and only then deletes the remote branch if it's still there, closes the ticket if the host didn't, removes the worktree (already gone is fine) and appends a `merge` event. Exit 0: merged. Exit 3: not MERGED at the timeout; run it again later and close nothing. Exit 1: refused or closed unmerged; treat the PR as unmerged and say why. Exit 2: it couldn't run; fix the invocation. Exit 4: merged and cleaned up, but the sweep of in-flight branches handed some back; the output has one `HANDBACK PR #<n> <branch>: <path> (<why>)` line per conflicted file. Resume that PR's worker with SendMessage, passing those lines as the conflict list (a worker that is gone gets them in a fresh dispatch's Notes). The sweep has already updated every other conflicting branch itself: merging main in, or rebasing with `--force-with-lease`, per the method.
7. **After the merge:** the script has closed the ticket and removed the worktree; the config's **Orchestrator-owned edits** (none if the config has no such line) are not made now: note each as due. The slot is free. Done when the next pass would see the slot as free.

## Orchestrator-owned edits and shared-file conflicts

- **Batch the edits.** Make no status edit per merge. At the end of a pass that leaves no ticket in flight, make every due edit in one branch and open one PR for them (title `chore: <what>`, no closing reference); its branch is `chore/orchestrator-edits-<date>`, an exception to the config's `<number>-<slug>` rule. It runs the review loop at its tier like a worker's PR (gate, then reviewer), then merges by the config's merge rule. With no edits due, no PR.
- **Conflicts twice.** When a file outside the config's shared files and generated globs comes back in a hand-back (the `HANDBACK` lines of merge exit 4) for the second time, counted per file path from the events log's `replay` events with outcome `handed-back`, in different PRs (two hand-backs in one PR don't count; if the events log is missing, suggest nothing), suggest adding it to **Shared files** with a per-PR rule, in one line. Say it on any repo, but it matters most with no merge history. Change the config only when the user says yes.

## Running out of usage

Agents share the user's plan, so a usage limit usually reaches several workers at once. Claude Code retries transient rate limits and overloads itself; what reaches you is a limit that holds until a reset.

- **On the first usage-limit error in a pass**, dispatch nothing more: no new workers, reviewers or resumes. Mark every affected ticket **paused**, and note the reset time if the error text gives one. Never switch an agent to another model to get around a limit; that's the user's call.
- **Resume a paused ticket** once the limit has lifted: past the reset time, or, when none is known, by trying one resume, which counts as the pass's first usage-limit error if it fails. Continue the same worker with SendMessage ("you were stopped by a usage limit; check `git status` and continue where you stopped"), so it keeps its context. If SendMessage can't reach it (another session, or the agent is gone), treat the ticket as stalled.
- **Resume a stalled ticket** with a new worker on the remote branch. First rescue its old worktree, if one still exists (the state file's, else the `git worktree list` entry for its branch, or for the worker's `agent-<id>` path) and holds work: commit everything in it as `chore(wip): <ticket title>`, push the branch, and `git worktree remove` it (unlock it first if a dead agent left it locked). If any of that work touches a path another ticket holds, don't rescue it; bring it to the user. Then dispatch the new worker; its brief says to continue the existing branch.
- **While a limit holds** and you can still run (another model's limit, or an API error rather than your own plan): schedule a one-shot cron with `CronCreate` at the reset time, in hops of at most 60 minutes. Each hop runs a `status` pass and dispatches nothing before the reset. These cron jobs are session-only and fire only while the REPL is idle.
- **If your own turn hits the limit**, nothing in this skill can run. The README tells the user to turn on Claude Code's `autoContinueAtUsageLimit` for unattended runs, and to run `/orchestrate:run` again if the session has ended; the next pass recovers paused and stalled tickets from the tracker and the state file.

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
   It reads this session's transcripts and the pass log, saves `.claude/orchestrate-profiles/<date>-<session>.json` with an `.html` viewer beside it, and prints a summary. Put the summary, unchanged, at the end of the report, with the path of the `.html`.

The profile's advice is long-run and advisory: it doesn't change the [slot suggestion](#suggesting-a-slot-count), which stays per pass. Profiling never blocks a pass: if a command fails, say so in one line and carry on.

## Escalate to the user

Put these to the user and wait; they are theirs to decide:
- A gate from the config, at start or at merge.
- A ticket handed back as `ready-for-human` or `needs-info`.
- A PR that contradicts an ADR without saying why the ADR is being reopened.
- A third review round.
- A parent whose sub-issues have all closed.
- Anything destructive or outward-facing the config doesn't already authorise.
