#!/usr/bin/env python3
"""PermissionRequest hook: record every permission prompt, decide none (#569).

Claude Code fires PermissionRequest right before it shows a prompt. This hook appends one JSON line to
`.claude/orchestrate-prompts.jsonl` in the main checkout and prints nothing, so the prompt still shows
and the developer's choice is unchanged. Whatever goes wrong (a bad payload, an unwritable file) is one
line on stderr; the exit code is always 0 and the hook never blocks.

The line holds: time, session_id, agent_type (null for the main session), tool_name, cwd, permission_mode and
tool_input, cut down: a Bash or Monitor `command` to 2000 characters, `file_path` for an edit, and for any other tool the
key names only (no file contents, no MCP arguments). The orchestrator and its workers prompt at the same time,
so a write is one O_APPEND write under an exclusive flock.

Stdlib only, Python 3.9+; runs under `python3 -I`, so it imports nothing from the plugin.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(".claude") / "orchestrate-prompts.jsonl"
COMMAND_LIMIT = 2000
COMMAND_TOOLS = ("Bash", "Monitor")
PATH_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def main_checkout(cwd: str) -> Path:
    """The main checkout holding cwd: inside a linked worktree (`.git` is a file) the main repo's root, so every
    session of a run writes the one file. Outside a checkout, cwd itself."""
    start = Path(cwd or ".").resolve()
    for directory in (start, *start.parents):
        dot_git = directory / ".git"
        if dot_git.is_dir():
            return directory
        if dot_git.is_file():
            try:
                gitdir = (directory / dot_git.read_text().split("gitdir:", 1)[1].strip().splitlines()[0]).resolve()
                if (gitdir / "commondir").is_file():
                    return (gitdir / (gitdir / "commondir").read_text().strip()).resolve().parent
            except (OSError, IndexError):
                pass
            return directory
    return start


def cut_input(tool: str, tool_input) -> dict | list:
    if not isinstance(tool_input, dict):
        return []
    if tool in COMMAND_TOOLS:
        command = tool_input.get("command")
        return {"command": command[:COMMAND_LIMIT] if isinstance(command, str) else None}
    if tool in PATH_TOOLS:
        path = tool_input.get("file_path", tool_input.get("notebook_path"))
        return {"file_path": path if isinstance(path, str) else None}
    return sorted(str(k) for k in tool_input)


def record(payload: dict) -> dict:
    tool = payload.get("tool_name")
    tool = tool if isinstance(tool, str) else ""
    agent = payload.get("agent_type")
    return {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": payload.get("session_id"),
        "agent_type": agent if isinstance(agent, str) and agent else None,
        "tool_name": tool,
        "cwd": payload.get("cwd"),
        "tool_input": cut_input(tool, payload.get("tool_input")),
        "permission_mode": payload.get("permission_mode"),
    }


def append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The log holds commands of up to 2000 characters in clear text, so it is owner-only (#567): created 0600, and an
    # existing file made earlier with 0644 is chmod-ed once.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.fstat(fd).st_mode & 0o077:
            os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.environ.get("CLAUDE_PROJECT_DIR", ".")
        entry = record(payload)
        append(main_checkout(cwd) / LOG, json.dumps(entry, separators=(",", ":")) + "\n")
    except Exception as e:  # never block a prompt
        print(f"prompt_log: not recorded: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
