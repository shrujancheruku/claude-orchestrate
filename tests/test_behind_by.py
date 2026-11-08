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
        for needle in ("--approved-head", "`wait-ci <pr> <head>`", "`re-review <pr>`", "a main-only update needs no re-review"):
            self.assertIn(needle, step)
        self.assertNotIn("read `behind_by`", s)
        self.assertNotIn("Post-approval risk guard:", s)

    def test_scout_has_no_pre_merge_checklist(self):
        # The gate script owns those checks (#270); the scout's behind_by job is a fact lookup only.
        s = read("agents/scout.md")
        self.assertNotIn("Pre-merge", s)
        self.assertNotIn("checklist", s)


if __name__ == "__main__":
    unittest.main()
