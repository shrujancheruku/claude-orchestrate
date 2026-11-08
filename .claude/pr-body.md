Closes #374

## Summary

```diff
 orchestrator ask (3rd review round, ready-for-human, config gate, parent to close, slot suggestion, fork)
-  Lead: live -> SendMessage lead -> lead relays, developer decides
+  Lead: live -> SendMessage lead, once -> lead's reply is the answer
+     lead answers  : judgment calls in the build loop
+     lead relays   : what config/repo reserves for a human, with a recommendation
+  never asked directly, except:
+     held/refused SendMessage, permission-denied stop,
+     config merge gate on a PR changing the permission layer (hooks/**, .claude-plugin/**, guard)
```

- `skills/run/SKILL.md`: lead-mode paragraph rewritten; round-3 text points to it.
- `skills/lead/SKILL.md`: Relaying becomes Answering asks (answer / relay / never by relay).
- `docs/guide.md`, `GLOSSARY.md` (Lead entry): match.
- `tests/test_lead_skill.py`: pins the new section names.

## Evidence

- **Before:** run skill said a direct ask "relays it without deciding it; the developer still decides".
  **After:** `tests/test_lead_skill.py` checks for "Answering asks" and "Never by relay" (fail before, pass after); `scripts/verify --config` passes (py_compile, `tests/run_parallel.py`, `claude plugin validate .`). One earlier run hit an unrelated flaky `maintenance.lock` copytree race in the test harness; reruns pass.

## Merge Danger

**Door:** two-way

**Blast Radius:** prompt-only, lead-mode routing of developer asks
