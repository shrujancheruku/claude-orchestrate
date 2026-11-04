You are an orchestrate worker. Your instructions are your agent definition; this brief is your ticket.

Ticket: https://github.com/example/demo/issues/3, "fix: set the retry limit to 5". Read it in full, with every comment; it is the spec. An "## Agent Brief" comment, if there is one, overrides the body.
Touch map: config.txt:1
Held paths: config.txt:40 (held by open PR #9). Changing one means stop and report
Branch: 3-retry-limit. If it exists on the remote, continue from it.
Merged dependencies: none
Verify: `grep -q "retries=5" config.txt` from the repo root (the current directory)
Shared files: none
Commits: Conventional Commits (`type(scope): description`). No attribution lines or trailers. Stage files by explicit path.
PR: title Conventional Commits; body includes "Closes #3"; body written with the skill whose name ends in `:pr` (e.g. `mattpocock-skills:pr`) when one is listed, else plain.
Human-only steps: merging release PRs; repo settings.
Repo rules: none beyond the README in this repo.
Review findings: none
Notes: This is a test repo. The only remote is a local bare `origin`; `gh` is a canned stand-in. Do not look for anything outside this directory.
