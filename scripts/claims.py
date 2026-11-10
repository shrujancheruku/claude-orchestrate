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
  building    a worker record from this session in the state file, or no record but the worktree's lock is this
              session's own live worker (`worker unknown`: a dispatch the state file couldn't join)
  idle        a building claim whose worker is not live (the lock is not `ours <live pid>`) and has made no
              worker-report since its last commit. `since` is that commit's time; `nudged` is the time of the pass log's
              `record --nudged` for it, when that is not older than `since`. A pass nudges it once, then hands it back
  waiting     replaces building and stalled (never a ready PR): the ticket's newest worker-report event is a
              `permission-denied` stop or an `exhausted` transient one. The developer must act; it never expires, and
              only a newer report ends it. `reason` is `permission-denied` or `exhausted`, `since` the report's time
  elsewhere   no record, and the worktree on its branch is locked by another live session (ADR 0009)
  stalled     otherwise: no PR, no record, no live lock (dead lock, no lock, no worktree, no branch)
A ticket's worktree is found first from its worker's record (the harness path `.claude/worktrees/agent-<id>`, or the
checkout on `worktree-agent-<id>`), then by the ticket branch. A worker that stopped before Orient's branch step has
only the harness branch: its claim has `branch None` and `pending_branch` set, and its worktree is never "gone".
`paused` is not here: it is the orchestrator's own overlay (as are a gate, a question and a third review round).
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
from repo import parse_ts, ticket_of_branch

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
    reason: str | None = None      # waiting only: `permission-denied` | `exhausted`
    since: str | None = None       # waiting and idle: UTC `YYYY-MM-DDTHH:MM:SSZ` of the report, or of the last commit
    nudged: str | None = None      # idle only: the same, of the pass's nudge, when it is not older than `since`
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
    if common.run("git", "rev-parse", "--verify", "-q", f"origin/{branch}", cwd=path).code == 0:
        unpushed = len(_git(path, "log", "--format=%H", f"origin/{branch}..").split())
    else:
        unpushed = len(_git(path, "rev-list", "HEAD", "--not", "--remotes").split())
    return dirty, unpushed


def _lock(wt) -> tuple | None:
    if wt is None or wt["locked"] is None:
        return None
    state, pid = common.worker_liveness(wt["locked"])
    if state == "foreign-live" and pid is None:
        return (state, None, wt["locked"] or "no reason given")
    return (state, pid)


def fmt_ts(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def last_commit_time(path):
    """The committer time of HEAD in the worktree at `path`, or None when git can't say (no commits, no directory)."""
    r = common.run("git", "log", "-1", "--format=%cI", cwd=path)
    return parse_ts(r.stdout.strip()) if r.code == 0 else None


def _reports(repo) -> dict:
    """{ticket as text: its newest worker-report event}, a duplicate passed over, from the run log."""
    out = {}
    for e in common.read_jsonl(runlog.log_path(repo, runlog.EVENTS_LOG)):
        if e.get("kind") == runlog.WORKER_REPORT and e.get("outcome") != "duplicate":
            out[str(e.get("ref"))] = e
    return out


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


def claims(repo, state_file, issues, prs, merged, worktrees=None, changes=worktree_state, session=None,
           commit_time=last_commit_time) -> dict:
    """{ticket: Claim} for each claimed open issue and each state-file ticket that is no longer open (done).

    issues: [{"number", "claimed", "labels"}] open issues. prs: [{"number", "headRefName", ...}] open PRs.
    merged: [{"headRefName", ...}] merged PRs. worktrees: `git worktree list`'s (default: asked of `repo`); changes:
    (path, branch) -> (dirty, unpushed), swappable so a pure test needs no repository. session: this caller's own id
    (`--session`); without it the session that rebuilt last is the current one. commit_time: path -> the HEAD commit's
    datetime | None, swappable like `changes`; a caller whose answer is always None never sees an idle claim."""
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
    reports, nudges = _reports(repo), _nudges(repo)
    events, cfg = list(common.read_jsonl(runlog.log_path(repo, runlog.EVENTS_LOG))), config.Config.load(repo)
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
        elif claim.state == "building" and wt and not (claim.lock and claim.lock[0] == "ours"):
            committed = commit_time(wt["path"])
            if committed and not (reported and reported > committed):
                claim.state, claim.since = "idle", fmt_ts(committed)
                if n in nudges and nudges[n] >= committed:
                    claim.nudged = fmt_ts(nudges[n])
        if wt and pr is None and claim.state in ("building", "idle", "stalled", "waiting", "elsewhere"):
            claim.changes = changes(wt["path"], wt["branch"])
        if claim.state in ("building", "in-review", "idle"):
            claim.escalate = escalation(events, claim.pr, record, cfg)
        out[n] = claim
    return out
