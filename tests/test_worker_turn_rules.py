"""Structural checks: the worker has no Agent tool, and the mechanic is retired (#43)."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class WorkerTurnRules(unittest.TestCase):
    def test_worker_has_no_agent_tool(self):
        fm = (ROOT / "agents/worker.md").read_text().split("---")[1]
        self.assertRegex(fm, r"(?m)^disallowedTools:.*\bAgent\b")
        self.assertNotRegex(fm, r"(?m)^tools:.*\bAgent\b")

    def test_worker_has_turn_rules_and_short_report(self):
        s = (ROOT / "agents/worker.md").read_text()
        self.assertIn("## Turn rules", s)
        # No behavioural case covers these, so each rule keeps one key term,
        # in its own bullet of the Turn rules section.
        rules = s[s.index("## Turn rules"):s.index("## Pushing")]
        bullets = [l for l in rules.splitlines() if l.startswith("- ")]
        for term in ("independent", "Chain", "grep -n", "re-read", "--jq"):
            self.assertTrue(any(term in b for b in bullets), term)
        report = s[s.index("## Report"):]
        self.assertIn("status: pr-opened | stopped", report)
        self.assertNotIn("changed:", report)

    def test_held_path_rule_compares_by_file(self):
        s = (ROOT / "agents/worker.md").read_text()
        stop = s[s.index("## Stop and report"):]
        # The rule is two parts in one bullet: strip `:line`, and a shared file overlaps.
        bullet = next(l for l in stop.splitlines() if ":line" in l)
        self.assertIn("overlap", bullet)

    def test_worker_leaves_no_background_commands(self):
        s = (ROOT / "agents/worker.md").read_text()
        # Step 3 (Verify) says foreground and finish before pushing or reporting;
        # step 5 (Report) and the Turn rules both say to leave nothing running.
        verify = s[s.index("3. **Verify.**"):s.index("4. **Ship.**")]
        self.assertIn("foreground", verify)
        report = s[s.index("5. **Report**"):s.index("## Turn rules")]
        self.assertIn("background", report)
        rules = s[s.index("## Turn rules"):s.index("## Pushing")]
        self.assertIn("background", rules)

    def test_run_skill_handles_locked_worktree_without_force(self):
        s = (ROOT / "skills/run/SKILL.md").read_text()
        # unlock must come before the plain remove
        self.assertLess(s.index("git worktree unlock <dir>"), s.index("git worktree remove <dir>"))
        files = [ROOT / "README.md"]
        for d in ("agents", "skills", "docs/agents"):
            files += [p for p in (ROOT / d).rglob("*.md")]
        for p in files:
            self.assertNotRegex(p.read_text(errors="ignore"),
                                r"worktree remove\b[^`\n]*(--force|\s-f\b)", str(p))

    def test_orient_checks_already_done_before_branching(self):
        s = (ROOT / "agents/worker.md").read_text()
        orient = s[s.index("1. **Orient.**"):s.index("2. **Build")]
        check = orient.index("compare each acceptance criterion with `origin/<default branch>`")
        self.assertLess(orient.index("git fetch origin"), check)
        self.assertLess(check, orient.index("If the brief's branch exists on the remote"))
        # The already-done stop reports status: stopped with no branch.
        self.assertIn("`status: stopped`", orient)
        self.assertIn("`branch: none`", orient)
        self.assertIn("already done", s[s.index("## Stop and report"):s.index("## Follow-ups")])

    def test_mechanic_is_gone(self):
        self.assertFalse((ROOT / "agents/mechanic.md").exists())

    def test_one_rule_merge_never_force(self):
        s = (ROOT / "agents/worker.md").read_text()
        # The old contradiction: step 4 forbade merging with no exception.
        self.assertNotIn("Never merge `origin/main` into your branch yourself", s)
        step4 = s[s.index("4. **Ship.**"):s.index("5. **Report**")]
        self.assertIn("conflict-list", step4)  # the one exception to the no-merge rule
        # Every sentence naming rebase or force is a prohibition or describes
        # the orchestrator's sweep, never an instruction to the worker.
        for sentence in re.split(r"(?<=[.:])\s", s):
            if re.search(r"(?i)rebase|force", sentence):
                self.assertRegex(sentence, r"(?i)\bnever\b|without force|sweep|orchestrator",
                                 sentence)
        fu = s[s.index("## Follow-ups"):s.index("## Report")]
        self.assertIn("merge", fu)
        self.assertIn("never force", fu)
        run = (ROOT / "skills/run/SKILL.md").read_text()
        self.assertIn("outside a conflict list", run)


if __name__ == "__main__":
    unittest.main()
