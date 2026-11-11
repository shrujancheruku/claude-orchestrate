"""The reviewer-report line grammar, parsed in one place (#546): `<name>: <value>` lines such as `verdict:`, `round:`,
`blocking:` and `non-blocking:`. `worker-report` (the run log's `review` event) and `profile.py` (the review quality
floor) both read a reviewer's report, and each had its own regex for the same lines.

Stdlib only, Python 3.9+. A library: imported as `import reviewreport` (ADR-0003); `profile.py` runs under
`python3 -I` and appends its own directory to `sys.path` first, as it does for `common`.
"""

from __future__ import annotations

import re


def field(name: str, text: str):
    """The last `<name>: value` line of a reviewer's report, backticks and blanks trimmed; None when there is none."""
    found = re.findall(rf"^[ \t>*-]*{name}:[ \t]*`*(.*?)`*[ \t]*$", text, re.I | re.M)
    return found[-1].strip() if found and found[-1].strip() else None


def count(name: str, text: str):
    """The leading integer of the last `<name>:` line, or None when the line is absent or holds no number."""
    m = re.match(r"\d+", field(name, text) or "")
    return int(m.group()) if m else None


# The posted review body (agents/reviewer.md, Post): one header line, then `spec:` and `standards:` lines.
#     Round <n> · verdict: approve|changes · tier: S|M|L · head: <hash> · suite: pass|fail <n>|not run (<reason>)
# `lead-brief` reads the tier from it and `worker-report --reviewer --review-body` checks the whole body, both here.
ROUND = re.compile(r"^\s*Round\s+\d+\b")
TIER = re.compile(r"\btier:\s*([SML])\b")
MALFORMED = "malformed"
_SUITE_OK = re.compile(r"(?:pass|fail)(?![\w-])(?:[ \t,:(;.-].*)?|not run\s*\(\s*\S[^)]*\)", re.I)


def tier_of(body):
    """The tier a review body states (`S`, `M` or `L`); `MALFORMED` for a review body (a `Round <n>` header) with no
    such tier; None for text that is not a review body at all."""
    first = (body or "").split("\n", 1)[0]
    m = TIER.search(first)
    if m:
        return m.group(1)
    return MALFORMED if ROUND.match(first) else None


def body_problem(body):
    """None when the review body is well formed, else one short phrase naming the first thing wrong (the `what` of a
    `next: fix-report <what>`): the header's verdict, tier, head and suite, then the `spec:` and `standards:` lines."""
    body = (body or "").strip()
    first = body.split("\n", 1)[0]
    if not ROUND.match(first):
        return "review body: first line is not `Round <n> · verdict: ... · tier: ... · head: ... · suite: ...`"
    parts = {k.strip().lower(): v.strip() for k, v in (p.split(":", 1) for p in first.split("·")[1:] if ":" in p)}
    if parts.get("verdict") not in ("approve", "changes"):
        return "review body: header needs `verdict: approve | changes`"
    if parts.get("tier") not in ("S", "M", "L"):
        return "review body: header needs `tier: S | M | L`"
    if not re.fullmatch(r"[0-9a-f]{7,40}", parts.get("head", ""), re.I):
        return "review body: header needs `head: <hash reviewed>`"
    if not _SUITE_OK.fullmatch(parts.get("suite", "")):
        return "review body: header needs `suite: pass | fail <n> | not run (<reason>)`"
    for name in ("spec", "standards"):
        if not re.search(rf"^[ \t>*-]*{name}:", body, re.I | re.M):
            return f"review body: no `{name}:` line (the evidence for each criterion and rule)"
    return None
