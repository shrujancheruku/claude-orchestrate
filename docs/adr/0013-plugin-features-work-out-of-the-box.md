# Plugin features work out of the box, and degrade instead of asking for config edits

## Context

Orchestrate is installed from GitHub into other people's repos. Twice a feature has shipped with a manual step outside the plugin: the profiling usage tap only ran if the developer edited their own status-line script to call it (issue #7), and an early setup draft copied plugin scripts into `~/.claude/`. Such steps are easy to miss, break silently when the plugin updates, and are invisible to the next person who installs it. ADR-0012 already rejected a `CI: none` config switch because "the plugin works out of the box", without recording that rule anywhere.

Some capabilities cannot be shipped by a plugin at all: sandbox settings, permission rules (`Write()` rules never match, plugins cannot ship rules), and `crossSessionInbound` live in the developer's own settings. The rule has to say what a feature does when it needs one of those.

## Decision

- **A feature works on install.** It uses only the plugin's own files (manifest, hooks, skills, agents, scripts) and the repo's `.claude/` directory. It never requires the developer to edit `~/.claude`, to copy plugin scripts anywhere, or to run a command outside the plugin's own skills.
- **A missing capability degrades, with one message.** Where a feature needs user config the plugin cannot ship, it keeps working without it and prints one line naming the exact setting and what it would add, once per session, instead of failing or prescribing an edit. Example: the launch of a background orchestrator says what `crossSessionInbound: "accept"` would enable and carries on.
- **Setup writes the repo's own files, and user settings only on an explicit yes.** `/orchestrate:setup` writes `.claude/orchestration.md`, `.worktreeinclude` and the board line (steps 4 and 5); it may offer to write `.claude/settings.local.json` (step 6; never the checked-in `settings.json`, never `~/.claude`), says plainly what that write changes, and writes nothing there on a no. Advice it prints (step 2 and the `autoCompactWindow` check) stays advice.
- **The constraint is raised at design time.** A ticket, grilling or review that finds a feature needing a manual step says so and changes the design, rather than documenting the step.

## Consequences

- Every feature has a degraded mode and a test for it; the message is the feature's only ask of the developer.
- Capabilities the plugin cannot ship are listed in one place in the guide, each with the setting it needs: the **Permission mode** section that #378 adds.
- Rejected: install instructions with manual steps outside the repo (stale across versions, invisible to the next install); setup writing `~/.claude` (another person's config, shared across their repos); features that refuse to run until a setting is present (one missing line stops the whole run).
