"""scripts/prompts: counts and lists the permission-prompt log (#569)."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "scripts" / "prompts"


def entry(time, tool="Bash", agent=None, session="s1", **tool_input):
    return {"time": time, "session_id": session, "agent_type": agent, "tool_name": tool, "cwd": "/x",
            "tool_input": tool_input or {"command": "ls"}, "permission_mode": "default"}


class Prompts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".claude").mkdir()

    def write(self, entries, name="orchestrate-prompts.jsonl"):
        (self.repo / ".claude" / name).write_text("".join(json.dumps(e) + "\n" for e in entries) + "not json\n")

    def run_prompts(self, *args):
        r = subprocess.run([str(PROMPTS), "--repo", str(self.repo), *args], capture_output=True, text=True, timeout=60)
        return r

    def test_no_log_is_zero(self):
        r = self.run_prompts()
        self.assertEqual((r.returncode, r.stdout), (0, "prompts: 0\n"))

    def test_counts_and_lists(self):
        self.write([
            entry("2026-11-09T10:00:00+00:00", command="git push"),
            entry("2026-11-09T10:01:00+00:00", "Edit", "orchestrate:worker", file_path="/a/b.py"),
            entry("2026-11-09T10:02:00+00:00", "Bash", "orchestrate:worker", command="make\n  test"),
            entry("2026-11-09T10:03:00+00:00", "Skill", "orchestrate:reviewer", **{}),
        ])
        out = self.run_prompts().stdout.splitlines()
        self.assertEqual(out[0], "prompts: 4")
        self.assertEqual(out[1], "by role: worker 2, orchestrator 1, reviewer 1")
        self.assertEqual(out[2], "by tool: Bash 2, Edit 1, Skill 1")
        self.assertEqual(out[3], "2026-11-09T10:00:00+00:00 orchestrator Bash git push")
        self.assertEqual(out[4], "2026-11-09T10:01:00+00:00 worker Edit /a/b.py")
        self.assertEqual(out[5], "2026-11-09T10:02:00+00:00 worker Bash make test")

    def test_a_prompt_seen_by_both_events_counts_once(self):
        def note(time, session="s1", kind="permission_prompt", message="Claude requested permissions to edit /r/hooks/guard.py which is a sensitive file."):
            return {"time": time, "source": "notification", "session_id": session, "agent_type": None, "tool_name": None,
                    "cwd": "/x", "tool_input": [], "message": message, "title": "t", "notification_type": kind}
        self.write([
            {**entry("2026-11-09T10:00:00+00:00", command="git push"), "source": "request"},
            note("2026-11-09T10:00:01+00:00", message="Claude needs your permission to use Bash"),  # the same prompt
            note("2026-11-09T10:05:00+00:00", kind="worker_permission_prompt"),  # no request: counts on its own
            {**entry("2026-11-09T10:10:00+00:00", command="a"), "source": "request"},
            note("2026-11-09T10:10:09+00:00", session="s2"),  # another session, 9 s later: not a pair
            entry("2026-11-09T10:20:00+00:00", command="old line with no source"),
        ])
        out = self.run_prompts().stdout.splitlines()
        self.assertEqual(out[0], "prompts: 5")
        self.assertEqual(out[1], "by role: orchestrator 4, worker 1")
        self.assertEqual(out[2], "by tool: Bash 3, ? 2")
        self.assertEqual(out[4], "2026-11-09T10:05:00+00:00 worker ? Claude requested permissions to edit /r/hooks/guard.py which is a sensitive file.")

    def test_one_notification_pairs_with_one_request(self):
        base = {"session_id": "s1", "agent_type": None, "cwd": "/x", "permission_mode": "default"}
        self.write([
            {**base, "time": "2026-11-09T10:00:00+00:00", "source": "request", "tool_name": "Bash", "tool_input": {"command": "a"}},
            {**base, "time": "2026-11-09T10:00:01+00:00", "source": "notification", "tool_name": None, "tool_input": [], "message": "m1"},
            {**base, "time": "2026-11-09T10:00:01+00:00", "source": "notification", "tool_name": None, "tool_input": [], "message": "m2"},
        ])
        self.assertEqual(self.run_prompts().stdout.splitlines()[0], "prompts: 2")

    def test_session_and_since(self):
        self.write([entry("2026-11-09T10:00:00Z", session="a"), entry("2026-11-09T11:00:00Z", session="b"),
                    entry("2026-11-09T12:00:00Z", session="b")])
        self.assertEqual(self.run_prompts("--session", "b").stdout.splitlines()[0], "prompts: 2")
        self.assertEqual(self.run_prompts("--since", "2026-11-09T11:30:00Z").stdout.splitlines()[0], "prompts: 1")
        self.assertEqual(self.run_prompts("--session", "b", "--since", "2026-11-09T11:00:00Z").stdout.splitlines()[0], "prompts: 2")

    def test_bad_since_and_exclusive_flags(self):
        r = self.run_prompts("--since", "soon")
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertEqual(len(r.stderr.splitlines()), 1)
        self.assertEqual(self.run_prompts("--since", "2026-11-09", "--watermark").returncode, 2)

    def test_watermark_is_the_last_pass_record(self):
        self.write([entry("2026-11-09T10:00:00Z"), entry("2026-11-09T12:00:00Z")])
        # no pass record yet: everything counts
        self.assertEqual(self.run_prompts("--session", "s1", "--watermark").stdout.splitlines()[0],
                         "prompts: 2 (no mark yet: counted from the start of the log)")
        profiles = self.repo / ".claude" / "orchestrate-profiles"
        profiles.mkdir()
        (profiles / "passes.jsonl").write_text(
            json.dumps({"ts": "2026-11-09T11:00:00+00:00", "session": "s1"}) + "\n"
            + json.dumps({"ts": "2026-11-09T13:00:00+00:00", "session": "other"}) + "\n")
        out = self.run_prompts("--session", "s1", "--watermark").stdout.splitlines()
        self.assertEqual(out[0], "prompts: 1")
        self.assertTrue(out[-1].startswith("2026-11-09T12:00:00Z"))

    def test_mark_works_without_a_pass_log(self):
        self.write([entry("2026-11-09T10:00:00Z"), entry("2999-01-01T00:00:00Z")])
        r = self.run_prompts("--session", "s1", "--mark")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("mark: 20"))
        self.assertFalse((self.repo / ".claude" / "orchestrate-profiles").exists())
        out = self.run_prompts("--session", "s1", "--watermark").stdout.splitlines()
        self.assertEqual(out[0], "prompts: 1")  # only the entry after the mark, and no "no mark" note
        # another session's mark does not move this one; no session reads only sessionless marks
        self.assertEqual(self.run_prompts("--session", "s2", "--watermark").stdout.splitlines()[0][:12], "prompts: 0 (")
        self.assertEqual(self.run_prompts("--watermark").stdout.splitlines()[0][:12], "prompts: 2 (")

    def test_session_mark_does_not_move_the_sessionless_watermark(self):
        self.write([entry("2026-11-09T10:00:00Z"), entry("2026-11-09T12:00:00Z"), entry("2026-11-09T14:00:00Z")])
        mark = self.repo / ".claude" / "orchestrate-prompts.mark"
        mark.parent.mkdir(exist_ok=True)
        mark.write_text(
            json.dumps({"ts": "2026-11-09T11:00:00+00:00", "session": None}) + "\n"
            + json.dumps({"ts": "2026-11-09T13:00:00+00:00", "session": "s1"}) + "\n")
        self.assertEqual(self.run_prompts("--watermark").stdout.splitlines()[0], "prompts: 2")
        self.assertEqual(self.run_prompts("--session", "s1", "--watermark").stdout.splitlines()[0], "prompts: 1")

    def test_mark_is_exclusive_with_since(self):
        self.assertEqual(self.run_prompts("--mark", "--since", "2026-11-09").returncode, 2)
        self.assertEqual(self.run_prompts("--mark", "--watermark").returncode, 2)

    def test_output_is_bounded(self):
        self.write([entry(f"2026-11-09T10:{i // 60:02d}:{i % 60:02d}Z", command="c" * 500) for i in range(100)])
        out = self.run_prompts().stdout.splitlines()
        self.assertEqual(out[0], "prompts: 100")
        self.assertEqual(out[3], "... 60 earlier")
        self.assertEqual(len(out), 4 + 40)
        self.assertTrue(all(len(line) < 300 for line in out))


if __name__ == "__main__":
    unittest.main()
