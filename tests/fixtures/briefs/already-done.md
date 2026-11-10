You are an orchestrate worker. Your instructions are your agent definition; this brief is your ticket.

Ticket: https://github.com/example/demo/issues/1, "feat: add hello.txt". Read it in full, with every comment; it is the spec. An "## Agent Brief" comment, if there is one, overrides the body.
Touch map: hello.txt:1
Held paths: none
Branch: 1-add-hello. If it exists on the remote, continue from it.
Merged dependencies: none
Verify: `test -f hello.txt` from the repo root (the current directory)
Plugin root: none (this fixture calls no plugin script).
Shared files: none
Commits: Conventional Commits (`type(scope): description`). No attribution lines or trailers. Stage files by explicit path.
PR: title Conventional Commits; body includes "Closes #1"; body written with the skill whose name ends in `:pr` (e.g. `mattpocock-skills:pr`) when one is listed, else plain.
Human-only steps: merging release PRs; repo settings.
Repo rules: none beyond the README in this repo.
Review findings: none
Notes: This is a test repo. The only remote is a local bare `origin`; `gh` is a canned stand-in. Do not look for anything outside this directory.
