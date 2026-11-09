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
from repo import STATE, main_checkout, parse_ts

PROFILES = Path(".claude") / "orchestrate-profiles"
EVENTS_LOG = "events.jsonl"
PASS_LOG = "passes.jsonl"

# Event kinds and the outcomes each may carry. scripts/replay.py owns the replay names
# (tests/test_runlog.py checks they agree); gate, watch-ci and merge write the others.
GATE, CI, MERGE, REPLAY, WORKER_REPORT, FRONTIER = "gate", "ci", "merge", "replay", "worker-report", "frontier"
THREADS = "threads"
GH_CALL = "gh_call"  # scripts/github.py: one per `gh` attempt (ok|failed|ratelimited), or "wait" for a sleep
PASS, FAIL, UNKNOWN, MERGED, TIMEOUT, CLOSED = "pass", "fail", "unknown", "merged", "timeout", "closed"
OUTCOMES = {
    GATE: (PASS, FAIL, UNKNOWN),
    CI: (PASS, FAIL),
    MERGE: (MERGED, TIMEOUT, CLOSED),
    REPLAY: ("line-replay", "word-merge", "regenerated", "handed-back"),
    # scripts/worker-report: the first word of its verdict line.
    WORKER_REPORT: ("valid", "interim", "not-a-stop", "not-a-pr-opened", "duplicate", "missing-report", "limit", "error"),
    THREADS: ("resolved", "reopened"),  # scripts/threads: the thread id and its author ride as extra fields
    FRONTIER: ("ran",),  # scripts/frontier: the takeable count rides as an extra field
    GH_CALL: ("ok", "failed", "ratelimited", "wait"),
}
EVENT_KINDS = tuple(OUTCOMES)
HANDED_BACK = "handed-back"
NUDGE_SCOPE = "nudge"  # the pass-log record `record --nudged <ticket>` writes: {"scope": "nudge", "nudged": [<ticket>]}


class NoSession(RuntimeError):
    """No session to stamp an event or a pass with. The message is the line a CLI prints on stderr
    (`runlog: no session, <kind> event not written`); the CLI keeps its own output and exit code (ADR-0014)."""


def log_path(repo, name: str) -> Path:
    """Where the log called `name` (EVENTS_LOG or PASS_LOG) lives for `repo`."""
    return main_checkout(repo) / PROFILES / name


SESSION_ENV = "ORCHESTRATE_SESSION"  # a script run with `--session <id>` sets it, so its events carry that id (#532)


def current_session(repo) -> str | None:
    """The session named by $ORCHESTRATE_SESSION (`--session`), else the session the state file is stamped with (rebuild-state writes it), or None when there is no
    usable state file. Read from the main checkout, like the log, so a linked worktree's script finds it.

    Not $CLAUDE_SESSION_ID: the run skill templates that variable into the commands it names only, so the
    scripts a run starts never see it, and every event was written with session null (#341).

    The file keeps one entry per session; its `session` field names the one that rebuilt last, in the old shape and the
    new alike, so no migration is needed here. No lock is taken: rebuild-state renames a temp file over the state
    file, so a read sees the old file or the new."""
    if os.environ.get(SESSION_ENV):
        return os.environ[SESSION_ENV]
    try:
        state = json.loads((main_checkout(repo) / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sid = state.get("session") if isinstance(state, dict) else None
    return sid if isinstance(sid, str) and sid else None


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

    Fields: ts (UTC ISO), session (default: the state file's, see current_session), kind, ref (PR or
    ticket), outcome, plus any extra keys. An unknown kind, or an outcome the kind doesn't have, raises
    ValueError. With no session from either source nothing is written and NoSession is raised;
    a library never ends the process, so the caller decides what that means.
    """
    if kind not in OUTCOMES:
        raise ValueError(f"unknown event kind {kind!r}; expected one of {EVENT_KINDS}")
    if outcome not in OUTCOMES[kind]:
        raise ValueError(f"unknown outcome {outcome!r} for {kind!r} events; expected one of {OUTCOMES[kind]}")
    session = session or current_session(repo)
    if not session:
        raise NoSession(f"runlog: no session, {kind} event not written")
    event = {
        "ts": _now(),
        "session": session,
        "kind": kind,
        "ref": ref,
        "outcome": outcome,
    }
    event.update(extra)
    _append(repo, EVENTS_LOG, event)
    return event


def latest_event(repo, kind: str, ref, skip_outcomes=()) -> dict | None:
    """The newest event of `kind` for `ref` (compared as text, so `#7`'s 7 and "7" match), across sessions
    (a resumed orchestrator is a new session); events whose outcome is in `skip_outcomes` are passed over."""
    found = None
    for e in common.read_jsonl(log_path(repo, EVENTS_LOG)):
        if e.get("kind") == kind and str(e.get("ref")) == str(ref) and e.get("outcome") not in skip_outcomes:
            found = e
    return found


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
        ts = parse_ts(e.get("ts"))
        if e.get("session") == session or (not e.get("session") and ts and start <= ts <= end):
            out.append(e)
    # A log with nothing for this session is the same as no log: an older plugin's session.
    return out or None


def append_pass(repo, session: str, entry: dict) -> dict:
    """Append one pass (a dict from the orchestrator) to the pass log, stamped with ts and session.
    An empty session raises NoSession and writes nothing."""
    if not session:
        raise NoSession("runlog: no session, pass not written")
    entry = {"ts": _now(), "session": session, **entry}
    _append(repo, PASS_LOG, entry)
    return entry


def read_passes(repo, session: str) -> list:
    """This session's passes, oldest first. A nudge record (`profile.py record --nudged`, which `claims` reads) is not a pass."""
    if not repo:
        return []
    return [p for p in common.read_jsonl(log_path(repo, PASS_LOG))
            if p.get("session") == session and p.get("scope") != NUDGE_SCOPE]
