"""The touch-map grammar, in one place: what the scout emits, a brief carries, the state file stores and `holds` compares.

    touch map = `none` | entry (SEP entry)*          SEP = `,` or `;` or `. ` outside parentheses
    entry     = path [`:` L [`-` L]] [`(` note `)`]  backticks around the path are tolerated

`parse` is strict: an entry outside the grammar (a bare line number from `a.py:10, 20`, a note before the line, `L10`
prefixes) raises TouchMapError rather than being half-read, so a bad map is reported where it is read and never
becomes a partial one. `format` is the canonical form: `path[:L[-L]] (note)` entries joined by `, `. Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class TouchMapError(ValueError):
    """An entry outside the grammar; the message names it."""


@dataclass(frozen=True)
class Entry:
    path: str
    start: int | None = None
    end: int | None = None
    note: str | None = None

    def __str__(self) -> str:
        out = self.path
        if self.start is not None:
            out += f":{self.start}" + (f"-{self.end}" if self.end is not None else "")
        if self.note:
            out += f" ({self.note})"
        return out


_ENTRY = re.compile(
    r"^`?(?P<path>[^\s`:()]+)`?"
    r"(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?"
    r"(?:\s*\((?P<note>[^()]*)\))?$"
)


def _split(text: str) -> list:
    """Split on `,` `;` and a sentence-ending `. `, outside parentheses; a trailing `.` is dropped."""
    entries, depth, current = [], 0, ""
    chars = text.strip()
    for i, ch in enumerate(chars):
        sentence_end = ch == "." and depth == 0 and bool(current.strip()) and (i + 1 == len(chars) or chars[i + 1].isspace())
        if depth == 0 and (ch in ",;" or sentence_end):
            entries.append(current)
            current = ""
            continue
        depth = max(0, depth + (ch == "(") - (ch == ")"))
        current += ch
    entries.append(current)
    return [e.strip() for e in entries if e.strip()]


def parse(text) -> list:
    """The entries of a touch-map string; [] for empty or `none`. Raises TouchMapError on any entry off the grammar."""
    text = (text or "").strip()
    if text.lower() in ("", "none", "none."):
        return []
    entries = []
    for raw in _split(text):
        m = _ENTRY.match(raw)
        if not m or m["path"].isdigit() or (m["end"] and int(m["end"]) < int(m["start"])):
            raise TouchMapError(f"bad touch-map entry: {raw!r} (want path[:L[-L]] [(note)])")
        path = m["path"][2:] if m["path"].startswith("./") else m["path"]
        note = " ".join(m["note"].split()) if m["note"] else None
        entries.append(Entry(path, int(m["start"]) if m["start"] else None,
                             int(m["end"]) if m["end"] else None, note or None))
    return entries


def format(entries) -> str:
    """The canonical one-line form of entries; `none` when there are none."""
    return ", ".join(str(e) for e in entries) or "none"


def overlaps(a: Entry, b: Entry) -> bool:
    """Whether two entries name the same file (a `dir/` entry covers what is under it); lines are ignored."""
    return a.path == b.path or (a.path.endswith("/") and b.path.startswith(a.path)) or \
        (b.path.endswith("/") and a.path.startswith(b.path))
