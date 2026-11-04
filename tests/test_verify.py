"""CLI tests for scripts/verify, driven through the harness."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv  # noqa: E402


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def verify(self, *args):
        return self.env.run("verify", *args, check=False)

    def test_all_pass(self):
        r = self.verify("true", "echo hi")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(l.startswith("pass") and "s  " in l for l in lines))

    def test_independent_failure_still_runs_the_rest(self):
        r = self.verify("echo boom; exit 3", "echo ran-second > ran.txt")
        self.assertEqual(r.returncode, 1)
        self.assertIn("(exit 3)", r.stdout)
        self.assertIn("boom", r.stdout)
        self.assertTrue((self.env.repo / "ran.txt").exists())
        self.assertIn("pass", r.stdout.splitlines()[-1])

    def test_dependent_failure_skips_later_stages(self):
        r = self.verify("exit 1", "true", "--then", "echo late > late.txt")
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.env.repo / "late.txt").exists())
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith("fail"))
        self.assertTrue(lines[1].startswith("pass"))  # same stage: still ran
        self.assertTrue(lines[-1].startswith("skip"))

    def test_passing_stage_lets_next_stage_run(self):
        r = self.verify("true", "--then", "echo ok")
        self.assertEqual(r.returncode, 0)
        self.assertEqual([l[:4] for l in r.stdout.splitlines()], ["pass", "pass"])

    def test_failure_tail_is_capped(self):
        r = self.verify("seq 1 500; exit 1")
        out = r.stdout.splitlines()
        self.assertIn("... 460 earlier lines omitted", r.stdout)
        self.assertIn("500", out[-1])
        self.assertNotIn(" 460\n", r.stdout)
        self.assertLessEqual(len(out), 42)

    def test_passing_output_is_not_shown(self):
        r = self.verify("echo noisy-output")
        self.assertNotIn("noisy-output", r.stdout.replace("echo noisy-output", ""))

    def test_no_commands_is_an_error(self):
        self.assertNotEqual(self.verify().returncode, 0)


if __name__ == "__main__":
    unittest.main()
