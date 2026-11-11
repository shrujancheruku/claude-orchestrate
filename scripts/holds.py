"""holds: the held-path rule as a library; `scripts/holds` is its command line, `gate` and `rescue` import it.

What is held, and does a set of candidate paths overlap it. A path is held by an open PR (its diff plus its ticket's touch
map, #398: the file list the Snapshot cached, else one `gh` read per PR), by a building ticket (its touch map in the state
file, or its worktree's changes when this session's own worker has no record), by an Elsewhere ticket (a live worker in
another session: the changes in its worktree, ADR 0009) or by an exclusive hold (the config). The ticket states come from
`claims`: one tracker Snapshot per run, never a `gh` call per ticket. Paths are compared as files: any `:line` or line
range is stripped, so a different section of one file still overlaps.

A release-please PR (head branch starts `release-please--`) holds nothing: it is never merged by the orchestrator and no PR
edits its files, so its manifest, changelog and version files can never be a real overlap. The test is the branch prefix,
not a file list, so a human PR that touches `CHANGELOG.md` is still held.

The library returns data and raises (ADR-0014): `find` gives a `Result` of `Held` rows and `Overlap` rows, and the callers
decide what a failure (`github.GhError`, a `ValueError` from the state file or the touch-map grammar) means for their exit
code. `Result.lines()` is the one rendering of the holder text, shared by the CLI and the gate.

Two open PRs on one file are an ordering, not a failure (#399): with `pr` (the gate's check) the PR with the lower number
is the older claim and passes; the younger waits (`waits-for`). A file also held by a building, elsewhere or exclusive
holder is a real overlap whatever the PRs. Without `pr` (dispatch, no PR yet) every open PR holder counts.
Stdlib only, Python 3.9+.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import claims
import common
import config
import github
import touchmap
import tracker
from repo import STATE, ticket_of_branch, worktree_changes

RELEASE_PLEASE_PREFIX = tracker.RELEASE_PLEASE_PREFIX

# Overlap kinds. COUNTED fails the check; the rest do not. YOUNGER is a candidate whose only holders are open PRs
# that all have a higher number than the checked PR: they will wait for it, it does not wait for them.
COUNTED, SHARED, YOUNGER, WAITS_FOR = "counted", "shared", "younger", "waits-for"
# The gate's check only: every holder of the path is a ticket still building (or in another session), so the PR is fine and
# waits for that ticket to merge; a send-back is for an overlap the PR itself must fix (#535).
WAITS_TICKET = "waits-ticket"


@dataclass(frozen=True)
class Held:
    path: str
    holders: tuple  # `open PR #9`, `open PR #9 file`, `building #12`, `elsewhere #13`, `an exclusive hold`

    def render(self) -> str:
        return f"{self.path} (held by {', '.join(self.holders)})"


@dataclass(frozen=True)
class Overlap:
    kind: str
    path: str  # the candidate
    holders: tuple
    pr: int = None  # WAITS_FOR: the older open PR to wait for
    ticket: int = None  # WAITS_FOR: that PR's ticket, when its branch names one; WAITS_TICKET: the lowest building ticket

    def render(self) -> str:
        shown = f"{self.path} (held by {', '.join(self.holders)})"
        return {COUNTED: f"overlap: {shown}", SHARED: f"shared (not counted): {shown}",
                YOUNGER: f"younger PR (not counted): {shown}"}[self.kind]


@dataclass(frozen=True)
class Result:
    held: list = field(default_factory=list)
    overlaps: list = field(default_factory=list)

    @property
    def counted(self) -> list:
        return [o for o in self.overlaps if o.kind == COUNTED]

    @property
    def waits(self) -> list:
        return [o for o in self.overlaps if o.kind == WAITS_FOR]

    @property
    def ticket_waits(self) -> list:
        return [o for o in self.overlaps if o.kind == WAITS_TICKET]

    @property
    def code(self) -> int:
        """The CLI's exit code: 1 a counted overlap, 3 only an older open PR or a building ticket in the way, else 0."""
        return 1 if self.counted else 3 if self.waits or self.ticket_waits else 0

    def wait_lines(self) -> list:
        """One `waits for #<ticket> (PR #<n>) to merge: <paths>` line per older PR, in PR order, then one
        `waits for #<ticket> (still building) to merge: <paths>` line per building ticket."""
        by_pr: dict = {}
        for o in self.waits:
            by_pr.setdefault(o.pr, []).append(o)
        lines = []
        for n, rows in sorted(by_pr.items()):
            who = f"#{rows[0].ticket} (PR #{n})" if rows[0].ticket is not None else f"PR #{n}"
            lines.append(f"waits for {who} to merge: {', '.join(sorted({o.path for o in rows}))}")
        by_ticket: dict = {}
        for o in self.ticket_waits:
            by_ticket.setdefault(o.ticket, []).append(o)
        for n, rows in sorted(by_ticket.items()):
            lines.append(f"waits for #{n} (still building) to merge: {', '.join(sorted({o.path for o in rows}))}")
        return lines

    def lines(self) -> list:
        """The CLI's stdout: the held paths (or `none`), each overlap that is not an ordering, then the verdict lines."""
        out = [h.render() for h in self.held] or ["none"]
        out += [o.render() for o in self.overlaps if o.kind not in (WAITS_FOR, WAITS_TICKET)]
        if self.counted:
            return out
        out += self.wait_lines()
        return out if self.waits or self.ticket_waits else out + ["overlap: none"]


def plain(entry) -> str:
    """A touch-map entry (an Entry, or text holding one) as a file path; unparseable text stands for itself."""
    if isinstance(entry, touchmap.Entry):
        return entry.path
    try:
        found = touchmap.parse(entry)
    except touchmap.TouchMapError:
        return str(entry).strip()
    return found[0].path if found else ""


def state_paths(value) -> list:
    """The paths of a state file's `touch_map`: the canonical string `rebuild-state` writes (or an older list of entries)."""
    if isinstance(value, list):
        value = ", ".join(e for e in value if isinstance(e, str))
    if not isinstance(value, str):
        return []
    if value.startswith(touchmap.UNPARSED):
        return [touchmap.HOLDS_ALL]
    try:
        return [e.path for e in touchmap.parse(value)]
    except touchmap.TouchMapError:
        return [touchmap.HOLDS_ALL]  # a hand edit the grammar rejects fails closed: it holds every path, never none


def candidate_paths(args: list, literal: bool = False) -> tuple:
    """(paths, ignored) for the `--touch-map` arguments: `ignored` lists the empty arguments, which the caller reports.
    Each is parsed by `touchmap.parse`, so one quoted string and separate arguments give the same paths; with `literal`,
    each argument is one path, unparsed. `none` is an empty touch map, not an ignored argument.
    An entry off the grammar raises touchmap.TouchMapError."""
    paths, ignored = [], []
    for arg in args:
        if not literal and arg.strip().lower() in ("none", "none."):
            continue
        found = [arg] if literal else [e.path for e in touchmap.parse(arg)]
        if not found:
            ignored.append(arg)
        paths.extend(found)
    return paths, ignored


def claimed_paths(found: dict, own, base: str, warn=None) -> tuple:
    """({ticket: [paths]} for the Building claims, {ticket: [paths]} for the Elsewhere claims), the ticket `own` left out.
    A Building claim holds its touch map (the state-file entries, through `plain`); a Building claim with no record
    (this session's own worker, `worker unknown`) has no touch map, so it holds its worktree's changes and is labelled
    building. An Elsewhere claim holds its worktree's changes, the ones a live worker has made so far (ADR 0009). An
    open PR is a different claim state, so a ticket with a PR is held through the PR (`pr_paths`), not here. A Parked
    claim (scripts/park-pr closed its PR, #708) is neither, so it holds nothing. A Building
    claim with a record and a worktree holds its touch map plus the worktree's changes (#398). `warn(text)` is told of a
    building ticket whose touch map is unparsed (it holds every path)."""
    tickets, others = {}, {}
    for n, c in sorted(found.items()):
        if n == own:
            continue
        if c.state in ("building", "paused"):  # a paused worker is resumed, so its map stays held (#657)
            if c.worker is None:
                paths = worktree_changes(c.worktree, base) if c.worktree else []
            else:
                paths = state_paths(c.worker.get("touch_map"))
                if touchmap.HOLDS_ALL in paths:
                    if warn:
                        warn(f"building #{n} has an unparsed touch map, so it holds every path until its state is fixed or it finishes")
                elif c.worktree:
                    # The map is a prediction: an edit outside it, committed or not, is held too (#398).
                    paths = paths + [p for p in worktree_changes(c.worktree, base) if p not in paths]
            if paths:
                tickets[n] = paths
        elif c.state == "elsewhere" or (c.state == "with-lead" and c.lock and c.lock[0] in ("ours", "foreign-live") and c.worktree):
            # A `lead` ticket with a live worker's worktree still holds its uncommitted changes, as it did before the
            # claims module: the claims state says with-lead, but a live lock means someone is still writing there.
            paths = worktree_changes(c.worktree, base)
            if paths:
                others[n] = paths
    return tickets, others


def covers(held: str, path: str) -> bool:
    """Whether a held entry and a path name the same file, or one is a directory (`db/`) containing the other (touchmap.overlaps)."""
    return touchmap.overlaps(touchmap.Entry(held), touchmap.Entry(path))


def pr_paths(files: list, record) -> tuple:
    """(paths, off_map) for an open PR: its changed files joined with its ticket's touch map from the worker record (#398),
    so a PR that opened with an empty commit still holds its declared map. `off_map` is the set of PR files outside a
    non-empty map, which the output names `open PR #n file`."""
    mapped = state_paths(record.get("touch_map")) if isinstance(record, dict) else []
    if touchmap.HOLDS_ALL in mapped:
        mapped = []  # an unparsed map is reported for the building claim; the PR still holds its diff
    paths = list(files) + [p for p in mapped if p not in files]
    off = {p for p in files if mapped and not any(covers(m, p) for m in mapped)}
    return paths, off


def held_paths(cfg, prs: dict, tickets: dict, others: dict, off_map: dict = None) -> list:
    """The `Held` rows in a stable order. `tickets` is {ticket: [paths]} for the Building tickets, `others` for the
    Elsewhere ones; `prs` is {pr: [paths]}; `off_map` is {pr: {paths}} the PR changed outside its ticket's touch map
    (named `open PR #n file`)."""
    held: dict = {}
    off_map = off_map or {}
    for n, paths in sorted(prs.items()):
        for p in paths:
            held.setdefault(p, []).append(f"open PR #{n} file" if p in off_map.get(n, ()) else f"open PR #{n}")
    for n, paths in sorted(tickets.items()):
        for p in paths:
            held.setdefault(p, []).append(f"building #{n}")
    for n, paths in sorted(others.items()):
        for p in paths:
            held.setdefault(p, []).append(f"elsewhere #{n}")
    for p in cfg.exclusive_holds:
        held.setdefault(plain(p), []).append("an exclusive hold")
    return [Held(p, tuple(who)) for p, who in held.items()]


PR_HOLDER = re.compile(r"open PR #(\d+)(?: file)?$")
TICKET_HOLDER = re.compile(r"(?:building|elsewhere) #(\d+)$")


def overlaps(held: list, candidates: list, cfg, own_pr=None, pr_tickets=None) -> list:
    """The `Overlap` rows for the candidates against the `Held` rows, in candidate order.
    With `own_pr` (the gate's check), an open-PR holder is an ordering: a lower-numbered PR is older and makes this one
    wait (WAITS_FOR, the oldest of them named); a higher-numbered one is younger and is not counted. Any other holder of
    the same path still counts. A shared file (config) is reported, not counted."""
    pr_tickets = pr_tickets or {}
    out = []
    for c in candidates:
        for h in held:
            if not covers(h.path, c):
                continue
            if cfg.is_shared(c) or cfg.is_shared(h.path):  # a directory candidate overlaps the held file under it
                out.append(Overlap(SHARED, c, h.holders))
            elif own_pr is not None and all(PR_HOLDER.match(w) or TICKET_HOLDER.match(w) for w in h.holders):
                # Only PRs and tickets still being built hold it, so the PR is not at fault: it waits on the older open PR
                # if there is one, else on the lowest building ticket; with only younger PRs it is the one they wait for.
                older = [n for n in (int(m.group(1)) for m in map(PR_HOLDER.match, h.holders) if m) if n < own_pr]
                building = [int(m.group(1)) for m in map(TICKET_HOLDER.match, h.holders) if m]
                if older:
                    out.append(Overlap(WAITS_FOR, c, h.holders, pr=min(older), ticket=pr_tickets.get(min(older))))
                elif building:
                    out.append(Overlap(WAITS_TICKET, c, h.holders, ticket=min(building)))
                else:
                    out.append(Overlap(YOUNGER, c, h.holders))
            else:
                out.append(Overlap(COUNTED, c, h.holders))
    return out


LANDS_IN = re.compile(r"^[\s#>*_-]*Lands in\b:?\**:?\s*(.*)$", re.IGNORECASE)


def ticket_paths(body) -> list:
    """The paths a ticket's `Lands in:` line (and the lines after it up to a blank one) names in backticks: the map a
    ticket declares before any scout reads it. [] when it has none or a token is off the touch-map grammar. The lead's
    watch uses it to ask `find` whether a takeable ticket is held, with no model judgement (#716)."""
    lines, inside, heading, found = (body or "").splitlines(), False, False, []
    for line in lines:
        m = LANDS_IN.match(line)
        if m:
            inside, heading = True, line.lstrip().startswith("#")  # a `## Lands in` heading runs to the next heading
            text = m.group(1)
        elif inside and line.lstrip().startswith("#"):
            inside = False
            continue
        elif inside and (line.strip() or heading):
            text = line
        else:
            inside = False
            continue
        for token in re.findall(r"`([^`]+)`", text):
            try:
                found += [e.path for e in touchmap.parse(token)]
            except touchmap.TouchMapError:
                continue
    return list(dict.fromkeys(found))


def held_tickets(snap, repo, tickets, state_file=None, cfg=None) -> tuple:
    """(the tickets held, {PR number: [tickets it holds]}) for `tickets` ({ticket: [paths]}): each is run through `find` as
    a dispatch check, and a ticket with a counted overlap is held. The open PRs among the holders of that overlap are the
    ones that block it; a ticket held only by a building ticket or an exclusive hold blocks no PR. Raises what `find` raises."""
    held, by_pr = set(), {}
    for n, paths in sorted(tickets.items()):
        result = find(snap, repo, state_file, candidates=paths, ticket=n, cfg=cfg)
        if result.counted:
            held.add(n)
        for pr in sorted({int(m.group(1)) for o in result.counted for h in o.holders for m in [PR_HOLDER.match(h)] if m}):
            by_pr.setdefault(pr, []).append(n)
    return held, by_pr


def find(snap, repo, state_file=None, candidates=None, ticket=None, pr=None, cfg=None, warn=None) -> Result:
    """What is held, and which of the candidates overlap it, from the tracker `snap` (a tracker.Snapshot).

    `candidates` are the paths to check (dispatch: the ticket's touch map, with `ticket` leaving its own PR and map out);
    with `pr` (the gate's check) the candidates are that PR's diff and the PR and the ticket its branch names are left out.
    `repo` is the main checkout, which holds the state file (default `<repo>/.claude/orchestrate-state.json`).
    Raises github.GhError (a `gh` failure, a rate limit over the wait budget, a malformed reply) and ValueError or OSError
    (an unreadable state file); the caller maps them. A PR file list the Snapshot cached is served from it; fetched ones
    are written back through `snap.save_cache` when the caller set `snap.use_cache`."""
    repo = Path(repo)
    cfg = cfg or config.for_checkout(repo)
    state_file = Path(state_file) if state_file else repo / STATE
    base = snap.default_branch
    own_pr, own_ticket = None, ticket
    if pr is not None:
        own_pr = common.pr_ref(pr)[0]
        view = github.pr_view(own_pr, "headRefName", cwd=repo)
        own_ticket = ticket_of_branch(view.get("headRefName"))
        candidates = github.pr_files(own_pr, cwd=repo)
    open_prs = [p for p in github.own_prs(snap.prs) if isinstance(p, dict) and p.get("baseRefName", base) == base]
    # The claims are read once from the tracker (open issues, open and merged PRs, the state file, the worktrees).
    # `changes` is a no-op: holds needs each worktree's changed paths, not the dirty and unpushed counts claims
    # would take, and a git failure there would stop a pass that never needed them.
    found = claims.claims(repo, state_file, snap.issues, snap.prs, snap.merged, changes=lambda path, branch: None)
    prs, pr_tickets, off_map = {}, {}, {}
    for p in open_prs:
        if not isinstance(p.get("number"), int):
            raise github.GhError(github.MALFORMED, "gh returned a PR list entry without a number")
        if str(p["number"]) == str(own_pr) or (own_ticket is not None and ticket_of_branch(p.get("headRefName")) == own_ticket):
            continue
        if str(p.get("headRefName") or "").startswith(RELEASE_PLEASE_PREFIX):
            continue  # the release PR's files are never a real overlap, and its diff is not fetched
        pr_tickets[p["number"]] = ticket_of_branch(p.get("headRefName"))
        claim = found.get(pr_tickets[p["number"]])
        # cached in the snapshot, keyed by head SHA (#446), joined with the ticket's touch map (#398)
        record = claim.worker if claim else None  # a draft is open work too: it joins the map like any open PR
        prs[p["number"]], off_map[p["number"]] = pr_paths(snap.pr_files(p), record)
    snap.save_cache()
    tickets, others = claimed_paths(found, own_ticket, base, warn=warn)
    held = held_paths(cfg, prs, tickets, others, off_map)
    return Result(held, overlaps(held, candidates or [], cfg,
                                 own_pr=int(own_pr) if own_pr is not None else None, pr_tickets=pr_tickets))
