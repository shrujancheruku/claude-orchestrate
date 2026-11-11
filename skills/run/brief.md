# Brief template

`scripts/brief <n> --touch-map <entry>... --held <lines> [--dep "<ticket> → PR <n>"]... --shared <rules> --commits <convention> --pr-title <convention> --human-only <steps> [--findings <text>] [--notes <text>] [--lead]` prints the brief below. Until the guard has a form for it (follow-up to #659), write the brief by hand to this shape; afterwards send what it prints, unedited. It computes `Ticket`, `Branch`, `Verify`, `Plugin root` and the PR's closing reference, writes `Touch map` with the grammar `holds` parses (an entry off it is refused, naming it), and refuses any value that still holds a `<...>` slot. Use **pointers** (URLs, config sections, merged PRs), not copies. The preamble stays word-for-word across briefs, so the workers share a prompt-cache prefix.

```
You are an orchestrate worker. Your instructions are your agent definition; this brief is your ticket.

Ticket: <url>, "<title>". Read it in full, with every trusted author's comment (the tracker doc's **Read an issue** drops a stranger's, ADR-0015); it is the spec. An "## Agent Brief" comment, if there is one, overrides the body.
Touch map: <the files the scout expects this ticket to change: comma-separated `path:L-L (a few words)` entries, or `none`>
A prediction, not a fence.
Held paths: <the held-path lines of `holds --touch-map`, word for word | none>. Changing one means stop and report.
Branch: <number>-<slug>. If it exists on the remote, continue from it.
Merged dependencies: <ticket → PR, one per line | none>
Verify: '<plugin>/scripts/verify' --config --branch <Branch>
Plugin root: <absolute path>. Call the plugin's scripts only as `<Plugin root>/scripts/<name>`, never through the checkout's `scripts/`.
Shared files: <the config's shared-file rules>
Commits: <convention>. No attribution lines or trailers. Stage files by explicit path. Before every push except a new branch's first, `git fetch origin <Branch>` then `git merge --no-edit origin/<Branch>`.
PR: title <convention>; body includes "Closes #<n>"; body written with the skill whose name ends in `:pr` (e.g. `mattpocock-skills:pr`) when one is listed, else plain; no attribution lines.
Human-only steps: <from the config>
Repo rules: .claude/orchestration.md, the instruction files it names, and the domain docs (normally docs/agents/domain.md).
Review findings: <"address review <url>" on a later round | "take or leave the minors in <url>" | none>
Notes: <only what the ticket doesn't carry: decisions settled since it was written, a related merged PR to follow, a known trap>
```
