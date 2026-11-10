"""Tests for tests/run_parallel.py, the parallel module runner."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_parallel  # noqa: E402

RUNNER = Path(__file__).resolve().parent / "run_parallel.py"
PASSING = "class T(unittest.TestCase):\n    def test_x(self): pass\n"


class RunParallelTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def module(self, name, body):
        (self.dir / name).write_text("import unittest\n" + body)

    def run_runner(self):
        return subprocess.run([sys.executable, str(RUNNER), "-j", "2", str(self.dir)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

    def test_passing_modules_pass_and_print_only_the_summary(self):
        self.module("test_a.py", PASSING)
        r = self.run_runner()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 1)
        self.assertIn("1/1", r.stdout)

    def test_a_failing_module_fails_the_run_and_is_named(self):
        self.module("test_ok.py", PASSING)
        self.module("test_bad.py", "class T(unittest.TestCase):\n    def test_x(self): self.fail('boom-marker')\n")
        r = self.run_runner()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL test_bad", r.stdout)
        self.assertIn("boom-marker", r.stdout)
        self.assertNotIn("test_ok", r.stdout)

    def test_a_module_that_errors_on_import_fails(self):
        self.module("test_broken.py", "import no_such_module_xyz\n")
        r = self.run_runner()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL test_broken", r.stdout)

    def test_a_module_with_zero_tests_fails(self):
        self.module("test_empty.py", "x = 1\n")
        r = self.run_runner()
        self.assertEqual(r.returncode, 1)
        self.assertIn("no tests found", r.stdout)

    def test_bare_name_imports_from_the_tests_dir_load(self):
        (self.dir / "helper_mod.py").write_text("VALUE = 3\n")
        self.module("test_bare.py", "import helper_mod\nclass T(unittest.TestCase):\n"
                                    "    def test_x(self): self.assertEqual(helper_mod.VALUE, 3)\n")
        r = self.run_runner()
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_no_modules_fails(self):
        self.assertEqual(self.run_runner().returncode, 1)

    def test_failure_output_is_tail_bounded(self):
        self.module("test_noisy.py", "class T(unittest.TestCase):\n    def test_x(self):\n"
                                     "        print('\\n'.join('line%d' % i for i in range(500)))\n"
                                     "        self.fail('end')\n")
        r = self.run_runner()
        self.assertLess(len(r.stdout.splitlines()), 60)
        self.assertNotIn("line0\n", r.stdout)

    def test_a_deprecated_call_fails_the_run(self):
        # PYTHONWARNINGS=error::DeprecationWarning: what this Python deprecates, a newer one removes (#506).
        self.module("test_dep.py", "import warnings\nclass T(unittest.TestCase):\n    def test_x(self):\n"
                                   "        warnings.warn('old api', DeprecationWarning)\n")
        r = self.run_runner()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL test_dep", r.stdout)
        self.assertIn("DeprecationWarning", r.stdout)

    def test_caller_pythonpath_does_not_reach_the_modules(self):
        self.module("test_env.py", "import os\nclass T(unittest.TestCase):\n    def test_x(self):\n"
                                   "        self.assertNotIn('elsewhere', os.environ['PYTHONPATH'])\n")
        env = dict(os.environ, PYTHONPATH="elsewhere")
        r = subprocess.run([sys.executable, str(RUNNER), "-j", "1", str(self.dir)], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stdout)


class EventsLogGuardTest(unittest.TestCase):
    """#710: the tests never write the real events log. A module that runs the guard gets ORCHESTRATE_STATE_DIR; the run
    fails when a fixture row lands in the events log of the checkout the tests sit in."""

    ROW = '{"ts":"2026-01-01T00:00:00+00:00","session":"s","kind":"gh_call","script":"agent:scout","path":"repos/o/r/issues"}\n'

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve() / "repo"
        (self.repo / "tests").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "scripts").symlink_to(Path(__file__).resolve().parent.parent / "scripts")
        self.log = self.repo / ".claude" / "orchestrate-profiles" / "events.jsonl"
        self.log.parent.mkdir(parents=True)
        self.log.write_text('{"kind":"gh_call","path":"repos/real/one"}\n')

    def run_runner(self):
        return subprocess.run([sys.executable, str(RUNNER), "-j", "1", str(self.repo / "tests")],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

    def module(self, name, body):
        (self.repo / "tests" / name).write_text("import unittest\nimport os\n" + body)

    def test_a_run_that_leaves_the_real_log_alone_passes(self):
        self.module("test_quiet.py", PASSING)
        r = self.run_runner()
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_a_run_whose_tests_add_a_fixture_row_to_the_real_log_fails(self):
        self.module("test_leaky.py", "class T(unittest.TestCase):\n    def test_x(self):\n"
                                     f"        open({str(self.log)!r}, 'a').write({self.ROW!r})\n")
        r = self.run_runner()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("real events log", r.stdout)
        self.assertIn("repos/o/r/issues", r.stdout)

    def test_another_sessions_rows_appended_meanwhile_are_not_the_tests(self):
        self.module("test_live.py", "class T(unittest.TestCase):\n    def test_x(self):\n"
                                    f"        open({str(self.log)!r}, 'a').write('{{\"kind\":\"merge\",\"ref\":5}}\\n')\n")
        r = self.run_runner()
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_only_the_guard_modules_get_the_state_dir_override(self):
        probe = ("class T(unittest.TestCase):\n    def test_x(self):\n"
                 "        self.assertEqual(bool(os.environ.get('ORCHESTRATE_STATE_DIR')), %s)\n")
        self.module("test_guard.py", probe % "True")
        self.module("test_other.py", probe % "False")
        r = self.run_runner()
        self.assertEqual(r.returncode, 0, r.stdout)


class DefaultJobsTest(unittest.TestCase):
    PS = ("  101 /usr/bin/python3 /x/tests/run_parallel.py\n"
          "  102 python3 /y/tests/run_parallel.py -j 2 /y/tests\n"
          "  103 /bin/sh -c python3 /z/tests/run_parallel.py\n"
          "  104 vim tests/run_parallel.py\n"
          "  105 python3 /y/tests/test_run_parallel.py\n"
          "  106 python3 -u /w/tests/run_parallel.py\n"
          "  107 /Library/Frameworks/Python.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python "
          "/v/tests/run_parallel.py\n")

    def test_other_runs_are_counted_without_this_one_or_lookalikes(self):
        self.assertEqual(run_parallel.count_other_runners(self.PS, me=101), 3)
        self.assertEqual(run_parallel.count_other_runners(self.PS, me=999), 4)
        self.assertEqual(run_parallel.count_other_runners("", me=1), 0)

    def test_jobs_drop_with_the_other_live_runs_and_never_below_one(self):
        self.assertEqual(run_parallel.default_jobs(8, 0), 8)
        self.assertEqual(run_parallel.default_jobs(8, 1), 4)
        self.assertEqual(run_parallel.default_jobs(8, 3), 2)
        self.assertEqual(run_parallel.default_jobs(2, 5), 1)


if __name__ == "__main__":
    unittest.main()
