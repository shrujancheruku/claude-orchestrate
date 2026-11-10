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
    "rate-limit cache path": r"github-ratelimit",
    "worker-report retry count": r"on the third in a row",
    "watch-ci constants": r"five minutes|three times in a row|60 seconds",
    "a rate limit's prose key": r"rate limit until",  # the one word is `retry-after <time>` (#542)
}


class RunSkillProse(unittest.TestCase):
    def test_no_restated_script_rules(self):
        for what, pattern in FORBIDDEN.items():
            with self.subTest(what):
                self.assertIsNone(re.search(pattern, SKILL), what)

    def test_every_ci_wait_is_the_monitor_and_sleep_is_forbidden(self):
        # Since 2026-11-08 the orchestrator waited for CI with `sleep` loops and merged ~7 minutes late (#542).
        self.assertRegex(SKILL, r"never run `sleep`, never poll `gh api` by hand")
        self.assertRegex(SKILL, r"Never wait with `sleep` or a hand-polled `gh api`")
        self.assertRegex(SKILL, r"no budget or rate-limit rule suspends `watch-ci`")

    def test_the_rate_limit_rule_is_stated_once(self):
        self.assertEqual(SKILL.count("never a sleep"), 1)
        self.assertEqual(SKILL.count("## Rate limits"), 1)

    def test_named_scripts_exist(self):
        for name in sorted(set(re.findall(r"`scripts/([A-Za-z][A-Za-z0-9_-]*(?:\.py)?)`", SKILL))):
            with self.subTest(name):
                self.assertTrue((ROOT / "scripts" / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
