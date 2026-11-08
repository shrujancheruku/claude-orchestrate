# Can the orchestrator read the lead's session id each pass?

Research for [#369](https://github.com/shrujancheruku/claude-orchestrate/issues/369) (map [#368](https://github.com/shrujancheruku/claude-orchestrate/issues/368)). Sources read 2026-11-08 against Claude Code v2.1.311 (local), the repo at `9bd4e0b`, and the Claude Code docs.

## Answer

- **Yes: `claude agents --json` reports `sessionId` for the live session named `lead`.** Each row has `pid`, `cwd`, `kind`, `startedAt`, `sessionId`, `name` and `status` (live) or `state` (completed background, `--all` only). The live lead shows up as `{"pid": 2739, "cwd": "/Users/shrujan/code/claude-orchestrate", "kind": "interactive", "sessionId": "15fc02ae-…", "name": "lead", "status": "idle"}` (local run). Plain `--json` is enough; `--all` only adds completed background sessions, which a live lead never is.
- **The guard doesn't stand in the way.** The orchestrator is the main session (launched with `--name orchestrator`, not `--agent`), so its hook input has no `agent_type`, and `hooks/guard.py` returns without judging the call. Even if a scout ran it, the read-only rules have nothing to refuse in `claude agents --json`.
- **Auto mode runs it without a prompt in practice.** The lead already runs `claude agents --json --all` in auto mode (session `15fc02ae`, `permissionMode: auto`) and got results back. No allow rule covers it, and this research session ran it in auto mode with no prompt either. No doc says how the classifier judges this command, so this is observed, not guaranteed.
- **`ListAgents` does not give the session id.** Its rows are `name [short id] · kind · status · started`, and the short id isn't a session-id prefix (lead `[9e4225]` vs `sessionId` `15fc02ae-…`). It's still the right tool for detecting lead mode, but not for the id.
- **Recommended: have `profile.py record` look the lead up itself.** It would run `claude agents --json`, keep rows where `name == "lead"`, `pid` is set and `cwd` is the repo root, and store that row's `sessionId` as `lead` (or `null` when there is none, or more than one, or when `claude` fails). That keeps the id out of the model's hands: no copying a UUID into the pass JSON, and no new Bash command shape for the classifier or the guard, since `python3 -I …/profile.py record …` already runs every profiled pass. A failed lookup degrades to `lead: null`, which fits "Profiling never blocks a pass".
- **The lead writing its own id is the fallback, not the first choice.** The lead knows `${CLAUDE_SESSION_ID}` (lead skill step 1). But the watermark `.claude/orchestrate-lead.json` is written only by `lead-brief --checkpoint` and holds `pr`, `merged_at` and `merges`. Adding the id there mixes two concerns and goes stale when the lead exits, so the orchestrator would also need a liveness check, which is the same `claude agents --json` lookup anyway.

## Evidence

### `claude agents --json` fields

- The CLI reference documents only the command: "`--json` to print active sessions as a JSON array for scripting (`--json --all` also includes completed background sessions)" ([cli-reference](https://code.claude.com/docs/en/cli-reference)). `claude agents --help` says the same ("Print active sessions (interactive and background) as a JSON array and exit (for scripting; does not require a TTY)"; "--all  With --json: also include completed background sessions"). **No doc lists the row fields.**
- The fields come from a local run of `claude agents --json` and `--json --all`. The union of keys was `cwd, id, kind, name, pid, sessionId, startedAt, state, status`. Live rows (interactive or background) carry `pid` and `status` (`idle`/`busy`). Completed background rows (`--all`) carry `id` (the session-id prefix) and `state` (`done`), with no `pid`. Every row carries `sessionId`.
- Every row has a `cwd`, and the listing spans all projects (it showed `look-together` and `issho` sessions too), so the lookup should filter by `cwd` as well as `name`. `--cwd <path>` also filters ("Show only background sessions started under <path>", per `--help`), but the help says *background*, so filtering in code on `cwd` is safer for an interactive lead.
- The repo already relies on these fields: lead skill step 4 reads `pid` and `sessionId` from `claude agents --json --all` rows named `orchestrator` (`skills/lead/SKILL.md`).

### The guard

- `hooks/guard.py` `main()` takes the role from `agent_type` (`role_of`) and returns when the role is `None` ("acts only when the call comes from one of this plugin's agents (the hook input names it in `agent_type`)", module docstring).
- The hooks docs say `agent_type` is "Present when the session uses `--agent` or the hook fires inside a subagent" ([hooks](https://code.claude.com/docs/en/hooks)). The lead launches the orchestrator with `claude --bg --name orchestrator … "/orchestrate:run --greedy"` (lead skill step 4), and the hand start is `claude --name orchestrator …`; neither uses `--agent`. There is no `agents/orchestrator.md`, only `reviewer`, `scout` and `worker`. So the orchestrator's own Bash calls are never judged by the guard.
- If an `orchestrate:scout` ran it, `READ_ONLY` applies (no expansions, no git/gh writes, no fs writes, no redirects). Plain `claude agents --json` passes all of them, but a `2>/dev/null` or `| python3 -c` wrapper could trip `rule_redirects`. That doesn't matter if `profile.py` does the lookup.

### Auto mode

- From the permission-facts memory (docs checked 2026-11-08): in auto mode, matching allow/ask/deny rules resolve first, and everything else goes to the classifier. The repo has no rule for `claude agents` (`.claude/settings.local.json`'s allow list covers `Edit(/.claude/worktrees/**)`, `git push`, `gh pr create/edit/ready` and `scripts/threads`), and plugins can't ship rules. So the classifier decides.
- Observed: the lead session `15fc02ae` (auto mode) ran `claude agents --json --all 2>/dev/null | python3 -I -c '…'` twice and got rows back, and this research session (auto mode) ran `claude agents --json` and `--json --all` with no prompt. It's a read-only listing, so the classifier allowing it is expected. **Undocumented**: no doc names this command's classifier verdict.
- A lookup from inside `profile.py` (a `subprocess` call) never reaches the classifier as its own command. The only Bash call is `python3 -I "${CLAUDE_SKILL_DIR}/../../scripts/profile.py" record …`, which the Profiling section already runs every profiled pass (`skills/run/SKILL.md`, Profiling).

### `ListAgents`

- Orchestrator transcript `16fba49d`: `ListAgents` returned "This session is orchestrator [9a9453] … Peer sessions (3): … lead [9e4225] · interactive · idle · started 15m ago". The live lead's `sessionId` is `15fc02ae-ec6c-4e34-993f-77ed49f0be8e`, so the bracketed id isn't a session-id prefix and there's no `sessionId` field. The run skill already uses `ListAgents` for lead-mode detection (`skills/run/SKILL.md` step 1, **Lead mode**). It can stay there; only the id needs `claude agents --json`.

### The lead writing its id

- The lead finds itself by `${CLAUDE_SESSION_ID}` (`skills/lead/SKILL.md` step 1), so it could write the id to a file.
- The watermark `.claude/orchestrate-lead.json` is "written only by `--checkpoint <pr>` (last absorbed PR, merged-at time, merge count)" (`scripts/lead-brief` docstring). The current file is `{"pr": 350, "merged_at": "…", "merges": 147}`. A stored id says nothing about whether the lead is still live, so the orchestrator would still need the `claude agents --json` check. That makes the file redundant for a live lead. It would help only to name a lead that has exited, and the map's `--lead <session id>` already covers hand profiling.

## Caveats

- A lead in another clone or worktree of the same repo has a different `cwd`. Lead-mode detection by `ListAgents` matches on name alone (`skills/run/SKILL.md` step 1), so a `cwd` filter in `record` could disagree with it when a `lead` is live elsewhere. The spec should pick one rule.
- Two live sessions named `lead` shouldn't happen (lead skill step 1 stops), but `record` should store `null` then, not guess.
- The JSON row shape is undocumented and could change between Claude Code versions. Parse defensively and degrade to `null`.
