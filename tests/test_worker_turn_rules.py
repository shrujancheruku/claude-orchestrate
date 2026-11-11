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

    def test_the_push_step_fetches_and_merges_the_workers_own_branch_first(self):
        # #757: merge/sweep may have pushed to the branch; a worker never diverges from its own remote branch.
        s = (ROOT / "agents/worker.md").read_text()
        pushing = s[s.index("## Pushing"):s.index("## Stop and report")]
        fetch = pushing.index("`git fetch origin <your branch>`")
        merge = pushing.index("`git merge --no-edit origin/<your branch>`")
        push = pushing.index("Only then push")
        self.assertLess(fetch, merge)
        self.assertLess(merge, push)
        brief = (ROOT / "skills/run/brief.md").read_text()
        self.assertIn("Before every push except a new branch's first, `git fetch origin <Branch>` then `git merge --no-edit origin/<Branch>`", brief)

    def test_worker_never_ends_its_turn_to_wait_and_the_rate_limit_notice_is_advisory(self):
        s = (ROOT / "agents/worker.md").read_text()
        rules = s[s.index("## Turn rules"):s.index("## Pushing")]
        wait = next(l for l in rules.splitlines() if l.startswith("- Never end your turn to wait"))
        for term in ("a rate limit", "a CI run", "a wake", "a monitor", "I will check back", "stop-reason: transient"):
            self.assertIn(term, wait)
        notice = next(l for l in rules.splitlines() if "ScheduleWakeup" in l)
        self.assertIn("advisory", notice)
        self.assertIn("never sleep or loop on a rate limit", notice)
        for agent in ("reviewer", "scout"):
            text = (ROOT / f"agents/{agent}.md").read_text()
            self.assertIn("ScheduleWakeup", text)
            self.assertIn("never sleep or loop on a rate limit", text)
        run = (ROOT / "skills/run/SKILL.md").read_text()
        self.assertIn("ScheduleWakeup", run)

    def test_worktree_gone_follows_a_verify_refusal_and_never_builds_one(self):
        s = (ROOT / "agents/worker.md").read_text()
        stop = s[s.index("## Stop and report"):s.index("## Discovered work")]
        bullet = next(l for l in stop.splitlines() if "`stop-reason: worktree-gone`" in l)
        for term in ("main checkout", "no longer exists", "never create a worktree", "scratchpad",
                     "never edit the main checkout", "fresh worker"):
            self.assertIn(term, bullet)
        self.assertIn("Verify refuses with exit 2 when run outside your worktree", bullet)
        self.assertNotIn("Whenever you're continued, first confirm", s)

    def test_run_skill_checks_the_worktree_before_continuing_a_no_pr_worker(self):
        s = (ROOT / "skills/run/SKILL.md").read_text()
        rule = next(l for l in s.splitlines() if l.startswith("- **Worktree check before any SendMessage"))
        for term in ("answered `valid stopped`", "`RESUME`", "usage-limit resume", "`claims <ticket>`", "`worktree gone`",
                     "`Build #<ticket>`", "stop question", "the answer"):
            self.assertIn(term, rule)
        backstop = next(l for l in s.splitlines() if l.startswith("- `valid stopped worktree-gone`"))
        for term in ("fresh worker", "no label", "slot"):
            self.assertIn(term, backstop)
        # Each of the three continuation paths points at the check.
        self.assertGreaterEqual(s.count("worktree check"), 3)

    def test_held_path_rule_defers_to_the_brief(self):
        s = (ROOT / "agents/worker.md").read_text()
        stop = s[s.index("## Stop and report"):]
        # The overlap rule lives in scripts/holds (#166); the worker only stops on a file the brief lists.
        bullet = next(l for l in stop.splitlines() if "Held paths" in l)
        self.assertIn("listed under Held paths in the brief", bullet)
        self.assertNotIn(":line", bullet)

    def test_report_block_carries_the_stop_reasons_and_rules_name_the_guard(self):
        s = (ROOT / "agents/worker.md").read_text()
        report = s[s.index("## Report"):]
        line = next(l for l in report.splitlines() if l.startswith("stop-reason:"))
        reasons = ("held-path", "no-spec", "ambiguous", "adr-conflict", "human-only",
                   "verify-outside-ticket", "already-done", "no-primary-source", "permission-denied", "worktree-gone")
        self.assertIn("denied-command:", report)
        for r in reasons:
            self.assertIn(r, line)
            self.assertIn(f"`stop-reason: {r}`", s[s.index("## Stop and report"):s.index("## Discovered work")])
        rules = s[s.index("## Turn rules"):s.index("## Pushing")]
        self.assertIn("The guard refuses a backgrounded command", rules)

    def test_a_denied_command_is_permission_denied_never_retried_or_worked_around(self):
        s = (ROOT / "agents/worker.md").read_text()
        stop = s[s.index("## Stop and report"):s.index("## Discovered work")]
        bullet = next(l for l in stop.splitlines() if "`stop-reason: permission-denied`" in l)
        self.assertIn("not `human-only`", bullet)
        self.assertIn("Never retry the call", bullet)
        self.assertIn("work around", bullet)
        self.assertIn("denied-command:", bullet)

    def test_run_skill_keeps_a_permission_denied_ticket_and_never_runs_the_command(self):
        s = (ROOT / "skills/run/SKILL.md").read_text()
        para = next(l for l in s.splitlines() if l.startswith("- `valid stopped permission-denied`"))
        for needle in ("not** a hand-back", "does not apply", "Keep the claim, `ready-for-agent`, the slot and the touch map",
                       "no `needs-lead`", "Never run the denied command yourself", "SendMessage", "`! <command>`",
                       "review loop with no worker turn"):
            self.assertIn(needle, para)

    def test_worker_resolves_review_threads_after_the_push_and_before_the_report(self):
        s = (ROOT / "agents/worker.md").read_text()
        para = s[s.index('For "address review"'):]
        self.assertLess(para.index("push, then close every thread"), para.index("and only then report"))
        self.assertIn("resolve <id> --fixed <the pushed sha>", para)

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
        # scripts/merge unlocks a harness lock itself (#231): no agent task to stop, no unlock step here
        self.assertNotIn("stop that worker's agent task", s)
        self.assertNotIn("git worktree unlock <dir>", s)
        files = [ROOT / "README.md", ROOT / "docs/guide.md"]
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
        self.assertLess(check, orient.index("your first git step is to move onto the brief's branch"))
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


class HandBackRules(unittest.TestCase):
    """#229: a worker's hand-back is trustworthy, and the run skill copes when it isn't."""

    def test_worker_waits_for_a_backgrounded_verify_and_never_stops_on_mechanical_steps(self):
        s = (ROOT / "agents/worker.md").read_text()
        verify = s[s.index("3. **Verify.**"):s.index("4. **Ship.**")]
        self.assertIn("Monitor", verify)
        self.assertIn("never hand back while it runs", verify)
        stop = s[s.index("## Stop and report"):s.index("## Discovered work")]
        self.assertRegex(stop, r"Uncommitted changes, an unpushed branch or an unopened PR are never a reason to stop")

    def test_run_skill_defers_the_interim_and_backstop_rules_to_worker_report(self):
        # #260: scripts/worker-report owns them; the prompt keeps what to do with each verdict.
        s = (ROOT / "skills/run/SKILL.md").read_text()
        reports = s[s.index("## Worker reports"):s.index("## Choosing the worker model")]
        self.assertIn("scripts/worker-report", reports)
        # #657: the second one is a `claims` word now, not a count in the prompt.
        self.assertIn("`repeat not-a-stop`, send nothing: it is a normal stop", reports)
        self.assertNotIn("from the same worker", reports)


if __name__ == "__main__":
    unittest.main()
