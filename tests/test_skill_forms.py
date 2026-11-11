"""Every plugin-script call the run and lead skills prescribe is a row of the guard's ORCHESTRATOR_FORMS (#545).

The skill text and the guard's table are two copies of one fact: which calls the orchestrator and the lead make. This
test pulls each `"${CLAUDE_SKILL_DIR}/../../scripts/<name>" ...` call out of both skills, fills its placeholders with
values of the right kind and asks the guard to approve it as the orchestrator, so a skill edit that adds or reshapes a
call fails here until the table has the row (and a row that no call needs is not pinned: test_invocation checks the
table against the scripts themselves).
"""

import re
import tempfile
import unittest
from pathlib import Path

from test_guard import GUARD_MODULE, approved

ROOT = Path(__file__).resolve().parent.parent
SKILLS = [ROOT / "skills" / "run" / "SKILL.md", ROOT / "skills" / "lead" / "SKILL.md"]
SID = "00000000-0000-0000-0000-0000000000aa"
CALL = re.compile(r'(python3 -I )?"\$\{CLAUDE_SKILL_DIR\}/\.\./\.\./scripts/([a-z][a-z.-]*)"([^`\n]*)')
# Calls with no row on purpose: launch starts sessions (#407), rescue pushes and opens PRs. They stay with the permission layer.
# The one launch call with a row is `--check-greedy` (#754); it is checked, the rest of launch stays exempt.
EXEMPT = {"launch", "rescue", "sandbox-git"}


class SkillCallsAreRows(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.main = self.root / "main"
        (self.main / ".git" / "worktrees" / "agent-abc").mkdir(parents=True)
        (self.main / ".git" / "worktrees" / "agent-abc" / "commondir").write_text("../..\n")
        self.work = self.main / ".claude" / "worktrees" / "agent-abc"
        self.work.mkdir(parents=True)
        (self.work / ".git").write_text(f"gitdir: {self.main}/.git/worktrees/agent-abc\n")
        (self.main / ".claude" / "orchestration.md").write_text("- **Post reviews:** yes\n")
        self.pad = f"/private/tmp/claude-0/-proj/{SID}/scratchpad"

    def fill(self, text):
        """The call's placeholders as concrete values; the order matters (the specific before the generic)."""
        rules = (
            (r"<id>\.\.\.", "T1 T2"), (r"<pr>…", "12 abc1234"), (r"<the worker's worktree>", str(self.work)),
            (r"<your scratchpad>", self.pad), (r"<the head commit the reviewer approved>", "abc1234abc1234"),
            (r"<the config's method>", "squash"), (r"<live \| none>", "live"), (r"<the slot count in force>", "3"),
            (r"<run id>", "111"), (r"<job id>", "222"), (r"<head>", "0123456789abcdef0123456789abcdef01234567"), (r"<branch>", "7-x"), (r"<pr>|<num>|<its merge PR>", "12"), (r"<ticket>|<n>|<donor>|<absorber>", "7"),
            (r"--greedy=N", "--greedy=5"), (r"\$\{CLAUDE_SESSION_ID\}", SID), (r"<[^<>]*>", "x"),
        )
        for pattern, value in rules:
            text = re.sub(pattern, value, text)
        return text

    def calls(self):
        for path in SKILLS:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                for m in CALL.finditer(line):
                    yield f"{path.parent.name}:{number}", m.group(2), (m.group(1) or "") + m.group(0)[len(m.group(1) or ""):]

    def test_every_call_the_skills_prescribe_is_approved_for_the_orchestrator(self):
        seen = set()
        for where, name, call in self.calls():
            if name in EXEMPT and not (name == "launch" and "--check-greedy" in call):
                continue
            seen.add(name)
            command = self.fill(call).rstrip(" ,;:")
            with self.subTest(f"{where} {name}: {command}"):
                self.assertIn(name, GUARD_MODULE.FORM_SCRIPTS)
                self.assertTrue(approved(None, command, self.main, session_id=SID))
        self.assertGreaterEqual(seen, {"board", "ci-rerun", "ci-times", "claims", "frontier", "gate", "holds", "lead-brief",
                                       "lead-route", "merge", "profile.py", "prompts", "rebuild-state", "scratch", "snapshot",
                                       "threads", "watch-ci", "worker-report"})

    def test_a_call_with_an_unknown_flag_would_fail_the_check(self):
        # The check can fail: the same extraction on a call the table does not know.
        self.assertFalse(approved(None, self.fill('"${CLAUDE_SKILL_DIR}/../../scripts/claims" --bogus <n>'), self.main, session_id=SID))


class WaitingFooterProse(unittest.TestCase):
    """The prose side of the developer-waits footer (#706): who records an ask, and who prints the footer."""

    def setUp(self):
        self.run_text = SKILLS[0].read_text()
        self.lead_text = SKILLS[1].read_text()

    def test_every_direct_ask_of_the_orchestrator_goes_through_waiting_add_and_is_cleared(self):
        for needle in ('scripts/waiting" --add "<the ask, with the choices>" --from orchestrator', '--where "<where they answer',
                       'scripts/waiting" --done <id>', "Every ask you put to the developer yourself"):
            with self.subTest(needle):
                self.assertIn(needle, self.run_text)

    def test_a_ready_gated_pr_is_sent_to_the_lead_and_not_also_asked_in_the_orchestrators_session(self):
        self.assertIn("`merge-ready: PR #<n> (#<ticket>) — <command>`", self.run_text)
        self.assertIn("do not also ask the developer to merge in your session", self.run_text)
        self.assertIn("`merged: PR #<n>`", self.run_text)

    def test_the_orchestrators_pass_report_carries_the_footer_only_when_the_config_says_so(self):
        self.assertIn("`orchestrator pass reports: yes`", self.run_text)
        self.assertIn('scripts/waiting" --repo . --plain', self.run_text)

    def test_the_lead_prints_the_plain_footer_after_every_turn_and_records_its_relayed_asks(self):
        for needle in ('scripts/waiting" --repo . --plain', 'scripts/waiting" --add "', 'scripts/waiting" --done <id>',
                       "merge-ready: PR #<n> (#<ticket>) — <command>", "Never run the command (ADR 0004)", "merged: PR #<n>"):
            with self.subTest(needle):
                self.assertIn(needle, self.lead_text)

    def test_setup_writes_the_section(self):
        template = (ROOT / "skills" / "setup" / "config-template.md").read_text()
        self.assertIn("## Developer waits", template)
        self.assertIn("Developer waits", (ROOT / "skills" / "setup" / "SKILL.md").read_text())


if __name__ == "__main__":
    unittest.main()
