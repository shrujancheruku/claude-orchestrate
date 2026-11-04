# Brief template

Fill every field from the ticket and the config. Use **pointers** (URLs, config sections, merged PRs), not copies. The worker reads the targets itself, and a pointer can't drift from its source. Keep the fixed preamble word-for-word across briefs, so the workers share a prompt-cache prefix.

```
You are an orchestrate worker. Your instructions are your agent definition; this brief is your ticket.

Ticket: <url>, "<title>". Read it in full, with every comment; it is the spec. An "## Agent Brief" comment, if there is one, overrides the body.
Touch map: <the files the scout expects this ticket to change, each as `path:line` of its entry point>. A prediction, not a fence.
Held paths: <files other tickets hold: open PRs' diffs, building tickets' touch maps, exclusive holds | none>. Changing one means stop and report.
Branch: <number>-<slug>. If it exists on the remote, continue from it.
Merged dependencies: <ticket → PR, one per line | none>
Verify: "<the plugin's absolute scripts/verify path, from the skill's base directory>" <commands from the config>
Shared files: <the config's shared-file rules>
Commits: <convention>. Stage files by explicit path.
PR: title <convention>; body includes "<closing reference>"; body written with the skill whose name ends in `:pr` (e.g. `mattpocock-skills:pr`) when one is listed, else plain.
Human-only steps: <from the config>
Repo rules: .claude/orchestration.md, the instruction files it names, and the domain docs (normally docs/agents/domain.md).
Review findings: <on a later round, "address review <url>"; the worker reads the findings from the PR. After an approve with minors: "take or leave the minors in <url>" | none>
Notes: <only what the ticket doesn't carry: decisions settled since it was written, a related merged PR to follow, a known trap>
```
