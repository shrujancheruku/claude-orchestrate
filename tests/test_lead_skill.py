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

    def test_digest_approvals_line_comes_from_prompts(self):
        digest = self.text.split("### Pass digest")[1].split("### ")[0]
        for needle in ["scripts/prompts", "--watermark", "--mark", "Approvals", "new kind of prompt", "by role:", "by tool:"]:
            with self.subTest(needle=needle):
                self.assertIn(needle, digest)

    def test_name_and_duplicate_check(self):
        self.check(["/rename lead", "another live session already has the name `lead`"])

    def test_launch_resume_and_live_branches(self):
        self.check(["claude agents --json --all", "SendMessage", "pid: null",
                    "scripts/launch\" --resume-latest --bg", "Never resume a live row",
                    "scripts/launch\" --bg [<accepted tokens>]`", "picks the mode", "--settings '{\"disableAutoMode\":\"disable\"}'", "--plugin-dir", "crossSessionInbound",
                    "scripts/launch\" --no-model", "**Orchestrator** row", "**Models**",
                    "a live row wins"])

    def test_the_launch_line_is_not_restated(self):
        self.assertNotIn("claude --name orchestrator", self.text)
        self.assertNotIn("claude --bg --name", self.text)
        self.assertNotIn("claude --name lead", self.text)
        self.assertIn("scripts/launch --lead", self.text)

    def test_start_skill_leaves_the_mode_to_launch_and_whitelists_arguments(self):
        start = (SKILL.parent.parent / "start" / "SKILL.md").read_text()
        self.assertNotIn("<your own permission mode>", start)
        for needle in ("Pass none", "`acceptEdits` by default", "`--lead`", "`--permission-mode`", "Never paste the raw arguments", "`--profile`"):
            with self.subTest(needle=needle):
                self.assertIn(needle, start)

    def test_setup_names_the_config_row(self):
        setup = (SKILL.parent.parent / "setup" / "SKILL.md").read_text()
        self.assertIn("Messages from your other sessions", setup)

    def test_model_not_found_only_fallback(self):
        self.check(["model_not_found", 'state: "failed"', "`error` field", "exactly once",
                    "usage limit", "never triggers"])

    def test_run_flags_go_through_the_lead(self):
        self.check(["argument-hint", "`--greedy=<digits>`", "`--profile`", "Never paste the raw arguments",
                    "the flags were not applied", "keeps its launch flags", "Never stop or relaunch",
                    "**Lead** row", "a hand-started `/orchestrate:lead` keeps its session's model"])
        self.assertNotIn("Fable is the recommended", self.text)
        self.assertNotIn("Fable is recommended", self.text)

    def test_fable_hint_sits_at_grooming_step_only(self):
        head = self.text.split("## Start-up")[0]
        self.assertNotIn("fable", head.lower())
        self.check(["`/model fable` suits it", "when already on Fable"])

    def test_resume_picks_the_newest_stopped_row_of_this_repo(self):
        self.check(["--resume-latest", "newest `startedAt`", "`cwd` is this repo", "lists last",
                    "which sessionId it chose"])

    def test_the_no_model_relaunch_uses_the_skill_dir_path(self):
        self.assertNotIn("`scripts/launch --no-model`", self.text)

    def test_grooming_breakage_sweep_and_pass_digest(self):
        self.check(["### Grooming", "### Breakage sweep", "### Pass digest",
                    "## Also, folded from #<n>", "Agent Brief", "Never fold into a ticket that is claimed or in review",
                    "ungated ticket into one gated on the permission layer", "`hooks/**`", "reread the absorbers",
                    "Every 10 merges", "`NOT removed`", "`gh_call`", "closed without a merged PR",
                    "After every pass table", "the developer's only view", "launched with `--bg`", "see Grooming"])

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
