"""The takeable rule and the frontier, as pure functions on the open-issues snapshot (ADR-0006, #273).

Stdlib only, Python 3.9+. A library: imported, never run; it never calls `gh`. `from_nodes` turns
`github.open_issues`' GraphQL nodes into the plain dicts every other function takes:
    {number, title, url, body, labels (set), claimed, blockers (open native blockers, sorted),
     sub_issues {total, completed}, parent (number | None)}
The snapshot is open-only, so an issue number absent from it is closed (or does not exist) and blocks nothing.
"""

from __future__ import annotations

import re

WAYFINDER_PREFIX = "wayfinder:"
# The tracker doc's fallback form: a `Blocked by: #1, #2` line (a bold or quoted lead-in allowed). The colon, or a `#`
# right after, is required so prose like "Blocked by the outage, see #12" is not a blocker.
BLOCKED_LINE = re.compile(r"^[\s>*_-]*blocked[ -]by\b[*_]*(?::|(?=\s*#))(.*)$", re.I)
ISSUE_REF = re.compile(r"#(\d+)")


def from_nodes(nodes) -> list:
    """Plain issue dicts from raw GraphQL nodes; KeyError, TypeError or ValueError when a node is malformed."""
    out = []
    for n in nodes:
        summary = n.get("subIssuesSummary") or {}
        parent = n.get("parent")
        out.append({
            "number": int(n["number"]), "title": n.get("title") or "", "url": n.get("url") or "",
            "body": n.get("body") or "",
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
