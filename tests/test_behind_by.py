"""Structural checks: "up to date" means behind_by is 0, from the compare API, before merging (#64)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(p):
    return (ROOT / p).read_text()


class BehindBy(unittest.TestCase):
    def test_run_rechecks_behind_by_before_merge(self):
        s = read("skills/run/SKILL.md")
        step = next(l for l in s.splitlines() if l.startswith("6. **Approved**"))
        for needle in ("read `behind_by` again now", "because another PR may have merged since the gate ran",
                       "`gh pr update-branch <pr>`", "wait for CI", "don't merge",
                       "If `update-branch` fails", "A main-only update needs no re-review"):
            self.assertIn(needle, step)
        self.assertNotIn("merge the base", step)
        self.assertLess(step.index("read `behind_by` again"), step.index("scripts/merge"))


if __name__ == "__main__":
    unittest.main()
