"""Structural test for the lead skill's start-up prompt (#214).

The live launch and cross-session messaging aren't testable in CI; this checks the prompt
still names every branch of the start-up, so none is dropped by an edit.
"""
import pathlib
import unittest

SKILL = pathlib.Path(__file__).resolve().parent.parent / "skills/lead/SKILL.md"


class LeadSkill(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text()

    def check(self, needles):
        for needle in needles:
            with self.subTest(needle=needle):
                self.assertIn(needle.lower(), self.text.lower())

    def test_names_script_and_scout(self):
        self.check(["scripts/lead-brief", "scout"])

    def test_name_and_duplicate_check(self):
        self.check(["/rename lead", "another live session already has the name `lead`"])

    def test_launch_resume_and_live_branches(self):
        self.check(["claude agents --json --all", "SendMessage", "pid: null",
                    "scripts/launch\" --resume <sessionId> --bg", "Never resume a live row",
                    "scripts/launch\" --bg --permission-mode", "--plugin-dir", "crossSessionInbound",
                    "scripts/launch --no-model", "**Orchestrator** row", "**Models**",
                    "a live row wins"])

    def test_the_launch_line_is_not_restated(self):
        self.assertNotIn("claude --name orchestrator", self.text)
        self.assertNotIn("claude --bg --name", self.text)
        self.assertNotIn("claude --name lead", self.text)
        self.assertIn("scripts/launch --lead", self.text)

    def test_start_skill_passes_the_callers_mode_and_whitelists_arguments(self):
        start = (SKILL.parent.parent / "start" / "SKILL.md").read_text()
        for needle in ("<your own permission mode>", "`default`", "`--lead`", "`--permission-mode`", "Never paste the raw arguments"):
            with self.subTest(needle=needle):
                self.assertIn(needle, start)

    def test_setup_names_the_config_row(self):
        setup = (SKILL.parent.parent / "setup" / "SKILL.md").read_text()
        self.assertIn("Messages from your other sessions", setup)

    def test_model_not_found_only_fallback(self):
        self.check(["model_not_found", 'state: "failed"', "`error` field", "exactly once",
                    "usage limit", "never triggers"])

    def test_staying_current(self):
        self.check(["needs-lead", "watermark", "outcomes only"])

    def test_moves(self):
        self.check(["scripts/ci-times", "--checkpoint", "scripts/lead-brief",
                    "`lead` label", "`needs-lead`", "You never merge anything", "ADR 0004",
                    "## Agent Brief", "wontfix", "blocking edges", "needs-triage",
                    "`Source:` line", "over: yes", "every 10 merges", "PR ready",
                    ":grilling", ":domain-modeling", "Never invoke `wayfinder`",
                    "Answering asks", "Never by relay"])


ROOT = pathlib.Path(__file__).resolve().parent.parent


class RunSkillLeftThreads(unittest.TestCase):
    """The run skill and the worker prompt keep the --left follow-up rule (#442) and the pass-record lines folded in."""

    def setUp(self):
        self.run = (ROOT / "skills/run/SKILL.md").read_text()
        self.worker = (ROOT / "agents/worker.md").read_text()

    def test_left_threads_get_one_followup_and_a_left_for_line(self):
        for text in (self.run, self.worker):
            for needle in ("--followup <n>", "--no-followup", "`Left for #<n>`", "followup-needed:"):
                self.assertIn(needle, text)
        for needle in ("needs-triage", "`lead`", "a worker never runs `gh issue create`"):
            self.assertIn(needle, self.run)
        self.assertNotIn("gh issue create", self.worker)

    def test_send_back_exception_is_limited_to_lines_naming_only_threads(self):
        self.assertIn("name only unresolved review threads", self.run)
        self.assertIn("any other refusal keeps the worker's path", self.run)

    def test_every_rescue_call_with_a_title_carries_pr(self):
        calls = self.run.split("scripts/rescue")[1:]
        titled = [tail[:400] for tail in calls if "--title" in tail[:400]]
        self.assertGreaterEqual(len(titled), 3)
        for head in titled:
            self.assertIn("--pr <num>", head)

    def test_lead_pr_worktree_has_one_named_form_and_the_not_removed_reasons(self):
        self.assertIn("worktree add <scratchpad>/pr-<n> <head>", self.run)
        for reason in ("not on the PR's branch", "detached at another sha", "foreign-live", "not a registered worktree"):
            self.assertIn(reason, self.run)

    def test_a_rejected_touch_map_fails_closed_and_shared_files_are_never_counted(self):
        self.assertIn("**fails closed**", self.run)
        self.assertIn("never counted as an overlap", self.run)


if __name__ == "__main__":
    unittest.main()
