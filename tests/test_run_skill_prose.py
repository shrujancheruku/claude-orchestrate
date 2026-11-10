"""The run skill applies script output; it must not restate rules the scripts own (#540).

The orchestrator reads ticket state through `claims`, never the state file, and never removes
a reviewer's scratch tree (`merge` does, when the PR lands). A script's internal constants live
in its docstring, not in the prompt.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = (ROOT / "skills/run/SKILL.md").read_text()

FORBIDDEN = {
    "reads the state file": r"(?i)\bin the state file\b",
    "removes a reviewer's tree": r"remove the `pr-",
    "stale reviewer-tree ticket": r"#445",
}
# The `watch-ci` paragraph still states these constants; #542 rewrites it and then moves this
# pattern into FORBIDDEN, dropping the line skip below.
WATCH_CI_CONSTANTS = r"five minutes|three times in a row|60 seconds"


class RunSkillProse(unittest.TestCase):
    def test_no_restated_script_rules(self):
        for what, pattern in FORBIDDEN.items():
            with self.subTest(what):
                self.assertIsNone(re.search(pattern, SKILL), what)

    def test_no_watch_ci_constants_outside_its_paragraph(self):
        rest = "\n".join(l for l in SKILL.splitlines() if "watch-ci" not in l)
        self.assertIsNone(re.search(WATCH_CI_CONSTANTS, rest))

    def test_named_scripts_exist(self):
        for name in sorted(set(re.findall(r"`scripts/([A-Za-z][A-Za-z0-9_-]*(?:\.py)?)`", SKILL))):
            with self.subTest(name):
                self.assertTrue((ROOT / "scripts" / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
