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
EXEMPT = {"launch", "rescue", "sandbox-git",
          "fold", "sweep"}  # no guard row yet (hooks/guard.py is held by #694); #661 adds both rows and drops these two


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
            (r"<run id>", "111"), (r"<job id>", "222"), (r"<head>", "abc1234"), (r"<branch>", "7-x"), (r"<pr>|<num>|<its merge PR>", "12"), (r"<ticket>|<n>", "7"),
            (r"\$\{CLAUDE_SESSION_ID\}", SID), (r"<[^<>]*>", "x"),
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
            if name in EXEMPT:
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


if __name__ == "__main__":
    unittest.main()
