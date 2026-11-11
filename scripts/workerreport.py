"""The worker-report line grammar, in one place (#659): the stop reasons and the shapes of the `adr-conflicts:` and
`discovered:` lines. `worker-report` classifies with them, `agents/worker.md` is checked against them by a test, and
`tests/test_worker_turn_rules.py` imports them rather than keeping a list of its own.

Stdlib only, Python 3.9+. A library: imported as `import workerreport` (ADR-0003).
"""

from __future__ import annotations

import re

STOP_REASONS = ("held-path", "no-spec", "ambiguous", "adr-conflict", "human-only", "verify-outside-ticket",
                "already-done", "no-primary-source", "permission-denied", "worktree-gone", "transient")

_LINE = r"^[ \t>*-]*{name}:[ \t]*`*(.*?)`*[ \t]*$"
# `discovered: <n> — <gist>` (`-`, `--` or an en dash also read); `none` or `0` for no items.
_DISCOVERED = re.compile(r"(?:none|0)\.?|[1-9]\d*[ \t]+(?:—|–|--?)[ \t]+\S.*", re.I)
# `adr-conflicts: <ADR> -> <how this PR departs from it, and why>` (`→` also read), or `none`.
_ADR = re.compile(r"none\.?|\S.*?[ \t]+(?:->|→)[ \t]+\S.*", re.I)


def lines(name: str, text: str) -> list:
    """The values of every `<name>:` line of a report, in order."""
    return re.findall(_LINE.format(name=re.escape(name)), text, re.I | re.M)


def problem(text: str):
    """None when the report's `discovered:` and `adr-conflicts:` lines (each optional) are well formed, else a short
    phrase naming the first bad line, for `next: fix-report <what>`."""
    for name, shape, want in (("discovered", _DISCOVERED, "<n> — <gist>"),
                              ("adr-conflicts", _ADR, "<ADR> -> <how it departs, and why>")):
        for value in lines(name, text):
            if value and not shape.fullmatch(value):
                return f"{name}: line is off the shape `{name}: {want}` (or `none`): {value[:60]!r}"
    return None
