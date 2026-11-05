"""The one reader of `.claude/orchestration.md` for the scripts: `Config.load(repo)`.

The markdown grammar lives here and nowhere else in scripts/ (hooks/guard.py is standalone, ADR-0003, and
keeps its own post-reviews reader; tests/test_config.py holds the two to the same answer). A malformed
entry never raises: it lands in `Config.problems` and each caller decides what to do with it.

Stdlib only, Python 3.9+. A library: imported, never run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import common

# Kinds of Problem, so a caller can act on the ones it cares about.
RUN, SHARED, GENERATED = "run", "shared", "generated"


class Problem(NamedTuple):
    kind: str  # RUN | SHARED | GENERATED
    message: str


class Config(NamedTuple):
    """What the config says. A missing or unreadable config is the empty Config, so callers degrade."""
    slots: int | None = None
    shared_files: tuple = ()  # paths or globs a PR may edit and replay resolves
    exclusive_holds: tuple = ()  # backticked paths in the `Exclusive holds` item; a trailing `/` is a directory
    generated: tuple = ()  # (glob, regenerate command) pairs
    check_commands: tuple = ()  # the `run:` commands of the repo-specific checks, in order
    post_reviews: bool = False
    risk_paths: tuple = ()  # globs; a PR touching one is reviewed as L
    tier_s_lines: int | None = None  # S: at most this many changed lines ...
    tier_s_files: int | None = None  # ... in at most this many files
    tier_l_lines: int | None = None  # L: more than this many changed lines
    verify_groups: tuple = ()  # the Verify section's commands: a tuple of groups, each a tuple of command strings
    has_verify: bool = False  # the config has a `## Verify` section at all
    problems: tuple = ()  # Problem entries

    @classmethod
    def load(cls, repo) -> "Config":
        try:
            text = (Path(repo) / common.CONFIG).read_text(encoding="utf-8", errors="replace")  # as the guard reads it
        except OSError:
            return cls()
        return cls.parse(text)

    @classmethod
    def parse(cls, text: str) -> "Config":
        problems: list = []
        shared = _nested(common.section_of(text, "Shared files"), _SHARED_ITEM)
        paths = []
        for line in shared:
            m = re.match(r"\s+[-*]\s+`([^`]+)`", line)
            if m:
                paths.append(m.group(1))
            else:
                problems.append(Problem(SHARED, f"shared-file entry without a backticked path: {line.strip()}"))
        generated = []
        for line in _nested(common.section_of(text, "Shared files"), _GENERATED_ITEM):
            m = re.match(r"\s+[-*]\s+`([^`]+)`\s*:\s*`([^`]+)`", line)
            if m:
                generated.append((m.group(1), m.group(2)))
            else:
                problems.append(Problem(GENERATED, f"generated entry is not `glob`: `command`: {line.strip()}"))
        review = common.section_of(text, "Review")
        commands, run_problems = _check_commands(review)
        problems += run_problems
        holds = tuple(re.findall(r"`([^`]+)`", _item_text(common.section_of(text, "Slots"), "Exclusive holds")))
        slots = re.search(r"\*\*Slots:\*\*\s*(\d+)", common.section_of(text, "Slots") or "")
        s_lines, s_files, l_lines = _tiers(review)
        return cls(
            slots=int(slots.group(1)) if slots else None,
            shared_files=tuple(paths),
            exclusive_holds=holds,
            generated=tuple(generated),
            check_commands=tuple(commands),
            # Same pattern as hooks/guard.py's POST_FLAG, over the whole file as the guard reads it.
            post_reviews=_post_reviews(text),
            risk_paths=_risk_paths(review),
            tier_s_lines=s_lines,
            tier_s_files=s_files,
            tier_l_lines=l_lines,
            verify_groups=_verify_groups(common.section_of(text, "Verify")),
            has_verify=common.section_of(text, "Verify") is not None,
            problems=tuple(problems),
        )

    def problems_of(self, kind: str) -> list:
        return [p for p in self.problems if p.kind == kind]


_SHARED_ITEM = re.compile(r"-\s+\*\*Shared files:?\*\*")
_GENERATED_ITEM = re.compile(r"-\s+\*\*Generated:?\*\*")
_POST_FLAG = re.compile(r"^[-*\s]*Post reviews[:*\s]*(yes|no)\b", re.IGNORECASE | re.MULTILINE)
_BULLET = re.compile(r"\s+[-*]\s+")


def _nested(section, item):
    """The nested bullet lines under the top-level list item matching `item`."""
    out, inside = [], False
    for line in (section or "").splitlines():
        if line.strip() and not line.startswith((" ", "\t")):
            inside = bool(item.match(line))
            continue
        if inside and _BULLET.match(line):
            out.append(line)
    return out


def _post_reviews(text):
    m = _POST_FLAG.search(text)
    return bool(m) and m.group(1).lower() == "yes"


_LABEL = re.compile(r"^\s*-\s+\*\*Repo-specific checks:?\*\*")
_MARKER = re.compile(r"(?<![\w-])run:")
_COMMAND = re.compile(r"(?<![\w-])run:\s*`([^`]+)`(?!`)")


def _check_commands(review):
    """(commands, problems) from the `Repo-specific checks` item: a command is marked `run: `cmd``.

    Opt-in: plain backticked text (a file name, a flag) is prose and never executed. A `run:` marker that
    yields no command (empty, nested or doubled backticks) is a problem, so it can't vanish quietly.
    """
    commands, problems, inside = [], [], False
    for line in (review or "").splitlines():
        if not line.startswith((" ", "\t")) and line.strip():
            inside = bool(_LABEL.match(line))
        if inside:
            found = [c.strip() for c in _COMMAND.findall(line) if c.strip()]
            commands += found
            bad = len(_MARKER.findall(line)) - len(found)
            problems += [Problem(RUN, "`run:` marker without a single `backticked` command")] * max(0, bad)
    return commands, problems


_VERIFY_ITEM = re.compile(r"[-*]\s+`([^`]+)`")
THEN = "--then"


def _verify_groups(section):
    """The commands of the `Verify` section as groups: a top-level bullet that opens with a backticked span.

    Only the first backticked span of a bullet is the command, and only when it starts the bullet; text
    after it (`(errors fail; warnings don't)`) and every bullet or paragraph that doesn't start with a span
    is prose. A bullet whose span is exactly `--then` starts the next group, which depends on the ones
    before it (verify skips it when one fails). Empty groups are dropped.
    """
    groups = [[]]
    for line in (section or "").splitlines():
        m = _VERIFY_ITEM.match(line)  # no leading space: nested bullets are prose
        if not m:
            continue
        cmd = m.group(1).strip()
        if cmd == THEN:
            groups.append([])
        elif cmd:
            groups[-1].append(cmd)
    return tuple(tuple(g) for g in groups if g)


def _item_text(review, label):
    """The text of the top-level item starting `- **<label>`, on one line (continuations joined)."""
    lines = (review or "").splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"-\s+\*\*{label}", line):
            text = [line]
            for more in lines[i + 1:]:
                if not more.startswith((" ", "\t")):
                    break
                text.append(more.strip())
            return " ".join(text)
    return ""


def _risk_paths(review):
    text = _item_text(review, "Risk paths")
    # Globs come after the "):" that closes the parenthetical; `none` gives no backticked glob.
    return tuple(re.findall(r"`([^`]+)`", text.split("):", 1)[-1]))


def _tiers(review):
    text = _item_text(review, "Tiers")
    s = re.search(r"S is at most <?(\d+)>? changed lines in at most <?(\d+)>? files", text)
    l = re.search(r"L touches[^;.]*? more than <?(\d+)>? lines", text)
    return (int(s.group(1)) if s else None, int(s.group(2)) if s else None, int(l.group(1)) if l else None)
