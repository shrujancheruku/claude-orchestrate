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

    def test_missing_cwd_is_could_not_run(self):
        r = self.verify("--cwd", str(self.env.repo / "nope"), "true")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)

    def test_no_commands_exits_2(self):
        self.assertEqual(self.verify().returncode, 2)

    def test_bad_timeout_exits_2(self):
        self.assertEqual(self.verify("--timeout", "soon", "true").returncode, 2)

    def test_hung_command_is_killed_and_fails(self):
        r = self.verify("--timeout", "0.5", "sleep 30", "echo ran-second > ran.txt")
        self.assertEqual(r.returncode, 1)
        self.assertIn("timed out after 0.5s", r.stdout)
        self.assertTrue((self.env.repo / "ran.txt").exists())


class VerifyConfigTest(unittest.TestCase):
    """`verify --config`: the commands come from the config, unexpanded by any caller's shell."""

    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def config(self, verify_section):
        p = self.env.repo / ".claude" / "orchestration.md"
        p.parent.mkdir(exist_ok=True)
        p.write_text("# Config\n\n" + verify_section + "\n## Shared files\n\n- **Shared files:** none\n")

    def verify(self, *args):
        return self.env.run("verify", *args, check=False)

    def test_command_with_substitution_and_quotes_runs_once_as_written(self):
        # The $(...) prints a newline then a second command; run as one command it only feeds echo.
        self.config("## Verify\n\n- `echo $(printf 'x\\ntouch planted.txt') > out.txt`\n")
        r = self.verify("--config")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(r.stdout.splitlines()), 1)
        self.assertFalse((self.env.repo / "planted.txt").exists())
        self.assertEqual((self.env.repo / "out.txt").read_text().split(), ["x", "touch", "planted.txt"])

    def test_this_repos_config_is_exactly_its_three_commands(self):
        import config
        root = Path(__file__).resolve().parent.parent
        groups = config.Config.load(root).verify_groups
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)
        self.assertTrue(groups[0][0].startswith("python3 -m py_compile"))
        self.assertEqual(groups[0][1:], ("python3 tests/run_parallel.py", "claude plugin validate ."))

    def test_prose_and_nested_bullets_are_not_commands(self):
        self.config("## Verify\n\nFrom the repo root, run `rm -rf x`.\n\n- `true` (a remark with `rm -rf y`)\n"
                    "  - `touch nested.txt`\n- Not a command: `touch prose.txt`\n\nNo lint step.\n")
        r = self.verify("--config")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 1)
        self.assertFalse((self.env.repo / "nested.txt").exists())
        self.assertFalse((self.env.repo / "prose.txt").exists())

    def test_then_bullet_starts_a_dependent_group(self):
        self.config("## Verify\n\n- `exit 1`\n- `--then`\n- `touch late.txt`\n")
        r = self.verify("--config")
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.env.repo / "late.txt").exists())
        self.assertTrue(r.stdout.splitlines()[1].startswith("skip"))

    def test_missing_config_exits_2(self):
        r = self.verify("--config")
        self.assertEqual(r.returncode, 2)
        self.assertIn("orchestration.md", r.stderr)

    def test_missing_section_exits_2(self):
        self.config("## Slots\n\n- **Slots:** 2\n")
        r = self.verify("--config")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no Verify section", r.stderr)

    def test_empty_section_exits_2(self):
        self.config("## Verify\n\nNothing to run here.\n")
        r = self.verify("--config")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no backticked command", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_config_with_commands_exits_2(self):
        self.config("## Verify\n\n- `touch ran.txt`\n")
        r = self.verify("--config", "true")
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.env.repo / "ran.txt").exists())


if __name__ == "__main__":
    unittest.main()
