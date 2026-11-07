# GitHub is the tracker the scripts read

## Context

Every script that reads the tracker calls `gh`: `gate`, `merge`, `holds`, `board`, `lead-route`, `lead-brief`, and soon `claims` (#262) and `frontier` (#273). The two newest read GitHub-only fields that have no equivalent elsewhere: `blockedBy` (native issue dependencies), `subIssuesSummary` and `parent` (sub-issues).

Meanwhile `/setup-matt-pocock-skills` ships three tracker docs (GitHub, GitLab, local), and `skills/setup/config-template.md` offers a "Backlog in config" form. A reader will ask whether orchestrate runs on another tracker. It does not. #239 found that an adapter with one implementation is a hypothetical seam, so no second tracker has a caller to shape one.

## Decision

- **The scripts read GitHub through `scripts/github.py`, and only GitHub.** There is no adapter interface behind it.
- **`docs/agents/issue-tracker.md` and `docs/agents/triage-labels.md` stay the prompts' reference** for operations and label strings. The prompts still read the tracker the doc's way; the scripts read `gh`.
- **`/orchestrate:setup` refuses to configure a repo whose tracker doc names a tracker other than GitHub**, and says why in one line, pointing here.
- **The config's "Backlog in config" form is for a GitHub repo without the tracker docs,** not for another host.

## Consequences

- A second tracker is a fresh effort that starts by reopening this ADR.
- A GitHub field rename breaks the scripts. The CLI tests on the fake `gh` catch it.
- Rejected: an adapter behind `github.py` (one implementation, no second caller to shape its interface); keeping the prose ambiguous (it already was, and that is what this ADR fixes).
- Reopen when a second host has a real user.
