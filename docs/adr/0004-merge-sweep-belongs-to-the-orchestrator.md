# The merge sweep belongs to the orchestrator, and a worker only merges on a conflict list

## Context

Several tickets are in flight at once, so every merge can leave other open PRs conflicting with the default branch. Someone has to bring them up to date (inferred framing).

- **Workers can't see each other.** A worker that merged or rebased main whenever it liked would do it against a moving target and could force-push over an update the orchestrator made (inferred).
- **A shared file collides the same way every time.** Two PRs each add a line to the same index. Replaying the later PR's added lines onto main resolves it without a human (`scripts/replay.py` docstring).
- **A force-push is the destructive step.** It is the one move that can lose a worker's commits (inferred), and under `Method: rebase` the orchestrator's sweep or merge rebases the history (#128).

## Decision

- **The sweep is `scripts/merge`'s job,** run by the orchestrator after it merges a PR. It updates each other open PR whose branch now conflicts with the default branch, in a throwaway worktree, and pushes. A PR that is merely behind is not touched.
- **The merge method decides how.** Squash or merge merges main into the branch with a plain push. Rebase rebases the branch and pushes with `--force-with-lease`. Only the script does either.
- **Conflicts are resolved in a fixed order** (`scripts/replay.py`). In a **shared file**: line replay (main's version plus the lines the PR added or changed), then word merge for a line changed on both sides, accepted only when `git merge-file` reports no conflict. In a file matching a generated glob: main's side, then the glob's regenerate command. Anything else is handed back. The result's diff against main may only hold lines the PR added or changed. Each file's outcome is logged as a `replay` event.
- **A hand-back leaves the branch untouched.** The script prints one `HANDBACK PR #n <branch>: <path> (<why>)` line per file and exits 4; the orchestrator resumes that PR's worker with those lines as the conflict list.
- **A worker brings in the default branch only on a conflict list,** by merging `origin/<default branch>`, resolving exactly the listed files and pushing without force. This holds whatever the merge method: a worker never rebases and never force-pushes. A push rejected because the sweep updated its branch is answered by fetching, then resetting to or merging `origin/<branch>` (`agents/worker.md`, Follow-ups). The orchestrator never asks a worker to merge main outside a conflict list; to refresh a branch it uses the sweep or `gh pr update-branch`.
- **The guard backs it up** by refusing a worker's force-push and its push to `main` or `master` (ADR-0002).

## Consequences

- Each merge carries the sweep's work, and a bug in `replay.py` touches every conflicting open PR; the diff check above (`scripts/replay.py` docstring) limits the damage (inferred).
- Under `Method: rebase`, a worker's pushed commits can change under it. It resets to or merges `origin/<branch>`, and never rebases.
- A conflict outside shared and generated files is handed back (`scripts/replay.py` docstring), which costs a worker turn.
- Rejected: workers rebasing or merging main themselves (inferred: it would race the sweep and invite force-pushes; issue #128 fixed the rule as merge, never force); the orchestrator resolving every conflict by hand (inferred); handing back every conflict (inferred).
- A branch that is merely behind passes the `gate` script and is updated by `merge` at merge time (exit 5), at the cost of one extra CI run (#230).
- Reopen when hand-backs on shared files show the replay rules wrong (inferred), or when the run log shows the CI runs after `merge` exits 5 to be a real cost (#230).
