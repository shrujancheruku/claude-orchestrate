Closes #366

## Summary

```text
agents/reviewer.md
  Read       one bounded read per call; compare versions with diff <(...) or in the head
             never write a file: no >, sed -i, or heredoc into a file
  Post       one call alone: no cd &&, no chaining, no redirect
             in-diff finding  -> inline comment (review comments)
             out-of-diff      -> body, under "## Outside the diff"
             no file-level comment, no second request
tests/fixtures/guard-corpus.jsonl
  reviewer rows for the refusals and reads seen on 2026-11-08
tests/test_threads.py, tests/test_discovered_work_prompts.py
  pinned text moved to the new post rules
```

The guard is unchanged. Its decisions on the reviewer shapes are pinned in the corpus.

## Evidence

- **Before:** the reviewer prompt posted file-level comments as a second `gh api` call, which the guard accepts, and taught reviewers to chain `cd` and other commands around the post. Six of eleven guard refusals on the 2026-11-08 run were reviewers hitting these shapes.
- **After:** `scripts/verify --config --branch 366-reviewer-post-one-call` passes (py_compile, `tests/run_parallel.py`, `claude plugin validate .`). The corpus rows pin the four refusals (chained post, redirect, `sed -i`, heredoc into a file) as `deny`, and the reads `diff <(...)` and `| head` as `allow`.

No external facts are stated in this PR.

## Merge Danger

**Door:** two-way

Prompt and test text only; the guard and the scripts are untouched. Reverting restores the old prompt.

**Blast Radius:** reviewer prompt only. A reviewer following the new rules posts one review per round; findings outside the diff move from file-level comments into the review body.
