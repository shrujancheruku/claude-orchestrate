# Scripts are found from the skill directory and run by path

## Context

The plugin ships helper scripts under `scripts/` (`gate`, `merge`, `verify`, ...) and a hook, and skills, agents and the hook config all have to call them.

- **Where the plugin lives varies.** It is installed under a versioned cache directory. `${CLAUDE_SKILL_DIR}` is set for a skill, but `${CLAUDE_PLUGIN_ROOT}` may be unset for a subagent.
- **`python3 -I` drops the script's own directory from `sys.path`.** A script run that way can't `import common` unless it puts its directory back itself. Running a script by path puts its directory first and never the current directory, so a repo's own `common.py` can't shadow the plugin's.
- **Some scripts want `-I`.** `profile.py` reads transcripts and `hooks/guard.py` reads whatever the harness hands it; isolated mode keeps the caller's `PYTHON*` environment and the current directory out of them.

Without one rule, each script could grow its own call shape, and a broken sibling import or a lost executable bit would only show up in a real session.

## Decision

One rule, checked by `tests/test_invocation.py`:

- **Found:** a skill calls a script as `"${CLAUDE_SKILL_DIR}/../../scripts/<name>"`. An agent never relies on `${CLAUDE_PLUGIN_ROOT}`; the brief hands it the absolute path.
- **A CLI is an executable without an extension** (`gate`, `merge`, `verify`, ...) with a `#!/usr/bin/env python3` shebang, run by path with no interpreter and no `-I`. It may import siblings (`common`, `checks`, `replay`).
- **A `.py` file is a library or an isolated script.** A library (`common.py`, `checks.py`, `replay.py`) is only imported. An isolated script (`scripts/profile.py`, and the hook `hooks/guard.py` via `hooks/hooks.json`) runs as `python3 -I <path>`. `profile.py` imports `common`, so it appends its own directory to `sys.path` first: appended, never inserted, so the stdlib `profile` module is not shadowed.
- **Never work around a failed import with `PYTHONPATH`.** Fix the script's kind to match the rules above.

## Consequences

- Adding a script means choosing its kind: a new `.py` file is declared a library or an isolated script in `tests/test_invocation.py`.
- CLIs trust the caller's `PYTHON*` environment; only the isolated scripts and the hook are shielded from it. The commands `verify` and `gate` run are the config's, by design, and no repo Python is imported.
- `profile.py` is both isolated and a user of a sibling, so it carries the `sys.path` append; a second such script repeats it.
- Rejected: `${CLAUDE_PLUGIN_ROOT}` everywhere (may be unset for subagents); `-I` on every script (breaks sibling imports); `PYTHONPATH` (leaks into the commands a script runs).
- Reopen when a harness sets one root variable for every caller, or a script needs a kind this rule doesn't cover.
