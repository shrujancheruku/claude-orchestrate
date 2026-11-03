---
name: run
description: One orchestration pass over the repo's backlog. Review and merge finished PRs, fill free slots with workers, escalate gates.
disable-model-invocation: true
argument-hint: "[status | <ticket> | <parent issue>]"
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

- **building**: no PR yet. A worker from this session is on it, or it **stalled** (no worker this session knows of: dispatch a new one, and its brief says to continue the existing branch).
- **in review**: its PR is open.
- **waiting**: blocked on the user (a gate, a question, a third review round).

The **held paths** at any moment are the files in open PRs' diffs, the touch maps of building tickets, and the config's exclusive holds. A ticket may not change a path another ticket holds.

Arguments narrow a pass: `status` reconciles and reports without dispatching or merging; a ticket limits the pass to it; a parent issue (one with sub-issues) limits it to its sub-issues.

## A pass

1. **Reconcile.** One scout call, within the pass's scope: each claimed open ticket, its PR, and check states; the first takeable tickets in order, a few more than the free slots; `ready-for-human` tickets; merged PRs whose tickets are still open; parents whose sub-issues are all closed. Done when every claimed ticket has a state.
2. **Finish work in review**, ticket by ticket, with the loop below.
3. **Fill free slots.** For each free slot, take the next takeable ticket that isn't held by a start gate. Check **overlap** before dispatch: the scout's touch map for the ticket, set against the held paths. An overlap outside the config's shared files holds the ticket until the other merges; take the next ticket instead. Assign the ticket to the user first, so another session skips it. Write its brief from [brief.md](brief.md), with the touch map and the held paths, and dispatch `orchestrate:worker` in the background, with the config's worker model. Done when every slot is full or no ticket can be taken.
4. **Report and yield.** A table: ticket, state, next step, who it waits on. Below it, the `ready-for-human` tickets and any parent whose sub-issues have all closed; ask the user whether to close each such parent (never close one yourself). Then end your turn: worker completions wake you. CI is external state, so under `/loop` set the wake-up from how long the repo's CI takes.

The backlog is done when nothing in scope is takeable or claimed. Say so, list what's left as `ready-for-human` or `needs-info`, and stop.

## Worker reports

- `pr-opened` → the review loop. If the report lists ADR conflicts, read them before the review and bring any you can't settle from the ADR itself to the user.
- `stopped` → when the config, the ticket or the code settles the question, answer it and continue the same worker with SendMessage, so it keeps its context. When it's the user's decision, hand the ticket back to the tracker:
  - A human-only step → label it `ready-for-human`. An ambiguity the ticket's author has to settle → label it `needs-info`.
  - Remove `ready-for-agent`, so it holds exactly one state role, and unassign it, so the frontier stays truthful.
  - Comment what's needed and the branch the work is on, starting with the line `> *This was generated by AI during triage.*`
  - Put it to the user. The slot is free.

## The review loop

1. **Pre-merge check:** the scout runs its checklist with the held paths (leaving out this ticket's own), the dependencies, the title convention and the shared-file rules. While CI is pending, move on to other tickets.
2. **CI failed** → send the failing step to the worker with SendMessage; back to 1 on its report.
3. **Review** with `orchestrate:reviewer` at the PR's tier and model. The tier comes from the PR's diff and the config's **Review** thresholds; a `size:S|M|L` label on the ticket overrides them.
   - **S** → model `haiku`.
   - **M** → model `sonnet`.
   - **L** → model `sonnet`, then read its risk notes and those files yourself (the risky parts, not the whole diff).
   Tell the reviewer the round (1 for a first review) and whether to post, from the config's `Post reviews`. A `posted: failed` verdict still counts; note the failure in your report.
4. **Changes requested** → send the findings to the same worker; back to 1 on its report. After three rounds, mark the ticket waiting and bring it to the user.
5. **Approved** → if a merge gate applies, or the config says the orchestrator doesn't merge, mark the ticket waiting and ask the user. Otherwise merge with the config's method, after one last check that CI is green and every dependency has merged.
6. **After the merge:** close the ticket if the host didn't; make the status update the config assigns to the orchestrator; remove the worker's worktree (`git worktree remove`); the slot is free. Done when the next pass would see the slot as free.

## Escalate to the user

Put these to the user and wait; they are theirs to decide:
- A gate from the config, at start or at merge.
- A ticket handed back as `ready-for-human` or `needs-info`.
- A PR that contradicts an ADR without saying why the ADR is being reopened.
- A third review round.
- A parent whose sub-issues have all closed.
- Anything destructive or outward-facing the config doesn't already authorise.
