# Scripts are found from the skill directory and run by path

## Context

The plugin ships helper scripts under `scripts/` (`gate`, `merge`, `verify`, ...) and a hook, and skills, agents and the hook config all have to call them.

- **Where the plugin lives varies.** It is installed under a versioned cache directory (the README's `profile.py` example path). `${CLAUDE_SKILL_DIR}` is set for a skill, but `${CLAUDE_PLUGIN_ROOT}` may be unset for a subagent (the README's convention before this ADR, from #100; not otherwise verified).
- **`python3 -I` drops the script's own directory from `sys.path`.** A script run that way can't `import common` unless it puts its directory back itself (`scripts/profile.py:30-35`). Running a script by path puts its directory first and never the current directory, so a repo's own `common.py` can't shadow the plugin's (#100 and the README's convention before this ADR).
- **Some scripts keep `-I`.** `profile.py` reads transcripts, so it keeps `-I` (issue #100); the hook runs as `python3 -I` in `hooks/hooks.json`.

Without one rule, each script could grow its own call shape (inferred). Before `tests/test_invocation.py`, a broken sibling import or a lost executable bit could slip through (#100).

## Decision

One rule, checked by `tests/test_invocation.py`:

- **Found:** a skill calls a script as `"${CLAUDE_SKILL_DIR}/../../scripts/<name>"`. An agent never relies on `${CLAUDE_PLUGIN_ROOT}`; the brief hands it the absolute path.
- **A CLI is an executable without an extension** (`gate`, `merge`, `verify`, ...) with a `#!/usr/bin/env python3` shebang, run by path with no interpreter and no `-I`. It may import siblings (`common`, `checks`, `replay`, `github`, `runlog`).
- **A `.py` file is a library or a standalone script.** A library (`common.py`, `checks.py`, `replay.py`, `github.py`, `runlog.py`) is only imported. A standalone script (`scripts/profile.py`, and the hook `hooks/guard.py` via `hooks/hooks.json`) is run by path under `python3 -I`. `profile.py` imports `common` and `runlog`, so it appends its own directory to `sys.path` first: appended, never inserted, so the stdlib `profile` module is not shadowed (`scripts/profile.py:30-35`).
- **Never work around a failed import with `PYTHONPATH`.** Fix the script's kind to match the rules above (the README's convention before this ADR; the reason is inferred).

## Consequences

- Adding a script means choosing its kind: a new `.py` file is declared a library or a standalone script in `tests/test_invocation.py`.
- CLIs trust the caller's `PYTHON*` environment; only the standalone scripts and the hook run under `-I`. The commands `verify` and `gate` run are the config's, by design, and no repo Python is imported.
- `profile.py` is both standalone and a user of a sibling, so it carries the `sys.path` append; a second such script would repeat it (inferred).
- Rejected: `${CLAUDE_PLUGIN_ROOT}` everywhere (inferred from the context above); `-I` on every script (breaks sibling imports without an append; inferred); `PYTHONPATH` (leaks into the commands a script runs; inferred).
- Reopen when a harness sets one root variable for every caller, or a script needs a kind this rule doesn't cover (inferred).
