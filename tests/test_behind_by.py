"""Structural checks: the orchestrator leaves "up to date" (behind_by 0) to `merge --approved-head` (#64, #167)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(p):
    return (ROOT / p).read_text()


class BehindBy(unittest.TestCase):
    def test_run_leaves_the_behind_check_to_merge(self):
        s = read("skills/run/SKILL.md")
        step = next(l for l in s.splitlines() if l.startswith("6. **Approved**"))
        for needle in ("--approved-head", "Exit 5", "Exit 6", "wait for CI", "a main-only update needs no re-review"):
            self.assertIn(needle, step)
        self.assertNotIn("read `behind_by`", s)
        self.assertNotIn("Post-approval risk guard:", s)

    def test_scout_no_longer_checks_behind_by_in_its_checklist(self):
        s = read("agents/scout.md")
        checklist = s.split("## Pre-merge checklist")[1].split("##")[0]
        self.assertNotIn("behind_by", checklist)


if __name__ == "__main__":
    unittest.main()
