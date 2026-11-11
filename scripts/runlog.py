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
import re
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
RESUME = "resume"  # scripts/launch --resume: one per resumed run; the profile measures the Session gap up to its ts
REVIEW = "review"  # scripts/worker-report <pr> --reviewer: one per reviewer report (ref = PR; round, head, suite, blocking, non_blocking, posted)
GH_CALL = "gh_call"  # scripts/github.py: one per `gh` attempt (ok|failed|ratelimited), or "wait" for a sleep
SWEEP = "sweep"  # scripts/sweep: `ran` is the watermark `--due` counts merges from; `prompt-kind` (ref = the kind) is a prompt kind already reported
PARK = "park"  # scripts/park-pr: a PR taken out of the merge queue (`parked`) or put back (`unparked`); ref = the PR; ticket, branch, why
SNAPSHOT = "snapshot"  # scripts/merge: `stale` after a merged hooks/** or .claude-plugin/** change (sha = origin/<default>); scripts/launch: `launched` on a fresh launch, which clears it (#727)
RESCUE = "rescue"  # scripts/rescue: one per rescue that ran (ref = the ticket; branch, reason); `sweep` reports the failed ones
TRANSITION = "transition"  # scripts/transition: one per stop-for-lead, adr-hold or to-info that ran (ref = the ticket; branch, pr); `claims` reads it as a waiting question, ADR conflict or info (#747)
GREEDY = "greedy"  # scripts/launch: one per fresh start: `set` (cap = the greedy cap) or `off`; `claims` reads the newest back (#657)
PASS, FAIL, UNKNOWN, MERGED, TIMEOUT, CLOSED = "pass", "fail", "unknown", "merged", "timeout", "closed"
OUTCOMES = {
    GATE: (PASS, FAIL, UNKNOWN),
    CI: (PASS, FAIL, "rerun"),  # "rerun": scripts/ci-rerun made the PR's CI run again (head; run and job for a job rerun)
    MERGE: (MERGED, TIMEOUT, CLOSED, "gate-hold"),  # "gate-hold": scripts/merge refused a PR under a config Merge gate (gate, branch ride along)
    REPLAY: ("line-replay", "word-merge", "regenerated", "handed-back"),
    # scripts/worker-report: the first word of its verdict line.
    WORKER_REPORT: ("valid", "interim", "not-a-stop", "not-a-pr-opened", "fix-report", "duplicate", "missing-report", "limit", "error"),
    REVIEW: ("approve", "request-changes", "incomplete"),
    THREADS: ("resolved", "reopened"),  # scripts/threads: the thread id and its author ride as extra fields
    FRONTIER: ("ran",),  # scripts/frontier: the takeable count rides as an extra field
    GH_CALL: ("ok", "failed", "ratelimited", "wait"),
    RESUME: ("resumed",),
    SWEEP: ("ran", "prompt-kind"),
    PARK: ("parked", "unparked"),
    SNAPSHOT: ("stale", "launched"),
    RESCUE: ("ok", "failed"),
    GREEDY: ("set", "off"),
    TRANSITION: ("stop-for-lead", "adr-hold", "to-info"),
}
GREEDY_DEFAULT, GREEDY_MAX = 5, 8  # `--greedy` alone caps at 5; `--greedy=N` takes N from 1 to 8 (ADR 0007)


def greedy_cap(text) -> int:
    """The greedy cap `--greedy[=N]` names (`text` is N, or empty for the default), the one 1..8 check launch and the
    run skill share. ValueError, with the line to print, for anything else (#657)."""
    if text in ("", None):
        return GREEDY_DEFAULT
    if not re.fullmatch(r"[0-9]+", str(text)):
        raise ValueError(f"--greedy={text} is not a number")
    cap = int(text)
    if not 1 <= cap <= GREEDY_MAX:
        raise ValueError(f"--greedy={text}: the cap is 1 to {GREEDY_MAX}")
    return cap

EVENT_KINDS = tuple(OUTCOMES)
HANDED_BACK = "handed-back"
NUDGE_SCOPE = "nudge"  # the pass-log record `record --nudged <ticket>` writes: {"scope": "nudge", "nudged": [<ticket>]}


class NoSession(RuntimeError):
    """No session to stamp an event or a pass with. The message is the line a CLI prints on stderr
    (`runlog: no session, <kind> event not written`); the CLI keeps its own output and exit code (ADR-0014)."""


STATE_DIR_ENV = "ORCHESTRATE_STATE_DIR"  # a directory that stands in for the main checkout (#710): the test runner sets it


def state_root(repo) -> Path:
    """The directory that holds the run state and the logs: $ORCHESTRATE_STATE_DIR when set, else the main checkout.

    The override exists for tests (#710): a test that drives the guard or a script that logs must never write the
    real events log of the checkout it runs in, under the live run's session. An empty value is no override."""
    override = os.environ.get(STATE_DIR_ENV)
    return Path(override) if override else main_checkout(repo)


def log_path(repo, name: str) -> Path:
    """Where the log called `name` (EVENTS_LOG or PASS_LOG) lives for `repo`."""
    return state_root(repo) / PROFILES / name


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
        state = json.loads((state_root(repo) / STATE).read_text(encoding="utf-8"))
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


def read_kinds(repo, kinds) -> list:
    """The events of the given kinds, oldest first, in one pass over the log. A line is parsed only when it holds one of
    the kinds as a quoted word, so the `gh_call` lines (most of a long log) cost a substring test, not a `json.loads`.
    The substring test is spacing-independent; the kind is then checked on the parsed event."""
    kinds = set(kinds)
    needles = [f'"{k}"' for k in kinds]
    out = []
    try:
        with open(log_path(repo, EVENTS_LOG), encoding="utf-8", errors="replace") as f:
            for line in f:
                if not any(n in line for n in needles):
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("kind") in kinds:
                    out.append(rec)
    except FileNotFoundError:
        pass
    return out


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


IDLE_REASONS = ("overlap", "gate", "empty", "unchecked", "limit")  # why a free slot stayed free (skills/run/SKILL.md, Profiling)
PASS_SCOPES = ("full", "status", "wake", NUDGE_SCOPE)  # besides `#<ticket>` and `parent #<n>`
_SCOPE = re.compile(r"(#\d+|parent #\d+)\Z")


class BadPass(ValueError):
    """A pass record off the schema; the message is the one line a CLI prints on stderr."""


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _tickets(v) -> bool:
    return isinstance(v, list) and all(_int(n) and n > 0 for n in v)


def validate_pass(entry) -> None:
    """Raise BadPass unless `entry` is a pass record the profile can read (the one place the schema lives).

    A nudge record is `{"scope": "nudge", "nudged": [ticket, ...]}`. Any other needs `scope`, `slots` and `free`;
    `idle` (reason -> count, summing to `free`) is required except on a `wake`; the rest is optional and typed."""
    if not isinstance(entry, dict):
        raise BadPass("pass record: expected a JSON object")
    scope = entry.get("scope")
    if not isinstance(scope, str) or not (scope in PASS_SCOPES or _SCOPE.match(scope)):
        raise BadPass(f"pass record: scope must be one of {', '.join(PASS_SCOPES)}, #<ticket> or parent #<n>, not {scope!r}")
    if scope == NUDGE_SCOPE:
        if set(entry) - {"scope", "nudged", "ts", "session"} or not entry.get("nudged") or not _tickets(entry["nudged"]):
            raise BadPass("pass record: a nudge record is only `nudged`, a list of ticket numbers")
        return
    types = {
        "slots": _int, "free": _int, "idle": lambda v: isinstance(v, dict), "takeable_waiting": _int,
        "claimed": lambda v: isinstance(v, dict) and all(isinstance(k, str) and isinstance(x, str) for k, x in v.items()),
        "greedy": lambda v: isinstance(v, bool), "limit_hit": lambda v: isinstance(v, bool),
        "reset_at": lambda v: v is None or isinstance(v, str), "suggestion": lambda v: v is None or isinstance(v, str),
        "dispatched": _tickets, "merged": _tickets, "nudged": _tickets,
        "ts": lambda v: isinstance(v, str), "session": lambda v: isinstance(v, str),
        # The lead's session id when the run has one, else null: the profile folds that transcript in (#638).
        "lead": lambda v: v is None or isinstance(v, str),
    }
    unknown = sorted(set(entry) - set(types) - {"scope"})
    if unknown:
        raise BadPass(f"pass record: unknown field {', '.join(unknown)}")
    for key in ("slots", "free"):
        if key not in entry:
            raise BadPass(f"pass record: missing {key}")
    for key, ok in types.items():
        if key in entry and not ok(entry[key]):
            raise BadPass(f"pass record: bad {key}: {entry[key]!r}")
    if entry["free"] < 0 or entry["slots"] < 1 or entry["free"] > entry["slots"]:
        raise BadPass(f"pass record: free {entry['free']} must be between 0 and slots {entry['slots']}")
    if "idle" not in entry:
        if scope != "wake":
            raise BadPass("pass record: missing idle (only a wake may omit it)")
        return
    for reason, count in entry["idle"].items():
        if reason not in IDLE_REASONS or not _int(count) or count < 0:
            raise BadPass(f"pass record: idle reason {reason!r} must be one of {', '.join(IDLE_REASONS)} with a count >= 0")
    if sum(entry["idle"].values()) != entry["free"]:
        raise BadPass(f"pass record: idle reasons sum to {sum(entry['idle'].values())}, not free {entry['free']}")


def append_pass(repo, session: str, entry: dict) -> dict:
    """Append one pass (a dict from the orchestrator) to the pass log, stamped with ts and session.
    An empty session raises NoSession and an off-schema record raises BadPass (`validate_pass`); neither writes anything."""
    if not session:
        raise NoSession("runlog: no session, pass not written")
    validate_pass(entry)
    entry = {"ts": _now(), "session": session, **entry}
    _append(repo, PASS_LOG, entry)
    return entry


def snapshot_stale(repo) -> dict | None:
    """The `snapshot` event that stands, else None: the newest one, when it is `stale`. A fresh launch writes `launched`,
    which ends it; a resume writes nothing, since a resumed run keeps the snapshot it launched with (#727)."""
    events = read_kinds(repo, [SNAPSHOT])
    return events[-1] if events and events[-1].get("outcome") == "stale" else None


def read_passes(repo, session: str) -> list:
    """This session's passes, oldest first. A nudge record (`profile.py record --nudged`, which `claims` reads) is not a pass."""
    if not repo:
        return []
    return [p for p in common.read_jsonl(log_path(repo, PASS_LOG))
            if p.get("session") == session and p.get("scope") != NUDGE_SCOPE]
