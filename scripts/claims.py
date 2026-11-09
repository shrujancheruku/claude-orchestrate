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
  elsewhere   no record, and the worktree on its branch is locked by another live session (ADR 0009)
  stalled     otherwise: no PR, no record, no live lock (dead lock, no lock, no worktree, no branch)
A ticket's worktree is found first from its worker's record (the harness path `.claude/worktrees/agent-<id>`, or the
checkout on `worktree-agent-<id>`), then by the ticket branch. A worker that stopped before Orient's branch step has
only the harness branch: its claim has `branch None` and `pending_branch` set, and its worktree is never "gone".
`paused` and `waiting` are not here: they are the orchestrator's own overlay.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import common
from repo import ticket_of_branch

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


def read_state(path, session=None) -> dict:
    """{ticket as str: worker record} of the current session; a missing file is no workers. `session` (the caller's own
    id, from `--session`) names the current session; without it the one that rebuilt last is (#532)."""
    current, sessions = _sessions(path)
    return sessions.get(session if session else current, {})


def read_other_state(path, session=None) -> dict:
    """{ticket as str: worker record} under any other session: candidates for Elsewhere, never for Building."""
    current, sessions = _sessions(path)
    current = session if session else current
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
    for pr in prs:
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
        if wt and pr is None and claim.state in ("building", "stalled", "elsewhere"):
            claim.changes = changes(wt["path"], wt["branch"])
        out[n] = claim
    return out
