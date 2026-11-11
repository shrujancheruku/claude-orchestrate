"""What state is each claimed ticket in? The one place that decides it (see GLOSSARY.md, Claim).

Stdlib only, Python 3.9+. A library: `scripts/claims` prints it, and any script that needs a claimed ticket's state
imports it with `import claims` (ADR-0003). It never calls `gh`: the tracker comes in as plain dicts. It reads the
state file once and `git worktree list` once.

The state, decided in this order:
  with-lead   open, claimed, labelled `lead`, no open PR
  in-review   an open, non-draft PR on the ticket's branch (a `lead` ticket's too). A draft PR is the worker's
              progress marker, not a review request: it counts as no PR here (ADR 0011), so the ticket is
              building (or stalled, or elsewhere) and `Claim.pr` still names the draft to link it
  done        the ticket's branch has a merged PR, or the ticket is in the state file but not among the open issues
  parked      no open PR, and the ticket's newest `park` event is `parked` (scripts/park-pr closed its PR to let a newer
              PR's fix land first): the branch and the claim are kept, nothing is rescued, redispatched or held for it,
              and it occupies no slot (`slot_use`, #716): the ticket stays taken, the slot is free for the next one
  building    a worker record from this session in the state file, or no record but the worktree's lock is this
              session's own live worker (`worker unknown`: a dispatch the state file couldn't join)
  idle        a building claim whose newest non-duplicate worker-report is an `interim` one, newer than the worker's
              `dispatched` time in its record: the worker ended its turn with a report nobody acts on, and a stopped
              subagent is never re-invoked unless messaged. The lock plays no part (it is always `ours`, ADR 0009). A
              record with no `dispatched` is never idle. `since` is that report's time; `nudged` is the time of the pass
              log's `record --nudged` after that dispatch, kept until a real completion (a later interim does not clear it).
              A pass nudges it once, then hands it back
  waiting     the user must act; it never expires. `reason` and `since` (the event's time) say which kind:
              with no ready PR it replaces building and stalled: the ticket's newest worker-report event is a
              `permission-denied` stop or an `exhausted` transient one (`permission-denied`, `exhausted`), and only a
              newer report ends it. On a ready PR it replaces in-review: the PR's newest `merge` event is `gate-hold`
              (`gate`: scripts/merge refused it under a config Merge gate), else its newest `review` event asked for
              changes in round ROUND_LIMIT-1 or later, so the next review is a third round (`round-3`). On either, the
              ticket's newest `transition` event (scripts/transition) while its label is still on is a stop for the lead
              (`question`: stop-for-lead, `needs-lead`), an ADR conflict (`adr-conflict`: adr-hold, `needs-lead`) or a
              question for the ticket's author (`info`: to-info, `needs-info`); removing the label ends it (#747)
  paused      replaces building and stalled (never a ready PR): the ticket's newest worker-report event is `limit`
              or `error` (the worker ended on a usage-limit or API error). `reason` is that word, `since` its time. A
              pass resumes it; only a newer report ends it. It occupies a slot and holds its touch map like building
  elsewhere   no record, and the worktree on its branch is locked by another live session (ADR 0009)
  stalled     otherwise: no PR, no record, no live lock (dead lock, no lock, no worktree, no branch)
A ticket's worktree is found first from its worker's record (the harness path `.claude/worktrees/agent-<id>`, or the
checkout on `worktree-agent-<id>`), then by the ticket branch. A worker that stopped before Orient's branch step has
only the harness branch: its claim has `branch None` and `pending_branch` set, and its worktree is never "gone".
Annotations, not states, on any open claim: `repeat` lists the worker-report outcomes (`not-a-stop`,
`fix-report`, `missing-report-none`) this ticket's worker has reported twice or more since its dispatch, and `incomplete` counts the
`incomplete` reviewer reports in a row that end the PR's reviews, so the run skill applies a word instead of counting
from its context (#657).
The run log and the pass log are read from the main checkout (runlog.py); a missing log is no events.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path

import common
import github
import config
import runlog
import tracker
from repo import STATE, parse_ts, ticket_of_branch
from repo import unpushed as unpushed_commits  # the one count rescue shares (#546)

LEAD_LABEL = "lead"  # a ticket the lead owns: no worker is dispatched for it


@dataclass
class Claim:
    ticket: int
    state: str
    branch: str | None = None      # the `<ticket>-` branch some worktree has checked out, else the PR's head branch
    worktree: str | None = None    # path of the worktree on that branch
    lock: tuple | None = None      # (state, pid) | ("foreign-live", None, reason) | None; state: ours | foreign-live | dead
    pr: int | None = None          # the open PR's number: the ready one, else the draft
    worker: dict | None = None     # the state-file record: agent id, model, last report, touch map
    changes: tuple | None = None   # (dirty paths, unpushed commits) for a claim with a worktree and no PR
    draft: bool = False            # `pr` is a draft: the ticket is building, not in review
    pending_branch: bool = False   # the worktree is on a `worktree-agent-*` branch: the worker never made the ticket branch
    reason: str | None = None      # waiting: `permission-denied` | `exhausted` | `gate` | `round-3` | `question` | `adr-conflict` | `info`; paused: `limit` | `error`
    since: str | None = None       # waiting, paused and idle: UTC `YYYY-MM-DDTHH:MM:SSZ` of the report
    nudged: str | None = None      # idle only: the same, of the pass's nudge after the dispatch, kept until a real completion
    repeat: tuple = ()             # worker-report outcomes seen twice or more from this ticket's worker (REPEAT_WORDS)
    incomplete: int = 0            # `incomplete` reviewer reports in a row at the end of the open PR's reviews
    resumed: str | None = None     # paused only: UTC time of the `pause-resume` event that ended this pause (same `since`), else None
    escalate: str | None = None    # the model to escalate to: a haiku worker whose PR hit a mechanical trigger (see escalation)


def _sessions(path) -> tuple:
    """(current session id | None, {session id: tickets object}) from the state file. The file holds one entry per
    session and `session` names the one that rebuilt last, the current one; the old `{"session", "tickets"}` shape is
    one session's entry (a file with no id, as older tests write, is the session ""). A missing file has no sessions;
    an unreadable one, or one with no tickets object, is ValueError (could not run)."""
    path = Path(path)
    # No lock here: rebuild-state writes a temp file and renames it over this one, so a read sees the old file or the new.
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None, {}
    except (OSError, ValueError) as e:
        raise ValueError(f"unreadable state file {path}: {e}") from None
    if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
        sessions = {k: v.get("tickets") for k, v in data["sessions"].items() if isinstance(v, dict)}
        current = data.get("session")
        if all(isinstance(t, dict) for t in sessions.values()):
            return (current if isinstance(current, str) else None), sessions
    elif isinstance(data, dict) and isinstance(data.get("tickets"), dict):
        sid = data.get("session")
        return (sid if isinstance(sid, str) else ""), {(sid if isinstance(sid, str) else ""): data["tickets"]}
    raise ValueError(f"state file {path} has no tickets object")


def add_session_arg(ap) -> None:
    """The `--session <id>` option every script that reads the state file or writes events takes (#532)."""
    ap.add_argument("--session", default=None, metavar="ID",
                    help="this session's id: its state-file entry is the current one, not the last rebuilder's")


def use_session(session) -> None:
    """Make `session` (a `--session` value) this process's current session: read_state, read_other_state, claims and
    the events runlog and github write all follow $ORCHESTRATE_SESSION. Without one nothing changes."""
    if session:
        os.environ[runlog.SESSION_ENV] = session


def _chosen(session, current):
    return session or os.environ.get(runlog.SESSION_ENV) or current


def read_state(path, session=None) -> dict:
    """{ticket as str: worker record} of the current session; a missing file is no workers. `session` (the caller's own
    id, from `--session`) names the current session; without it the one that rebuilt last is (#532)."""
    current, sessions = _sessions(path)
    return sessions.get(_chosen(session, current), {})


def read_other_state(path, session=None) -> dict:
    """{ticket as str: worker record} under any other session: candidates for Elsewhere, never for Building."""
    current, sessions = _sessions(path)
    current = _chosen(session, current)
    out = {}
    for sid, tickets in sessions.items():
        if sid != current:
            out.update(tickets)
    return out


def find_worktree(ticket, worktrees, record=None):
    """The worktree that is `ticket`'s: the one its worker's record names (by path, or by the agent id in the harness
    path `.claude/worktrees/agent-<id>` or on the branch `worktree-agent-<id>`), else the one on a `<ticket>-` branch.
    None when no listed worktree is. Pure: `worktrees` is `common.worktrees`'s list, `record` a state-file record."""
    record = record if isinstance(record, dict) else {}
    named = str(record.get("worktree") or "")
    agent = str(record.get("agent_id") or "")
    agent = agent[len("agent-"):] if agent.startswith("agent-") else agent
    for wt in worktrees:
        if named and wt["path"] == named:
            return wt
        if agent and (wt["branch"] == f"worktree-agent-{agent}" or Path(wt["path"]).name == f"agent-{agent}"):
            return wt
    return next((wt for wt in worktrees if ticket_of_branch(wt["branch"]) == ticket), None)


def is_harness_branch(branch) -> bool:
    return str(branch or "").startswith("worktree-agent-")


def _git(path, *args) -> str:
    r = common.run("git", *args, cwd=path)
    if r.code != 0:
        raise ValueError(f"git {' '.join(args[:2])} failed in {path}: {common.one_line(r.stderr) or 'no reason given'}")
    return r.stdout


def worktree_state(path, branch) -> tuple:
    """(dirty paths, unpushed commits) in the worktree at `path`. A branch never pushed counts every commit that no
    remote has. A failed git call is ValueError (could not run): an empty answer would read as clean."""
    dirty = len([ln for ln in _git(path, "status", "--porcelain").splitlines() if ln.strip()])
    count = unpushed_commits(path, branch)
    if count is None:
        raise ValueError(f"git could not count the unpushed commits in {path}")
    return dirty, count


def _lock(wt) -> tuple | None:
    if wt is None or wt["locked"] is None:
        return None
    state, pid = common.worker_liveness(wt["locked"])
    if state == "foreign-live" and pid is None:
        return (state, None, wt["locked"] or "no reason given")
    return (state, pid)


def fmt_ts(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


CLAIM_EVENT_KINDS = (runlog.WORKER_REPORT, runlog.CI, runlog.REVIEW, runlog.PARK, runlog.MERGE, runlog.TRANSITION, runlog.PAUSE_RESUME)  # the only kinds a claim's state reads
ROUND_LIMIT = 3  # a review of this round is the user's to allow: a PR whose round ROUND_LIMIT-1 asked for changes waits
REPEAT_WORDS = ("not-a-stop", "fix-report", "missing-report-none")  # worker-report outcomes whose second occurrence the run skill acts on


def _repeats(events, ticket, since) -> tuple:
    """The REPEAT_WORDS that `ticket`'s worker-report events (a duplicate passed over) carry twice or more after `since`
    (the worker's dispatch time; None counts the whole log)."""
    seen = {}
    for e in events:
        if e.get("kind") != runlog.WORKER_REPORT or str(e.get("ref")) != str(ticket) or e.get("outcome") == "duplicate":
            continue
        ts = parse_ts(e.get("ts"))
        if since and (not ts or ts <= since):
            continue
        word = e.get("outcome")
        if word == "missing-report" and e.get("pr") is None:
            word = "missing-report-none"
        seen[word] = seen.get(word, 0) + 1
    return tuple(w for w in REPEAT_WORDS if seen.get(w, 0) >= 2)


def _pr_wait(events, pr) -> tuple:
    """(reason, event) when the open PR `pr` waits on the user, else (None, None): `gate` when its newest `merge` event
    is a `gate-hold`, else `round-3` when its newest `review` event asked for changes in round ROUND_LIMIT-1 or later."""
    merge = [e for e in events if e.get("kind") == runlog.MERGE and str(e.get("ref")) == str(pr)]
    if merge and merge[-1].get("outcome") == "gate-hold":
        return "gate", merge[-1]
    review = [e for e in events if e.get("kind") == runlog.REVIEW and str(e.get("ref")) == str(pr)]
    last = review[-1] if review else None
    if last and last.get("outcome") == "request-changes" and isinstance(last.get("round"), int) and last["round"] >= ROUND_LIMIT - 1:
        return "round-3", last
    return None, None


# transition verb -> (reason, the label that keeps it waiting)
TRANSITION_WAITS = {"stop-for-lead": ("question", tracker.NEEDS_LEAD), "adr-hold": ("adr-conflict", tracker.NEEDS_LEAD),
                    "to-info": ("info", tracker.NEEDS_INFO)}


def _transition_wait(events, ticket, labels) -> tuple:
    """(reason, event) when `ticket`'s newest `transition` event is a stop for the lead, an ADR hold or a to-info whose
    label is still on the ticket, else (None, None). The label is the evidence the question is unanswered (#747)."""
    last = None
    for e in events:
        if e.get("kind") == runlog.TRANSITION and str(e.get("ref")) == str(ticket):
            last = e
    reason, label = TRANSITION_WAITS.get((last or {}).get("outcome"), (None, None))
    return (reason, last) if reason and label in labels else (None, None)


def _incomplete(events, pr) -> int:
    """How many `incomplete` reviewer reports end the PR's `review` events, back to back."""
    n = 0
    for e in reversed([e for e in events if e.get("kind") == runlog.REVIEW and str(e.get("ref")) == str(pr)]):
        if e.get("outcome") != "incomplete":
            break
        n += 1
    return n


def greedy_cap(repo) -> int | None:
    """The greedy cap in force: the `cap` of the newest `greedy` event when it is `set` (scripts/launch writes one per
    fresh start, `off` for a start that is not greedy), else None. It survives a compaction, which the report line alone
    did not (#657)."""
    last = None
    for e in runlog.read_kinds(repo, [runlog.GREEDY]):
        last = e
    cap = (last or {}).get("cap")
    return cap if last and last.get("outcome") == "set" and isinstance(cap, int) and not isinstance(cap, bool) else None


def _resumed(events, ticket, since) -> str | None:
    """The time of the newest `pause-resume` event for `ticket` that ended the pause starting at `since`, else None (#788)."""
    found = None
    for e in events:
        if e.get("kind") == runlog.PAUSE_RESUME and str(e.get("ref")) == str(ticket) and e.get("since") == since:
            found = e.get("ts")
    return found


def resume(repo, claim, session=None) -> tuple:
    """(True, "") after writing the `pause-resume` event for a paused `claim` not yet resumed, else (False, why).

    The run log, not the skill's memory, says a resume went out: `claims` keeps saying `paused` until the worker's newer
    report, so a second call for the same `since` is refused (`already resumed <ts>`); a newer pause has a new `since`."""
    if claim.state != "paused":
        return False, f"not paused ({claim.state})"
    if claim.resumed:
        return False, f"already resumed {claim.resumed}"
    runlog.append_event(repo, runlog.PAUSE_RESUME, "sent", ref=str(claim.ticket), session=session, since=claim.since)
    return True, ""


def _parked(events) -> set:
    """The tickets whose newest `park` event is `parked` (scripts/park-pr), from the run-log events."""
    last = {}
    for e in events:
        if e.get("kind") == runlog.PARK and e.get("ticket") is not None:
            last[str(e["ticket"])] = e.get("outcome")
    return {int(t) for t, outcome in last.items() if outcome == "parked" and t.isdigit()}


def _reports(events) -> dict:
    """{ticket as text: its newest worker-report event}, a duplicate passed over, from the run-log events."""
    out = {}
    for e in events:
        if e.get("kind") == runlog.WORKER_REPORT and e.get("outcome") != "duplicate":
            out[str(e.get("ref"))] = e
    return out


class _LazyConfig:
    """The config, parsed on the first attribute read: only a haiku worker's escalation reads it, and most calls
    have none."""

    def __init__(self, repo):
        self._repo, self._cfg = repo, None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if self._cfg is None:
            self._cfg = config.for_checkout(self._repo)  # the main checkout's, never a PR worktree's (#546)
        return getattr(self._cfg, name)


def _nudges(repo) -> dict:
    """{ticket: time of its newest nudge} from the pass log (`profile.py record --nudged <n>`), any session's."""
    out = {}
    for p in common.read_jsonl(runlog.log_path(repo, runlog.PASS_LOG)):
        ts = parse_ts(p.get("ts"))
        nudged = p.get("nudged")
        for n in nudged if isinstance(nudged, list) else ():
            if ts and isinstance(n, int) and (n not in out or ts > out[n]):
                out[n] = ts
    return out


def escalation(events, pr, worker, cfg) -> str | None:
    """The model a haiku worker escalates to, or None. Two mechanical triggers, both on the ticket's PR: a second CI
    failure (two `ci fail` events, on two heads when they name one), or a `review` event of round 2 or later that asked
    for changes. An unanswerable stop is a judgement and stays the orchestrator's. A worker whose model is not haiku, or
    unknown, is never escalated (a sonnet worker once; a missing model counts as not haiku)."""
    if pr is None or "haiku" not in str((worker or {}).get("model") or "").lower():
        return None
    fails = {(e.get("head") or i) for i, e in enumerate(events)
             if e.get("kind") == runlog.CI and e.get("outcome") == runlog.FAIL and str(e.get("ref")) == str(pr)}
    changes = any(e.get("kind") == runlog.REVIEW and e.get("outcome") == "request-changes" and str(e.get("ref")) == str(pr)
                  and isinstance(e.get("round"), int) and e["round"] >= 2 for e in events)
    return (cfg.worker_model or config.DEFAULT_WORKER_MODEL) if len(fails) >= 2 or changes else None


NO_SLOT = ("done", "with-lead", "parked")  # states that occupy no slot: nothing is being built (a parked claim keeps only the ticket, #716)


def slot_use(found) -> tuple:
    """(claims occupying a slot, parked claims) of `claims()`'s result: the one count `claims --slots`, `frontier --slots`,
    the lead-brief watch and the profile all use, so a parked claim is listed everywhere but never counted against the cap."""
    return (sum(1 for c in found.values() if c.state not in NO_SLOT),
            sum(1 for c in found.values() if c.state == "parked"))


def slots_line(found, cap, greedy=None) -> str:
    """`slots: <building>/<cap> building, <k> parked` (the cap is `?` when the config has no Slots line). Under greedy
    (`greedy_cap`) the cap is the greedy one and the line ends ` greedy`."""
    building, parked = slot_use(found)
    if greedy is not None:
        return f"slots: {building}/{greedy} building, {parked} parked greedy"
    return f"slots: {building}/{cap if cap is not None else '?'} building, {parked} parked"


def claims(repo, state_file, issues, prs, merged, worktrees=None, changes=worktree_state, session=None) -> dict:
    """{ticket: Claim} for each claimed open issue and each state-file ticket that is no longer open (done).

    issues: [{"number", "claimed", "labels"}] open issues. prs: [{"number", "headRefName", ...}] open PRs.
    merged: [{"headRefName", ...}] merged PRs. worktrees: `git worktree list`'s (default: asked of `repo`); changes:
    (path, branch) -> (dirty, unpushed), swappable so a pure test needs no repository. session: this caller's own id
    (`--session`); without it the session that rebuilt last is the current one."""
    state = read_state(state_file, session)
    other = read_other_state(state_file, session)
    # A worktree git still lists but whose directory is gone (prunable) is gone: git commands in it can't run.
    wts = [w for w in common.worktrees(repo) if Path(w["path"]).is_dir()] if worktrees is None else worktrees
    open_issues = {int(i["number"]): i for i in issues}
    open_pr = {}
    for pr in github.own_prs(prs):  # a fork PR is never a ticket's PR (ADR-0015)
        n = ticket_of_branch((pr or {}).get("headRefName"))
        # A ready PR wins over a draft on the same ticket.
        if n is not None and (n not in open_pr or (open_pr[n].get("isDraft") and not pr.get("isDraft"))):
            open_pr[n] = pr
    merged_branch = {}
    for pr in merged:
        n = ticket_of_branch((pr or {}).get("headRefName"))
        if n is not None:
            merged_branch.setdefault(n, pr["headRefName"])
    records = {int(k): v for k, v in state.items() if str(k).isdigit()}
    foreign = {int(k): v for k, v in other.items() if str(k).isdigit() and int(k) not in records}
    tickets = sorted({n for n, i in open_issues.items() if i.get("claimed")} | (set(records) - set(open_issues)))
    # One pass over the events log, parsing only the kinds a claim reads (it grows with every `gh_call` line, #683).
    events, nudges = runlog.read_kinds(repo, CLAIM_EVENT_KINDS), _nudges(repo)
    reports, cfg, parked = _reports(events), _LazyConfig(repo), _parked(events)
    out = {}
    for n in tickets:
        record = records.get(n) if isinstance(records.get(n), dict) else None
        # Another session's record finds the worktree (its lock may be live: Elsewhere) but is never this session's worker.
        wt, shown = find_worktree(n, wts, record or foreign.get(n)), open_pr.get(n)
        draft = bool(shown and shown.get("isDraft"))
        pr = None if draft else shown  # the PR that decides the state: a draft decides nothing
        pending = bool(wt) and ticket_of_branch(wt["branch"]) is None and is_harness_branch(wt["branch"])
        branch = wt["branch"] if wt and not pending else (shown["headRefName"] if shown else merged_branch.get(n))
        claim = Claim(n, "", branch, wt["path"] if wt else None, _lock(wt), shown["number"] if shown else None, record,
                      draft=draft, pending_branch=pending and branch is None)
        labels = set((open_issues.get(n) or {}).get("labels") or ())
        if n in open_issues and LEAD_LABEL in labels and pr is None:
            claim.state = "with-lead"
        elif pr is not None:
            claim.state = "in-review"
        elif n in merged_branch or n not in open_issues:
            claim.state = "done"
        elif n in parked and shown is None:  # a `park` event over a still-open (draft) PR is not trusted (#755)
            claim.state = "parked"
        elif record is not None:
            claim.state = "building"
        elif claim.lock and claim.lock[0] == "ours":
            claim.state = "building"  # this session's own worker with no record: nothing may rescue over it
        elif claim.lock and claim.lock[0] == "foreign-live":
            claim.state = "elsewhere"
        else:
            claim.state = "stalled"
        report = reports.get(str(n))
        reported = parse_ts((report or {}).get("ts"))
        if pr is None and claim.state in ("building", "stalled") and report and (
                report.get("stop_reason") == "permission-denied" or report.get("exhausted") is True):
            # The developer must act: never rescued, resumed or redispatched; only a newer report ends it.
            claim.reason = "permission-denied" if report.get("stop_reason") == "permission-denied" else "exhausted"
            claim.since = fmt_ts(reported) if reported else None
            claim.state = "waiting"
        elif pr is None and claim.state in ("building", "stalled") and report and report.get("outcome") in ("limit", "error"):
            # The worker hit a usage-limit or API error: a pass resumes it; only a newer report ends the pause.
            claim.reason, claim.state = report["outcome"], "paused"
            claim.since = fmt_ts(reported) if reported else None
            claim.resumed = _resumed(events, n, claim.since)
        elif claim.state == "in-review" and _pr_wait(events, pr["number"])[0]:
            claim.reason, event = _pr_wait(events, pr["number"])
            at = parse_ts(event.get("ts"))
            claim.state, claim.since = "waiting", fmt_ts(at) if at else None
        elif claim.state in ("building", "stalled", "in-review") and _transition_wait(events, n, labels)[0]:
            claim.reason, event = _transition_wait(events, n, labels)
            at = parse_ts(event.get("ts"))
            claim.state, claim.since = "waiting", fmt_ts(at) if at else None
        elif claim.state == "building" and record is not None and report and report.get("outcome") == "interim":
            # The worker ended its turn without handing back. Only an interim newer than this worker's own dispatch
            # counts, so a redispatch never reads an older worker's interim as its own.
            dispatched = parse_ts(record.get("dispatched"))
            if dispatched and reported and reported > dispatched:
                claim.state, claim.since = "idle", fmt_ts(reported)
                # A nudge stays on until a real completion (that ends idle above), so a second interim after a nudge
                # reads `idle nudged` and the pass hands it back (#690). Only this dispatch's own nudge counts.
                if n in nudges and nudges[n] > dispatched:
                    claim.nudged = fmt_ts(nudges[n])
        if wt and pr is None and claim.state in ("building", "idle", "stalled", "waiting", "paused", "elsewhere"):
            claim.changes = changes(wt["path"], wt["branch"])
        if claim.state in ("building", "in-review", "idle") or (claim.state == "waiting" and claim.pr is not None):
            # A round-2 request for changes is a `waiting round-3` claim too, and a haiku worker still escalates on it.
            claim.escalate = escalation(events, claim.pr, record, cfg)
        if claim.state not in ("done", "parked"):
            claim.repeat = _repeats(events, n, parse_ts((record or {}).get("dispatched")))
            claim.incomplete = _incomplete(events, shown["number"]) if pr is not None else 0
        out[n] = claim
    return out


def worker_busy(repo, branch, state_file=None, session=None) -> str | None:
    """Why a worker may still be fixing on `branch`, else None (#757).

    `merge` and its sweep push, or ask the host to merge, the default branch into a PR's branch; a worker with commits of
    its own then finds its push rejected non-fast-forward. Only a ready PR reaches them (a draft is skipped), and a ready
    PR's claim reads in-review whatever the worker does, so the signal is the ticket's worktree: uncommitted paths or
    unpushed commits mean a worker still fixing. A branch with no ticket number, and a ticket with no worktree, has no
    worker to wait for. Only the host-free parts of the claim are read (the state file, `git worktree list`, the run
    log), so no host call is made; a worktree whose git state can't be read counts as busy (fail closed: the update
    waits for the next call)."""
    ticket = ticket_of_branch(branch)
    if ticket is None:
        return None
    repo = Path(repo)
    prs = [{"number": 0, "headRefName": branch, "isDraft": False}]
    found = claims(repo, state_file or repo / STATE, [{"number": ticket, "claimed": True, "labels": []}], prs, [],
                   session=session, changes=lambda path, br: (0, 0))
    claim = found.get(ticket)
    if claim is None:
        return None
    if claim.worktree and claim.branch == branch:
        try:
            dirty, unpushed = worktree_state(claim.worktree, branch)
        except ValueError:
            return "its worktree could not be read"
        if dirty or unpushed:
            return f"its worktree holds {dirty} uncommitted path(s) and {unpushed} unpushed commit(s)"
    return None
