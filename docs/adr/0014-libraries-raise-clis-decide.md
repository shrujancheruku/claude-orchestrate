# Libraries raise, CLIs decide: one verdict grammar for every orchestrator-facing script

## Context

ADR-0006 moved the mechanical rules into scripts, and ADR-0003 split them into CLIs run by path and `.py` libraries imported as siblings. Two things then grew unevenly. `gate` and `merge` end with a `next:` verdict line the run skill applies word for word (#347), but `watch-ci`, `holds`, `rescue` and the rest encode their verdicts in free-form lines and exit codes, so exit 3 came to mean four different things and the run skill carries a 696-word paragraph interpreting `watch-ci` alone. And `runlog.append_event`, a library function, ends the process (exit 2) when the state file has no session: `frontier`, `gate`, `watch-ci`, `threads` and `worker-report` call it after printing a complete answer, three of them inside `except OSError`, so a correct answer is read as "could not run" on a fresh checkout, and `threads` exits after its GitHub mutation has landed (review #533, 2026-11-09).

## Decision

- **A library never exits the process.** A `.py` module raises (a named exception the caller can catch) or returns; only a CLI's `main` calls `common.die` or `sys.exit`. Each CLI decides what a library failure means for its own verdict and exit code.
- **Every orchestrator-facing CLI ends with one `next:` line**, printed through one helper that owns the word list and its exit-code mapping. A script that touched other PRs prints one `also: <word> <pr> ...` line per PR before it (`merge`, `watch-ci`). A script with no primary PR (`watch-ci`) puts every PR action in `also:` lines and keeps `next:` for the run's own state.
- **The words are shared, the numbers are not re-assigned.** A rate limit is `retry-after <time>` in every script; a program error is `could-not-run <Class>: <message>` (exit 2), never a user decision such as `ask-user`. Existing exit codes keep their numbers, since tests and the guard read them; new meanings get new words, not new numbers.
- **The run skill reads the words.** It never re-derives a verdict from the lines above the `next:` line, and `docs/guide.md` documents the grammar once for every script.

## Consequences

- Adding a verdict touches the helper, the script and one guide section; the prompts change by a sentence at most.
- `tests/test_exit_codes.py` becomes a table over the helper; each CLI gains a "no session in the state file" case asserting its normal output and exit code plus one stderr line.
- Rejected: returning `None` from `append_event` (a new caller forgets the case silently); promoting one of `watch-ci`'s PRs to `next:` (no PR is primary there); renumbering exit codes to one table (the guard and the tests read the numbers, and the words already carry the meaning).
