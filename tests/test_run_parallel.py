"""Tests for tests/run_parallel.py, the parallel module runner."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
