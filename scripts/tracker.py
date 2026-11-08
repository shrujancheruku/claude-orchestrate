"""The takeable rule and the frontier, as pure functions on the open issues, and the Snapshot that holds them (ADR-0006, #273, #337).

Stdlib only, Python 3.9+. A library: imported, never run. The functions never call `gh`; only a `Snapshot` does, through
`github`, and once per part per process. `from_nodes` turns `github.open_issues`' GraphQL nodes into the plain dicts every
other function takes:
    {number, title, url, body, labels (set), claimed, blockers (open native blockers, sorted),
     sub_issues {total, completed}, parent (number | None),
     body_edits [ISO time], comments [{body, created, edits [ISO time]}]}
The issues are open-only, so an issue number absent from them is closed (or does not exist) and blocks nothing.
`Snapshot.blockers` is the one blocker rule (native edges plus the body `Blocked by:` line): frontier, lead-route, gate
and merge all ask it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import github

WAYFINDER_PREFIX = "wayfinder:"
TRIAGE_LABELS = Path("docs/agents/triage-labels.md")
DEFAULTS = {"ready": "ready-for-agent", "human": "ready-for-human", "lead": "lead"}
# The lead label stays fixed (as in scripts/claims): its table row has no role name in column one to match.
ROLES = {"ready-for-agent": "ready", "ready-for-human": "human"}
# The tracker doc's fallback form: a `Blocked by: #1, #2` line (a bullet, quote or bold lead-in allowed). The colon is
# required, so prose like "Blocked by the outage, see #12" or "Blocked by #12" is not a blocker (it would hold a ticket silently).
BLOCKED_LINE = re.compile(r"^[\s>*_-]*blocked[ -]by\b[*_]*:(.*)$", re.I)
ISSUE_REF = re.compile(r"#(\d+)")


def label_strings(root: Path) -> dict:
    """The repo's label strings: the mapping table's second column, by role (first column); a default for a missing row.
    ValueError when the table exists but cannot be read (the label strings would be a guess)."""
    labels = dict(DEFAULTS)
    try:
        text = (root / TRIAGE_LABELS).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return labels
    except OSError as e:
        raise ValueError(f"cannot read {TRIAGE_LABELS}: {e.strerror or e}") from None
    for line in text.splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] in ROLES and re.fullmatch(r"[\w:./-]+", cells[1]):
            labels[ROLES[cells[0]]] = cells[1]
    return labels


def _edit_times(node) -> list:
    return [t["editedAt"] for t in ((node or {}).get("userContentEdits") or {}).get("nodes") or [] if t.get("editedAt")]


def from_nodes(nodes) -> list:
    """Plain issue dicts from raw GraphQL nodes; KeyError, TypeError or ValueError when a node is malformed."""
    out = []
    for n in nodes:
        summary = n.get("subIssuesSummary") or {}
        parent = n.get("parent")
        out.append({
            "number": int(n["number"]), "title": n.get("title") or "", "url": n.get("url") or "",
            "body": n.get("body") or "",
            "body_edits": _edit_times(n),
            "comments": [{"body": c.get("body") or "", "created": c.get("createdAt"), "edits": _edit_times(c)}
                         for c in (n.get("comments") or {}).get("nodes") or []],
            "labels": {x["name"] for x in (n.get("labels") or {}).get("nodes") or []},
            "claimed": ((n.get("assignees") or {}).get("totalCount") or 0) > 0,
            "blockers": sorted(int(b["number"]) for b in (n.get("blockedBy") or {}).get("nodes") or []
                               if str(b.get("state") or "").upper() == "OPEN"),
            "sub_issues": {"total": int(summary.get("total") or 0), "completed": int(summary.get("completed") or 0)},
            "parent": int(parent["number"]) if parent else None,
        })
    return out


def body_blockers(issue) -> list:
    """Every `#<n>` on a `Blocked by:` line of the issue's body, in order, once each."""
    found = []
    for line in (issue.get("body") or "").splitlines():
        m = BLOCKED_LINE.match(line)
        if m:
            found += [int(x) for x in ISSUE_REF.findall(m.group(1)) if int(x) not in found]
    return found


def drift(issues) -> list:
    """(issue, n): the body says blocked by open issue n, and the tracker has no such edge."""
    open_numbers = {i["number"] for i in issues}
    return [(i, n) for i in issues for n in body_blockers(i) if n in open_numbers and n not in i["blockers"] and n != i["number"]]


def open_blockers(issue, open_numbers) -> list:
    """(number, from_body): every open blocker, native first; a body-only one is flagged. An issue never blocks itself."""
    native = [(n, False) for n in issue["blockers"]]
    body = [(n, True) for n in body_blockers(issue)
            if n in open_numbers and n not in issue["blockers"] and n != issue["number"]]
    return native + body


def why_not(issue, labels, issues=None) -> str | None:
    """The first reason a `ready-for-agent` ticket is not takeable (claimed, blocked-by, parent, lead, wayfinder), else None.

    `labels` is {"ready": ..., "lead": ...}. `issues` is the whole snapshot, for body blockers; omitted, only native ones count.
    """
    open_numbers = {i["number"] for i in issues} if issues is not None else set(issue["blockers"])
    if issue["claimed"]:
        return "claimed"
    blockers = open_blockers(issue, open_numbers)
    if blockers:
        return "blocked-by " + " ".join(f"#{n} (body)" if from_body else f"#{n}" for n, from_body in blockers)
    if issue["sub_issues"]["total"] > 0:
        return "parent"
    if labels["lead"] in issue["labels"]:
        return "lead"
    if any(x.startswith(WAYFINDER_PREFIX) for x in issue["labels"]):
        return "wayfinder"
    return None


def takeable(issues, labels) -> list:
    """The takeable tickets, ascending: open, `ready-for-agent`, and none of the reasons in `why_not`."""
    return sorted((i for i in issues if labels["ready"] in i["labels"] and why_not(i, labels, issues) is None),
                  key=lambda i: i["number"])


def backlog(issues, labels) -> list:
    """The unclaimed `ready-for-agent` tickets in the order the board's Backlog column shows them: the takeable ones
    (frontier order, ascending) and then the held ones (blocked), ascending. Parents, `lead` and `wayfinder:` tickets are not
    in it, as they are not work a worker takes."""
    take = takeable(issues, labels)
    taken = {i["number"] for i in take}
    held = sorted((i for i in issues if labels["ready"] in i["labels"] and i["number"] not in taken
                   and not i["claimed"] and i["sub_issues"]["total"] == 0 and labels["lead"] not in i["labels"]
                   and not any(x.startswith(WAYFINDER_PREFIX) for x in i["labels"])), key=lambda i: i["number"])
    return take + held


def closed_parents(issues) -> list:
    """Open issues whose sub-issues are all closed: done work waiting for a human to close the parent."""
    return sorted((i for i in issues if i["sub_issues"]["total"] > 0
                   and i["sub_issues"]["completed"] == i["sub_issues"]["total"]), key=lambda i: i["number"])


def scoped(issues, parent=None, ticket=None) -> list:
    """The sub-issues of one parent, or the one ticket; everything when neither is given."""
    if parent is not None:
        return [i for i in issues if i["parent"] == parent]
    if ticket is not None:
        return [i for i in issues if i["number"] == ticket]
    return list(issues)


class Snapshot:
    """What the tracker says right now: open issues, open PRs, merged PRs of the default branch, and the closed `wontfix`
    tickets still assigned. Each part is read through `github` on first use and kept, so one process reads each once and
    a script that needs only the issues never reads the PRs. `Snapshot.read(path)` serves every part from a file that
    `scripts/snapshot` wrote, and then nothing is fetched."""

    PARTS = ("repo", "issues", "prs", "merged", "abandoned")

    def __init__(self, cwd=None, **parts):
        self.cwd = cwd
        self._parts = {k: v for k, v in parts.items() if k in self.PARTS and v is not None}

    @classmethod
    def open(cls, path, cwd=None):
        """The snapshot in the file `path`, or a fetching one when `path` is None."""
        return cls(cwd) if path is None else cls.read(path)

    @classmethod
    def read(cls, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as e:
            raise ValueError(f"cannot read snapshot {path}: {e.strerror or e}") from None
        if not isinstance(data, dict) or not all(k in data for k in cls.PARTS):
            raise ValueError(f"{path} is not a snapshot")
        for i in data["issues"]:
            i["labels"] = set(i["labels"])
        return cls(**data)

    def write(self, path) -> None:
        data = {k: getattr(self, k) for k in self.PARTS}
        data["issues"] = [dict(i, labels=sorted(i["labels"])) for i in data["issues"]]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data), encoding="utf-8")

    def _part(self, name, fetch):
        if name not in self._parts:
            self._parts[name] = fetch()
        return self._parts[name]

    @property
    def repo(self) -> dict:
        return self._part("repo", lambda: github.repo_info(self.cwd))

    @property
    def default_branch(self) -> str:
        name = (self.repo.get("defaultBranchRef") or {}).get("name")
        if not isinstance(name, str):
            raise github.GhError(github.MALFORMED, "gh returned no default branch")
        return name

    @property
    def issues(self) -> list:
        return self._part("issues", lambda: from_nodes(github.open_issues(self.repo, cwd=self.cwd)))

    @property
    def prs(self) -> list:
        return self._part("prs", lambda: github.open_prs_with_heads(cwd=self.cwd))

    @property
    def merged(self) -> list:
        return self._part("merged", lambda: github.merged_prs(self.default_branch, cwd=self.cwd))

    @property
    def abandoned(self) -> list:
        """Numbers of closed `wontfix` tickets that still have an assignee."""
        return self._part("abandoned", lambda: github.abandoned_tickets(self.repo, cwd=self.cwd))

    def blockers(self, issue) -> list:
        """(number, from_body) for each open blocker of an issue (a dict, or a number: absent means closed, so none)."""
        if not isinstance(issue, dict):
            issue = next((i for i in self.issues if i["number"] == issue), None)
            if issue is None:
                return []
        return open_blockers(issue, {i["number"] for i in self.issues})
