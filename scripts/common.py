"""Shared helpers for the plugin scripts: config sections, the events log, bounded output.

Stdlib only, Python 3.9+. A sibling script imports it with `import common`
(its own directory is already on sys.path).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

EXIT_OK, EXIT_VERDICT, EXIT_COULD_NOT_RUN = 0, 1, 2
NOT_FOUND = 127  # the shell's "command not found"; what run() reports for a missing binary

CONFIG = Path(".claude") / "orchestration.md"
# The events log sits beside the pass log (passes.jsonl) the profiler already reads.
PROFILES = Path(".claude") / "orchestrate-profiles"
EVENTS_LOG = "events.jsonl"
EVENT_KINDS = ("gate", "ci", "merge", "replay")
TAIL_LINES = 40  # failure tails
LINE_WIDTH = 200  # one line per item, never wider


def config_section(repo, name: str) -> str | None:
    """Body of the config's `## <name>` section (case-insensitive), or None.

    A missing or unreadable config is None too, so callers degrade rather than crash.
    """
    try:
        text = (Path(repo) / CONFIG).read_text()
    except OSError:
        return None
    return section_of(text, name)


def section_of(text: str, name: str) -> str | None:
    """Lines under `## name` up to the next `## ` heading; `###` subsections stay inside."""
    want = name.strip().lower()
    body = None
    for line in text.splitlines():
        m = re.match(r"##(?!#)\s+(.*?)\s*$", line)
        if m:
            if body is not None:
                break
            if m.group(1).lower() == want:
                body = []
            continue
        if body is not None:
            body.append(line)
    return None if body is None else "\n".join(body).strip("\n")


def append_event(repo, kind: str, outcome: str, ref=None, session: str | None = None, **extra) -> dict:
    """Append one event line to events.jsonl beside the pass log. Write-only: nothing here reads it back.

    Fields: ts (UTC ISO), session, kind (gate|ci|merge|replay), ref (PR or ticket), outcome,
    plus any extra keys. Session defaults to $CLAUDE_SESSION_ID.
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind {kind!r}; expected one of {EVENT_KINDS}")
    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": session if session is not None else os.environ.get("CLAUDE_SESSION_ID"),
        "kind": kind,
        "ref": ref,
        "outcome": outcome,
    }
    event.update(extra)
    path = Path(repo) / PROFILES / EVENTS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    # One write of one line: small appends stay whole when scripts run in parallel.
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")
    return event


class Run(NamedTuple):
    """What run() returns: unpacks as (code, stdout, stderr); `returncode` is an alias of `code`."""
    code: int
    stdout: str
    stderr: str

    @property
    def returncode(self) -> int:
        return self.code


def run(*cmd, cwd=None, timeout=None) -> Run:
    """Run a command (no shell) and return (code, stdout, stderr), never raising for a missing tool.

    A binary that is missing or not executable (OSError) is code 127 with the reason in stderr, so a
    caller treats "gh is not installed" like any failed call instead of crashing. A timeout is 124.
    """
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True,
                           errors="replace", timeout=timeout)
    except OSError as e:
        return Run(NOT_FOUND, "", f"{cmd[0]}: {e.strerror or e}")
    except subprocess.TimeoutExpired:
        return Run(124, "", f"{cmd[0]}: timed out after {timeout:g}s")
    return Run(p.returncode, p.stdout, p.stderr)


def pr_ref(s) -> tuple:
    """(pr, ref) from a PR argument: `#12` and `12` both give ("12", 12); a non-number keeps its text as ref."""
    pr = str(s).lstrip("#")
    return pr, int(pr) if pr.isdigit() else pr


def die(message: str) -> None:
    """Exit with "could not run" (2): message on stderr, no traceback."""
    print(message, file=sys.stderr)
    sys.exit(EXIT_COULD_NOT_RUN)


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def read_jsonl(path):
    """Yield each JSON line of a file; unreadable lines and a missing file are skipped."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except FileNotFoundError:
        return


def one_line(text, width: int = LINE_WIDTH) -> str:
    """Collapse whitespace to a single line, cut with an ellipsis past width."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def tail(text: str, n: int = TAIL_LINES) -> str:
    """Last n lines of text, with a marker line saying how many were dropped."""
    lines = text.rstrip("\n").splitlines()
    if len(lines) <= n:
        return "\n".join(lines)
    return "\n".join([f"... {len(lines) - n} earlier lines omitted"] + lines[-n:])
