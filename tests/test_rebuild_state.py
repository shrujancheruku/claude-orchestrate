import json
import unittest

from harness import ScriptEnv


def dispatch(tool_id, description, subagent="orchestrate:worker", model=None):
    inp = {"description": description, "subagent_type": subagent, "prompt": "brief"}
    if model:
        inp["model"] = model
    return {"type": "assistant", "timestamp": "2026-11-04T09:00:00.000Z", "sessionId": "sess-1",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_id, "name": "Agent", "input": inp}]}}


def reply(ts, model=None):
    msg = {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}
    if model:
        msg["model"] = model
    return {"type": "assistant", "timestamp": ts, "message": msg}


class RebuildState(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.main = self.env.repo.parent / "projects" / "sess-1.jsonl"
        self.sub = self.main.with_suffix("") / "subagents"
        self.sub.mkdir(parents=True)
        self.main_records = []

    def agent(self, agent_id, tool_id, records, meta=None):
        (self.sub / f"agent-{agent_id}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        (self.sub / f"agent-{agent_id}.meta.json").write_text(json.dumps(
            dict({"agentType": "orchestrate:worker", "toolUseId": tool_id}, **(meta or {}))))

    def rebuild(self):
        self.main.write_text("".join(json.dumps(r) + "\n" for r in self.main_records))
        res = self.env.run("rebuild-state", self.main)
        return res, json.loads((self.env.repo / ".claude" / "orchestrate-state.json").read_text())

    def test_each_ticket_has_agent_worktree_model_and_last_report(self):
        self.main_records += [dispatch("t1", "Build #183"), dispatch("t2", "Build #12", model="sonnet")]
        (self.env.repo / ".claude" / "worktrees" / "agent-aaa111").mkdir(parents=True)
        self.agent("aaa111", "t1", [reply("2026-11-04T09:01:00Z", "claude-sonnet-5-5"),
                                    reply("2026-11-04T09:30:00.500Z", "claude-sonnet-5-5")])
        # No assistant model in the transcript: falls back to the dispatch's model.
        self.agent("bbb222", "t2", [{"type": "user", "timestamp": "2026-11-04T10:00:00Z"}])
        res, state = self.rebuild()
        self.assertEqual(state["session"], "sess-1")
        self.assertEqual(list(state["tickets"]), ["12", "183"])
        t = state["tickets"]["183"]
        self.assertEqual(t["agent_id"], "aaa111")
        self.assertEqual(t["worktree"], str(self.env.repo.resolve() / ".claude" / "worktrees" / "agent-aaa111"))
        self.assertEqual(t["model"], "claude-sonnet-5-5")
        self.assertEqual(t["last_report"], "2026-11-04T09:30:00.500000Z")
        self.assertEqual(state["tickets"]["12"]["model"], "sonnet")
        self.assertEqual(state["tickets"]["12"]["last_report"], "2026-11-04T10:00:00Z")
        self.assertIn("#183 aaa111", res.stdout)

    def test_other_dispatches_are_ignored(self):
        self.main_records += [
            dispatch("t1", "Build #5"),
            dispatch("t2", "Fix the thing"),
            dispatch("t4", "Review PR #8", subagent="orchestrate:reviewer"),
            dispatch("t5", "Build #9"),  # no metadata/transcript to join
            {"type": "assistant", "message": {"content": "not a list"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Agent", "input": None}]}},
        ]
        for i, tid in enumerate(("t1", "t2", "t4")):
            self.agent(f"ag{i}", tid, [reply("2026-11-04T09:01:00Z", "claude-haiku-5-5")])
        (self.sub / "agent-broken.meta.json").write_text("{not json")
        _, state = self.rebuild()
        self.assertEqual(list(state["tickets"]), ["5"])

    def test_description_only_needs_the_ticket_number(self):
        self.main_records += [dispatch("t1", "Build ticket #35"), dispatch("t2", "Build #7 please")]
        self.agent("a1", "t1", [reply("2026-11-04T09:01:00Z", "claude-sonnet-5-5")])
        self.agent("a2", "t2", [reply("2026-11-04T09:02:00Z", "claude-sonnet-5-5")])
        _, state = self.rebuild()
        self.assertEqual(list(state["tickets"]), ["7", "35"])

    def test_worktree_is_null_once_removed(self):
        self.main_records += [dispatch("t1", "Build #1"), dispatch("t2", "Build #2")]
        self.agent("gone", "t1", [reply("2026-11-04T09:01:00Z")])
        self.agent("here", "t2", [reply("2026-11-04T09:01:00Z")])
        (self.env.repo / ".claude" / "worktrees" / "agent-here").mkdir(parents=True)
        res, state = self.rebuild()
        self.assertIsNone(state["tickets"]["1"]["worktree"])
        self.assertTrue(state["tickets"]["2"]["worktree"].endswith("agent-here"))

    def test_latest_dispatch_for_a_ticket_wins(self):
        self.main_records += [dispatch("t1", "Build #5"), dispatch("t2", "Build #5")]
        self.agent("old", "t1", [reply("2026-11-04T09:01:00Z", "claude-haiku-5-5")])
        self.agent("new", "t2", [reply("2026-11-04T11:00:00Z", "claude-sonnet-5-5")])
        _, state = self.rebuild()
        self.assertEqual(state["tickets"]["5"]["agent_id"], "new")

    def test_empty_session_writes_empty_state(self):
        _, state = self.rebuild()
        self.assertEqual(state["tickets"], {})


if __name__ == "__main__":
    unittest.main()
