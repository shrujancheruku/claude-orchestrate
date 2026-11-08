Closes #367

## What

`gate` now hands the tracker snapshot it already holds to `holds --pr` through `--snapshot FILE` (a temp file it writes and removes), so one gate run reads the open issues once instead of twice. The dependencies check and the overlap check share one `tracker.Snapshot`.

## Notes

- `Snapshot.read` requires every part, and `holds` never reads `abandoned`, so the file carries an empty `abandoned` rather than a fetch. `scripts/tracker.py` is held by #406, so the reader was not loosened here.
- A gh failure or malformed issues reply while building the snapshot is still gate exit 2.

## Tests

- New: a gate run makes exactly one issues read (`api graphql`) and no unmatched gh call.
- The malformed-issues-reply test no longer needs a first good reply for `holds`; it sends the one bad node.
- `verify --config` passes.
