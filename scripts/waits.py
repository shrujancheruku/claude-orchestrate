"""The shared waits file: everything the developer is waiting to answer, in one place every session can reach (#706).

Stdlib only, Python 3.9+. A library: `waiting` (reader and the lead's `--add`/`--done`), `merge` (a PR stopped at a
config merge gate) and, through its own copy of the append (it runs under `python3 -I` and imports nothing from the
plugin), `hooks/prompt_log.py` (a session stuck on a permission prompt) write it.

The file is `.claude/orchestrate-waits.jsonl` in the main checkout, 0600, append-only under an exclusive flock. A line
is either an add, `{"op":"add","id":...,"text":...,"from":...,"where":...,...}`, or a clear, `{"op":"done","id":...}`.
An add with an id already live replaces it, so a script re-records an entry by writing it again.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from repo import main_checkout

WAITS = Path(".claude") / "orchestrate-waits.jsonl"
FIELD_LIMIT = 500  # characters kept of any text field: an entry is one footer line


def path_of(repo) -> Path:
    return main_checkout(repo) / WAITS


def _append(repo, rec: dict) -> None:
    path = path_of(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.fstat(fd).st_mode & 0o077:
            os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.write(fd, (json.dumps(rec, separators=(",", ":")) + "\n").encode("utf-8"))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def add(repo, text: str, source: str = "lead", where: str = "", wait_id: str | None = None, **extra) -> str:
    """Record a wait and return its id. `source` is who is waiting (`lead`, `orchestrator`, `merge`, `prompt`);
    `where` says where the developer answers. `extra` rides along (`pr`, `ticket`, `head`, `command`, `session`)."""
    wait_id = wait_id or secrets.token_hex(3)
    rec = {"op": "add", "id": wait_id, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "text": str(text)[:FIELD_LIMIT], "from": source, "where": str(where)[:FIELD_LIMIT]}
    rec.update({k: v for k, v in extra.items() if v is not None})
    _append(repo, rec)
    return wait_id


def done(repo, wait_id: str) -> bool:
    """Clear a wait; False when no live entry has that id (nothing is written then)."""
    if not any(e["id"] == wait_id for e in live(repo)):
        return False
    _append(repo, {"op": "done", "id": wait_id})
    return True


def clear_pr(repo, pr) -> int:
    """Clear every live entry about PR `pr` (it merged or closed); returns how many."""
    n = 0
    for e in live(repo):
        if str(e.get("pr")) == str(pr):
            _append(repo, {"op": "done", "id": e["id"]})
            n += 1
    return n


def live(repo) -> list:
    """The live entries, oldest first. A line that is not JSON is skipped; a missing file is no waits."""
    entries: dict = {}
    try:
        lines = path_of(repo).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
            continue
        if rec.get("op") == "add":
            entries.pop(rec["id"], None)  # a re-recorded entry moves to the end
            entries[rec["id"]] = rec
        elif rec.get("op") == "done":
            entries.pop(rec["id"], None)
    return list(entries.values())
