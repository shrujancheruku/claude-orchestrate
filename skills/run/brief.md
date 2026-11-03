# Brief template

Fill every field from the ticket and the config. Use **pointers** (URLs, config sections, merged PRs), not copies. The worker reads the targets itself, and a pointer can't drift from its source. Keep the fixed preamble word-for-word across briefs, so the workers share a prompt-cache prefix.

```
You are an orchestrate worker. Your instructions are your agent definition; this brief is your ticket.

Ticket: <url>, "<title>". Read it in full; it is the spec.
Lane: <lane>. You may change: <path globs>. A change anywhere else means stop and report.
Branch: <lane>/<number>-<slug>. If it exists on the remote, continue from it.
Size: <S | M | L>
Merged dependencies: <ticket → PR, one per line | none>
Verify: <commands from the config, for this lane>
Shared files: <the config's shared-file rules>
Commits: <convention>. Stage files by explicit path.
PR: title <convention>; body includes "<closing reference>".
Human-only steps: <from the config>
Repo rules: .claude/orchestration.md and the instruction files it names.
Notes: <only what the ticket doesn't carry: decisions settled since it was written, a related merged PR to follow, a known trap>
```
