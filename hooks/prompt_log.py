#!/usr/bin/env python3
"""PermissionRequest and Notification hook: record every permission prompt, decide none (#569, #579).

Claude Code fires PermissionRequest right before it shows a prompt, and a Notification of type
`permission_prompt` or `worker_permission_prompt` when it shows one. The notification also covers a prompt that
returns before the hook path (the "sensitive file" check), which PermissionRequest never sees. This hook appends one
JSON line to `.claude/orchestrate-prompts.jsonl` in the main checkout and prints nothing, so the prompt still shows
and the developer's choice is unchanged. Whatever goes wrong (a bad payload, an unwritable file) is one
line on stderr; the exit code is always 0 and the hook never blocks.

A line has `"role"` only when ORCHESTRATE_ROLE is set, which scripts/launch does for the orchestrator's line (#642).

Every line has `"source"`: `request` (PermissionRequest) or `notification`. A notification line has no tool: it
holds `message`, `title` and `notification_type` (each cut to 500 characters) in place of `tool_name`/`tool_input`.
scripts/prompts counts a prompt both events saw once.

A request line holds: time, session_id, agent_type (null for the main session), tool_name, cwd, permission_mode and
tool_input, cut down: a Bash or Monitor `command` to 2000 characters, `file_path` for an edit or a Read, `path` (null when
absent) plus the key names for Grep and Glob, and for any other tool the key names only (no file contents, no pattern
text, no MCP arguments). The orchestrator and its workers prompt at the same time,
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
MESSAGE_LIMIT = 500
COMMAND_TOOLS = ("Bash", "Monitor")
PATH_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SEARCH_TOOLS = ("Grep", "Glob")


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
    if tool == "Read":
        path = tool_input.get("file_path")
        return {"file_path": path if isinstance(path, str) else None}
    if tool in SEARCH_TOOLS:
        # `path` is null when the search runs from the cwd; the pattern text is never kept.
        path = tool_input.get("path")
        return {"path": path if isinstance(path, str) else None, "keys": sorted(str(k) for k in tool_input)}
    return sorted(str(k) for k in tool_input)


def text_field(payload: dict, key: str):
    value = payload.get(key)
    return value[:MESSAGE_LIMIT] if isinstance(value, str) else None


def record(payload: dict) -> dict:
    agent = payload.get("agent_type")
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": payload.get("session_id"),
        "agent_type": agent if isinstance(agent, str) and agent else None,
        "cwd": payload.get("cwd"),
        "permission_mode": payload.get("permission_mode"),
    }
    # ORCHESTRATE_ROLE is set by scripts/launch on the orchestrator's line only (#579). The lead and a hand-started
    # session are also the main session with no agent_type, so scripts/prompts needs this to tell them apart (#642).
    role = os.environ.get("ORCHESTRATE_ROLE")
    if role:
        entry["role"] = role[:50]
    if payload.get("hook_event_name") == "Notification" or ("message" in payload and "tool_name" not in payload):
        # The prompt as the developer saw it. A Notification names no tool: the message is all there is
        # ("Claude requested permissions to edit <path> which is a sensitive file"). It fires for prompts that
        # PermissionRequest never sees (#579).
        return {**entry, "source": "notification", "tool_name": None, "tool_input": [],
                "message": text_field(payload, "message"), "title": text_field(payload, "title"),
                "notification_type": text_field(payload, "notification_type")}
    tool = payload.get("tool_name")
    tool = tool if isinstance(tool, str) else ""
    return {**entry, "source": "request", "tool_name": tool, "tool_input": cut_input(tool, payload.get("tool_input"))}


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
