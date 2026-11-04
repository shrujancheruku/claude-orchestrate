"""Unit tests for scripts/common.py: run, pr_ref, die and the exit-code constants."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common  # noqa: E402


class RunTest(unittest.TestCase):
    def test_returns_code_stdout_stderr(self):
        code, out, err = common.run(sys.executable, "-c", "import sys; print('o'); print('e', file=sys.stderr); sys.exit(3)")
        self.assertEqual((code, out, err), (3, "o\n", "e\n"))

    def test_returncode_alias(self):
        self.assertEqual(common.run(sys.executable, "-c", "pass").returncode, 0)

    def test_missing_binary_is_127_with_a_message(self):
        code, out, err = common.run("definitely-not-a-binary-xyz", "--version")
        self.assertEqual(code, 127)
        self.assertEqual(out, "")
        self.assertIn("definitely-not-a-binary-xyz", err)

    def test_missing_cwd_is_127_not_a_crash(self):
        self.assertEqual(common.run(sys.executable, "-c", "pass", cwd="/no/such/dir/xyz").code, 127)


class PrRefTest(unittest.TestCase):
    def test_hash_and_bare_number(self):
        self.assertEqual(common.pr_ref("#12"), ("12", 12))
        self.assertEqual(common.pr_ref("12"), ("12", 12))

    def test_non_number_keeps_its_text(self):
        self.assertEqual(common.pr_ref("#abc"), ("abc", "abc"))


class DieTest(unittest.TestCase):
    def test_exits_2_with_the_message_on_stderr(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            common.die("boom")
        self.assertEqual(cm.exception.code, common.EXIT_COULD_NOT_RUN)
        self.assertEqual(err.getvalue(), "boom\n")

    def test_convention_values(self):
        self.assertEqual((common.EXIT_OK, common.EXIT_VERDICT, common.EXIT_COULD_NOT_RUN), (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
