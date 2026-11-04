"""Profile an orchestrator session: slot efficiency, token use and plan headroom.

    profile.py <session id | transcript.jsonl> [--repo PATH] [--slots N] [--threshold PCT] [--out DIR] [--quiet]
    profile.py record --repo PATH --session ID '<pass json>'

The first form reads the session's transcript and its subagents' transcripts,
the repo's pass log and the status-line usage log, then writes
`<repo>/.claude/orchestrate-profiles/<date>-<session>.json` and a `.html` copy
of the viewer with that profile embedded, and prints a summary. The second
form appends one pass to the pass log; `/orchestrate:run --profile` calls it
at the end of each pass. Everything here only reads, apart from those files.
"""

# macOS ships Python 3.9, which needs this for `str | None` annotations.
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 2
PLUGIN = "orchestrate:"
PROFILES = Path(".claude") / "orchestrate-profiles"
PASS_LOG = "passes.jsonl"
VIEWER = Path(__file__).resolve().parent.parent / "viewer" / "profile.html"
# Activity in an agent's transcript further apart than this is two segments:
# a worker waiting on review between them isn't building.
SEGMENT_GAP_S = 120
TOKEN_KEYS = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_read_input_tokens": "cache_read",
    "cache_creation_input_tokens": "cache_write",
}
# Proxy prices: USD per million tokens at API list, per model version and token type.
# Checked against the pricing page below on 2026-11-04. Cache writes are the 5-minute
# rate (1.25x input); the harness doesn't log the cache TTL, so 1-hour writes are
# priced as 5-minute. Update the date with the numbers.
# A model id matches the longest key it contains ("claude-opus-5-5-..." -> "claude-opus-5-5").
# Only Claude Haiku 5.5 has a long-context tier: a request whose prompt (input + cache
# read + cache write tokens) is over 100,000 tokens pays the `long` rates. Every other
# listed model has one rate across its whole context window.
PRICES_AS_OF = "2026-11-04"
PRICES_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"


def _rates(input_, output, cache_read):
    return {"input": input_, "output": output, "cache_read": cache_read, "cache_write": input_ * 1.25}


PRICES = {
    "claude-fable-5-1": _rates(10, 50, 0.25),
    "claude-mythos-5-1": _rates(10, 50, 0.25),
    "claude-fable-5": _rates(10, 50, 1),
    "claude-mythos-5": _rates(10, 50, 1),
    "claude-opus-5-5": _rates(4, 20, 0.20),
    "claude-opus-5": _rates(5, 25, 0.50),
    "claude-opus-4-8": _rates(5, 25, 0.50),
    "claude-opus-4-7": _rates(5, 25, 0.50),
    "claude-opus-4-6": _rates(5, 25, 0.50),
    "claude-opus-4-5": _rates(5, 25, 0.50),
    "claude-opus-4-1": _rates(15, 75, 1.50),
    "claude-opus-4": _rates(15, 75, 1.50),
    "claude-sonnet-5-5": _rates(2, 10, 0.10),
    "claude-sonnet-5": _rates(2, 10, 0.20),
    "claude-sonnet-4-6": _rates(3, 15, 0.30),
    "claude-sonnet-4-5": _rates(3, 15, 0.30),
    "claude-sonnet-4": _rates(3, 15, 0.30),
    "claude-haiku-5-5": {
        **_rates(0.10, 0.50, 0.01),
        "long_over": 100_000,
        "long": _rates(0.50, 2.50, 0.05),
    },
    "claude-haiku-4-5": _rates(1, 5, 0.10),
    "claude-3-5-haiku": _rates(0.80, 4, 0.08),
}
LIMIT_TEXT = re.compile(r"usage limit|rate limit|limit reached|resets? (?:at|in)", re.I)


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def usage_log() -> Path:
    return Path(os.environ.get("ORCHESTRATE_USAGE_LOG") or claude_dir() / "orchestrate" / "usage.jsonl")


def price_key(model) -> str | None:
    name = (model or "").lower()
    return max((k for k in PRICES if k in name), key=len, default=None)


def model_family(model) -> str | None:
    """opus, sonnet, haiku, fable or mythos for a priced model; None when it has no price."""
    key = price_key(model)
    return re.search(r"opus|sonnet|haiku|fable|mythos", key).group(0) if key else None


def is_exact_version(model) -> bool:
    """False when the id names a newer version than its price key ("claude-opus-4-9" matched "claude-opus-4")."""
    key = price_key(model)
    return bool(key) and not re.match(r"-\d{1,2}(?!\d)", (model or "").lower().split(key, 1)[1])


def request_cost(model, usage: dict) -> float | None:
    """Proxy USD for one API request, or None for a model with no price."""
    key = price_key(model)
    if not key:
        return None
    price = PRICES[key]
    tokens = {name: usage.get(key) or 0 for key, name in TOKEN_KEYS.items()}
    if price.get("long_over") and tokens["input"] + tokens["cache_read"] + tokens["cache_write"] > price["long_over"]:
        price = price["long"]
    return sum(tokens[k] * price[k] for k in tokens) / 1e6


def parse_ts(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # Epoch seconds, or milliseconds from some callers.
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def read_jsonl(path: Path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except FileNotFoundError:
        return


def blocks(record) -> list:
    # Other record types (api-request-blob) echo earlier turns' blocks; only conversation turns count.
    if record.get("type") not in ("user", "assistant"):
        return []
    msg = record.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return content if isinstance(content, list) else []


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
        self.records = list(read_jsonl(path))
        usage = {}
        self.requests = {}  # message id -> (model, usage), each API request once
        self.models = []
        self.times = []
        for r in self.records:
            ts = parse_ts(r.get("timestamp"))
            if ts:
                self.times.append(ts)
            msg = r.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
                # A response is logged once per content block, each with the same usage.
                usage[msg.get("id") or id(r)] = msg["usage"]
                self.requests[msg.get("id") or id(r)] = (msg.get("model"), msg["usage"])
                if msg.get("model") and msg["model"] not in self.models:
                    self.models.append(msg["model"])
        self.times.sort()
        self.tokens = {v: 0 for v in TOKEN_KEYS.values()}
        for u in usage.values():
            for k, v in TOKEN_KEYS.items():
                self.tokens[v] += u.get(k) or 0

    def cost(self) -> dict:
        """Proxy USD by model family; requests from a model with no price count under `unpriced`."""
        out = {}
        for model, u in self.requests.values():
            c = request_cost(model, u)
            key = model_family(model) if c is not None else "unpriced"
            out[key] = out.get(key, 0.0) + (c or 0.0)
        return out

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
            for b in blocks(r):
                if b.get("type") == "tool_use" and b.get("name") == name:
                    yield parse_ts(r.get("timestamp")), b

    def tool_results(self) -> dict:
        out = {}
        for r in self.records:
            for b in blocks(r):
                if b.get("type") == "tool_result":
                    out[b.get("tool_use_id")] = text_of(b)
        return out

    def api_errors(self):
        for r in self.records:
            if r.get("isApiErrorMessage"):
                text = " ".join(text_of(b) for b in blocks(r))
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


def find_transcript(arg: str) -> Path:
    p = Path(arg).expanduser()
    if p.suffix == ".jsonl" and p.exists():
        return p
    hits = sorted((claude_dir() / "projects").glob(f"*/{arg}.jsonl"))
    if not hits:
        sys.exit(f"profile: no transcript for session {arg} under {claude_dir() / 'projects'}")
    return hits[0]


def repo_of(main: Transcript) -> Path | None:
    for r in main.records:
        if r.get("cwd"):
            return Path(r["cwd"])
    return None


def config_slots(repo: Path | None) -> int | None:
    if not repo:
        return None
    try:
        text = (repo / ".claude" / "orchestration.md").read_text()
    except OSError:
        return None
    m = re.search(r"\*\*Slots:\*\*\s*(\d+)", text)
    return int(m.group(1)) if m else None


def merge_intervals(intervals: list) -> list:
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def seconds(intervals) -> float:
    return sum((e - s).total_seconds() for s, e in intervals)


def clip(intervals, start, end) -> list:
    return [[max(s, start), min(e, end)] for s, e in intervals if e > start and s < end]


def subagent_metas(main: Transcript) -> dict:
    """toolUseId -> (agent file stem, metadata) for every subagent the harness recorded for the session."""
    by_tool_use = {}
    for meta_path in (main.path.with_suffix("") / "subagents").glob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text())
        except ValueError:
            continue
        by_tool_use[meta.get("toolUseId")] = (meta_path.name[: -len(".meta.json")], meta)
    return by_tool_use


def collect_agents(main: Transcript) -> list:
    """Every orchestrate agent the session dispatched, joined to its own transcript."""
    sub = main.path.with_suffix("") / "subagents"
    by_tool_use = subagent_metas(main)
    agents = []
    for ts, use in main.tool_uses("Agent"):
        inp = use.get("input") or {}
        role = inp.get("subagent_type") or ""
        if not role.startswith(PLUGIN):
            continue
        name, meta = by_tool_use.get(use.get("id"), (None, {}))
        t = Transcript(sub / f"{name}.jsonl") if name else None
        desc = inp.get("description") or meta.get("description") or ""
        asked = re.search(r"\bround[:\s#]*(\d+)", f"{desc} {inp.get('prompt') or ''}", re.I)
        agents.append({
            "id": name[len("agent-"):] if name and name.startswith("agent-") else name,
            "role": role[len(PLUGIN):],
            # The metadata names the agent's model; the transcript and the dispatch are fallbacks.
            "model": meta.get("model") or (t.models[0] if t and t.models else None) or inp.get("model"),
            "description": desc,
            "round": int(asked.group(1)) if asked else 1,  # the review round a reviewer was dispatched for
            "tool_use_id": use.get("id"),
            "requests": len(t.requests) if t else 0,
            "contexts": t.contexts() if t else [],
            "tool_output_tokens": t.tool_output_tokens() if t else 0,
            "dispatched": ts,
            "segments": t.segments() if t else [],
            "tokens": t.tokens if t else {v: 0 for v in TOKEN_KEYS.values()},
            "cost": t.cost() if t else {},
            "last_record": t.times[-1] if t and t.times else None,
            "transcript": t,
        })
    return agents


def pr_opened(t: Transcript | None) -> int | None:
    if not t:
        return None
    results = t.tool_results()
    for _, use in t.tool_uses("Bash"):
        if re.search(r"\bgh\s+pr\s+create\b", (use.get("input") or {}).get("command", "")):
            m = re.search(r"/pull/(\d+)", results.get(use.get("id"), ""))
            if m:
                return int(m.group(1))
    return None


def ticket_endings(main: Transcript) -> tuple:
    """When the orchestrator merged each PR, and when it handed each ticket back."""
    merged, handed_back = {}, {}
    for ts, use in main.tool_uses("Bash"):
        cmd = (use.get("input") or {}).get("command", "")
        for m in re.finditer(r"\bgh\s+pr\s+merge\s+(\d+)", cmd):
            merged.setdefault(int(m.group(1)), ts)
        for m in re.finditer(r"\bgh\s+issue\s+edit\s+(\d+)[^&;|]*--remove-assignee", cmd):
            handed_back.setdefault(int(m.group(1)), ts)
    return merged, handed_back


def build_tickets(agents: list, main: Transcript, end: datetime) -> list:
    merged, handed_back = ticket_endings(main)
    tickets = {}
    for a in agents:
        if a["role"] != "worker":
            continue
        m = re.search(r"#(\d+)", a["description"])
        if not m:
            continue
        n = int(m.group(1))
        tk = tickets.setdefault(n, {"ticket": n, "pr": None, "workers": [], "dispatched": a["dispatched"]})
        tk["workers"].append(a)
        tk["pr"] = tk["pr"] or pr_opened(a["transcript"])
        a["ticket"] = n
    pr_to_ticket = {tk["pr"]: n for n, tk in tickets.items() if tk["pr"]}
    for a in agents:
        if a["role"] != "worker":
            m = re.search(r"\bPR #?(\d+)", a["description"]) or re.search(r"#(\d+)", a["description"])
            if m:
                a["pr"] = int(m.group(1))
                a["ticket"] = pr_to_ticket.get(a["pr"])
    out = []
    for n, tk in sorted(tickets.items()):
        start = tk["dispatched"] or min(s for w in tk["workers"] for s, _ in w["segments"])
        if tk["pr"] in merged:
            ended, outcome = merged[tk["pr"]], "merged"
        elif n in handed_back:
            ended, outcome = handed_back[n], "handed-back"
        else:
            ended, outcome = end, "open"
        building = clip(merge_intervals([seg for w in tk["workers"] for seg in w["segments"]]), start, ended)
        tokens = sum(sum(a["tokens"].values()) for a in agents if a.get("ticket") == n)
        mine = [a for a in agents if a.get("ticket") == n]
        cost = {"worker": 0.0, "reviewer": 0.0, "other": 0.0}
        for a in mine:
            cost[a["role"] if a["role"] in cost else "other"] += sum(a["cost"].values())
        cost["total"] = sum(cost.values())
        out.append({
            "ticket": n,
            "cost": cost,
            # Worker models in dispatch order: Haiku then Sonnet is an escalation.
            "worker_models": [worker_model_name(w["model"]) for w in tk["workers"]],
            # Filled once events.jsonl is read (a later ticket).
            "gate_runs": None,
            "pr": tk["pr"],
            "outcome": outcome,
            "start": start,
            "end": ended,
            "building": building,
            "tokens": tokens,
        })
    return out


def assign_slots(tickets: list) -> None:
    """Lay tickets into slot rows for the timeline: each takes the first row free at its start."""
    free_at = []
    for tk in sorted(tickets, key=lambda t: t["start"]):
        for i, f in enumerate(free_at):
            if f <= tk["start"]:
                tk["slot"], free_at[i] = i, tk["end"]
                break
        else:
            tk["slot"] = len(free_at)
            free_at.append(tk["end"])


def read_passes(repo: Path | None, session: str) -> list:
    if not repo:
        return []
    return [p for p in read_jsonl(repo / PROFILES / PASS_LOG) if p.get("session") == session]


def read_usage(start: datetime, end: datetime) -> list:
    out = []
    for u in read_jsonl(usage_log()):
        ts = parse_ts(u.get("ts"))
        if ts and start <= ts <= end:
            out.append(u)
    return out


def meter_use(usage: list, key: str) -> float | None:
    """Percentage points a meter climbed over the samples; a reset (a drop) adds nothing."""
    vals = [float(u[key]) for u in usage if u.get(key) is not None]
    if not vals:
        return None
    return sum(max(b - a, 0.0) for a, b in zip(vals, vals[1:]))


def cost_metrics(orch: Transcript, agents: list, merged: int) -> dict:
    """Proxy $ for the whole session: the orchestrator and every agent, split by role and model."""
    by_role, by_model, by_role_model = {}, {}, {}
    for role, cost in [("orchestrator", orch.cost())] + [(a["role"], a["cost"]) for a in agents]:
        for model, usd in cost.items():
            by_role[role] = by_role.get(role, 0.0) + usd
            by_model[model] = by_model.get(model, 0.0) + usd
            rm = by_role_model.setdefault(role, {})
            rm[model] = rm.get(model, 0.0) + usd
    total = sum(by_role.values())
    per = (lambda x: x / merged) if merged else (lambda x: None)
    return {
        "prices_as_of": PRICES_AS_OF,
        "prices_source": PRICES_SOURCE,
        "total": total,
        "per_merged_ticket": per(total),
        "by_role": by_role,
        "by_model": by_model,
        "by_role_model": by_role_model,
        "per_merged_ticket_by_role": {k: per(v) for k, v in by_role.items()},
        "per_merged_ticket_by_model": {k: per(v) for k, v in by_model.items()},
        # Models with no entry in PRICES: their requests cost $0 here, so say so.
        # Requests with no usage at all (the harness's <synthetic> messages) cost nothing and don't count.
        "unpriced_models": sorted({
            m for t in [orch] + [a["transcript"] for a in agents if a["transcript"]]
            for m, u in t.requests.values() if m and not model_family(m) and any(u.get(k) for k in TOKEN_KEYS)}),
        # Models priced at an older sibling's rate because their own version isn't listed.
        "approximate_models": sorted({
            m for t in [orch] + [a["transcript"] for a in agents if a["transcript"]]
            for m, _ in t.requests.values() if m and model_family(m) and not is_exact_version(m)}),
    }


VERDICT = re.compile(r"^\W*verdict:\s*(approve|changes)\b", re.I | re.M)
ROUND_LINE = re.compile(r"^\W*round:\s*(\d+)", re.I | re.M)


def review_verdicts(t: Transcript | None, first_round: int) -> list:
    """The verdicts a reviewer returned, in order, whether it posted (short hand-back) or not (full block).

    Only the reviewer's own text counts, not the commands it ran, so a posted review isn't counted
    twice. A hand-back names its round; a full verdict doesn't, so the dispatch's round numbers
    the first verdict and each later one (the reviewer was continued) follows on."""
    out = []
    for r in t.records if t else []:
        if r.get("type") != "assistant":
            continue
        for b in blocks(r):
            text = b.get("text") or "" if b.get("type") == "text" else ""
            m = VERDICT.search(text)
            if not m:
                continue
            said = ROUND_LINE.search(text)
            minor = re.search(r"^\W*non-blocking:\s*(\d+)", text, re.I | re.M)
            blocking = re.search(r"^\W*blocking:\s*(\d+)", text, re.I | re.M)
            out.append({
                "round": int(said.group(1)) if said else first_round + len(out),
                "verdict": m.group(1).lower(),
                "blocking": int(blocking.group(1)) if blocking else text.count("[blocking]"),
                "minor": int(minor.group(1)) if minor else text.count("[minor]"),
                # A second verdict from the same agent means the orchestrator continued it.
                "continued": bool(out),
            })
    return out


def review_metrics(agents: list, tickets: list, merged_prs: set) -> dict:
    """The review quality floor, from reviewer transcripts alone (so it holds with posting on or off)."""
    last_model = {tk["ticket"]: tk["worker_models"][-1] for tk in tickets if tk["worker_models"]}
    by_pr = {}
    for a in agents:
        if a["role"] != "reviewer":
            continue
        key = a.get("pr") or a["id"]
        pr = by_pr.setdefault(key, {"pr": a.get("pr"), "ticket": a.get("ticket"), "verdicts": []})
        pr["verdicts"] += review_verdicts(a["transcript"], a["round"])
    prs = []
    for pr in by_pr.values():
        v = sorted(pr["verdicts"], key=lambda x: x["round"])
        if not v:
            continue
        first = next((x for x in v if x["round"] == 1), None)
        rounds = {x["round"] for x in v}
        prs.append({
            "pr": pr["pr"], "ticket": pr["ticket"], "worker_model": last_model.get(pr["ticket"]),
            "rounds": len(rounds), "round1": first["verdict"] if first else None,
            "merged": pr["pr"] in merged_prs, "verdicts": v,
            # A round-1 approve with minors that merged with no second round: the minors rode along.
            "minors_merged_no_round2": bool(first and first["verdict"] == "approve" and first["minor"] and 2 not in rounds and pr["pr"] in merged_prs),
        })
    firsts = [p for p in prs if p["round1"]]
    changes = sum(1 for p in firsts if p["round1"] == "changes")
    later = [x for p in prs for x in p["verdicts"] if x["round"] >= 2]
    return {
        "prs": prs,
        "reviewed_prs": len(prs),
        "rounds_total": sum(p["rounds"] for p in prs),
        "rounds_per_pr": sum(p["rounds"] for p in prs) / len(prs) if prs else None,
        "round1_approvals": len(firsts) - changes,
        "round1_changes": changes,
        "round1_changes_rate": changes / len(firsts) if firsts else None,
        "minors_merged_no_round2": sum(1 for p in prs if p["minors_merged_no_round2"]),
        "continued_rounds": sum(1 for x in later if x["continued"]),
        "fresh_rounds": sum(1 for x in later if not x["continued"]),
    }


def percentile(xs: list, q: float):
    """Nearest-rank percentile; None for no data."""
    xs = sorted(xs)
    return xs[max(-(-len(xs) * q // 100) - 1, 0)] if xs else None


def worker_model_name(model) -> str:
    return model_family(model) or (re.search(r"opus|sonnet|haiku|fable|mythos", (model or "").lower()) or [model or "unknown"])[0]


def worker_metrics(agents: list, tickets: list, review: dict) -> dict:
    """Workers split by model: usage, requests, context and tool output, then reviews by the model that finished the ticket."""
    out = {}
    for a in agents:
        if a["role"] != "worker":
            continue
        m = out.setdefault(worker_model_name(a["model"]), {
            "workers": 0, "tokens": 0, "cost": 0.0, "requests": [], "contexts": [], "tool_output_tokens": []})
        m["workers"] += 1
        m["tokens"] += sum(a["tokens"].values())
        m["cost"] += sum(a["cost"].values())
        m["requests"].append(a["requests"])
        m["contexts"] += a["contexts"]
        m["tool_output_tokens"].append(a["tool_output_tokens"])
    for tk in tickets:
        if tk["worker_models"]:
            m = out[tk["worker_models"][-1]]
            m["tickets"] = m.get("tickets", 0) + 1
    result = {}
    for name, m in out.items():
        mine = [p for p in review["prs"] if p["worker_model"] == name]
        firsts = [p for p in mine if p["round1"]]
        result[name] = {
            "workers": m["workers"], "tickets": m.get("tickets", 0), "tokens": m["tokens"], "cost": m["cost"],
            "requests": sum(m["requests"]), "requests_median": statistics.median(m["requests"]),
            "context_median": statistics.median(m["contexts"]) if m["contexts"] else None,
            "context_p90": percentile(m["contexts"], 90),
            "tool_output_tokens": sum(m["tool_output_tokens"]),
            "tool_output_tokens_median": statistics.median(m["tool_output_tokens"]),
            "reviewed_prs": len(mine), "rounds": sum(p["rounds"] for p in mine),
            "round1_approvals": sum(1 for p in firsts if p["round1"] == "approve"),
            "round1_changes": sum(1 for p in firsts if p["round1"] == "changes"),
        }
    return result


def escalations(tickets: list) -> dict:
    """Tickets whose first worker ran Haiku and a later one ran something stronger."""
    hit = [tk["ticket"] for tk in tickets if "haiku" in tk["worker_models"] and tk["worker_models"][-1] != "haiku"]
    return {"count": len(hit), "tickets": hit}


def depth2_agents(main: Transcript, agents: list) -> list:
    """Agents a dispatched agent spawned itself, joined to the spawning agent by toolUseId.

    Plugin agents have no Agent tool, so any hit is a defect. A spawn call with no recorded
    subagent is still listed: it was tried."""
    metas = subagent_metas(main)
    sub = main.path.with_suffix("") / "subagents"
    out = []
    for a in agents:
        if not a["transcript"]:
            continue
        for _, use in a["transcript"].tool_uses("Agent"):
            name, meta = metas.get(use.get("id"), (None, {}))
            t = Transcript(sub / f"{name}.jsonl") if name else None
            inp = use.get("input") or {}
            out.append({
                "parent": a["id"], "parent_role": a["role"], "ticket": a.get("ticket"),
                "tool_use_id": use.get("id"), "agent": name[len("agent-"):] if name and name.startswith("agent-") else name,
                "subagent_type": inp.get("subagent_type") or meta.get("agentType"),
                "description": inp.get("description") or "",
                "tokens": sum(t.tokens.values()) if t else 0, "cost": sum(t.cost().values()) if t else 0.0,
            })
    return out


def windows(usage: list, passes: list, threshold: float, now: datetime) -> list:
    """Each 5-hour window the samples saw: its peak, and whether it reset with room left under pressure."""
    by_reset = {}
    for u in usage:
        reset = parse_ts(u.get("five_hour_resets_at"))
        if reset is None or u.get("five_hour") is None:
            continue
        w = by_reset.setdefault(iso(reset), {"resets_at": iso(reset), "peak": 0.0, "start": u["ts"]})
        w["peak"] = max(w["peak"], float(u["five_hour"]))
    out = []
    for w in sorted(by_reset.values(), key=lambda w: w["resets_at"]):
        start, reset = parse_ts(w["start"]), parse_ts(w["resets_at"])
        inside = [p for p in passes if start <= (parse_ts(p.get("ts")) or start) <= reset]
        # Pressure: a pass in this window had every slot full and takeable tickets waiting.
        pressure = any(p.get("free") == 0 and (p.get("takeable_waiting") or 0) > 0 for p in inside) if inside else None
        w["closed"] = reset <= now
        w["pressure"] = pressure
        w["underused"] = bool(w["closed"] and w["peak"] < threshold and pressure)
        out.append(w)
    return out


def limit_events(main: Transcript, agents: list, passes: list) -> list:
    out = []
    for p in passes:
        if p.get("limit_hit"):
            out.append({"ts": p.get("ts"), "source": "pass", "resets_at": p.get("reset_at"), "detail": p.get("scope") or ""})
    for t in [main] + [a["transcript"] for a in agents if a["transcript"]]:
        for ts, text in t.api_errors():
            out.append({"ts": iso(ts), "source": "transcript", "resets_at": None, "detail": text})
    out.sort(key=lambda e: e["ts"] or "")
    return out


def idle_reasons(passes: list, end: datetime) -> dict:
    """Idle slot-seconds by the reason each pass gave, held until the next pass."""
    out = {}
    for p, nxt in zip(passes, passes[1:] + [None]):
        s = parse_ts(p.get("ts"))
        e = parse_ts(nxt.get("ts")) if nxt else end
        if not s or not e or e <= s:
            continue
        for reason, count in (p.get("idle") or {}).items():
            out[reason] = out.get(reason, 0) + count * (e - s).total_seconds()
    return out


def recommend(slots: int, occupancy: float, wins: list, limits: list) -> str | None:
    if limits or any(w["peak"] >= 100 for w in wins):
        return f"Try slots {slots} → {slots - 1}: usage limits hit." if slots > 1 else None
    if occupancy >= 0.8 and any(w["underused"] for w in wins):
        return f"Try slots {slots} → {slots + 1}: slots were full and a window reset with room left." if slots < 5 else None
    return None


def profile(transcript: Path, repo: Path | None, slots: int | None, threshold: float, now: datetime) -> dict:
    main = Transcript(transcript)
    session = transcript.stem
    repo = repo or repo_of(main)
    passes = read_passes(repo, session)
    agents = collect_agents(main)
    end = main.times[-1] if main.times else now
    starts = [a["dispatched"] for a in agents if a["dispatched"]] + [parse_ts(p.get("ts")) for p in passes]
    starts = [s for s in starts if s]
    start = min(starts) if starts else (main.times[0] if main.times else now)
    tickets = build_tickets(agents, main, end)
    assign_slots(tickets)
    peak = max([tk["slot"] + 1 for tk in tickets] or [1])
    slots = slots or next((p["slots"] for p in reversed(passes) if p.get("slots")), None) or config_slots(repo) or peak
    # More tickets ran at once than the count allows (an older config, or lanes): the session's own peak is the truth.
    slots_raised = peak > slots
    slots = max(slots, peak)
    span = max((end - start).total_seconds(), 1.0)
    occupied = sum(seconds(clip([[tk["start"], tk["end"]]], start, end)) for tk in tickets)
    building = sum(seconds(tk["building"]) for tk in tickets)
    usage = read_usage(start, end)
    wins = windows(usage, passes, threshold, now)
    limits = limit_events(main, agents, passes)
    review = review_metrics(agents, tickets, set(ticket_endings(main)[0]))
    depth2 = depth2_agents(main, agents)
    idle_total = max(slots * span - occupied, 0.0)
    idle = idle_reasons(passes, end)
    idle["unknown"] = max(idle_total - sum(idle.values()), 0.0)

    by_role, by_model = {}, {}
    orch = {"role": "orchestrator", "model": main.models[0] if main.models else None, "tokens": main.tokens}
    for a in [orch] + agents:
        for bucket, key in ((by_role, a["role"]), (by_model, a["model"] or "unknown")):
            acc = bucket.setdefault(key, {v: 0 for v in TOKEN_KEYS.values()})
            for k, v in a["tokens"].items():
                acc[k] += v
    total = sum(sum(t.values()) for t in by_role.values())
    reads = sum(t["cache_read"] for t in by_role.values())
    fresh = sum(t["input"] + t["cache_write"] for t in by_role.values())
    merged = [tk for tk in tickets if tk["outcome"] == "merged"]
    occupancy = occupied / (slots * span)
    five = [float(u["five_hour"]) for u in usage if u.get("five_hour") is not None]
    cost = cost_metrics(main, agents, len(merged))
    # Agents can write after the orchestrator's last record (or the session is still running).
    last = max([end] + [a["last_record"] for a in agents if a["last_record"]])
    warnings = []
    if last > end:
        warnings.append(f"the last transcript record ({iso(last)}) is later than the profile's end ({iso(end)}): this profile is partial")
    if cost["unpriced_models"]:
        warnings.append("no price for model(s) " + ", ".join(cost["unpriced_models"]) + ": their cost counts as $0")
    if cost["approximate_models"]:
        warnings.append("version not in PRICES for " + ", ".join(cost["approximate_models"]) + ": priced at the nearest older version's rates")
    if depth2:
        warnings.append(f"{len(depth2)} agent(s) spawned by another agent (depth 2): plugin agents must not delegate further")
    meters = {}
    for key in ("five_hour", "seven_day"):
        used = meter_use(usage, key)
        meters[key] = {"used": used, "per_merged_ticket": used / len(merged) if used is not None and merged else None}

    def interval(xs):
        return [[iso(s), iso(e)] for s, e in xs]

    return {
        "schema": SCHEMA,
        "session": session,
        "repo": str(repo) if repo else None,
        "generated_at": iso(now),
        "start": iso(start),
        "end": iso(end),
        "last_record": iso(last),
        "warnings": warnings,
        "slots": slots,
        "slots_raised": slots_raised,
        "threshold": threshold,
        "sources": {"pass_log": bool(passes), "usage_tap": bool(usage)},
        "orchestrator": {"model": orch["model"], "tokens": main.tokens, "compactions": main.compactions()},
        "agents": [
            {
                "id": a["id"], "role": a["role"], "model": a["model"], "description": a["description"],
                "ticket": a.get("ticket"), "pr": a.get("pr"), "dispatched": iso(a["dispatched"]),
                "segments": interval(a["segments"]), "tokens": a["tokens"], "cost": a["cost"],
                "requests": a["requests"], "context_median": statistics.median(a["contexts"]) if a["contexts"] else None,
                "tool_output_tokens": a["tool_output_tokens"], "round": a["round"] if a["role"] == "reviewer" else None,
            }
            for a in agents
        ],
        "tickets": [
            {
                "ticket": tk["ticket"], "pr": tk["pr"], "outcome": tk["outcome"], "slot": tk["slot"],
                "start": iso(tk["start"]), "end": iso(tk["end"]), "building": interval(tk["building"]),
                "tokens": tk["tokens"], "cost": tk["cost"], "worker_models": tk["worker_models"],
                "gate_runs": tk["gate_runs"],
            }
            for tk in tickets
        ],
        "passes": passes,
        "usage": usage,
        "limits": limits,
        "metrics": {
            "span_s": span,
            "occupancy": occupancy,
            "building_share": building / (slots * span),
            "review_share": max(occupied - building, 0.0) / (slots * span),
            "idle_s": idle,
            "tokens_total": total,
            "tokens_by_role": by_role,
            "tokens_by_model": by_model,
            "orchestrator_share": sum(main.tokens.values()) / total if total else 0.0,
            "cache_hit_ratio": reads / (reads + fresh) if reads + fresh else 0.0,
            "tickets_merged": len(merged),
            "tokens_per_merged_ticket": sum(tk["tokens"] for tk in merged) / len(merged) if merged else None,
            "five_hour_start": five[0] if five else None,
            "five_hour_end": five[-1] if five else None,
            "five_hour_peak": max(five) if five else None,
            "windows": wins,
            "limit_hits": len(limits),
            "cost": cost,
            "meters": meters,
            "review": review,
            "worker_models": worker_metrics(agents, tickets, review),
            "escalations": escalations(tickets),
            "depth2": depth2,
        },
        "recommendation": recommend(slots, occupancy, wins, limits),
    }


def fmt_tokens(n: float) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k"


def fmt_dur(s: float) -> str:
    return f"{s / 3600:.1f}h" if s >= 3600 else f"{s / 60:.0f}m"


def fmt_usd(x) -> str:
    return "–" if x is None else f"${x:.2f}"


def summary(p: dict, saved: Path) -> str:
    """About five lines: header, $ per merged ticket, slots, plan, saved. Notes and warnings add lines only when they apply."""
    m = p["metrics"]
    c = m["cost"]
    idle = ", ".join(f"{k} {fmt_dur(v)}" for k, v in sorted(m["idle_s"].items()) if v >= 60) or "none"
    if m["five_hour_start"] is None:
        plan = "no usage samples (status-line tap not wired in)"
    else:
        plan = f"5h window {m['five_hour_start']:.0f}% → {m['five_hour_end']:.0f}%, peak {m['five_hour_peak']:.0f}%"
        used = m["meters"]["five_hour"]["per_merged_ticket"]
        week = m["meters"]["seven_day"]["per_merged_ticket"]
        if used is not None:
            plan += f", {used:.1f} pts per merged ticket" + (f" (7d {week:.1f})" if week is not None else "")
    plan += f" · {m['limit_hits']} limit hit{'s' if m['limit_hits'] != 1 else ''}"
    by_model = " · ".join(
        f"{k} {fmt_usd(v)}" for k, v in sorted(c["per_merged_ticket_by_model"].items(), key=lambda kv: -(kv[1] or 0)) if v)
    if c["per_merged_ticket"] is None:
        spend = f"no tickets merged; {fmt_usd(c['total'])} proxy in all"
    else:
        spend = f"{fmt_usd(c['per_merged_ticket'])} per merged ticket ({m['tickets_merged']} merged): {by_model}"
        orch = c["per_merged_ticket_by_role"].get("orchestrator")
        spend += f" · orchestrator {fmt_usd(orch)}"
    lines = [
        f"Profile · session {p['session'][:8]} · {p['start'][:16]}Z → {p['end'][:16]}Z ({fmt_dur(m['span_s'])}) · {p['slots']} slots",
        f"proxy $ {spend}",
        f"slots   occupied {m['occupancy']:.0%}  (building {m['building_share']:.0%}, review/merge {m['review_share']:.0%})  idle: {idle}",
        f"plan    {plan}",
    ]
    for w in p["warnings"]:
        lines.append(f"WARNING {w}")
    if p["recommendation"]:
        lines.append(f"advice  {p['recommendation']}")
    if p.get("slots_raised"):
        lines.append(f"note    up to {p['slots']} tickets ran at once, more than the slot count; occupancy is against {p['slots']}")
    if not p["sources"]["pass_log"]:
        lines.append("note    no pass log for this session: idle reasons and window pressure are unknown")
    lines.append(f"saved   {saved}  (+ .html)")
    return "\n".join(lines)


def write_html(data: dict, path: Path) -> None:
    viewer = VIEWER.read_text()
    # `</` can't appear inside a script element; JSON allows escaping the slash.
    payload = json.dumps(data).replace("</", "<\\/")
    marker = '<script id="profile-data" type="application/json">null</script>'
    path.write_text(viewer.replace(marker, f'<script id="profile-data" type="application/json">{payload}</script>'))


def cmd_profile(argv: list) -> None:
    ap = argparse.ArgumentParser(prog="profile.py")
    ap.add_argument("session", help="session ID or transcript path")
    ap.add_argument("--repo", type=Path, help="repo the session ran in (default: the transcript's cwd)")
    ap.add_argument("--slots", type=int, help="slot count (default: the pass log, then the config)")
    ap.add_argument("--threshold", type=float, default=50.0, help="a 5h window resetting below this %% is underused")
    ap.add_argument("--out", type=Path, help="directory to save into (default: <repo>/.claude/orchestrate-profiles)")
    ap.add_argument("--quiet", action="store_true", help="save without printing the summary")
    args = ap.parse_args(argv)
    transcript = find_transcript(args.session)
    now = datetime.now(timezone.utc)
    data = profile(transcript, args.repo, args.slots, args.threshold, now)
    out = args.out or (Path(data["repo"]) / PROFILES if data["repo"] else Path.cwd())
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{data['start'][:10]}-{data['session'][:8]}.json"
    path.write_text(json.dumps(data, indent=1))
    write_html(data, path.with_suffix(".html"))
    if not args.quiet:
        print(summary(data, path))


def cmd_record(argv: list) -> None:
    ap = argparse.ArgumentParser(prog="profile.py record")
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("pass_json", help="the pass as JSON (see skills/run/SKILL.md, Profiling)")
    args = ap.parse_args(argv)
    try:
        entry = json.loads(args.pass_json)
    except ValueError as e:
        sys.exit(f"profile record: not JSON: {e}")
    if not isinstance(entry, dict):
        sys.exit("profile record: expected a JSON object")
    entry = {"ts": iso(datetime.now(timezone.utc)), "session": args.session, **entry}
    log = args.repo / PROFILES / PASS_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main(argv: list) -> None:
    if argv and argv[0] == "record":
        cmd_record(argv[1:])
    else:
        cmd_profile(argv)


if __name__ == "__main__":
    main(sys.argv[1:])
