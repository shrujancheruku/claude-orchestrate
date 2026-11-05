"""A session's transcripts: the main one, the subagents beside it, and the agents it dispatched.

Stdlib only, Python 3.9+. A library: rebuild-state, guard-corpus and profile.py import it (ADR-0003).
It only reads. Every reader goes through common.read_jsonl, so a stray or torn line is skipped, and
a missing file reads as empty. The one join of a dispatch to its agent (its toolUseId to the
subagent's `.meta.json`) lives here, so every script that reports on agents agrees on it.
"""

# macOS ships Python 3.9, which needs this for `str | None` annotations.
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import common

PLUGIN = common.PLUGIN
TOKEN_KEYS = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_read_input_tokens": "cache_read",
    "cache_creation_input_tokens": "cache_write",
}
# Activity in an agent's transcript further apart than this is two segments:
# a worker waiting on review between them isn't building.
SEGMENT_GAP_S = 120
LIMIT_TEXT = re.compile(r"usage limit|rate limit|limit reached|resets? (?:at|in)", re.I)
# The harness's completion notice for an agent names its worktree: `<task-id>` is the agent id, and
# `<worktreePath>` the agent's own `.claude/worktrees/agent-<id>`. The notice is queued into the main transcript.
TASK_ID = re.compile(r"<task-id>([^<]+)</task-id>")
WORKTREE_PATH = re.compile(r"<worktreePath>([^<]+)</worktreePath>")


def parse_ts(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # Epoch seconds, or milliseconds from some callers.
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    # A naive value would raise TypeError against aware bounds; treat it as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def text_of(block) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return block.get("text", "") or ""


class Transcript:
    """One transcript: tokens counted once per API message, plus its activity times."""

    def __init__(self, path: Path):
        self.path = path
        self.records = list(common.read_jsonl(path))
        self.requests = {}  # message id -> (model, usage), each API request once: the one source of per-request usage
        self.models = []
        self.times = []
        for r in self.records:
            ts = parse_ts(r.get("timestamp"))
            if ts:
                self.times.append(ts)
            msg = r.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
                # A response is logged once per content block, each with the same usage.
                self.requests[msg.get("id") or id(r)] = (msg.get("model"), msg["usage"])
                if msg.get("model") and msg["model"] not in self.models:
                    self.models.append(msg["model"])
        self.times.sort()
        self.tokens = {v: 0 for v in TOKEN_KEYS.values()}
        for _, u in self.requests.values():
            for k, v in TOKEN_KEYS.items():
                self.tokens[v] += u.get(k) or 0

    def segments(self) -> list:
        out = []
        for ts in self.times:
            if out and (ts - out[-1][1]).total_seconds() <= SEGMENT_GAP_S:
                out[-1][1] = ts
            else:
                out.append([ts, ts])
        return out

    def tool_uses(self, name: str):
        for r in self.records:
            for b in common.blocks(r):
                if b.get("type") == "tool_use" and b.get("name") == name:
                    yield parse_ts(r.get("timestamp")), b

    def tool_results(self) -> dict:
        out = {}
        for r in self.records:
            for b in common.blocks(r):
                if b.get("type") == "tool_result":
                    out[b.get("tool_use_id")] = text_of(b)
        return out

    def api_errors(self):
        for r in self.records:
            if r.get("isApiErrorMessage"):
                text = " ".join(text_of(b) for b in common.blocks(r))
                if LIMIT_TEXT.search(text):
                    yield parse_ts(r.get("timestamp")), text[:200]

    def contexts(self) -> list:
        """The prompt size of each API request: input plus cache reads and writes."""
        return [(u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                + (u.get("cache_creation_input_tokens") or 0) for _, u in self.requests.values()]

    def tool_output_tokens(self) -> int:
        """Tokens the tools handed back, estimated at 4 characters each: the transcript holds text, not counts."""
        return sum(len(text) for text in self.tool_results().values()) // 4

    def compactions(self) -> int:
        return sum(1 for r in self.records if r.get("type") == "system" and r.get("subtype") == "compact_boundary")

    def compaction_details(self) -> list:
        """Each compaction: when, what triggered it (auto|manual) and the context size it compacted (`preTokens`).

        Trigger and size are None when the harness didn't record them."""
        out = []
        for r in self.records:
            if r.get("type") == "system" and r.get("subtype") == "compact_boundary":
                meta = r.get("compactMetadata") or r.get("compact_metadata") or {}
                out.append({"ts": iso(parse_ts(r.get("timestamp"))), "trigger": meta.get("trigger"), "pre_tokens": meta.get("preTokens")})
        return out

    def turns(self) -> list:
        """One entry per API request (a response logged once per content block is one turn), in order:
        its time, model, usage, tool calls and text."""
        out, by_id = [], {}
        for r in self.records:
            msg = r.get("message")
            if r.get("type") != "assistant" or not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                continue
            key = msg.get("id") or id(r)
            turn = by_id.get(key)
            if turn is None:
                turn = by_id[key] = {"ts": parse_ts(r.get("timestamp")), "model": msg.get("model"), "usage": msg["usage"], "tools": [], "text": ""}
                out.append(turn)
            for b in common.blocks(r):
                if b.get("type") == "tool_use":
                    turn["tools"].append((b.get("name") or "", b.get("input") or {}))
                elif b.get("type") == "text":
                    turn["text"] += b.get("text") or ""
        return out

    def subagent(self, stem: str) -> Transcript:
        """The transcript of the subagent called `stem` (`agent-<id>`) beside this main transcript."""
        return Transcript(self.path.with_suffix("") / "subagents" / f"{stem}.jsonl")

    def bash_calls(self):
        """(command, cwd) for each Bash call in this transcript; cwd is the record's own, or None.

        Reads only tool_use inputs, never tool output."""
        for r in self.records:
            for b in common.blocks(r):
                if b.get("type") == "tool_use" and b.get("name") == "Bash":
                    command = (b.get("input") or {}).get("command")
                    if isinstance(command, str) and command.strip():
                        yield command, r.get("cwd")

    def completions(self) -> dict:
        """agent id -> the worktree path the harness reported for it, from its completion notices.

        One agent can notify more than once; the last notice wins."""
        out = {}
        for r in self.records:
            texts = [r["content"]] if isinstance(r.get("content"), str) else []
            texts += [b.get("text") or "" for b in common.blocks(r) if b.get("type") == "text"]
            for text in texts:
                for notice in text.split("<task-notification>")[1:]:
                    task, path = TASK_ID.search(notice), WORKTREE_PATH.search(notice)
                    if task and path:
                        out[task.group(1).strip()] = path.group(1).strip()
        return out


def subagent_metas(main: Transcript) -> dict:
    """toolUseId -> (agent file stem, metadata) for every subagent the harness recorded for the session."""
    return common.subagent_metas(main.path)


def agents(main: Transcript) -> list:
    """Every plugin agent the session dispatched, joined to its own transcript (None when the harness kept none).

    A dispatch of any other agent type is skipped. `role` is the agent type without its plugin prefix
    (`worker`, `reviewer`, `scout`). `ticket` is the dispatch description's first `#<n>` (common.ticket_of).
    The two model fields keep different fallback orders, on purpose: `model` is the transcript's model
    (from its usage-bearing responses), else the dispatch's, else the metadata's; `worker_model` is the
    metadata's (the dispatch alias the worker-model split reads), else the transcript's, else the dispatch's.
    """
    by_tool_use = subagent_metas(main)
    out = []
    for ts, use in main.tool_uses("Agent"):
        inp = use.get("input") or {}
        role = inp.get("subagent_type") or ""
        if not role.startswith(PLUGIN):
            continue
        name, meta = by_tool_use.get(use.get("id"), (None, {}))
        t = main.subagent(name) if name else None
        desc = inp.get("description") or meta.get("description") or ""
        first = t.models[0] if t and t.models else None
        out.append({
            "id": name[len("agent-"):] if name and name.startswith("agent-") else name,
            "role": role[len(PLUGIN):],
            "ticket": common.ticket_of(desc),
            "model": first or inp.get("model") or meta.get("model"),
            "worker_model": meta.get("model") or first or inp.get("model"),
            "dispatch_model": inp.get("model"),
            "meta_model": meta.get("model"),
            "description": desc,
            "prompt": inp.get("prompt") or "",
            "tool_use_id": use.get("id"),
            "dispatched": ts,
            "transcript": t,
            "requests": len(t.requests) if t else 0,
            "contexts": t.contexts() if t else [],
            "tool_output_tokens": t.tool_output_tokens() if t else 0,
            "segments": t.segments() if t else [],
            "tokens": t.tokens if t else {v: 0 for v in TOKEN_KEYS.values()},
            "last_record": t.times[-1] if t and t.times else None,
        })
    return out


def subagents(main: Transcript) -> list:
    """(agent type, Transcript) for each subagent beside the session with a `.meta.json` naming its type.

    Unlike agents(), this needs no dispatch in the main transcript: it is every subagent the harness kept."""
    out = []
    for path in sorted((main.path.with_suffix("") / "subagents").glob("*.jsonl")):
        try:
            role = json.loads(path.with_suffix(".meta.json").read_text())["agentType"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        out.append((role, Transcript(path)))
    return out


def brief_field(prompt, label: str) -> str | None:
    """The text after `<label>:` on its line of a dispatch prompt (a brief), up to the sentence's end; None when absent.

    The label may be in bold (`**Touch map:**`) or after a list marker. The first match wins."""
    m = re.search(rf"^\W*{re.escape(label)}:\**[ \t]*(.*)$", str(prompt or ""), re.M)
    if not m:
        return None
    return m.group(1).split(". ")[0].rstrip(". ")


def branch_of(prompt) -> str | None:
    """The ticket branch a brief names (`Branch: <number>-<slug>`), or None."""
    value = brief_field(prompt, "Branch")
    return value.split()[0] if value else None


def touch_map(prompt) -> list:
    """The entries of a brief's `Touch map:` line as written (comma-separated `path:line`); [] when there is none."""
    value = brief_field(prompt, "Touch map")
    return [entry.strip() for entry in value.split(",") if entry.strip()] if value else []
