# Brief template

Fill every field from the ticket and the config. Use **pointers** (URLs, config sections, merged PRs), not copies. The worker reads the targets itself, and a pointer can't drift from its source. Keep the fixed preamble word-for-word across briefs, so the workers share a prompt-cache prefix.

```
You are an orchestrate worker. Your instructions are your agent definition; this brief is your ticket.

Ticket: <url>, "<title>". Read it in full, with every trusted author's comment (the tracker doc's **Read an issue** drops a stranger's, ADR-0015); it is the spec. An "## Agent Brief" comment, if there is one, overrides the body.
Touch map: <the files the scout expects this ticket to change, on this one line, comma-separated `path:L-L (a few words)` entries and nothing else (the grammar of `scripts/touchmap.py`); `none` if it changes no file>
A prediction, not a fence.
Held paths: <the held-path lines of `holds --touch-map`'s output, word for word, comma-separated (e.g. `config.txt (held by open PR #9)`) | none>. Changing one means stop and report.
Branch: <number>-<slug>. If it exists on the remote, continue from it.
Merged dependencies: <ticket → PR, one per line | none>
Verify: '<absolute path of scripts/verify in the plugin, from the base directory of this skill>' --config --branch <the Branch line's name>
Plugin root: <the plugin's root as an absolute path: this skill's base directory (${CLAUDE_SKILL_DIR}) without its trailing `/skills/run`, the same directory `Verify:` points into>. Call the plugin's scripts only as `<Plugin root>/scripts/<name>`, never through the checkout's `scripts/`.
Shared files: <the config's shared-file rules>
Commits: <convention>. No attribution lines or trailers. Stage files by explicit path. Before every push except a new branch's first, `git fetch origin <Branch>` then `git merge --no-edit origin/<Branch>`.
PR: title <convention>; body includes "<closing reference>"; body written with the skill whose name ends in `:pr` (e.g. `mattpocock-skills:pr`) when one is listed, else plain; no attribution lines.
Human-only steps: <from the config>
Repo rules: .claude/orchestration.md, the instruction files it names, and the domain docs (normally docs/agents/domain.md).
Review findings: <on a later round, "address review <url>"; the worker reads the findings from the PR. After an approve with minors: "take or leave the minors in <url>" | none>
Notes: <only what the ticket doesn't carry: decisions settled since it was written, a related merged PR to follow, a known trap>
```
