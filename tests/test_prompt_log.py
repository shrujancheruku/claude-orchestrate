"""hooks/prompt_log.py: one log line per permission prompt, and never a decision (#569)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "prompt_log.py"


def run_hook(stdin: str, cwd=None):
    return subprocess.run([sys.executable, "-I", str(HOOK)], input=stdin, capture_output=True, text=True, timeout=60, cwd=cwd)


class PromptLog(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        (self.repo / ".git").mkdir()
        self.log = self.repo / ".claude" / "orchestrate-prompts.jsonl"

    def payload(self, tool, tool_input, **kw):
        p = {"session_id": "s1", "cwd": str(self.repo), "permission_mode": "default", "hook_event_name": "PermissionRequest",
             "tool_name": tool, "tool_input": tool_input}
        p.update(kw)
        return json.dumps(p)

    def lines(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()]

    def test_hooks_json_registers_permission_request(self):
        entry = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PermissionRequest"][0]
        self.assertEqual(entry["matcher"], "*")
        self.assertIn("hooks/prompt_log.py", entry["hooks"][0]["command"])

    def test_log_is_owner_only_and_an_older_0644_file_is_tightened(self):
        import importlib.util
        import stat
        spec = importlib.util.spec_from_file_location("prompt_log_under_test", HOOK)
        prompt_log = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prompt_log)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            path.write_text("old\n")
            path.chmod(0o644)
            prompt_log.append(path, "x\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            fresh = Path(tmp) / "new.jsonl"
            prompt_log.append(fresh, "x\n")
            self.assertEqual(stat.S_IMODE(fresh.stat().st_mode), 0o600)

    def test_hooks_json_runs_the_guard_for_monitor(self):
        entry = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"][0]
        self.assertIn("Monitor", entry["matcher"].split("|"))

    def test_gitignored(self):
        self.assertIn(".claude/orchestrate-prompts.jsonl", (ROOT / ".gitignore").read_text().splitlines())

    def test_one_line_per_tool(self):
        long_cmd = "echo " + "x" * 3000
        cases = [
            ("Bash", {"command": long_cmd, "description": "d"}, {"command": long_cmd[:2000]}),
            ("Monitor", {"command": "tail -f log", "timeout_ms": 5}, {"command": "tail -f log"}),
            ("Edit", {"file_path": "/a/b.py", "old_string": "SECRET", "new_string": "X"}, {"file_path": "/a/b.py"}),
            ("Write", {"file_path": "/a/c.py", "content": "SECRET"}, {"file_path": "/a/c.py"}),
            ("Skill", {"skill": "x", "args": "SECRET"}, ["args", "skill"]),
            ("mcp__srv__tool", {"q": "SECRET", "a": 1}, ["a", "q"]),
        ]
        for tool, given, expected in cases:
            with self.subTest(tool):
                r = run_hook(self.payload(tool, given))
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
                last = self.lines()[-1]
                self.assertEqual(last["tool_name"], tool)
                self.assertEqual(last["tool_input"], expected)
                self.assertEqual((last["session_id"], last["permission_mode"], last["cwd"]), ("s1", "default", str(self.repo)))
                self.assertIsNone(last["agent_type"])
                self.assertIn("time", last)
        self.assertEqual(len(self.lines()), len(cases))
        self.assertNotIn("SECRET", self.log.read_text())

    def test_agent_type_recorded(self):
        run_hook(self.payload("Bash", {"command": "ls"}, agent_type="orchestrate:worker"))
        self.assertEqual(self.lines()[0]["agent_type"], "orchestrate:worker")

    def test_worktree_writes_to_the_main_checkout(self):
        wt = self.repo / ".claude" / "worktrees" / "w"
        wt.mkdir(parents=True)
        gitdir = self.repo / ".git" / "worktrees" / "w"
        gitdir.mkdir(parents=True)
        (gitdir / "commondir").write_text("../..\n")
        (wt / ".git").write_text(f"gitdir: {gitdir}\n")
        run_hook(self.payload("Bash", {"command": "ls"}, cwd=str(wt)))
        self.assertEqual(len(self.lines()), 1)
        self.assertFalse((wt / ".claude" / "orchestrate-prompts.jsonl").exists())

    def test_malformed_payload_is_swallowed(self):
        for bad in ("", "not json", "[1]", '{"cwd": 3, "tool_input": 5}'):
            with self.subTest(bad):
                r = run_hook(bad, cwd=self.repo)
                self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_unwritable_file_is_swallowed(self):
        (self.repo / ".claude").mkdir()
        self.log.mkdir()  # a directory where the file should be
        r = run_hook(self.payload("Bash", {"command": "ls"}))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("prompt_log", r.stderr)

    def test_concurrent_appends_do_not_interleave(self):
        n = 12
        procs = [subprocess.Popen([sys.executable, "-I", str(HOOK)], stdin=subprocess.PIPE, text=True,
                                  stdout=subprocess.PIPE) for _ in range(n)]
        for i, p in enumerate(procs):
            p.stdin.write(self.payload("Bash", {"command": f"c{i} " + "y" * 1500}, session_id=f"s{i}"))
            p.stdin.close()
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
            p.stdout.close()
        got = self.lines()  # every line parses
        self.assertEqual(sorted(x["session_id"] for x in got), sorted(f"s{i}" for i in range(n)))


if __name__ == "__main__":
    unittest.main()
