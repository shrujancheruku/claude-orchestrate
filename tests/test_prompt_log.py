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


def run_hook(stdin: str, cwd=None, role=None):
    env = {k: v for k, v in os.environ.items() if k != "ORCHESTRATE_ROLE"}
    if role is not None:
        env["ORCHESTRATE_ROLE"] = role
    return subprocess.run([sys.executable, "-I", str(HOOK)], input=stdin, capture_output=True, text=True, timeout=60, cwd=cwd, env=env)


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

    def test_hooks_json_registers_the_permission_notifications(self):
        entry = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["Notification"][0]
        self.assertEqual(entry["matcher"], "permission_prompt|worker_permission_prompt")
        self.assertIn("hooks/prompt_log.py", entry["hooks"][0]["command"])

    def notification(self, **kw):
        p = {"session_id": "s1", "cwd": str(self.repo), "hook_event_name": "Notification",
             "message": "Claude requested permissions to edit /r/hooks/guard.py which is a sensitive file.",
             "title": "Permission needed", "notification_type": "permission_prompt"}
        p.update(kw)
        return json.dumps(p)

    def test_notification_line_holds_message_and_title_and_decides_nothing(self):
        for kind in ("permission_prompt", "worker_permission_prompt"):
            with self.subTest(kind):
                r = run_hook(self.notification(notification_type=kind))
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
                last = self.lines()[-1]
                self.assertEqual((last["source"], last["notification_type"], last["title"]), ("notification", kind, "Permission needed"))
                self.assertIn("sensitive file", last["message"])
                self.assertEqual((last["session_id"], last["cwd"], last["tool_name"]), ("s1", str(self.repo), None))
                self.assertIsNone(last["agent_type"])

    def test_notification_without_the_event_name_and_a_long_message(self):
        run_hook(self.notification(hook_event_name=None, message="m" * 900, agent_type="orchestrate:worker"))
        last = self.lines()[0]
        self.assertEqual((last["source"], len(last["message"]), last["agent_type"]), ("notification", 500, "orchestrate:worker"))

    def test_request_line_says_so(self):
        run_hook(self.payload("Bash", {"command": "ls"}))
        self.assertEqual(self.lines()[0]["source"], "request")

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
            ("Read", {"file_path": "/a/d.py", "offset": 3}, {"file_path": "/a/d.py"}),
            ("Grep", {"pattern": "SECRET", "path": "/a/src"}, {"path": "/a/src", "keys": ["path", "pattern"]}),
            ("Glob", {"pattern": "SECRET*"}, {"path": None, "keys": ["pattern"]}),
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

    def test_role_recorded_only_when_orchestrate_role_is_set(self):
        r = run_hook(self.payload("Bash", {"command": "ls"}), role="orchestrator")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        run_hook(self.payload("Bash", {"command": "ls"}))
        run_hook(self.payload("Bash", {"command": "ls"}), role="")
        first, second, third = self.lines()
        self.assertEqual(first["role"], "orchestrator")
        self.assertNotIn("role", second)
        self.assertNotIn("role", third)

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


class PromptWaits(unittest.TestCase):
    """A prompt puts its session on the developer's waits file and the answer clears it (#706)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        (self.repo / ".git").mkdir()
        self.waits = self.repo / ".claude" / "orchestrate-waits.jsonl"
        sys.path.insert(0, str(ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(ROOT / "scripts"))

    def send(self, event, **kw):
        p = {"session_id": "sess-1234567", "cwd": str(self.repo), "hook_event_name": event}
        p.update(kw)
        r = run_hook(json.dumps(p))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def live(self):
        import waits
        return waits.live(self.repo)

    def test_hooks_json_registers_the_clearing_events(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
        for event in ("PostToolUse", "Stop"):
            self.assertIn("hooks/prompt_log.py", hooks[event][0]["hooks"][0]["command"])

    def test_a_prompt_adds_an_entry_that_says_where_to_answer(self):
        self.send("PermissionRequest", tool_name="Bash", tool_input={"command": "git push origin x\nsecond line"})
        (entry,) = self.live()
        self.assertEqual((entry["kind"], entry["from"], entry["session"]), ("prompt", "prompt", "sess-1234567"))
        self.assertIn("Bash git push origin x", entry["text"])
        self.assertNotIn("second line", entry["text"])
        self.assertEqual(entry["where"], "in that session: claude attach sess-123")
        self.assertEqual(self.waits.stat().st_mode & 0o777, 0o600)

    def test_a_notification_for_a_prompt_already_recorded_adds_nothing(self):
        self.send("PermissionRequest", tool_name="Bash", tool_input={"command": "x"})
        self.send("Notification", message="Claude needs your permission", notification_type="permission_prompt")
        self.assertEqual(len(self.live()), 1)

    def test_a_notification_alone_adds_an_entry(self):
        self.send("Notification", message="Claude requested permissions to edit /r/hooks/guard.py", notification_type="permission_prompt")
        (entry,) = self.live()
        self.assertIn("edit /r/hooks/guard.py", entry["text"])

    def test_the_answer_clears_it_for_that_session_only(self):
        self.send("PermissionRequest", tool_name="Bash", tool_input={"command": "x"})
        self.send("PermissionRequest", tool_name="Bash", tool_input={"command": "y"}, session_id="other")
        self.send("PostToolUse", tool_name="Bash", tool_input={})
        self.assertEqual([e["session"] for e in self.live()], ["other"])

    def test_a_tool_of_another_agent_in_the_same_session_does_not_clear_it(self):
        self.send("PermissionRequest", tool_name="Bash", tool_input={"command": "x"})
        self.send("PostToolUse", tool_name="Read", tool_input={}, agent_type="orchestrate:worker")
        self.assertEqual(len(self.live()), 1)

    def test_the_end_of_a_turn_clears_the_session(self):
        self.send("PermissionRequest", tool_name="Bash", tool_input={"command": "x"}, agent_type="orchestrate:worker")
        self.send("Stop")
        self.assertEqual(self.live(), [])

    def test_a_post_tool_event_with_no_waits_file_writes_nothing(self):
        self.send("PostToolUse", tool_name="Bash", tool_input={})
        self.assertFalse(self.waits.exists())


if __name__ == "__main__":
    unittest.main()
