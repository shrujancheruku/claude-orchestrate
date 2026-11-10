"""Profile an orchestrator session: slot efficiency, token use and plan headroom.

    profile.py <session id | transcript.jsonl> [--repo PATH] [--slots N] [--threshold PCT] [--out DIR] [--quiet]
    profile.py record --repo PATH --session ID '<pass json>'
    profile.py compare <before.json> <after.json> [--max-cost USD] [--max-orchestrator-cost USD]

The first form reads the session's transcript and its subagents' transcripts,
the repo's pass log and, if one exists, `usage.jsonl` (the plugin writes none, so
plan headroom is normally not measured), then writes
`<repo>/.claude/orchestrate-profiles/<date>-<session>.json` and a `.html` copy
of the viewer with that profile embedded, and prints a summary. The second
form appends one pass to the pass log; `/orchestrate:run --profile` calls it
at the end of each pass. The third prints two saved profiles side by side and
says whether the later one is a conclusive trial. Everything here only reads, apart from those files.
"""

# macOS ships Python 3.9, which needs this for `str | None` annotations.
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

# `python3 -I` leaves the script's directory off sys.path. Append it (never insert): this file is named
# profile.py, so putting its directory first would shadow the stdlib `profile` module.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)
import common  # noqa: E402
import config  # noqa: E402
import runlog  # noqa: E402
import session  # noqa: E402
from repo import iso, parse_ts  # noqa: E402
from session import TOKEN_KEYS, Transcript  # noqa: E402

SCHEMA = 3
PROFILES = runlog.PROFILES  # saved profiles sit beside the run log
VIEWER = Path(__file__).resolve().parent.parent / "viewer" / "profile.html"
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


def usage_log() -> Path:
    return Path(os.environ.get("ORCHESTRATE_USAGE_LOG") or common.claude_dir() / "orchestrate" / "usage.jsonl")


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



def find_transcript(arg: str) -> Path:
    found = common.find_transcript(arg)
    if not found:
        common.die(f"profile: no transcript for session {arg} under {common.claude_dir() / 'projects'}")
    return found


def repo_of(main: Transcript) -> Path | None:
    for r in main.records:
        if r.get("cwd"):
            return Path(r["cwd"])
    return None


def config_slots(repo: Path | None) -> int | None:
    return config.for_checkout(repo).slots if repo else None


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


def dispatched_round(text: str) -> int:
    """The review round a reviewer was dispatched for: "Tier M, round 2" first, else the last "round N" in the brief."""
    m = re.search(r"\btier\s*[SML],?\s*round[:\s#]*(\d+)", text, re.I)
    if m:
        return int(m.group(1))
    found = re.findall(r"\bround[:\s#]*(\d+)", text, re.I)
    return int(found[-1]) if found else 1


def dispatched_tier(text: str) -> str | None:
    """The review tier a reviewer was dispatched at: "Tier: L", "Tier M", else a "(L)" or "(L, proto)" in the description."""
    m = re.search(r"\btier[:\s]+([SML])\b", text, re.I) or re.search(r"\(([SML])[,)]", text)
    return m.group(1).upper() if m else None


def cost_of(t: Transcript) -> dict:
    """Proxy USD by model family for one transcript; requests from a model with no price count under `unpriced`."""
    out = {}
    for model, u in t.requests.values():
        c = request_cost(model, u)
        key = model_family(model) if c is not None else "unpriced"
        out[key] = out.get(key, 0.0) + (c or 0.0)
    return out


def collect_agents(main: Transcript) -> list:
    """Every orchestrate agent the session dispatched (session.agents), with what only the profile reads: the
    review round and tier, and the proxy cost."""
    agents = session.agents(main)
    for a in agents:
        a["round"] = dispatched_round(f"{a['description']} {a['prompt']}")  # the review round a reviewer was dispatched for
        a["tier"] = dispatched_tier(f"{a['prompt']} {a['description']}") if a["role"] == "reviewer" else None
        a["cost"] = cost_of(a["transcript"]) if a["transcript"] else {}
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


# `gh pr merge <n>`, or the plugin's merge script (any path to scripts/merge, quoted or not) invoked with <n>.
MERGE_CMD = re.compile(r"\bgh\s+pr\s+merge\s+(\d+)|scripts/merge[\"']?\s+(\d+)")


def ticket_endings(main: Transcript, events: list | None = None) -> tuple:
    """When each PR merged, when each ticket was handed back, and which merges are unverified.

    With an events log, only a `merge` event with outcome `merged` counts: scripts/merge writes it after
    re-checking that the PR is MERGED. Without one (an older plugin), the transcript's merge commands stand in,
    errored calls included, and those merges are unverified."""
    merged, handed_back, unverified = {}, {}, set()
    if events is not None:
        for e in events:
            ts = parse_ts(e.get("ts"))
            if e.get("kind") == runlog.MERGE and e.get("outcome") == runlog.MERGED and ts and str(e.get("ref", "")).isdigit():
                merged.setdefault(int(e["ref"]), ts)
    for ts, use in main.tool_uses("Bash"):
        cmd = (use.get("input") or {}).get("command", "")
        if events is None:
            for m in MERGE_CMD.finditer(cmd):
                pr = int(m.group(1) or m.group(2))
                if pr not in merged:
                    merged[pr] = ts
                    unverified.add(pr)
        for m in re.finditer(r"\bgh\s+issue\s+edit\s+(\d+)[^&;|]*--remove-assignee", cmd):
            handed_back.setdefault(int(m.group(1)), ts)
    return merged, handed_back, unverified


def build_tickets(agents: list, main: Transcript, end: datetime, events: list | None = None) -> list:
    merged, handed_back, unverified = ticket_endings(main, events)
    tickets = {}
    for a in agents:
        if a["role"] != "worker":
            continue
        n = common.ticket_of(a["description"])
        if n is None:
            continue
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
            ended, outcome = merged[tk["pr"]], runlog.MERGED
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
            "worker_models": [worker_model_name(w["worker_model"]) for w in tk["workers"]],
            # Filled from events.jsonl in profile(); None when there is no events log.
            "gate_runs": None,
            "gate_failed": None,
            "pr": tk["pr"],
            "outcome": outcome,
            "merge_unverified": outcome == "merged" and tk["pr"] in unverified,
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


def slot_segments(passes: list, tickets: list, start: datetime, end: datetime, forced: int | None, fallback: int | None) -> tuple:
    """Slot capacity over time as [(from, to, slots)], and whether tickets ever ran at once beyond the slots in force.

    Each pass's own `slots` holds from its timestamp until the next pass's. `forced` (--slots) overrides them all.
    With no count anywhere, `fallback` (the config) applies, then the session's peak concurrency."""
    # A pass logged after the last record (the run builds the profile at the end of the pass that just ran) still sets
    # the count in force. It is clamped to `end`, so it adds no capacity and no negative segment, but it is the final count.
    cuts = [(min(parse_ts(p.get("ts")), end), int(p["slots"])) for p in passes if p.get("slots") and parse_ts(p.get("ts"))]
    cuts = sorted(cuts, key=lambda c: c[0])
    if forced:
        cuts = [(start, forced)]
    elif not cuts and fallback:
        cuts = [(start, fallback)]
    elif cuts:
        cuts[0] = (min(cuts[0][0], start), cuts[0][1])  # the first pass's count holds before it too
    bounds = [(c[0], cuts[i + 1][0] if i + 1 < len(cuts) else end, c[1]) for i, c in enumerate(cuts)]
    if not bounds:
        bounds = [(start, end, None)]
    out, raised = [], False
    for a, b, n in bounds:
        edges = []
        for tk in tickets:
            for s0, s1 in clip([[tk["start"], tk["end"]]], a, b):
                edges += [(s0, 1), (s1, -1)]
        now = conc = 0
        for _, d in sorted(edges, key=lambda e: (e[0], e[1])):  # an end sorts before a start at the same instant
            now += d
            conc = max(conc, now)
        if n is None:
            n = max(conc, 1)
        elif conc > n:
            raised, n = True, conc
        out.append((a, b, n))
    return out, raised


def read_usage(start: datetime, end: datetime) -> list:
    out = []
    for u in common.read_jsonl(usage_log()):
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
    for role, cost in [("orchestrator", cost_of(orch))] + [(a["role"], a["cost"]) for a in agents]:
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


# Two shapes: a line "verdict: approve" / "round: 1", or the posted review's header
# "Round 1 · verdict: approve · tier: L" (agents/reviewer.md, Body).
VERDICT = re.compile(r"(?:^|·)\W*verdict:\s*(approve|changes)\b", re.I | re.M)
ROUND_LINE = re.compile(r"^\W*round(?::\s*|\s+)(\d+)", re.I | re.M)
# A finding line, once: in a posted review the inline `comments` repeat the body's findings.
FINDING = re.compile(r"^\s*(?:[-*]|\d+\.)\s*\[(blocking|minor)\]", re.I | re.M)


def review_body(text: str) -> str:
    """A posted review is handed back as the JSON that was posted; what counts is its body."""
    # The JSON may follow some prose: take the first "{" that starts an object with a string `body`.
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        # Only a body with the verdict in it: a malformed outer payload must not fall through to an inline comment's own `body`.
        if isinstance(obj, dict) and isinstance(obj.get("body"), str) and VERDICT.search(obj["body"]):
            return obj["body"]
    return text


def review_verdicts(t: Transcript | None, first_round: int) -> list:
    """The verdicts a reviewer returned, in order, whether it posted (short report) or not (full block).

    Only the reviewer's own text counts, not the commands it ran, so a posted review isn't counted
    twice. A report names its round; a full verdict doesn't, so the dispatch's round numbers
    the first verdict and each later one (the reviewer was continued) follows on."""
    out = []
    # A reviewer that ends with SubagentHandback returns its verdict in the call, not in a text block;
    # when it does, those messages are the verdicts and any text blocks are thinking aloud.
    texts, handbacks = [], []
    for r in t.records if t else []:
        if r.get("type") != "assistant":
            continue
        for b in common.blocks(r):
            if b.get("type") == "text":
                texts.append(b.get("text") or "")
            elif b.get("type") == "tool_use" and b.get("name") == "SubagentHandback":
                handbacks.append(str((b.get("input") or {}).get("message") or ""))
    for text in handbacks or texts:
        text = review_body(text)
        m = VERDICT.search(text)
        if not m:
            continue
        said = ROUND_LINE.search(text)
        minor = re.search(r"^\W*non-blocking:\s*(\d+)", text, re.I | re.M)
        blocking = re.search(r"^\W*blocking:\s*(\d+)", text, re.I | re.M)
        out.append({
            "round": int(said.group(1)) if said else first_round + len(out),
            "verdict": m.group(1).lower(),
            "blocking": int(blocking.group(1)) if blocking else len([f for f in FINDING.findall(text) if f.lower() == "blocking"]),
            "minor": int(minor.group(1)) if minor else len([f for f in FINDING.findall(text) if f.lower() == "minor"]),
            # A second verdict from the same agent means the orchestrator continued it.
            "continued": bool(out),
        })
    return out


def review_metrics(agents: list, tickets: list, merged_prs: set) -> dict:
    """The review quality floor, from reviewer transcripts alone (so it holds with posting on or off)."""
    last_model = {tk["ticket"]: tk["worker_models"][-1] for tk in tickets if tk["worker_models"]}
    first_model = {tk["ticket"]: tk["worker_models"][0] for tk in tickets if tk["worker_models"]}
    by_pr = {}
    unparsed = 0  # reviewer agents that returned no verdict the parser could read: a parser miss shows here
    for a in agents:
        if a["role"] != "reviewer":
            continue
        key = a.get("pr") or a["id"]
        pr = by_pr.setdefault(key, {"pr": a.get("pr"), "ticket": a.get("ticket"), "verdicts": [], "tier": None})
        # The PR's tier is the first one a reviewer was dispatched at.
        pr["tier"] = pr["tier"] or a.get("tier")
        # A second reviewer whose brief parses to a round already seen follows the highest one.
        seen = {v["round"] for v in pr["verdicts"]}
        first = a["round"] if a["round"] not in seen else max(seen) + 1
        got = review_verdicts(a["transcript"], first)
        unparsed += not got
        pr["verdicts"] += got
    prs = []
    for pr in by_pr.values():
        v = sorted(pr["verdicts"], key=lambda x: x["round"])
        if not v:
            continue
        first = next((x for x in v if x["round"] == 1), None)
        rounds = {x["round"] for x in v}
        prs.append({
            "pr": pr["pr"], "ticket": pr["ticket"], "tier": pr["tier"], "worker_model": last_model.get(pr["ticket"]), "first_worker_model": first_model.get(pr["ticket"]),
            "rounds": len(rounds), "round1": first["verdict"] if first else None,
            "merged": pr["pr"] in merged_prs, "verdicts": v,
            # A round-1 approve with minors that merged with no second round: the minors rode along.
            # A round-1 approve with nothing to take or leave: the "outright" approvals of the quality floor.
            "round1_outright": bool(first and first["verdict"] == "approve" and not first["minor"]),
            "minors_merged_no_round2": bool(first and first["verdict"] == "approve" and first["minor"] and 2 not in rounds and pr["pr"] in merged_prs),
        })
    firsts = [p for p in prs if p["round1"]]
    changes = sum(1 for p in firsts if p["round1"] == "changes")
    later = [x for p in prs for x in p["verdicts"] if x["round"] >= 2]
    return {
        "prs": prs,
        "reviewed_prs": len(prs),
        "unparsed_reviewers": unparsed,
        "rounds_total": sum(p["rounds"] for p in prs),
        "rounds_per_pr": sum(p["rounds"] for p in prs) / len(prs) if prs else None,
        "round1_approvals": len(firsts) - changes,
        "round1_changes": changes,
        "round1_changes_rate": changes / len(firsts) if firsts else None,
        "round1_outright": sum(1 for p in firsts if p["round1_outright"]),
        "round1_outright_rate": sum(1 for p in firsts if p["round1_outright"]) / len(firsts) if firsts else None,
        "minors_merged_no_round2": sum(1 for p in prs if p["minors_merged_no_round2"]),
        "continued_rounds": sum(1 for x in later if x["continued"]),
        "fresh_rounds": sum(1 for x in later if not x["continued"]),
    }


def tier_metrics(tickets: list, review: dict) -> dict:
    """Per review tier: merged tickets and their $, and the round-1 and rounds figures of the tier's reviewed PRs.

    `compare` reweights these by a baseline's tier mix, so a trial that happened to draw more L tickets isn't
    judged cheaper or dearer for that alone. A ticket with no reviewer has no tier ("?")."""
    out = {}

    def tier(name):
        return out.setdefault(name or "?", {"merged": 0, "cost": 0.0, "reviewed_prs": 0, "rounds": 0,
                                            "round1": 0, "round1_changes": 0, "round1_outright": 0})
    for tk in tickets:
        if tk["outcome"] == "merged":
            t = tier(tk["tier"])
            t["merged"] += 1
            t["cost"] += tk["cost"]["total"]
    for p in review["prs"]:
        t = tier(p["tier"])
        t["reviewed_prs"] += 1
        t["rounds"] += p["rounds"]
        if p["round1"]:
            t["round1"] += 1
            t["round1_changes"] += p["round1"] == "changes"
            t["round1_outright"] += p["round1_outright"]
    return out


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
        m = out.setdefault(worker_model_name(a["worker_model"]), {
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
        # Round 1 is the first worker's work, so an escalated ticket's round 1 is credited to the model that started it.
        firsts = [p for p in review["prs"] if p["round1"] and p["first_worker_model"] == name]
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
    metas = session.subagent_metas(main)
    out = []
    for a in agents:
        if not a["transcript"]:
            continue
        for _, use in a["transcript"].tool_uses("Agent"):
            name, meta = metas.get(use.get("id"), (None, {}))
            t = main.subagent(name) if name else None
            inp = use.get("input") or {}
            out.append({
                "parent": a["id"], "parent_role": a["role"], "ticket": a.get("ticket"),
                "tool_use_id": use.get("id"), "agent": name[len("agent-"):] if name and name.startswith("agent-") else name,
                "subagent_type": inp.get("subagent_type") or meta.get("agentType"),
                "description": inp.get("description") or "",
                "tokens": sum(t.tokens.values()) if t else 0, "cost": sum(cost_of(t).values()) if t else 0.0,
            })
    return out


# What an orchestrator turn was spent on. Keyed by the tool and, for Bash, the plugin script it runs;
# the regexes below are only the fallback for hand-run commands. `review_posting` also holds the
# script gate, which runs just before a reviewer and is part of getting a PR reviewed.
JOBS = ["ci_wait", "review_posting", "state_file", "merge_cleanup", "dispatch", "send_message", "tracker",
        "end_of_wake", "no_tool_call", "other"]
JOB_TOOLS = {"Monitor": "ci_wait", "Agent": "dispatch", "SendMessage": "send_message"}
JOB_SCRIPTS = {"watch-ci": "ci_wait", "gate": "review_posting", "rebuild-state": "state_file", "merge": "merge_cleanup"}
SCRIPT_CALL = re.compile(r"(?:^|[\s/\"'])scripts/(watch-ci|gate|rebuild-state|merge|verify)(?![\w.-])")
JOB_REGEXES = [
    ("merge_cleanup", re.compile(r"\bgh\s+pr\s+merge\b|\bgit\s+worktree\s+(?:remove|prune)\b|\bgit\s+branch\s+-[dD]\b|\bgit\s+push\s+\S+\s+(?:--delete|:)")),
    ("ci_wait", re.compile(r"\bsleep\s+\d|\bgh\s+pr\s+checks\b|\bgh\s+run\s+(?:watch|view|list)\b")),
    ("review_posting", re.compile(r"\bgh\s+pr\s+(?:review|diff)\b|\bgh\s+pr\s+comment\b|\bgh\s+api\s+\S*pulls/\d+/(?:reviews|comments)")),
    ("state_file", re.compile(r"orchestrate-state\.json")),
    ("tracker", re.compile(r"\bgh\s+(?:issue|label|pr\s+(?:view|list)|api)\b")),
]


def classify_call(name: str, inp: dict):
    """The job one tool call belongs to, or None when it is not one of the named jobs."""
    if name in JOB_TOOLS:
        return JOB_TOOLS[name]
    if name in ("Read", "Write", "Edit", "NotebookEdit") and str(inp.get("file_path") or "").endswith("orchestrate-state.json"):
        return "state_file"
    if name == "Bash":
        cmd = str(inp.get("command") or "")
        m = SCRIPT_CALL.search(cmd)
        if m and m.group(1) in JOB_SCRIPTS:
            return JOB_SCRIPTS[m.group(1)]
        for job, rx in JOB_REGEXES:
            if rx.search(cmd):
                return job
    return None


def classify_turn(turn: dict) -> str:
    """One job per turn: the first tool call that is a named job, else `other`; a turn with no call is
    end-of-wake text when it says anything and `no_tool_call` when it doesn't."""
    if not turn["tools"]:
        return "end_of_wake" if turn["text"].strip() else "no_tool_call"
    return next((j for j in (classify_call(n, i) for n, i in turn["tools"]) if j), "other")


def user_text(record) -> str:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


AGENT_MESSAGE = re.compile(r'<agent-message from="(\w+)">')


def handback_tokens(main: Transcript, agents: list) -> dict:
    """What each role's reports put into the orchestrator's context, estimated at 4 characters a token.

    A background agent's report arrives as a user message holding `<agent-message from="<agentId>">`;
    every such message counts once (a continued agent sends several). The completion notice that names
    the call by `tool-use-id` carries only a stub, so it is not counted. A foreground agent's report is
    the Agent call's own result."""
    role_of_id = {a["id"]: a["role"] for a in agents if a["id"]}
    role_of_use = {a["tool_use_id"]: a["role"] for a in agents}
    out = {}

    def add(role, text):
        e = out.setdefault(role, {"messages": 0, "chars": 0})
        e["messages"] += 1
        e["chars"] += len(text)

    for use_id, text in main.tool_results().items():
        if use_id in role_of_use:
            add(role_of_use[use_id], text)
    for r in main.records:
        if r.get("type") != "user":
            continue
        text = user_text(r)
        tags = list(AGENT_MESSAGE.finditer(text))
        for i, m in enumerate(tags):
            if m.group(1) in role_of_id:
                add(role_of_id[m.group(1)], text[m.end(): tags[i + 1].start() if i + 1 < len(tags) else len(text)])
    return {role: {"messages": e["messages"], "tokens": e["chars"] // 4} for role, e in sorted(out.items())}


def idle_wakes(passes: list, agents: list, merge_times) -> dict:
    """Passes that dispatched, reviewed and merged nothing. A pass covers the time since the one before it."""
    acted = sorted(t for t in [a["dispatched"] for a in agents if a["role"] in ("worker", "reviewer")] + list(merge_times) if t)
    rows = sorted((parse_ts(p.get("ts")), i) for i, p in enumerate(passes) if parse_ts(p.get("ts")))
    idle, prev = 0, None
    for ts, i in rows:
        p = passes[i]
        # Anything acted on in (prev, ts].
        did = bisect_right(acted, ts) - (bisect_right(acted, prev) if prev else 0) > 0
        if not (p.get("dispatched") or p.get("merged") or did):
            idle += 1
        prev = ts
    return {"passes": len(rows), "idle": idle}


def orchestrator_metrics(main: Transcript, agents: list, passes: list, merged: int, events: list | None = None) -> dict:
    """The orchestrator's turns by job, and the context they ran in."""
    turns = []
    for t in main.turns():
        usage = t["usage"]
        turns.append({
            "ts": iso(t["ts"]), "job": classify_turn(t),
            "context": sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
            "usd": request_cost(t["model"], usage) or 0.0,
        })
    per = (lambda x: x / merged) if merged else (lambda x: None)
    by_job = {}
    for job in JOBS:
        mine = [t for t in turns if t["job"] == job]
        usd = sum(t["usd"] for t in mine)
        by_job[job] = {"turns": len(mine), "usd": usd, "turns_per_merged_ticket": per(len(mine)), "usd_per_merged_ticket": per(usd)}
    compactions = main.compaction_details()
    autos = [c["pre_tokens"] for c in compactions if c["trigger"] == "auto" and c["pre_tokens"]]
    own_text = sum(len(b.get("text") or "") for r in main.records if r.get("type") == "assistant" for b in common.blocks(r) if b.get("type") == "text")
    results = main.tool_results()
    bash_out = sum(len(results.get(use.get("id"), "")) for _, use in main.tool_uses("Bash"))
    return {
        "jobs": {"by_job": by_job, "turns": len(turns), "usd": sum(t["usd"] for t in turns)},
        "context": {
            "turns": [{"ts": t["ts"], "context": t["context"], "job": t["job"]} for t in turns],
            "compactions": compactions,
            # Auto compactions fire near the window the session really had, whatever the setting says.
            "effective_window": statistics.median(autos) if autos else None,
            "handback_tokens": handback_tokens(main, agents),
            "own_text_tokens": own_text // 4,
            "bash_output_tokens": bash_out // 4,
            "estimate": "chars / 4",
            "idle_wakes": idle_wakes(passes, agents, ticket_endings(main, events)[0].values()),
        },
    }


def count_by_outcome(events: list) -> dict:
    out = {}
    for e in events:
        out[str(e.get("outcome"))] = out.get(str(e.get("outcome")), 0) + 1
    return out


def event_metrics(events: list | None, review: dict) -> dict:
    """Script facts: gate runs (pass|fail|unknown), CI runs, merges and the shared-file replay hand-back rate."""
    if events is None:
        return {"available": False}
    of = lambda kind: [e for e in events if e.get("kind") == kind]
    gate, replay = of(runlog.GATE), of(runlog.REPLAY)
    gate_out = count_by_outcome(gate)
    failed_checks = {}
    for e in gate:
        for name in e.get("failed") or []:
            failed_checks[name] = failed_checks.get(name, 0) + 1
    # The replay outcomes (line-replay, word-merge, regenerated, handed-back) are scripts/merge's `replay`
    # events; with none logged the rate reads "–".
    handed = sum(1 for e in replay if e.get("outcome") == runlog.HANDED_BACK)
    return {
        "available": True,
        # A gate failure goes back to the worker without a review round: counted here, never in `review`.
        "gate": {"runs": len(gate), "pass": gate_out.get(runlog.PASS, 0), "fail": gate_out.get(runlog.FAIL, 0), "unknown": gate_out.get(runlog.UNKNOWN, 0),
                 "failed_checks": failed_checks, "review_rounds": review["rounds_total"]},
        "ci": {"runs": len(of(runlog.CI)), "by_outcome": count_by_outcome(of(runlog.CI))},
        # scripts/merge writes merged|timeout|closed; only `merged` is a merge.
        "merges": count_by_outcome(of(runlog.MERGE)).get(runlog.MERGED, 0),
        "merges_by_outcome": count_by_outcome(of(runlog.MERGE)),
        "replay": {"events": len(replay), "by_outcome": count_by_outcome(replay), "handed_back": handed,
                   "handback_rate": handed / len(replay) if replay else None},
    }


def peak_per_minute(times: list) -> int:
    """The most events in any 60-second span of the sorted datetimes `times`."""
    peak, lo = 0, 0
    for hi, t in enumerate(times):
        while (t - times[lo]).total_seconds() >= 60:
            lo += 1
        peak = max(peak, hi - lo + 1)
    return peak


def gh_block(events: list) -> dict:
    """The counts for one stretch of `gh_call` events: attempts (a retry is its own attempt), writes, GraphQL,
    attempts by script, retries (attempts after the first), rate-limited replies, the lowest `remaining` seen,
    the busiest minute of writes, the seconds slept (of which `shared` waits for another process's hit), and the
    GraphQL points spent (the `cost` the helper records on a GraphQL call; a call without one adds nothing)."""
    calls = [e for e in events if e.get("outcome") != "wait"]
    waits = [e for e in events if e.get("outcome") == "wait"]
    by_script = {}
    for e in calls:
        name = str(e.get("script") or "?")
        by_script[name] = by_script.get(name, 0) + 1
    remaining = [e["remaining"] for e in calls if isinstance(e.get("remaining"), int)]
    writes = sorted(t for t in (parse_ts(e.get("ts")) for e in calls if e.get("write")) if t)
    seconds = lambda es: round(sum(e.get("seconds") or 0 for e in es), 1)
    return {
        "calls": len(calls),
        "writes": sum(1 for e in calls if e.get("write")),
        "graphql": sum(1 for e in calls if e.get("graphql")),
        "graphql_points": sum(e["cost"] for e in calls if e.get("graphql") and isinstance(e.get("cost"), int) and not isinstance(e["cost"], bool)),
        "by_script": dict(sorted(by_script.items(), key=lambda kv: (-kv[1], kv[0]))),
        "retries": sum(1 for e in calls if (e.get("attempt") or 1) > 1),
        "rate_limited": sum(1 for e in calls if e.get("outcome") == "ratelimited"),
        "lowest_remaining": min(remaining) if remaining else None,
        "peak_writes_per_minute": peak_per_minute(writes),
        "waited_s": seconds(waits),
        "waited_shared_s": seconds([e for e in waits if e.get("shared")]),
    }


def gh_metrics(events: list | None, passes: list) -> dict:
    """GitHub use from the helper's `gh_call` events: a run total and one block per pass (the calls made up to
    that pass's record, after the previous one). `{"available": False}` for a log written before the call log."""
    calls = [e for e in events or [] if e.get("kind") == runlog.GH_CALL]
    if not calls:
        return {"available": False}
    bounds = [(p.get("n") or i + 1, parse_ts(p.get("ts"))) for i, p in enumerate(passes)]
    bounds = [(n, t) for n, t in bounds if t]
    buckets = [[] for _ in bounds] + [[]]  # the last holds calls after the final pass record
    for e in calls:
        ts = parse_ts(e.get("ts"))
        buckets[next((i for i, (_, t) in enumerate(bounds) if ts and ts <= t), len(bounds))].append(e)
    rows = [{"pass": n, **gh_block(b)} for (n, _), b in zip(bounds, buckets) if b]
    if buckets[-1]:
        rows.append({"pass": None, **gh_block(buckets[-1])})  # outside any recorded pass
    return {"available": True, "total": gh_block(calls), "passes": rows, "pass_records": len(bounds)}


def gh_lines(g: dict) -> list:
    """The summary's GitHub line: the run's totals and its busiest scripts."""
    if not g.get("available"):
        return ["GitHub calls: not recorded"]
    def line(b):
        low = "–" if b["lowest_remaining"] is None else b["lowest_remaining"]
        wait = f" · waited {fmt_dur(b['waited_s'])} ({fmt_dur(b['waited_shared_s'])} for another script's hit)" if b["waited_s"] else ""
        return (f"{b['calls']} calls, {b['writes']} writes (peak {b['peak_writes_per_minute']}/min), {b['graphql']} GraphQL, "
                f"{b['retries']} retries, lowest remaining {low}{wait}")
    t = g["total"]
    top = ", ".join(f"{k} {v}" for k, v in list(t["by_script"].items())[:4])
    # One line however long the run: the per-pass blocks live in the saved JSON (metrics.gh_calls.passes).
    out = [f"GitHub calls: {line(t)}" + (f" · top: {top}" if top else "")]
    # Points per pass: each row's sum of GraphQL `cost` (calls outside any record form a row of their own).
    # Shown only when the log carries costs, so a log from before #490 keeps the old summary.
    # N is the pass records (a pass with no calls counts, the row for calls outside any pass does not).
    records = g.get("pass_records") or 0
    points = [r.get("graphql_points", 0) for r in g.get("passes") or [] if r.get("pass") is not None]
    if t.get("graphql_points") and records:
        out.append(f"graphql points per pass: avg {sum(points) / records:.0f}, max {max(points, default=0)} "
                   f"(total {sum(points)} over {records} pass{'es' if records != 1 else ''})")
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
    """Idle slot-seconds by the reason each pass gave, held until the next pass. A claimed ticket the pass's `claimed`
    map shows as `idle` (its worker not live, nothing reported since its last commit: #501) holds a slot doing nothing:
    its seconds count under `worker-idle`."""
    out = {}
    for p, nxt in zip(passes, passes[1:] + [None]):
        s = parse_ts(p.get("ts"))
        e = parse_ts(nxt.get("ts")) if nxt else end
        if not s or not e or e <= s:
            continue
        for reason, count in (p.get("idle") or {}).items():
            out[reason] = out.get(reason, 0) + count * (e - s).total_seconds()
        claimed = p.get("claimed")
        stale = sum(1 for state in claimed.values() if state == "idle") if isinstance(claimed, dict) else 0
        if stale:
            out["worker-idle"] = out.get("worker-idle", 0) + stale * (e - s).total_seconds()
    return out


def recommend(slots: int, occupancy: float, wins: list, limits: list) -> str | None:
    if limits or any(w["peak"] >= 100 for w in wins):
        return f"Try slots {slots} → {slots - 1}: usage limits hit." if slots > 1 else None
    if occupancy >= 0.8 and any(w["underused"] for w in wins):
        return f"Try slots {slots} → {slots + 1}: slots were full and a window reset with room left." if slots < 5 else None
    return None


# How long after the transcript's last record a pass record still belongs to the same wake.
# Fixed on purpose: nothing in the plugin sets a wake interval to read it from (a wake is an event, a worker or
# reviewer completion or a `watch-ci` line, not a timer), so the bound is only how far `record` and the merge
# script's event can trail the transcript's last line within one wake. Five minutes covers a `gh` retry with its
# backoff; a record later than that belongs to another wake. The edge is inclusive.
WAKE_GRACE = timedelta(minutes=5)


SILENCE = timedelta(minutes=30)  # a transcript silence this long, with no `resume` event, is noted (GLOSSARY: Session gap)


def session_gaps(times: list, events: list | None) -> list:
    """Each Session gap, as [start, end]: from the session's last transcript entry before a `resume` event up to the
    event. `times` is the sorted transcript timestamps. A resume with no earlier entry has no gap to measure."""
    gaps = []
    for e in events or []:
        at = parse_ts(e.get("ts")) if e.get("kind") == runlog.RESUME else None
        before = [t for t in times if t < at] if at else []
        if before:
            gaps.append([before[-1], at])
    return merge_intervals(gaps)


def unexplained_silences(times: list, gaps: list) -> list:
    """Silences over SILENCE between consecutive transcript entries that no `resume` event explains: noted, never
    excluded (a hand `claude --resume` skips `launch`, so no event marks it)."""
    return [[a, b] for a, b in zip(times, times[1:])
            if b - a > SILENCE and not any(g[0] < b and g[1] > a for g in gaps)]


def outside_gaps(start: datetime, end: datetime, gaps: list) -> list:
    """The profile's [start, end] window minus its Session gaps, as intervals: the span per-hour figures divide by and
    the window the lead's cost is clipped to (later tickets reuse this)."""
    out, at = [], start
    for s, e in merge_intervals([list(g) for g in clip(gaps, start, end)]):
        if s > at:
            out.append([at, s])
        at = max(at, e)
    if end > at:
        out.append([at, end])
    return out


def profile(transcript: Path, repo: Path | None, slots: int | None, threshold: float, now: datetime) -> dict:
    main = Transcript(transcript)
    sid = transcript.stem
    repo = repo or repo_of(main)
    passes = runlog.read_passes(repo, sid)
    agents = collect_agents(main)
    # The window ends at the later of the transcript's last record and the last pass record, when that record is from
    # the transcript's own wake (WAKE_GRACE): `record` runs in the wake of the merge it reports, so the pass record and
    # the merge event land a moment before or after the transcript's last line. A pass logged much later was written
    # by another wake and is not part of this transcript's window.
    end = main.times[-1] if main.times else now
    pass_ts = [t for t in (parse_ts(p.get("ts")) for p in passes) if t]
    end = max([end] + [t for t in pass_ts if t - end <= WAKE_GRACE])
    starts = [a["dispatched"] for a in agents if a["dispatched"]] + [parse_ts(p.get("ts")) for p in passes]
    starts = [s for s in starts if s]
    start = min(starts) if starts else (main.times[0] if main.times else now)
    events = runlog.read_events(repo, sid, start, end)
    tickets = build_tickets(agents, main, end, events)
    assign_slots(tickets)
    # Each pass's own slot count holds until the next pass. More tickets at once than the count in force
    # (an older config, or lanes) means the session's own concurrency is the truth for that stretch.
    segments, slots_raised = slot_segments(passes, tickets, start, end, slots, config_slots(repo))
    slots = segments[-1][2]
    gaps = session_gaps(main.times, events)
    silences = unexplained_silences(main.times, gaps)
    # The span per-hour figures use leaves the Session gaps out: a resumed run's downtime is not slow work.
    span = max(seconds(outside_gaps(start, end, gaps)), 1.0)
    capacity = sum(n * (b - a).total_seconds() for a, b, n in segments) or span
    occupied = sum(seconds(clip([[tk["start"], tk["end"]]], start, end)) for tk in tickets)
    building = sum(seconds(tk["building"]) for tk in tickets)
    usage = read_usage(start, end)
    wins = windows(usage, passes, threshold, now)
    limits = limit_events(main, agents, passes)
    review = review_metrics(agents, tickets, set(ticket_endings(main, events)[0]))
    depth2 = depth2_agents(main, agents)
    pr_tier = {p["ticket"]: p["tier"] for p in review["prs"] if p["tier"]}
    for tk in tickets:
        tk["tier"] = pr_tier.get(tk["ticket"])
    if events is not None:
        gates = [e for e in events if e.get("kind") == runlog.GATE]
        for tk in tickets:
            mine = [e for e in gates if tk["pr"] and str(e.get("ref")) == str(tk["pr"])]
            tk["gate_runs"], tk["gate_failed"] = len(mine), sum(1 for e in mine if e.get("outcome") == runlog.FAIL)
    idle_total = max(capacity - occupied, 0.0)
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
    occupancy = occupied / capacity
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
        "session": sid,
        "repo": str(repo) if repo else None,
        "generated_at": iso(now),
        "start": iso(start),
        "end": iso(end),
        "last_record": iso(last),
        "warnings": warnings,
        "slots": slots,
        "slots_raised": slots_raised,
        # Any pass ran under --greedy: its `slots` is the greedy cap in force, so occupancy is against the cap.
        "greedy": any(p.get("greedy") is True for p in passes),
        # Slot count over time: [from, slots] per stretch; `slots` above is the last one.
        "slots_timeline": [[iso(a), n] for a, _, n in segments],
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
                "ticket": tk["ticket"], "pr": tk["pr"], "tier": tk["tier"], "outcome": tk["outcome"], "merge_unverified": tk["merge_unverified"], "slot": tk["slot"],
                "start": iso(tk["start"]), "end": iso(tk["end"]), "building": interval(tk["building"]),
                "tokens": tk["tokens"], "cost": tk["cost"], "worker_models": tk["worker_models"],
                "gate_runs": tk["gate_runs"], "gate_failed": tk["gate_failed"],
            }
            for tk in tickets
        ],
        "passes": passes,
        "usage": usage,
        "limits": limits,
        "metrics": {
            "span_s": span,
            # Downtime of a resumed run (`launch --resume` logs a `resume` event), outside span_s.
            "session_gap": {"total_s": seconds(clip(gaps, start, end)), "intervals": interval(clip(gaps, start, end))},
            # Transcript silences over 30 minutes that no event explains (a hand resume): noted, still inside span_s.
            "unexplained_silences": interval(silences),
            "occupancy": occupancy,
            "building_share": building / capacity,
            "review_share": max(occupied - building, 0.0) / capacity,
            "idle_s": idle,
            "tokens_total": total,
            "tokens_by_role": by_role,
            "tokens_by_model": by_model,
            "orchestrator_share": sum(main.tokens.values()) / total if total else 0.0,
            "cache_hit_ratio": reads / (reads + fresh) if reads + fresh else 0.0,
            "tickets_merged": len(merged),
            "merges_unverified": sum(1 for tk in merged if tk["merge_unverified"]),
            "tokens_per_merged_ticket": sum(tk["tokens"] for tk in merged) / len(merged) if merged else None,
            "five_hour_start": five[0] if five else None,
            "five_hour_end": five[-1] if five else None,
            "five_hour_peak": max(five) if five else None,
            "windows": wins,
            "limit_hits": len(limits),
            "cost": cost,
            "meters": meters,
            "review": review,
            "tiers": tier_metrics(tickets, review),
            "worker_models": worker_metrics(agents, tickets, review),
            "escalations": escalations(tickets),
            "depth2": depth2,
            **orchestrator_metrics(main, agents, passes, len(merged), events),
            "events": event_metrics(events, review),
            "gh_calls": gh_metrics(events, passes),
        },
        "recommendation": recommend(slots, occupancy, wins, limits),
    }


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
        plan = "headroom not measured (limit hits come from transcripts)"
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
        f"Profile · session {p['session'][:8]} · {p['start'][:16]}Z → {p['end'][:16]}Z ({fmt_dur(m['span_s'])}) · {slots_label(p)}",
        f"proxy $ {spend}",
        f"slots   occupied {m['occupancy']:.0%}  (building {m['building_share']:.0%}, review/merge {m['review_share']:.0%})  idle: {idle}",
        f"plan    {plan}",
        *gh_lines(m.get("gh_calls") or {"available": False}),
    ]
    for w in p["warnings"]:
        lines.append(f"WARNING {w}")
    if p["recommendation"]:
        lines.append(f"advice  {p['recommendation']}")
    gap = m.get("session_gap") or {}
    if gap.get("intervals"):
        lines.append(f"note    session gap {fmt_dur(gap['total_s'])} over {len(gap['intervals'])} resume(s), outside the span")
    for s, e in m.get("unexplained_silences") or []:
        lines.append(f"WARNING no transcript entry from {s[:16]}Z to {e[:16]}Z and no resume event (a hand `claude --resume`?): inside the span, not excluded")
    if p.get("slots_raised"):
        lines.append("note    more tickets ran at once than the slot count in force; occupancy is against the higher count")
    if p["metrics"].get("merges_unverified"):
        lines.append(f"note    {p['metrics']['merges_unverified']} merged ticket(s) unverified: no events log, counted from merge commands in the transcript")
    if not p["sources"]["pass_log"]:
        lines.append("note    no pass log for this session: idle reasons and window pressure are unknown")
    lines.append(f"saved   {saved}  (+ .html)")
    return "\n".join(lines)


def slots_label(p: dict) -> str:
    """`2 slots`, or `3→2 slots` when the count changed during the session, with ` (greedy)` after a greedy pass."""
    counts = []
    for _, n in p.get("slots_timeline") or [(None, p["slots"])]:
        if not counts or counts[-1] != n:
            counts.append(n)
    return "→".join(str(n) for n in counts) + " slots" + (" (greedy)" if p.get("greedy") else "")


def write_html(data: dict, path: Path) -> None:
    viewer = VIEWER.read_text()
    # `</` can't appear inside a script element; JSON allows escaping the slash.
    payload = json.dumps(data).replace("</", "<\\/")
    marker = '<script id="profile-data" type="application/json">null</script>'
    path.write_text(viewer.replace(marker, f'<script id="profile-data" type="application/json">{payload}</script>'))


def cmd_profile(argv: list) -> None:
    ap = argparse.ArgumentParser(prog="profile.py", allow_abbrev=False)  # the guard approves the full flag names only (#545)
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
    ap = argparse.ArgumentParser(prog="profile.py record", allow_abbrev=False)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--scope", default="full", help="full (default), status, wake, #<ticket> or parent #<n>")
    ap.add_argument("--slots", type=int, help="the slot count in force when it is not the config's (a greedy cap)")
    ap.add_argument("--greedy", action="store_true", help="the pass ran under --greedy (needs --slots: the cap is in no file)")
    ap.add_argument("--idle", nargs="*", metavar="REASON=N", help=f"why free slots stayed free ({', '.join(runlog.IDLE_REASONS)}); must sum to the free slots")
    ap.add_argument("--dispatched", type=int, nargs="*", default=[], metavar="TICKET")
    ap.add_argument("--merged", type=int, nargs="*", default=[], metavar="TICKET")
    ap.add_argument("--suggestion", help='the slot suggestion, e.g. "2 → 3"')
    ap.add_argument("--limit-hit", action="store_true")
    ap.add_argument("--reset-at", help="ISO time the usage limit resets")
    ap.add_argument("--nudged", type=int, nargs="*", metavar="TICKET",
                    help="the idle tickets this pass nudged (`claims` then prints `nudged <ts>`); alone, it writes just the nudge")
    ap.add_argument("--snapshot", help="the tracker snapshot to derive from (default: the snapshot file, else fetch)")
    ap.add_argument("--state", type=Path, help="state file (default: <repo>/.claude/orchestrate-state.json)")
    args = ap.parse_args(argv)
    if not args.session:
        common.die("profile record: runlog: no session, pass not written")  # before any derivation: nothing to read for
    try:
        # A nudge alone is not a pass the profile counts for slots; it is the one record `claims` reads for a nudge.
        if args.nudged and argv_is_only_nudge(args):
            runlog.append_pass(args.repo, args.session, {"scope": runlog.NUDGE_SCOPE, "nudged": args.nudged})
            return
        runlog.append_pass(args.repo, args.session, derive_pass(args))
    except runlog.NoSession as e:
        common.die(f"profile record: {e}")  # this CLI's whole job is the record
    except runlog.BadPass as e:
        common.die(f"profile record: {e}")


def argv_is_only_nudge(args) -> bool:
    return (args.scope == "full" and args.slots is None and not args.greedy and args.idle is None and not args.dispatched
            and not args.merged and args.suggestion is None and not args.limit_hit and args.reset_at is None)


def derive_pass(args) -> dict:
    """The pass record from the flags (what only the orchestrator knows) and the libraries (everything else): `claimed`
    from `claims`, `slots` and `free` from the config and the claimed, `takeable_waiting` from the snapshot, `greedy` from
    the flag. Dies in one line when a source can't be read; `append_pass` refuses what is off the schema."""
    import claims
    import github
    import tracker
    from repo import STATE
    root = args.repo.resolve()
    if args.greedy and args.slots is None:
        common.die("profile record: --greedy needs --slots <the greedy cap>")
    slots = args.slots if args.slots is not None else config.for_checkout(root).slots
    if slots is None:
        common.die("profile record: no slot count: the config has no Slots line; pass --slots")
    idle = {}
    for item in args.idle or ():
        reason, _, count = item.partition("=")
        if not count.isdigit() or reason in idle:
            common.die(f"profile record: --idle takes REASON=N once each, not {item!r}")
        idle[reason] = int(count)
    snap_path = args.snapshot or (str(root / tracker.SNAPSHOT_FILE) if (root / tracker.SNAPSHOT_FILE).is_file() else None)
    try:
        snap = tracker.Snapshot.open(snap_path, root)
        found = claims.claims(root, args.state or root / STATE, snap.issues, snap.prs, snap.merged, session=args.session)
        takeable = {i["number"] for i in tracker.takeable(snap.issues, tracker.label_strings(root))}
    except github.GhError as e:
        common.die(f"profile record: cannot derive the pass: gh failed: {e.message}")
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        common.die(f"profile record: cannot derive the pass: {e}")
    # A lead's ticket holds no slot (skills/run/SKILL.md, step 3), and a done one is not occupying anything.
    held = [n for n, c in found.items() if c.state not in ("done", "with-lead")]
    entry = {
        "scope": args.scope,
        "slots": slots,
        "greedy": args.greedy,
        "claimed": {str(n): found[n].state for n in sorted(found)},
        "free": max(slots - len(held), 0),
        # A ticket dispatched this pass can still read as takeable in a snapshot taken before it.
        "takeable_waiting": len(takeable - set(args.dispatched)),
        "limit_hit": args.limit_hit,
        "reset_at": args.reset_at,
        "dispatched": args.dispatched,
        "merged": args.merged,
        "suggestion": args.suggestion,
    }
    if args.idle is not None or args.scope != "wake":
        entry["idle"] = idle
    if args.nudged:
        entry["nudged"] = args.nudged
    return entry


MIN_MERGED = 20  # a trial with fewer merged tickets is inconclusive
MAX_L_SHARE_GAP = 25.0  # percentage points between the two sessions' L shares
# The gating targets ($ per merged ticket) live in the spec (#27), not here: pass them with --max-cost
# and --max-orchestrator-cost. The review-quality rows are judged against the baseline itself.


def load_profile(path: Path) -> dict:
    try:
        p = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        common.die(f"profile compare: can't read {path}: {e}")
    if not isinstance(p, dict) or not isinstance(p.get("metrics"), dict):
        common.die(f"profile compare: {path} is not an orchestrate profile")
    if p.get("schema") != SCHEMA:
        common.die(f"profile compare: {path} is schema {p.get('schema')}, not {SCHEMA}; re-profile it with the current profile.py")
    # Without a pass log there are no idle reasons, lead lookup or stop starts from `claimed`.
    if not ((p["metrics"].get("context") or {}).get("idle_wakes") or {}).get("passes"):
        common.die(f"profile compare: {path} is not a profiled run: it has no pass log")
    return p


def _rate(num, den):
    return num / den if den else None


def figures(p: dict) -> dict:
    """The headline figures `compare` sets side by side, from one profile."""
    m = p["metrics"]
    c, r, tiers = m["cost"], m["review"], m["tiers"]
    merged = m["tickets_merged"]
    # Tickets with no reviewer have no tier; they stay out of the L share.
    tiered = sum(t["merged"] for k, t in tiers.items() if k != "?")
    return {
        "merged": merged,
        "cost": c["per_merged_ticket"],
        "orchestrator": c["per_merged_ticket_by_role"].get("orchestrator"),
        "changes": r["round1_changes_rate"],
        "outright": r["round1_outright_rate"],
        "approvals": None if r["round1_changes_rate"] is None else 1 - r["round1_changes_rate"],
        "rounds": r["rounds_per_pr"],
        "l_share": _rate(tiers.get("L", {}).get("merged", 0), tiered),
        "untiered": tiers.get("?", {}).get("merged", 0),
        "unparsed": r.get("unparsed_reviewers"),
        "tiers": tiers,
        # What the tier columns don't hold per merged ticket: the orchestrator, and agents on tickets that never merged.
        "overhead": None if not merged else c["per_merged_ticket"] - sum(t["cost"] for t in tiers.values()) / merged,
    }


def mix_adjust(before: dict, after: dict) -> dict:
    """The trial's figures as if it had drawn the baseline's tier mix.

    Each tier's figure in the trial is weighted by the baseline's share of that tier, over the tiers the trial
    has data for (weights renormalised). The orchestrator's $ isn't attributed to tiers, so it isn't adjusted."""
    def weighted(field, weight, value):
        tiers = [t for t in before["tiers"] if t in after["tiers"] and before["tiers"][t][weight] and after["tiers"][t][weight]]
        total = sum(before["tiers"][t][weight] for t in tiers)
        if not total:
            return None
        return sum(before["tiers"][t][weight] / total * value(after["tiers"][t]) for t in tiers)

    cost = weighted("cost", "merged", lambda t: t["cost"] / t["merged"])
    changes = weighted("changes", "round1", lambda t: t["round1_changes"] / t["round1"])
    outright = weighted("outright", "round1", lambda t: t["round1_outright"] / t["round1"])
    return {
        "cost": None if cost is None or after["overhead"] is None else cost + after["overhead"],
        "orchestrator": after["orchestrator"],
        "changes": changes,
        "outright": outright,
        "approvals": None if changes is None else 1 - changes,
        "rounds": weighted("rounds", "reviewed_prs", lambda t: t["rounds"] / t["reviewed_prs"]),
    }


def gating(base: dict, f: dict, max_cost, max_orch) -> dict:
    """pass / fail per gating row for one set of figures; None when it can't be judged."""
    def le(x, limit):
        return None if x is None or limit is None else x <= limit + 1e-9

    def ge(x, limit):
        return None if x is None or limit is None else x >= limit - 1e-9
    outright, approvals = ge(f["outright"], base["outright"]), ge(f["approvals"], base["approvals"])
    return {
        "cost": le(f["cost"], max_cost),
        "orchestrator": le(f["orchestrator"], max_orch),
        "changes": le(f["changes"], base["changes"]),
        # Round-1 approvals, as the spec's target row words it: as many outright as the baseline, or as many
        # counting approve-with-minors (which is 1 - the "changes" rate, so it overlaps that row on purpose).
        "approvals": None if outright is None and approvals is None else bool(outright or approvals),
        "rounds": le(f["rounds"], base["rounds"]),
    }


ROWS = [
    ("cost", "proxy $ per merged ticket", "usd", "max-cost"),
    ("orchestrator", "orchestrator $ per merged ticket", "usd", "max-orchestrator-cost"),
    ("changes", 'round-1 "changes" rate', "pct", None),
    ("approvals", "round-1 approvals (counting minors)", "pct", None),
    ("rounds", "rounds per PR", "num", None),
]


LABELS = {k: label for k, label, _, _ in ROWS}


def compare(before: dict, after: dict, max_cost=None, max_orch=None) -> dict:
    b, a = figures(before), figures(after)
    adj = mix_adjust(b, a)
    raw_verdict = gating(b, a, max_cost, max_orch)
    adj_verdict = gating(b, {**a, **adj}, max_cost, max_orch)
    flips = [k for k in raw_verdict if raw_verdict[k] is not None and adj_verdict[k] is not None and raw_verdict[k] != adj_verdict[k]]
    inconclusive = []
    if a["merged"] < MIN_MERGED:
        inconclusive.append(f"only {a['merged']} merged tickets (needs {MIN_MERGED})")
    if a["l_share"] is not None and b["l_share"] is not None and abs(a["l_share"] - b["l_share"]) * 100 > MAX_L_SHARE_GAP:
        inconclusive.append(f"L share {a['l_share']:.0%} against the baseline's {b['l_share']:.0%} differs by more than {MAX_L_SHARE_GAP:.0f} points")
    if flips:
        inconclusive.append("the mix adjustment flips the verdict on " + ", ".join(LABELS[k] for k in flips))
    return {"before": b, "after": a, "adjusted": adj, "raw_verdict": raw_verdict, "adjusted_verdict": adj_verdict,
            "inconclusive": inconclusive}


def _show(x, kind) -> str:
    if x is None:
        return "–"
    return {"usd": fmt_usd, "pct": lambda v: f"{v:.0%}", "num": lambda v: f"{v:.2f}"}[kind](x)


def _delta(b, a, kind) -> str:
    if b is None or a is None:
        return "–"
    d = a - b
    return {"usd": f"{'+' if d >= 0 else '-'}${abs(d):.2f}", "pct": f"{d * 100:+.0f} pts", "num": f"{d:+.2f}"}[kind]


def _verdict(v) -> str:
    return "n/a" if v is None else "pass" if v else "FAIL"


def compare_report(before_name: str, after_name: str, r: dict) -> str:
    b, a = r["before"], r["after"]
    lines = [f"Compare · before {before_name} → after {after_name}",
             f"{'':38}{'before':>10}{'after':>10}{'delta':>12}"]

    def row(label, bv, av, kind):
        lines.append(f"{label:38}{_show(bv, kind):>10}{_show(av, kind):>10}{_delta(bv, av, kind):>12}")
    lines.append(f"{'merged tickets':38}{b['merged']:>10}{a['merged']:>10}{a['merged'] - b['merged']:>+12}")
    for key, label, kind, _ in ROWS:
        row(label, b[key], a[key], kind)
    row("round-1 approvals (outright)", b["outright"], a["outright"], "pct")
    row("L-tier share of merged tickets", b["l_share"], a["l_share"], "pct")
    for name, f in (("before", b), ("after", a)):
        unparsed = "unknown" if f["unparsed"] is None else f["unparsed"]
        lines.append(f"  {name}: {f['untiered']} merged tickets with no tier (left out of the L share); {unparsed} reviewer agents returned no parseable verdict")
    lines.append("per tier, merged tickets and $ per merged ticket (workers and reviewers):")
    for t in sorted(set(b["tiers"]) | set(a["tiers"])):
        cells = []
        for f in (b, a):
            x = f["tiers"].get(t)
            cells.append((x["merged"], x["cost"] / x["merged"] if x["merged"] else None) if x else (0, None))
        lines.append(f"  tier {t:<32}{cells[0][0]:>3} {_show(cells[0][1], 'usd'):>6}{cells[1][0]:>3} {_show(cells[1][1], 'usd'):>6}")
    lines.append("gating, as measured and mixed-adjusted to the baseline's tier mix (the orchestrator's $ isn't attributed to tiers):")
    for key, label, kind, flag in ROWS:
        adj = r["adjusted"][key]
        note = "" if flag is None or r["raw_verdict"][key] is not None else f" (pass --{flag} to judge)"
        lines.append(f"  {label:36}{_show(a[key], kind):>8} {_verdict(r['raw_verdict'][key]):>5}  →{_show(adj, kind):>8} {_verdict(r['adjusted_verdict'][key]):>5}{note}")
    if r["inconclusive"]:
        lines.append("INCONCLUSIVE: " + "; ".join(r["inconclusive"]))
    else:
        judged = [v for v in r["raw_verdict"].values() if v is not None]
        unjudged = [LABELS[k] for k in ("cost", "orchestrator") if r["raw_verdict"][k] is None]
        lines.append("conclusive: " + ("every judged gating row passes" if all(judged) else "a gating row fails")
                     + (f" ({' and '.join(unjudged)} not judged: no target given)" if unjudged else ""))
    return "\n".join(lines)


def cmd_compare(argv: list) -> None:
    ap = argparse.ArgumentParser(prog="profile.py compare")
    ap.add_argument("before", type=Path, help="the baseline profile JSON")
    ap.add_argument("after", type=Path, help="the trial's profile JSON")
    ap.add_argument("--max-cost", type=float, help="gating target: proxy $ per merged ticket (from the spec)")
    ap.add_argument("--max-orchestrator-cost", type=float, help="gating target: orchestrator $ per merged ticket (from the spec)")
    args = ap.parse_args(argv)
    r = compare(load_profile(args.before), load_profile(args.after), args.max_cost, args.max_orchestrator_cost)
    print(compare_report(args.before.name, args.after.name, r))


def main(argv: list) -> None:
    if argv and argv[0] == "record":
        cmd_record(argv[1:])
    elif argv and argv[0] == "compare":
        cmd_compare(argv[1:])
    else:
        cmd_profile(argv)


if __name__ == "__main__":
    main(sys.argv[1:])
