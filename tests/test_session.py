"""Tests for scripts/session.py: the one reader of a session's transcripts and the agents it dispatched."""

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import session  # noqa: E402
from transcripts import bash, completion, dispatch, main_path, reply, write_jsonl, write_session  # noqa: E402


class SessionReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projects = Path(self.tmp.name).resolve() / "projects"
        self.main = main_path(self.projects)

    def load(self):
        return session.Transcript(self.main)

    def test_agents_join_each_dispatch_to_its_transcript_and_keep_both_model_orders(self):
        write_session(self.main,
                      [dispatch("t1", "Build #5", model="sonnet", prompt="Touch map: a.py:1.\nBranch: 5-x.")],
                      [{"id": "aaa", "tool_use": "t1", "records": [reply("2026-11-04T09:01:00Z", "claude-opus-5-5", usage={"input_tokens": 1})],
                        "meta": {"model": "haiku", "description": "ignored"}}])
        (a,) = session.agents(self.load())
        self.assertEqual((a["role"], a["id"], a["ticket"]), ("worker", "aaa", 5))
        self.assertEqual(a["model"], "claude-opus-5-5")  # the transcript's model first
        self.assertEqual(a["worker_model"], "haiku")  # the metadata's first (the dispatch alias)
        self.assertEqual(a["dispatch_model"], "sonnet")
        self.assertEqual(a["prompt"], "Touch map: a.py:1.\nBranch: 5-x.")
        self.assertEqual(a["description"], "Build #5")
        self.assertIsNotNone(a["transcript"])

    def test_only_plugin_agents_are_listed_and_an_undelivered_one_has_no_transcript(self):
        write_session(self.main, [dispatch("t1", "Build #1"), dispatch("t2", "Explore", subagent="general-purpose"),
                                  dispatch("t3", "Build #2")],
                      [{"id": "x", "tool_use": "t1", "records": []}])
        agents = session.agents(self.load())
        self.assertEqual([a["ticket"] for a in agents], [1, 2])
        self.assertEqual(agents[0]["id"], "x")
        self.assertIsNone(agents[1]["id"])
        self.assertIsNone(agents[1]["transcript"])

    def test_subagents_need_a_meta_naming_their_type_and_stray_lines_are_skipped(self):
        write_session(self.main, [], [{"id": "a1", "tool_use": "u", "agent_type": "orchestrate:scout", "records": [
            "[]", bash("b1", "git log", cwd="/repo"), "3"]}])
        (sub,) = [s for s in session.subagents(self.load())]
        role, transcript = sub
        self.assertEqual(role, "orchestrate:scout")
        self.assertEqual(list(transcript.bash_calls()), [("git log", "/repo")])

    def test_bash_calls_read_only_bash_inputs(self):
        write_jsonl(self.main, [bash("b1", "ls -la", cwd="/x"), bash("b2", "   "), {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "git push"}]}}])
        self.assertEqual(list(self.load().bash_calls()), [("ls -la", "/x")])

    def test_completions_name_each_agents_worktree_and_the_last_notice_wins(self):
        write_jsonl(self.main, [completion("aaa", "/w/first"), completion("aaa", "/w/last"),
                                completion("bbb", "/w/b")])
        self.assertEqual(self.load().completions(), {"aaa": "/w/last", "bbb": "/w/b"})

    def test_brief_fields_read_the_label_on_its_own_line(self):
        brief = "Build #3.\n- **Touch map:** scripts/x.py:4, scripts/y.py (reading). A prediction, not a fence.\nBranch: 3-slug. Go."
        self.assertEqual(session.touch_map(brief), ["scripts/x.py:4", "scripts/y.py (reading)"])
        self.assertEqual(session.branch_of(brief), "3-slug")
        self.assertEqual(session.touch_map("Touch map is mentioned mid-sentence: a.py"), [])
        self.assertEqual(session.touch_map(None), [])
        self.assertIsNone(session.branch_of("no brief here"))


if __name__ == "__main__":
    unittest.main()
