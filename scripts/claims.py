"""What state is each claimed ticket in? The one place that decides it (see GLOSSARY.md, Claim).

Stdlib only, Python 3.9+. A library: `scripts/claims` prints it, and any script that needs a claimed ticket's state
imports it with `import claims` (ADR-0003). It never calls `gh`: the tracker comes in as plain dicts. It reads the
state file once and `git worktree list` once.

The state, decided in this order:
  with-lead   open, claimed, labelled `lead`, no open PR
  in-review   an open PR on the ticket's branch (a `lead` ticket's too)
  done        the ticket's branch has a merged PR, or the ticket is in the state file but not among the open issues
  building    a worker record from this session in the state file
  elsewhere   no record, and the worktree on its branch is locked by a live session (ADR 0009)
  stalled     otherwise: no PR, no record, no live lock (dead lock, no lock, no worktree, no branch)
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
    lock: tuple | None = None      # ("live", pid) | ("live", None, reason) | ("dead", pid) | None
    pr: int | None = None          # the open PR's number
    worker: dict | None = None     # the state-file record: agent id, model, last report, touch map
    changes: tuple | None = None   # (dirty paths, unpushed commits) for a claim with a worktree and no PR


def read_state(path) -> dict:
    """{ticket as str: worker record}: a missing file is no workers; an unreadable one, or one with no `tickets`
    object, is ValueError (could not run)."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise ValueError(f"unreadable state file {path}: {e}") from None
    tickets = data.get("tickets") if isinstance(data, dict) else None
    if not isinstance(tickets, dict):
        raise ValueError(f"state file {path} has no tickets object")
    return tickets


def _git(path, *args) -> str:
    r = common.run("git", *args, cwd=path)
    if r.code != 0:
        raise ValueError(f"git {' '.join(args[:2])} failed in {path}: {common.one_line(r.stderr) or 'no reason given'}")
    return r.stdout


def worktree_changes(path, branch) -> tuple:
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
    state, pid = common.lock_state(wt["locked"])
    if state == "live" and pid is None:
        return ("live", None, wt["locked"] or "no reason given")
    return (state, pid)


def claims(repo, state_file, issues, prs, merged, worktrees=None, changes=worktree_changes) -> dict:
    """{ticket: Claim} for each claimed open issue and each state-file ticket that is no longer open (done).

    issues: [{"number", "claimed", "labels"}] open issues. prs: [{"number", "headRefName", ...}] open PRs.
    merged: [{"headRefName", ...}] merged PRs. worktrees: `git worktree list`'s (default: asked of `repo`); changes:
    (path, branch) -> (dirty, unpushed), swappable so a pure test needs no repository."""
    state = read_state(state_file)
    wts = common.worktrees(repo) if worktrees is None else worktrees
    open_issues = {int(i["number"]): i for i in issues}
    open_pr = {}
    for pr in prs:
        n = ticket_of_branch((pr or {}).get("headRefName"))
        if n is not None:
            open_pr.setdefault(n, pr)
    merged_branch = {}
    for pr in merged:
        n = ticket_of_branch((pr or {}).get("headRefName"))
        if n is not None:
            merged_branch.setdefault(n, pr["headRefName"])
    on_branch = {}
    for wt in wts:
        n = ticket_of_branch(wt["branch"])
        if n is not None:
            on_branch.setdefault(n, wt)
    records = {int(k): v for k, v in state.items() if str(k).isdigit()}
    tickets = sorted({n for n, i in open_issues.items() if i.get("claimed")} | (set(records) - set(open_issues)))
    out = {}
    for n in tickets:
        wt, pr = on_branch.get(n), open_pr.get(n)
        record = records.get(n) if isinstance(records.get(n), dict) else None
        branch = wt["branch"] if wt else (pr["headRefName"] if pr else merged_branch.get(n))
        claim = Claim(n, "", branch, wt["path"] if wt else None, _lock(wt), pr["number"] if pr else None, record)
        labels = set((open_issues.get(n) or {}).get("labels") or ())
        if n in open_issues and LEAD_LABEL in labels and pr is None:
            claim.state = "with-lead"
        elif pr is not None:
            claim.state = "in-review"
        elif n in merged_branch or n not in open_issues:
            claim.state = "done"
        elif record is not None:
            claim.state = "building"
        elif claim.lock and claim.lock[0] == "live":
            claim.state = "elsewhere"
        else:
            claim.state = "stalled"
        if wt and pr is None and claim.state in ("building", "stalled", "elsewhere"):
            claim.changes = changes(wt["path"], wt["branch"])
        out[n] = claim
    return out
