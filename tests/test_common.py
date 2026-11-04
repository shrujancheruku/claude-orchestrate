"""Unit tests for scripts/common.py: run, pr_ref, die and the exit-code constants."""

import json
import subprocess
import sys
import tempfile
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


def _vcs(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class EventsRootTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve() / "repo"
        self.repo.mkdir()
        _vcs(self.repo, "init", "-q", "-b", "main")
        for k, v in (("user.name", "T"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            _vcs(self.repo, "config", k, v)
        _vcs(self.repo, "commit", "-q", "--allow-empty", "-m", "chore: init")
        self.log = self.repo / ".claude" / "orchestrate-profiles" / "events.jsonl"

    def test_subdirectory_writes_to_the_checkout_log(self):
        sub = self.repo / "a" / "b"
        sub.mkdir(parents=True)
        common.append_event(sub, "gate", "pass", ref=1)
        self.assertEqual(json.loads(self.log.read_text())["ref"], 1)
        self.assertFalse((sub / ".claude").exists())

    def test_linked_worktree_writes_to_the_main_checkout_log(self):
        wt = self.repo / ".claude" / "worktrees" / "w1"
        _vcs(self.repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        common.append_event(wt, "ci", "pass", ref=2)
        self.assertEqual([json.loads(l)["ref"] for l in self.log.read_text().splitlines()], [2])
        self.assertFalse((wt / ".claude" / "orchestrate-profiles").exists())

    def test_outside_a_checkout_falls_back_to_the_given_directory(self):
        with tempfile.TemporaryDirectory() as d:
            common.append_event(d, "gate", "pass")
            self.assertTrue((Path(d) / ".claude" / "orchestrate-profiles" / "events.jsonl").exists())


class ReadJsonlTest(unittest.TestCase):
    def test_yields_only_object_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.jsonl"
            p.write_text('{"a": 1}\n[]\n3\n\n"s"\nnull\nnot json\n{"b": 2}\n')
            self.assertEqual(list(common.read_jsonl(p)), [{"a": 1}, {"b": 2}])
            self.assertEqual(list(common.read_jsonl(Path(d) / "missing")), [])


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
