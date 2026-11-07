import json
import unittest

from harness import ScriptEnv, git
from transcripts import completion, dispatch, main_path, reply, write_session


class RebuildState(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        self.main = main_path(self.env.repo.parent / "projects")
        self.main_records = []
        self.agents = []

    def agent(self, agent_id, tool_id, records, meta=None):
        self.agents.append({"id": agent_id, "tool_use": tool_id, "records": records, "meta": meta or {}})

    def rebuild(self):
        write_session(self.main, self.main_records, self.agents)
        res = self.env.run("rebuild-state", self.main)
        return res, json.loads((self.env.repo / ".claude" / "orchestrate-state.json").read_text())

    def harness_worktree(self, agent_id):
        """A directory standing in for the harness's worktree of an agent, outside the repo."""
        path = self.env.repo.parent / "harness" / f"agent-{agent_id}"
        path.mkdir(parents=True)
        return path

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
        sub = self.main.with_suffix("") / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "agent-broken.meta.json").write_text("{not json")
        _, state = self.rebuild()
        self.assertEqual(list(state["tickets"]), ["5"])

    def test_description_only_needs_the_ticket_number(self):
        self.main_records += [dispatch("t1", "Build ticket #35"), dispatch("t2", "Build #7 please")]
        self.agent("a1", "t1", [reply("2026-11-04T09:01:00Z", "claude-sonnet-5-5")])
        self.agent("a2", "t2", [reply("2026-11-04T09:02:00Z", "claude-sonnet-5-5")])
        _, state = self.rebuild()
        self.assertEqual(list(state["tickets"]), ["7", "35"])

    def test_first_number_in_the_description_is_the_ticket(self):
        # A description may name other tickets too; the first #<n> is the one it was dispatched for.
        self.main_records += [dispatch("t1", "Build #35 after #31"), dispatch("t2", "Unblock #31 for #35")]
        self.agent("a1", "t1", [reply("2026-11-04T09:01:00Z", "claude-sonnet-5-5")])
        self.agent("a2", "t2", [reply("2026-11-04T09:02:00Z", "claude-sonnet-5-5")])
        _, state = self.rebuild()
        self.assertEqual(list(state["tickets"]), ["31", "35"])
        self.assertEqual(state["tickets"]["35"]["agent_id"], "a1")
        self.assertEqual(state["tickets"]["31"]["agent_id"], "a2")

    def test_worktree_is_null_once_removed(self):
        self.main_records += [dispatch("t1", "Build #1"), dispatch("t2", "Build #2")]
        self.agent("gone", "t1", [reply("2026-11-04T09:01:00Z")])
        self.agent("here", "t2", [reply("2026-11-04T09:01:00Z")])
        (self.env.repo / ".claude" / "worktrees" / "agent-here").mkdir(parents=True)
        res, state = self.rebuild()
        self.assertIsNone(state["tickets"]["1"]["worktree"])
        self.assertTrue(state["tickets"]["2"]["worktree"].endswith("agent-here"))

    def test_worktree_is_the_path_the_harness_reported_in_the_completion(self):
        path = self.harness_worktree("c1")
        self.main_records += [dispatch("t1", "Build #1"), completion("c1", str(path))]
        self.agent("c1", "t1", [reply("2026-11-04T09:01:00Z")])
        # The harness's own .claude/worktrees path also exists: the completion's path still wins.
        (self.env.repo / ".claude" / "worktrees" / "agent-c1").mkdir(parents=True)
        _, state = self.rebuild()
        self.assertEqual(state["tickets"]["1"]["worktree"], str(path))

    def test_a_completed_path_that_is_gone_falls_back_to_the_harness_path(self):
        self.main_records += [dispatch("t1", "Build #1"),
                              completion("c1", str(self.env.repo.parent / "removed" / "agent-c1"))]
        self.agent("c1", "t1", [reply("2026-11-04T09:01:00Z")])
        (self.env.repo / ".claude" / "worktrees" / "agent-c1").mkdir(parents=True)
        _, state = self.rebuild()
        self.assertEqual(state["tickets"]["1"]["worktree"], str(self.env.repo.resolve() / ".claude" / "worktrees" / "agent-c1"))

    def test_worktree_falls_back_to_the_worktree_of_the_briefs_branch(self):
        # The worker's own worktree is gone, but its ticket branch is checked out in another worktree.
        branch_wt = self.env.repo.parent / "resumed"
        git(self.env.repo, "worktree", "add", "-q", "-b", "5-slug", str(branch_wt))
        self.main_records += [dispatch("t1", "Build #5", prompt="Branch: 5-slug. Build it.")]
        self.agent("gone", "t1", [reply("2026-11-04T09:01:00Z")])
        _, state = self.rebuild()
        self.assertEqual(state["tickets"]["5"]["worktree"], str(branch_wt.resolve()))

    def test_worktree_is_null_when_no_source_has_it(self):
        self.main_records += [dispatch("t1", "Build #5", prompt="Branch: 5-slug. Build it.")]
        self.agent("gone", "t1", [reply("2026-11-04T09:01:00Z")])
        _, state = self.rebuild()
        self.assertIsNone(state["tickets"]["5"]["worktree"])

    def test_touch_map_is_read_from_the_brief_as_written(self):
        brief = ("Build #5.\nTouch map: scripts/a.py:10, scripts/b.py:20 (the join, twice). A prediction, not a fence.\n"
                 "Branch: 5-slug. If it exists on the remote, continue from it.")
        self.main_records += [dispatch("t1", "Build #5", prompt=brief), dispatch("t2", "Build #6", prompt="no brief")]
        self.agent("a1", "t1", [reply("2026-11-04T09:01:00Z")])
        self.agent("a2", "t2", [reply("2026-11-04T09:01:00Z")])
        _, state = self.rebuild()
        self.assertEqual(state["tickets"]["5"]["touch_map"], ["scripts/a.py:10", "scripts/b.py:20 (the join, twice)"])
        self.assertEqual(state["tickets"]["6"]["touch_map"], [])

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
