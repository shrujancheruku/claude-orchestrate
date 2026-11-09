import json
import threading
from pathlib import Path
import unittest

from harness import ScriptEnv, git
from transcripts import completion, dispatch, main_path, reply, write_session

import claims  # noqa: E402 - harness puts scripts/ on sys.path


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
        raw = self.raw()
        # The session's own entry, flattened: the tests below read {"session", "tickets"}.
        return res, {"session": raw["session"], "tickets": raw["sessions"][raw["session"]]["tickets"]}

    def raw(self):
        return json.loads((self.env.repo / ".claude" / "orchestrate-state.json").read_text())

    def second_session(self, sid, ticket):
        """Write another session's transcript, which dispatched one worker for `ticket`, and return its path."""
        path = main_path(self.env.repo.parent / "projects", sid)
        tool = f"t-{sid}"
        write_session(path, [dispatch(tool, f"Build #{ticket}", sid=sid)],
                      [{"id": f"ag-{sid}", "tool_use": tool, "records": [reply("2026-11-04T09:01:00Z", "claude-sonnet-5-5")], "meta": {}}])
        return path

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

    def test_touch_map_is_stored_in_canonical_form(self):
        brief = ("Build #5.\nTouch map: `scripts/a.py`:10; scripts/b.py:20 (the join, twice)\nA prediction, not a fence.\n"
                 "Branch: 5-slug. If it exists on the remote, continue from it.")
        self.main_records += [dispatch("t1", "Build #5", prompt=brief), dispatch("t2", "Build #6", prompt="no brief")]
        self.agent("a1", "t1", [reply("2026-11-04T09:01:00Z")])
        self.agent("a2", "t2", [reply("2026-11-04T09:01:00Z")])
        _, state = self.rebuild()
        self.assertEqual(state["tickets"]["5"]["touch_map"], "scripts/a.py:10, scripts/b.py:20 (the join, twice)")
        self.assertEqual(state["tickets"]["6"]["touch_map"], "none")

    def test_an_unparseable_touch_map_is_reported_and_kept_as_unparsed(self):
        brief = "Build #5.\nTouch map: a.py:10, 20\nBranch: 5-slug."
        self.main_records += [dispatch("t1", "Build #5", prompt=brief)]
        self.agent("a1", "t1", [reply("2026-11-04T09:01:00Z")])
        res, state = self.rebuild()
        self.assertEqual(state["tickets"]["5"]["touch_map"], "unparsed:a.py:10, 20")
        self.assertIn("#5 touch map not read, held as every path: bad touch-map entry: '20'", res.stdout)

    def test_latest_dispatch_for_a_ticket_wins(self):
        self.main_records += [dispatch("t1", "Build #5"), dispatch("t2", "Build #5")]
        self.agent("old", "t1", [reply("2026-11-04T09:01:00Z", "claude-haiku-5-5")])
        self.agent("new", "t2", [reply("2026-11-04T11:00:00Z", "claude-sonnet-5-5")])
        _, state = self.rebuild()
        self.assertEqual(state["tickets"]["5"]["agent_id"], "new")

    def test_empty_session_writes_empty_state(self):
        _, state = self.rebuild()
        self.assertEqual(state["tickets"], {})

    def tickets_by_session(self):
        # The harness seeds a "test-session" entry (old shape, migrated on the first rebuild); it is kept like any other.
        return {k: list(v["tickets"]) for k, v in self.raw()["sessions"].items() if k != "test-session"}

    def test_two_sessions_keep_both_entries_and_the_same_session_replaces_only_its_own(self):
        self.main_records += [dispatch("t1", "Build #5")]
        self.agent("a1", "t1", [reply("2026-11-04T09:01:00Z", "claude-sonnet-5-5")])
        self.rebuild()
        self.env.run("rebuild-state", self.second_session("sess-2", 9))
        self.assertEqual(self.tickets_by_session(), {"sess-1": ["5"], "sess-2": ["9"]})
        self.assertEqual(self.raw()["session"], "sess-2")  # the one that rebuilt last is the current session
        self.main_records = [dispatch("t1", "Build #6")]
        self.rebuild()
        self.assertEqual(self.tickets_by_session(), {"sess-1": ["6"], "sess-2": ["9"]})

    def test_a_rebuild_prunes_sessions_that_are_not_agent_rows_and_keeps_live_and_stopped_ones(self):
        self.main_records += [dispatch("t1", "Build #5")]
        self.agent("a1", "t1", [reply("2026-11-04T09:01:00Z", "claude-sonnet-5-5")])
        self.rebuild()
        self.env.run("rebuild-state", self.second_session("sess-live", 9))
        self.env.run("rebuild-state", self.second_session("sess-stopped", 10))
        self.env.run("rebuild-state", self.second_session("sess-unknown", 11))
        self.assertEqual(set(self.tickets_by_session()), {"sess-1", "sess-live", "sess-stopped", "sess-unknown"})
        # The stopped row counts as a row (`--all`); sess-1 is the one rebuilding, so it stays even unlisted.
        self.env.set_agents("sess-live", "sess-stopped")
        self.rebuild()
        self.assertEqual(set(self.tickets_by_session()), {"sess-1", "sess-live", "sess-stopped"})

    def test_nothing_is_pruned_when_the_agent_rows_cannot_be_read(self):
        self.main_records += [dispatch("t1", "Build #5")]
        self.agent("a1", "t1", [reply("2026-11-04T09:01:00Z", "claude-sonnet-5-5")])
        self.rebuild()
        self.env.run("rebuild-state", self.second_session("sess-2", 9))
        self.rebuild()  # the fake `claude` fails: no rows were set
        self.assertEqual(set(self.tickets_by_session()), {"sess-1", "sess-2"})

    def test_with_session_each_of_two_alternating_sessions_reads_its_own_workers(self):
        self.main_records += [dispatch("t1", "Build #5")]
        self.agent("a1", "t1", [reply("2026-11-04T09:01:00Z", "claude-sonnet-5-5")])
        self.rebuild()
        self.env.run("rebuild-state", "--session", self.second_session("sess-2", 9))
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        self.assertEqual(self.raw()["session"], "sess-2")  # sess-2 rebuilt last
        self.assertEqual(list(claims.read_state(state, "sess-1")), ["5"])
        self.assertEqual(list(claims.read_other_state(state, "sess-1")), ["9"])
        self.assertEqual(list(claims.read_state(state, "sess-2")), ["9"])
        self.assertEqual(list(claims.read_other_state(state, "sess-2")), ["5"])
        self.assertEqual(list(claims.read_state(state)), ["9"])  # without the flag: the last rebuilder, as before

    def test_a_rebuild_leaves_the_working_tree_clean(self):
        self.main_records += [dispatch("t1", "Build #5")]
        self.agent("a1", "t1", [reply("2026-11-04T09:01:00Z", "claude-sonnet-5-5")])
        # The test repo is a fresh one: give it this repo's own .gitignore, which is what lists the state and lock files.
        (self.env.repo / ".gitignore").write_text((Path(__file__).resolve().parent.parent / ".gitignore").read_text())
        git(self.env.repo, "add", ".gitignore")
        git(self.env.repo, "commit", "-m", "chore: gitignore")
        self.rebuild()
        self.assertTrue((self.env.repo / ".claude" / "orchestrate-state.json.lock").exists())
        self.assertEqual(git(self.env.repo, "status", "--porcelain"), "")

    def test_an_old_shape_file_is_one_session_and_is_rewritten_in_the_new_shape(self):
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        state.parent.mkdir(exist_ok=True)
        state.write_text(json.dumps({"session": "old", "tickets": {"3": {"agent_id": "x"}}}))
        self.rebuild()
        raw = self.raw()
        self.assertEqual(raw["sessions"]["old"], {"tickets": {"3": {"agent_id": "x"}}})
        self.assertIn("sess-1", raw["sessions"])
        self.assertNotIn("tickets", raw)

    def test_an_entry_without_a_tickets_object_is_dropped_on_rebuild(self):
        state = self.env.repo / ".claude" / "orchestrate-state.json"
        state.parent.mkdir(exist_ok=True)
        state.write_text(json.dumps({"session": "x", "sessions": {
            "x": {"tickets": {"3": {}}}, "bad": {"tickets": None}, "worse": {}, "str": "no"}}))
        self.rebuild()
        self.assertEqual(self.tickets_by_session(), {"x": ["3"], "sess-1": []})

    def test_concurrent_rebuilds_for_two_sessions_lose_no_entry(self):
        # Many rounds, so an unlocked read-modify-write would lose an entry.
        for rnd in range(5):
            paths = [self.second_session(f"s{rnd}-{i}", 10 + i) for i in range(6)]
            codes = []
            threads = [threading.Thread(target=lambda p=p: codes.append(self.env.run("rebuild-state", p, check=False).returncode))
                       for p in paths]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(codes, [0] * len(paths))
            self.assertTrue({f"s{rnd}-{i}" for i in range(6)} <= set(self.raw()["sessions"]))
        self.assertEqual(len(self.raw()["sessions"]), 31)  # 30 and the seeded "test-session", kept


if __name__ == "__main__":
    unittest.main()
