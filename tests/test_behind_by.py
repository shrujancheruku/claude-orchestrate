"""Structural checks: "up to date" means behind_by is 0, from the compare API, before merging (#64)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPARE = "compare/<base>...<head> --jq .behind_by"


def read(p):
    return (ROOT / p).read_text()


class BehindBy(unittest.TestCase):
    def test_scout_reads_behind_by_not_mergeable_state(self):
        s = read("agents/scout.md")
        self.assertIn(COMPARE, s)
        self.assertIn("never inferred from `mergeable` or `mergeStateStatus`", s)
        self.assertIn("`behind_by` from the compare call above is 0", s)
        self.assertIn("A mergeable or CLEAN host state is not a pass", s)
        self.assertIn("If the compare call fails, report 3 as `pending`, never `pass`", s)
        self.assertIn("Item 3 also carries its behind_by number in parentheses", s)

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
