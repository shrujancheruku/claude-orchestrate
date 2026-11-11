"""The one reader of `.claude/orchestration.md` for the scripts: `Config.load(repo)`.

The markdown grammar lives here and nowhere else in scripts/ (hooks/guard.py is standalone, ADR-0003, and
keeps its own post-reviews reader; tests/test_config.py holds the two to the same answer). A malformed
entry never raises: it lands in `Config.problems` and each caller decides what to do with it.

Stdlib only, Python 3.9+. A library: imported, never run.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import NamedTuple

import common

# Kinds of Problem, so a caller can act on the ones it cares about.
RUN, SHARED, GENERATED = "run", "shared", "generated"
SIZES = ("S", "M", "L")  # in order: a `size:` label is a floor on this scale
DEFAULT_WORKER_MODEL = "sonnet"  # a config with no Worker row
DEFAULT_S_FILES = 3  # the S file limit when the config does not set one


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
    merge_gates: tuple = ()  # globs of the Gates section's Merge gates line; a PR touching one is merged by the developer (#656)
    tier_s_lines: int | None = None  # S: at most this many changed lines ...
    tier_s_files: int | None = None  # ... in at most this many files
    tier_l_lines: int | None = None  # L: more than this many changed lines
    verify_groups: tuple = ()  # the Verify section's commands: a tuple of groups, each a tuple of command strings
    has_verify: bool = False  # the config has a `## Verify` section at all
    orchestrator_model: str | None = None  # the Models table's Orchestrator row; None when blank, the old wording or missing
    lead_model: str | None = None  # the Models table's Lead row, read the same way; None when blank or missing
    worker_model: str | None = None  # the Worker row's first word: the model of every ticket that is not small
    worker_small_model: str | None = None  # the Worker row's `; <model> for S tickets` clause; None when absent or unparsed
    ci_wait_limit: int | None = None  # minutes: the optional `**CI wait limit:**` line; None when absent or not a number
    board: str | None = None  # the `- **Board:** <project URL>` line setup writes; None when absent
    board_bad: str | None = None  # the Board line's text when it is present but names no URL; None when it parses or is absent (#738)
    trusted_authors: tuple = ()  # logins on the optional `- **Trusted authors:**` line (Backlog): trusted besides OWNER, MEMBER, COLLABORATOR (ADR-0015)
    idle_window: int = 10  # minutes: the Velocity section's `**Idle window:**`; how long a slot may sit idle with work waiting before the lead is woken (#716)
    hold_threshold: int = 3  # the Velocity section's `**Expensive hold:**`: takeable tickets one held PR blocks before the digest asks the developer
    velocity_watch: bool = True  # the Velocity section's `**Velocity watch:** on|off`; a missing section is on
    update_required: bool = False  # the PRs and merging section's `**Merge:** up to date: required`; else a behind PR with no file overlap merges without an update (#651)
    pr_title_ticket: bool = False  # the PR title convention ends in ` (#<n>)`: the gate then requires the closed ticket's number (#695)
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
            merge_gates=_merge_gates(common.section_of(text, "Gates")),
            tier_s_lines=s_lines,
            tier_s_files=s_files,
            tier_l_lines=l_lines,
            verify_groups=_verify_groups(common.section_of(text, "Verify")),
            has_verify=common.section_of(text, "Verify") is not None,
            orchestrator_model=_model_row(common.section_of(text, "Models"), "orchestrator"),
            lead_model=_model_row(common.section_of(text, "Models"), "lead"),
            worker_model=_model_row(common.section_of(text, "Models"), "worker"),
            worker_small_model=_worker_small_model(common.section_of(text, "Models")),
            ci_wait_limit=_ci_wait_limit(text),
            board=_board(text),
            board_bad=_board_bad(text),
            trusted_authors=_trusted_authors(common.section_of(text, "Backlog")),
            pr_title_ticket=_pr_title_ticket(common.section_of(text, "PRs and merging")),
            update_required=_update_required(common.section_of(text, "PRs and merging")),
            problems=tuple(problems),
            **_velocity(common.section_of(text, "Velocity")),
        )

    def is_shared(self, path: str) -> bool:
        return shared_match(path, self.shared_files)

    def model_for(self, labels, touch_map_paths, investigate=False) -> str:
        """The worker model for a ticket (ADR-0006: the rule is a script's, not the skill's prose).

        `labels`: the ticket's labels. `touch_map_paths`: the touch map's paths, or None while the scout has not
        produced one (an unlabelled ticket then takes the default; `frontier --touch-map` asks again). `investigate`:
        the title or body says "investigate" or "design". The small model (the Worker row's `; <model> for S tickets`
        clause) goes to a `size:S` ticket, or an unlabelled one whose touch map is within the S file limit, and only
        when the touch map hits no risk path; everything else gets the default (the row's first word, else `sonnet`).
        A cell that does not parse has no small model, so a guess never routes toward haiku."""
        default = self.worker_model or DEFAULT_WORKER_MODEL
        if investigate or not self.worker_small_model:
            return default
        sized = [x for x in SIZES if f"size:{x}" in set(labels or ())]
        if sized and sized != ["S"]:  # any bigger label wins (ADR-0016: a label is a floor)
            return default
        paths = None if touch_map_paths is None else list(touch_map_paths)
        if paths is not None and (self.risk_hit(paths) or len(set(paths)) > self._s_files()):
            return default
        if sized or paths is not None:  # `size:S`, or an unlabelled ticket whose known map is small and safe
            return self.worker_small_model
        return default

    def _s_files(self) -> int:
        return self.tier_s_files if self.tier_s_files is not None else DEFAULT_S_FILES

    def risk_hit(self, paths) -> bool:
        """Whether any of `paths` matches a risk path glob (a `dir/` touch-map entry stands for a file under it)."""
        return any(shared_match(p + "x" if p.endswith("/") else p, self.risk_paths) for p in paths)

    def merge_gate_for(self, paths):
        """The first Merge gates glob (config order) that any of `paths` matches, else None (a `dir/` entry stands for a file under it).

        A gated PR is never merged by a script (#656): `merge` refuses it and the developer merges it by hand."""
        for glob in self.merge_gates:
            if any(shared_match(p + "x" if p.endswith("/") else p, (glob,)) for p in paths):
                return glob
        return None

    def tier_for(self, lines, files, labels=(), risk_hit=False) -> str:
        """The reviewer tier `S`, `M` or `L` from a diff's changed lines and file count.

        A risk path is L. Otherwise S is at most the S line and file limits, L is more than the L line limit, M is the
        rest (a limit the config does not set cannot make a PR S or L). A `size:` label is a floor, never a ceiling
        (ADR-0016): a label above the computed tier raises it, one below is ignored."""
        if risk_hit or (self.tier_l_lines is not None and lines > self.tier_l_lines):
            computed = "L"
        elif (self.tier_s_lines is not None and self.tier_s_files is not None
              and lines <= self.tier_s_lines and files <= self.tier_s_files):
            computed = "S"
        else:
            computed = "M"
        floor = max((SIZES.index(x) for x in SIZES if f"size:{x}" in set(labels or ())), default=0)
        return SIZES[max(SIZES.index(computed), floor)]

    def problems_of(self, kind: str) -> list:
        return [p for p in self.problems if p.kind == kind]


def for_checkout(cwd) -> "Config":
    """The config of the main checkout that owns `cwd` (a worktree, a subdirectory or the checkout itself).

    The one lookup every script uses (#535): a PR that edits `.claude/orchestration.md` must not edit the gate, the
    tiers or the shared-file list that judge it, so the trusted copy is the main checkout's, never the PR's worktree.
    Two deliberate exceptions: `verify --config` searches upward from its cwd, because a worker verifies its own
    branch's Verify section on purpose, and the guard has its own file reader (`python3 -I`).
    """
    import repo  # here, not at the top: `repo` imports `common` as this module does, and only this function needs it
    return Config.load(repo.main_checkout(cwd))


def shared_match(path: str, patterns) -> bool:
    """The one Shared files rule: a path equals a pattern or matches it as a glob, case-sensitively (not fnmatch.fnmatch, which folds case on macOS and Windows)."""
    return any(path == p or fnmatch.fnmatchcase(path, p) for p in patterns)


_SHARED_ITEM = re.compile(r"-\s+\*\*Shared files:?\*\*")
_GENERATED_ITEM = re.compile(r"-\s+\*\*Generated:?\*\*")
_CI_WAIT = re.compile(r"^[-*\s]*\*\*CI wait limit:?\*\*:?\s*(\d+)\b", re.IGNORECASE | re.MULTILINE)
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


def _merge_gates(gates):
    """The backticked globs of the Merge gates item's first sentence; a later sentence may quote a PR title in backticks."""
    text = _item_text(gates, "Merge gates")
    return tuple(re.findall(r"`([^`]+)`", re.split(r"\.\s", text, maxsplit=1)[0]))


def _pr_title_ticket(section) -> bool:
    """Whether the `PR title:` item asks for the ticket suffix, written `(#<n>)` (#695). Only the title sentence counts:
    the item's `Body:` part names the closing reference `#<n>` and is cut off."""
    text = _item_text(section, "PR title")
    return "(#<n>)" in re.split(r"\*\*Body", text, maxsplit=1)[0]


def _update_required(section) -> bool:
    """True only for `up to date: required` on the `Merge:` item; a missing item, `when overlapping` or other text is the default (#651)."""
    # The value is the item's first words; the explanation after it may name `up to date: required` without asking for it.
    return bool(re.match(r"-\s+\*\*Merge:\*\*\s*`?up to date:\s*required\b", _item_text(section, "Merge:"), re.IGNORECASE))


def _tiers(review):
    text = _item_text(review, "Tiers")
    s = re.search(r"S is at most <?(\d+)>? changed lines in at most <?(\d+)>? files", text)
    l = re.search(r"L touches[^;.]*? more than <?(\d+)>? lines", text)
    return (int(s.group(1)) if s else None, int(s.group(2)) if s else None, int(l.group(1)) if l else None)


def _velocity(section) -> dict:
    """The Velocity section's three lines as Config fields. A missing section, a missing line or one that does not
    parse keeps the default (idle window 10 minutes, expensive hold 3, watch on). The window reads `10m` or `10`."""
    out = {}
    m = re.search(r"\*\*Idle window:?\*\*:?\s*(\d+)", section or "", re.IGNORECASE)
    if m and int(m.group(1)) > 0:
        out["idle_window"] = int(m.group(1))
    m = re.search(r"\*\*Expensive hold:?\*\*:?\s*(\d+)", section or "", re.IGNORECASE)
    if m and int(m.group(1)) > 0:
        out["hold_threshold"] = int(m.group(1))
    m = re.search(r"\*\*Velocity watch:?\*\*:?\s*(on|off)\b", section or "", re.IGNORECASE)
    if m:
        out["velocity_watch"] = m.group(1).lower() == "on"
    return out


def _ci_wait_limit(text):
    """Minutes from `- **CI wait limit:** <minutes>`; the template's `<minutes ...>` placeholder is not a number.

    Only the Review section is read; `0` counts as set here and ci-times treats it as unset (the default).
    """
    m = _CI_WAIT.search(common.section_of(text, "Review") or "")
    return int(m.group(1)) if m else None


_BOARD_LINE = re.compile(r"^-[ \t]+\*\*Board:\*\*[ \t]*(.*)$", re.M)
_BOARD_URL = re.compile(r"<?(https?://[^\s<>]+)>?")


def _board_line(text):
    """The text after `- **Board:**` on the config's Board line, or None when the line is absent. An HTML comment is
    no line (the template shows the line inside one)."""
    m = _BOARD_LINE.search(re.sub(r"<!--.*?-->", "", text, flags=re.S))
    return m.group(1).strip() if m else None


def _board(text):
    """The Projects board URL from the line `- **Board:** <url>` (a top-level bullet; setup writes it), bare or as an
    `<url>` autolink (#738), else None. A line that is present but names no URL is `_board_bad`'s."""
    line = _board_line(text)
    m = _BOARD_URL.fullmatch(line.rstrip(".,;")) if line else None
    return m.group(1) if m else None


def _board_bad(text):
    """The Board line when it is present but does not parse (so a caller can tell it from no line at all), else None."""
    line = _board_line(text)
    return None if line is None or _board(text) else line


_TRUSTED_LINE = re.compile(r"^[-*]\s+\*\*Trusted authors:?\*\*:?(.*)$", re.IGNORECASE | re.MULTILINE)
_LOGIN = re.compile(r"@?([A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?)")


def _trusted_authors(backlog):
    """The logins named (backticked) on the `- **Trusted authors:**` line of the Backlog section, lowercased: GitHub
    logins are case-insensitive. The template's `<login>` placeholder is not a login, so it names no one."""
    m = _TRUSTED_LINE.search(backlog or "")
    spans = re.findall(r"`([^`]+)`", m.group(1)) if m else []
    return tuple(dict.fromkeys(g.group(1).lower() for g in (_LOGIN.fullmatch(x.strip()) for x in spans) if g))


_OLD_ORCHESTRATOR ="the session's model"  # the template's wording before the lead layer: means no model


_SMALL_CLAUSE = re.compile(r";\s*`?([\w.\-]+)`?\s+for\s+S\s+tickets", re.IGNORECASE)


def _worker_small_model(models):
    """The model of the Worker row's `; <model> for S tickets` clause, or None (no clause, or a cell that doesn't parse)."""
    for line in (models or "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].lower() == "worker":
            m = _SMALL_CLAUSE.search(cells[1])
            return m.group(1).lower() if m else None
    return None


def _model_row(models, role):
    """The model of the Models table's row for `role` (Orchestrator, Lead): its first word, or None.

    The cell reads `opus (used when the lead launches it; ...)`: the model is the first word, the rest is
    prose. Blank, the old `the session's model` wording and a missing row are all None, so a hand-started
    session keeps its own model.
    """
    for line in (models or "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].lower() == role:
            cell = cells[1]
            if not cell or cell.lower().startswith(_OLD_ORCHESTRATOR):
                return None
            return cell.split()[0].strip("`,;:()")
    return None
