# The guard reads shell with its own lexer and fails closed

## Context

`hooks/guard.py` runs before every Bash call a plugin agent makes and refuses the commands that agent's role must leave to someone else, such as a scout or reviewer writing, or a worker merging. It decides from the command text, and a wrong allow lets a role do what the plugin says it can't (inferred). The role split is in the guard table in `docs/guide.md`.

- **Text matching is not enough.** `gh pr "merge"`, `git $(echo push)`, `sh -c '...'`, and a heredoc body that merely mentions `git push` read differently to a regex than to the shell. Matching the raw string both misses real writes and refuses harmless searches (inferred; a regex on the raw text read `grep scripts/merge` or a commit message naming the script as a merge, #327).
- **No parser is on hand.** The hook is one stdlib Python file that Claude Code starts per call (`hooks/hooks.json`, `python3 -I`). `shlex` splits words but knows nothing about `$(...)`, backticks, heredocs, line continuations or `sh -c`, and a third-party parser would add a dependency to every install (both inferred).
- **A crash must not read as a pass.** A hook that errors lets the command run unchecked (`guard.py:968`, the comment on the `except Exception` in `main`).

## Decision

- **The guard has one lexer of its own** (`parse` in `hooks/guard.py`). It splits a command into simple commands and words, with quotes, escapes, line continuations, `$(...)`, backticks, heredocs and `sh -c` resolved. Every rule decides on those words, not on the command text, the check for `scripts/merge` (`runs_merge`) included.
- **A command it can't parse or check is refused.** A command that doesn't parse (`ParseError`) is denied with a message telling the agent to rewrite it. Any other exception while checking is caught in `main` and denied the same way. An expansion whose value the guard can't know is kept as an unknown word, not treated as harmless.
- **Input that is not a call the guard governs returns without a verdict:** stdin that is invalid JSON or JSON that isn't an object, no plugin agent named (the orchestrator's own session is never refused; since #358 it can only be given an explicit `allow` for its routine steps), or no command.
- **A role the guard doesn't know is read-only,** the same as a scout.
- **It is a guardrail against mistakes, not a sandbox.** Aliases, `eval`, scripts written to disk and then run, and computed strings can still get past it. The user's permission mode and sandbox apply on top.
- **Its decisions are pinned by tests:** `tests/test_guard.py` and the regression corpus (`tests/fixtures/guard-corpus.jsonl`, run by `tests/test_guard_corpus.py`), so a change that flips a decision shows in the PR diff.

## Consequences

- A rare legitimate command the lexer can't read is refused and the agent rewrites it. A false refusal costs a turn; a false allow costs the role boundary (inferred).
- New shell syntax in agent commands needs a lexer change and a corpus line, not a regex tweak (inferred).
- The lexer is code the plugin owns and tests.
- Rejected: matching on the command string (misses quoting and expansion, refuses harmless text; inferred); `shlex` alone (no `$(...)`, heredocs or `sh -c`; inferred); failing open on a parse error (a crash would let the command run unchecked; `guard.py:968`); a third-party shell parser (a dependency on every install for a guardrail; inferred).
- Reopen when the guard is relied on as a sandbox, or Claude Code hands hooks an already-parsed command (inferred).

## Note, 2026-11-09 (#378)

The guard assumes the orchestrator runs in `acceptEdits` or `bypassPermissions`. Auto mode is unsupported for the orchestrator session: workers inherit it, and the auto-mode classifier skips their file edits whatever the guard allows. `scripts/launch` passes the mode and a per-session `disableAutoMode`; see Permission mode in `docs/guide.md`.

## Note, 2026-11-09 (#407)

The orchestrator's own session is refused for one class of command: a hand-made wait (`sleep`, `read -t`, an `until … gh …` poll loop). A background orchestrator that sleeps holds its whole run and reads stale state when it wakes; `scripts/watch-ci` owns waiting on CI and names the next step. The deny reason points at `watch-ci`. Every other main-session command stays unrefused: the guard only ever allows it or says nothing (line 15 above).
