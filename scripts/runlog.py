"""The run log: the events log and the pass log, kept in the main checkout.

Stdlib only, Python 3.9+. A library: a CLI imports it with `import runlog`, and profile.py
appends its own directory to sys.path to do the same (ADR-0003).

Every writer (gate, merge, watch-ci, `profile.py record`) and the reader (profile.py) go through
this module, so both logs land in, and are read from, the one main checkout that `main_checkout`
resolves, whichever subdirectory or linked worktree the caller stands in.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import common

PROFILES = Path(".claude") / "orchestrate-profiles"
EVENTS_LOG = "events.jsonl"
PASS_LOG = "passes.jsonl"

# Event kinds and the outcomes each may carry. scripts/replay.py owns the replay names
# (tests/test_runlog.py checks they agree); gate, watch-ci and merge write the others.
GATE, CI, MERGE, REPLAY = "gate", "ci", "merge", "replay"
PASS, FAIL, UNKNOWN, MERGED, TIMEOUT, CLOSED = "pass", "fail", "unknown", "merged", "timeout", "closed"
OUTCOMES = {
    GATE: (PASS, FAIL, UNKNOWN),
    CI: (PASS, FAIL),
    MERGE: (MERGED, TIMEOUT, CLOSED),
    REPLAY: ("line-replay", "word-merge", "regenerated", "handed-back"),
}
EVENT_KINDS = tuple(OUTCOMES)
HANDED_BACK = "handed-back"


def main_checkout(repo) -> Path:
    """The main checkout that owns the run log for `repo`, which may be a subdirectory or a linked worktree.

    A linked worktree shares the main checkout's common dir, so its parent is the one root every
    worktree agrees on. Outside a checkout (or with the VCS tool missing) `repo` itself is the root.

    Known limits: a bare repository with linked worktrees has a common dir not named `.git`, so each
    worktree is its own root there; a `repo` that no longer exists (a worktree removed before the
    profile runs) resolves to that dead path, so its logs are not found.
    """
    repo = Path(repo)
    code, out, _ = common.run("git", "rev-parse", "--git-common-dir", cwd=repo)
    if code != 0 or not out.strip():
        return repo
    common_dir = Path(out.strip())
    if not common_dir.is_absolute():
        common_dir = repo / common_dir
    common_dir = common_dir.resolve()
    if common_dir.name == ".git":
        return common_dir.parent
    # A submodule or a GIT_DIR checkout: the common dir isn't `.git`, so use this checkout's own toplevel.
    code, out, _ = common.run("git", "rev-parse", "--show-toplevel", cwd=repo)
    return Path(out.strip()) if code == 0 and out.strip() else repo


def log_path(repo, name: str) -> Path:
    """Where the log called `name` (EVENTS_LOG or PASS_LOG) lives for `repo`."""
    return main_checkout(repo) / PROFILES / name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(repo, name: str, record: dict) -> None:
    path = log_path(repo, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    # One write of one line: small appends stay whole when scripts run in parallel.
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def append_event(repo, kind: str, outcome: str, ref=None, session: str | None = None, **extra) -> dict:
    """Append one event to the events log.

    Fields: ts (UTC ISO), session (default $CLAUDE_SESSION_ID), kind, ref (PR or ticket), outcome,
    plus any extra keys. An unknown kind, or an outcome the kind doesn't have, raises ValueError.
    """
    if kind not in OUTCOMES:
        raise ValueError(f"unknown event kind {kind!r}; expected one of {EVENT_KINDS}")
    if outcome not in OUTCOMES[kind]:
        raise ValueError(f"unknown outcome {outcome!r} for {kind!r} events; expected one of {OUTCOMES[kind]}")
    event = {
        "ts": _now(),
        "session": session if session is not None else os.environ.get("CLAUDE_SESSION_ID"),
        "kind": kind,
        "ref": ref,
        "outcome": outcome,
    }
    event.update(extra)
    _append(repo, EVENTS_LOG, event)
    return event


def read_events(repo, session: str, start: datetime, end: datetime) -> list | None:
    """This session's events, or None when the log doesn't exist or holds none of this session's
    (no scripts ran, or an older plugin).

    An event with no session (a script run where the session ID wasn't in its environment) counts
    when it falls within [start, end]."""
    if not repo:
        return None
    path = log_path(repo, EVENTS_LOG)
    if not path.exists():
        return None
    out = []
    for e in common.read_jsonl(path):
        ts = _parse_ts(e.get("ts"))
        if e.get("session") == session or (not e.get("session") and ts and start <= ts <= end):
            out.append(e)
    # A log with nothing for this session is the same as no log: an older plugin's session.
    return out or None


def append_pass(repo, session: str, entry: dict) -> dict:
    """Append one pass (a dict from the orchestrator) to the pass log, stamped with ts and session."""
    entry = {"ts": _now(), "session": session, **entry}
    _append(repo, PASS_LOG, entry)
    return entry


def read_passes(repo, session: str) -> list:
    """This session's passes, oldest first."""
    if not repo:
        return []
    return [p for p in common.read_jsonl(log_path(repo, PASS_LOG)) if p.get("session") == session]


def _parse_ts(value):
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
