"""Regression corpus for hooks/guard.py: every Bash call mined from real sessions (scripts/guard-corpus).

Each line of tests/fixtures/guard-corpus.jsonl is {"role", "command", "expected"} plus an optional
"gap": "#N". `expected` is what the guard should decide. It began as a snapshot of the guard
and was then hand-checked for the write-shaped calls (git push, gh pr merge/create/review,
gh api with a method or -f/-F, gh issue edit/close/comment). A line the guard gets wrong today
carries `gap` (the ticket for the guard bug) and the right `expected`; the test requires the guard
to still be wrong there, so the fix shows up here as a failure that says to delete the `gap`.

A guard change that flips a decision therefore shows as a failure here, and the fix is a
reviewed edit to the corpus line in the same PR.

Lines whose long quoted text became `TEXT` (a `bash -c "..."`, `python3 -c "..."` or `gh api "..."`
call) and heredoc bodies (`BODY`) are shape checks, not the original command: the quoted text is gone.
A line with `"background": true` is a call made with `run_in_background` set.
Lines with `"source": "hand"` are written by hand, not mined: they pin refusals no real session
exercised (a worker pushing to main, a scout writing), so over-allowing shows up too.

Run: python3 -m unittest discover tests
"""

import importlib.machinery
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from collections import Counter
from pathlib import Path

import transcripts

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "hooks" / "guard.py"
CORPUS = ROOT / "tests" / "fixtures" / "guard-corpus.jsonl"
EXTRACT = ROOT / "scripts" / "guard-corpus"

# Independent of the extract script's own scrubbing, so a hole in one is caught by the other.
LEAKS = re.compile(
    r"/Users/|/home/(?!user\b)|-(?:Users|home)-(?!owner\b)|[\w.+-]+@(?!example\.com)[\w-]+\.\w+"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-(?!000000000000)[0-9a-f]{12}\b"
    r"|/private/tmp/claude-(?!0/)\d+|/var/folders/(?!xx/yy/T/)|\btoolu_(?!REDACTED)\w+"
    r"|\bagent-(?!x\b)[0-9a-f]{16,}|\ba[0-9a-f]{16}\b|\.ssh/(?!KEY)|SSH_AUTH_SOCK=(?!SOCK)"
    r"|\bgh[pousr]_\w{20,}|github_pat_|\bsk-[\w-]{16,}|\bxox[a-z]-|AKIA[0-9A-Z]{16}|eyJ[\w-]{10,}\.[\w-]{10,}\."
    r"|(?i:bearer\s+(?!REDACTED)\S{8,})|\b(?!0{40}\b)[0-9a-f]{40}\b"
)


def load_extract():
    # The script does `import common`; run by path it has its own directory on sys.path, a loader doesn't.
    if str(EXTRACT.parent) not in sys.path:
        sys.path.insert(0, str(EXTRACT.parent))
    loader = importlib.machinery.SourceFileLoader("guard_corpus", str(EXTRACT))
    spec = importlib.util.spec_from_loader("guard_corpus", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def rows():
    return [json.loads(line) for line in CORPUS.read_text().splitlines() if line.strip()]


class Decider:
    """Runs the guard's main() in-process on the hook's JSON input, so 500 calls take a second."""

    def __init__(self, cwd):
        spec = importlib.util.spec_from_file_location("guard_under_test", GUARD)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.cwd = cwd

    def __call__(self, role, command, background=False, timeout=None):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.cwd}
        if background:
            payload["tool_input"]["run_in_background"] = True
        if timeout is not None:
            payload["tool_input"]["timeout"] = timeout
        if role:
            payload["agent_type"] = role
        stdin, stdout = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO(json.dumps(payload)), io.StringIO()
        try:
            self.module.main()
            out = sys.stdout.getvalue()
        except SystemExit:
            out = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = stdin, stdout
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"].replace("allow", "approve") if out else "allow"


class GuardCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = rows()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        # The same cwd the extract script snapshots with: a config that turns review posting on.
        load_extract().write_config(cls.tmp.name)
        cls.decide = Decider(cls.tmp.name)

    def test_corpus_is_seeded_and_well_formed(self):
        self.assertGreater(len(self.rows), 400)
        for n, row in enumerate(self.rows, 1):
            with self.subTest(line=n):
                self.assertLessEqual(set(row), {"role", "command", "expected", "gap", "source", "background", "timeout"})
                self.assertTrue(row["role"].startswith("orchestrate:"), row["role"])
                self.assertTrue(row["command"].strip())
                self.assertIn(row["expected"], ("allow", "approve", "deny"))
                if "gap" in row:
                    self.assertRegex(row["gap"], r"^#\d+$")
        keys = [(r["role"], r["command"]) for r in self.rows]
        self.assertEqual([k for k, c in Counter(keys).items() if c > 1], [], "duplicate lines")

    def test_guard_decides_every_line_as_labelled(self):
        for n, row in enumerate(self.rows, 1):
            actual = self.decide(row["role"], row["command"], row.get("background", False), row.get("timeout"))
            label = f"line {n} ({row['role']}): {row['command'][:100]!r}"
            with self.subTest(label):
                # `allow` means the rules accept the call (silently or by an explicit approval); `approve` is
                # the explicit `permissionDecision: "allow"` (#358).
                matches = actual == row["expected"] or (row["expected"] == "allow" and actual == "approve")
                if "gap" in row:
                    self.assertFalse(
                        matches,
                        f"{row['gap']} looks fixed: the guard now gives the expected decision; drop `gap`",
                    )
                else:
                    self.assertTrue(matches, f"guard says {actual}, corpus says {row['expected']}")

    def test_hand_written_lines_pin_over_allowing(self):
        hand = [r for r in self.rows if r.get("source") == "hand"]
        self.assertGreaterEqual(len(hand), 10)
        for role in ("orchestrate:worker", "orchestrate:scout"):
            self.assertTrue([r for r in hand if r["role"] == role and r["expected"] == "deny"], role)

    def test_corpus_covers_the_decisions_that_matter(self):
        kinds = Counter((r["role"], r["expected"]) for r in self.rows)
        for role in ("orchestrate:worker", "orchestrate:reviewer", "orchestrate:scout"):
            self.assertGreater(kinds[(role, "allow")], 0, role)
        self.assertGreater(kinds[("orchestrate:reviewer", "deny")], 0)

    def test_corpus_has_no_paths_tokens_or_output(self):
        text = CORPUS.read_text()
        self.assertEqual(LEAKS.findall(text), [])
        extract = load_extract()
        for n, row in enumerate(self.rows, 1):
            with self.subTest(line=n):
                self.assertEqual(extract.normalise(row["command"]), row["command"], "not normalised")
                # No tool output: every line is a command, with nothing but its label beside it.
                self.assertLess(len(row["command"]), 5000)


class Extract(unittest.TestCase):
    """The extract script, on a made-up session."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # A real checkout: the repo a call is normalised against is found by asking git, as the script does.
        self.dir = Path(self.tmp.name).resolve()
        self.repo = self.dir / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.out = self.dir / "corpus.jsonl"
        projects = self.dir / "projects" / "proj"
        (projects / "sess" / "subagents").mkdir(parents=True)
        transcripts.write_jsonl(projects / "sess.jsonl", [self.call("rm -rf /Users/me/x", str(self.repo))])
        self.sub = projects / "sess" / "subagents"
        self.projects = projects.parent

    @staticmethod
    def call(command, cwd):
        return {"type": "assistant", "cwd": cwd,
                "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}},
                                        {"type": "tool_use", "name": "Read", "input": {"file_path": "/Users/me/a"}}]}}

    def add_agent(self, name, agent_type, commands, cwd=None):
        cwd = cwd or str(self.repo / ".claude" / "worktrees" / "agent-abc")
        transcripts.write_agent(self.sub, name, [self.call(c, cwd) for c in commands], {"agentType": agent_type})

    def run_extract(self, *extra):
        import subprocess
        r = subprocess.run([str(EXTRACT), "sess", "--projects-dir", str(self.projects), "--out", str(self.out), *extra],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_find_session_by_id_by_path_and_missing(self):
        extract = load_extract()
        self.assertEqual(extract.find_session("sess", self.projects), self.projects / "proj" / "sess.jsonl")
        path = self.projects / "proj" / "sess.jsonl"
        self.assertEqual(extract.find_session(str(path), self.projects), path)
        with self.assertRaises(SystemExit) as cm, unittest.mock.patch("sys.stderr", io.StringIO()) as err:
            extract.find_session("nope", self.projects)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("no transcript for session 'nope'", err.getvalue())

    def lines(self):
        return [json.loads(line) for line in self.out.read_text().splitlines()]

    def test_one_line_per_distinct_subagent_call_with_a_snapshot_decision(self):
        self.add_agent("a1", "orchestrate:scout", ["git commit -m x", "git log", "git log"])
        self.add_agent("a2", "orchestrate:worker", ["git push origin main"])
        self.run_extract()
        self.assertEqual(
            self.lines(),
            [{"role": "orchestrate:scout", "command": "git commit -m x", "expected": "deny"},
             {"role": "orchestrate:scout", "command": "git log", "expected": "allow"},
             {"role": "orchestrate:worker", "command": "git push origin main", "expected": "deny"}],
        )  # the main session's `rm -rf` is not mined: the guard never acts on it

    def test_normalises_paths_secrets_and_bodies(self):
        token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
        self.add_agent("a1", "orchestrate:worker", [
            f"cd {self.repo}/.claude/worktrees/agent-abc && cat /Users/me/notes.md",
            f"curl -H 'Authorization: Bearer {token}' https://x.example/a me@corp.io",
            "gh pr create --title t --body-file - <<'EOF'\nprivate text\nEOF",
            'git commit -m "a long private message about the internals of some unreleased feature"',
        ])
        self.run_extract()
        text = "\n".join(r["command"] for r in self.lines())
        for gone in ("/Users/me", "me@corp.io", token, "private text", "internals"):
            self.assertNotIn(gone, text)
        self.assertIn("cd /repo/.claude/worktrees/agent-x && cat /home/user/notes.md", text)

    def test_rerun_adds_only_new_lines_and_keeps_hand_labels(self):
        self.add_agent("a1", "orchestrate:scout", ["git log"])
        self.run_extract()
        row = self.lines()[0]
        self.out.write_text(json.dumps({**row, "expected": "deny", "gap": "#1"}) + "\n")  # hand-corrected
        self.add_agent("a2", "orchestrate:scout", ["git log", "git status"])
        self.assertIn("added 1", self.run_extract())
        self.assertEqual([(r["command"], r["expected"], r.get("gap")) for r in self.lines()],
                         [("git log", "deny", "#1"), ("git status", "allow", None)])
        self.assertIn("added 0", self.run_extract())

    def test_scrubs_any_user_and_session_ids(self):
        self.add_agent("a1", "orchestrate:worker", [
            "ls /Users/zed/code/app/src /home/zed/.ssh/id_rsa; ls /Users/zed/.claude/projects/-Users-zed-code-app/"
            "11111111-2222-4333-8444-555555555555.jsonl",
            "gh api repos/zedcorp/app/issues/1; cat /private/tmp/claude-501/x /var/folders/ab/cd1234/T/tmpq/y",
            'echo toolu_01AbCdEf agent-message from="a1234567890abcdef" $SSH_AUTH_SOCK=1; SSH_AUTH_SOCK=/tmp/s/agent.1 ssh',
        ], cwd="/Users/zed/code/app")
        self.run_extract()
        text = "\n".join(r["command"] for r in self.lines())
        for gone in ("zed", "11111111", "claude-501", "ab/cd1234", "toolu_01", "a123456", "id_rsa", "/tmp/s/agent"):
            self.assertNotIn(gone, text)
        self.assertEqual(LEAKS.findall(text), [])

    def test_scrubs_the_running_users_dashed_and_dotted_home(self):
        from unittest import mock
        extract = load_extract()
        with mock.patch.object(Path, "home", return_value=Path("/Users/jane-doe.x")):
            out = extract.normalise("ls /Users/jane-doe.x/code /x/-Users-jane-doe-x-code-app/y")
        self.assertEqual(out, "ls /home/user/code /x/-Users-owner-code-app/y")
        self.assertEqual(LEAKS.findall(out), [])

    def test_blank_corpus_lines_are_skipped(self):
        self.add_agent("a1", "orchestrate:scout", ["git log"])
        row = json.dumps({"role": "orchestrate:scout", "command": "git status", "expected": "allow"})
        self.out.write_text(f"\n{row}\n   \n")
        self.assertIn("added 1", self.run_extract())
        self.assertEqual([r["command"] for r in self.lines()], ["git status", "git log"])

    def test_a_garbled_corpus_line_is_named_by_number(self):
        import subprocess
        self.add_agent("a1", "orchestrate:scout", ["git log"])
        self.out.write_text('\n{"role": "x", "command": "y", "expected": "allow"}\n{oops\n')
        r = subprocess.run([str(EXTRACT), "sess", "--projects-dir", str(self.projects), "--out", str(self.out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("line 3", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_crashing_guard_is_reported_with_its_command_and_stderr(self):
        bad = self.dir / "bad-guard.py"
        bad.write_text("import sys; sys.stderr.write('boom'); sys.exit(3)\n")
        extract = load_extract()
        extract.GUARD = bad
        with self.assertRaises(SystemExit) as cm, unittest.mock.patch("sys.stderr", io.StringIO()) as err:
            extract.decide("orchestrate:scout", "git log", str(self.dir))
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("boom", err.getvalue())
        self.assertIn("git log", err.getvalue())

    def test_stray_lines_and_non_dict_blocks_do_not_stop_the_extract(self):
        # A `[]` line used to make the extract raise (common.read_jsonl skips it now); non-dict blocks are skipped too.
        good = {"type": "assistant", "cwd": str(self.repo), "message": {"content": [
            "text", 3, None, {"type": "tool_use", "name": "Bash", "input": {"command": "git log"}}]}}
        transcripts.write_agent(self.sub, "a1", ["[]", json.dumps(good), "3", "not json"], {"agentType": "orchestrate:scout"})
        self.run_extract()
        self.assertEqual(self.lines(), [{"role": "orchestrate:scout", "command": "git log", "expected": "allow"}])

    def test_dry_run_writes_nothing(self):
        self.add_agent("a1", "orchestrate:scout", ["git log"])
        self.run_extract("--dry-run")
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
