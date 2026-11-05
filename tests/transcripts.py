"""Fixture builder for session transcripts, shaped as the harness writes them.

One builder for the rebuild-state, profile and guard-corpus tests: a main transcript, the subagents beside it
(`<session>/subagents/agent-<id>.jsonl` with its `.meta.json`), and the records a session carries (a dispatch,
a reply, a Bash call, a completion notice). Tests import it as `transcripts`, since the test directory is on sys.path.
"""

import json
from pathlib import Path

T0 = "2026-11-04T09:00:00.000Z"


def write_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" if not isinstance(r, str) else r + "\n" for r in records))


def main_path(projects_dir, sid="sess-1", project="-repo"):
    """Where a session's main transcript lives under a projects dir."""
    return Path(projects_dir) / project / f"{sid}.jsonl"


def dispatch(tool_id, description, subagent="orchestrate:worker", model=None, prompt="brief", ts=T0, sid="sess-1"):
    """An assistant record that dispatches an Agent: `description` and `prompt` are the brief's text."""
    inp = {"description": description, "subagent_type": subagent, "prompt": prompt}
    if model:
        inp["model"] = model
    return {"type": "assistant", "timestamp": ts, "sessionId": sid,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_id, "name": "Agent", "input": inp}]}}


def reply(ts, model=None, text="hi", usage=None):
    """An assistant response; a `model` and `usage` make it count as a model request in the profile."""
    msg = {"role": "assistant", "content": [{"type": "text", "text": text}]}
    if model:
        msg["model"] = model
    if usage:
        msg["usage"] = usage
    return {"type": "assistant", "timestamp": ts, "message": msg}


def bash(tool_id, command, cwd=None, ts=T0):
    """An assistant record that makes one Bash call, recorded in `cwd` when given."""
    rec = {"type": "assistant", "timestamp": ts,
           "message": {"role": "assistant", "content": [
               {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}]}}
    if cwd is not None:
        rec["cwd"] = cwd
    return rec


def completion(agent_id, worktree, branch=None, ts=T0, sid="sess-1"):
    """The harness's completion notice for a worker, queued into the main transcript; it names the worktree it used."""
    branch = branch or f"worktree-agent-{agent_id}"
    notice = (f"<task-notification>\n<task-id>{agent_id}</task-id>\n<status>completed</status>\n"
              f"<worktree><worktreePath>{worktree}</worktreePath><worktreeBranch>{branch}</worktreeBranch></worktree>\n"
              "</task-notification>")
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": ts, "sessionId": sid, "content": notice}


def write_session(main, records, agents=()):
    """Write a session: the main transcript `records`, and each subagent in `agents`.

    An agent is a dict: `id` (its agent id, without `agent-`), `tool_use` (the dispatch's tool_use id it joins to),
    `records` (its own transcript), and optionally `agent_type` (default orchestrate:worker) and `meta` (extra
    fields for its .meta.json, e.g. `model`, `description`).
    """
    write_jsonl(main, records)
    sub = Path(main).with_suffix("") / "subagents"
    for a in agents:
        stem = f"agent-{a['id']}"
        write_jsonl(sub / f"{stem}.jsonl", a.get("records", []))
        meta = dict({"agentType": a.get("agent_type", "orchestrate:worker"), "toolUseId": a.get("tool_use")},
                    **a.get("meta", {}))
        (sub / f"{stem}.meta.json").write_text(json.dumps(meta))
    return sub
