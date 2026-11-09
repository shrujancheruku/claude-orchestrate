# Orchestrate acts only on tracker text from trusted authors, and a fork PR is never the run's own

## Context

Orchestrate runs on public repos, where any GitHub user can comment on an issue or open a pull request from a fork. On 2026-11-09 a first-time contributor opened #548 and #549 from a fork against two `ready-for-agent` tickets of map #533, one of them on the risk path `scripts/merge`. A scout sweep of origin/main (recorded in #551) found that nothing in `scripts/`, `agents/`, `skills/` or `hooks/` checks who wrote the text it acts on:

- a worker and a reviewer read the ticket with every comment, and an `## Agent Brief` comment from anyone overrides the body, so a stranger can rewrite a spec;
- `lead-route` and `lead-brief` parse stop, ADR-hold and resend markers, risk notes, `tier:` and discovered-work lines from any comment or review;
- fork PRs count as held paths in `holds` and as a ticket's PR in `claims` and `worker-report`, so an outside PR can hold the run's files;
- the worker addresses every unresolved review thread, whoever opened it.

The guard (ADR-0002) bounds what an agent can run, not what it is told to build. Labels are the one input only collaborators can set, which is why `ready-for-agent` is the start gate.

## Decision

- **A trusted author** is one whose `author_association` on the repo is `OWNER`, `MEMBER` or `COLLABORATOR`, or whose login the config's optional `Trusted authors:` line names. One function owns the rule; every script and prompt that reads tracker text goes through it (ADR-0006).
- **Only trusted text steers the run.** Comments, reviews and review threads from anyone else are never parsed for markers, never read as a brief or a spec change, and never addressed by a worker. They are counted, so a run can say they exist, but their text does not enter an agent's instructions.
- **A ticket body is trusted once a trusted author has applied `ready-for-agent`.** Only collaborators can label, and only the author or a collaborator can edit a body, so no further check applies to it.
- **A fork PR is never the run's own.** A PR whose head is in another repository (`isCrossRepository`) is not a ticket's PR, holds no path, and is not matched by a worker report. `merge` keeps refusing to act on one.
- **An untrusted unresolved review thread still blocks merge.** `merge` names it as untrusted so the developer can resolve or dismiss it; blocking is the safe direction, since an outsider can only stall a merge, not cause one.

## Consequences

- An outside contributor's comment or PR can no longer change what a worker builds or what the lead reads as a stop or a risk note. A developer who wants an outside voice heard restates it in their own comment or adds the login to `Trusted authors:`.
- Repos where non-collaborators wrote `## Agent Brief` comments that agents followed lose that behaviour: a breaking change, released as a major bump.
- The tracker queries carry `authorAssociation` and the author login, so each comment read costs a little more.
- The guide gains a section on running on a public repo, naming the GitHub settings that help (interaction limits, approval for fork workflows).
- Rejected: making the repo private (the plugin is installed from it); filtering by the presence of a marker's exact wording alone (anyone can type it); trusting anyone who has ever had a PR merged (`CONTRIBUTOR` is too wide for text that becomes a spec); leaving the rule to each prompt (ADR-0006: a mechanical rule the prompts restate becomes a script).
