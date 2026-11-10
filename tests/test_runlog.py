"""Unit tests for scripts/runlog.py: where both logs land, what they read back, and the vocabulary."""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common  # noqa: E402
import profile as prof  # noqa: E402
import replay  # noqa: E402
import repo  # noqa: E402
import runlog  # noqa: E402


PASS = {"scope": "full", "slots": 2, "free": 0, "idle": {}}  # the smallest full pass record the schema accepts


def _vcs(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class RunLogTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve() / "repo"
        self.repo.mkdir()
        _vcs(self.repo, "init", "-q", "-b", "main")
        for k, v in (("user.name", "T"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            _vcs(self.repo, "config", k, v)
        _vcs(self.repo, "commit", "-q", "--allow-empty", "-m", "chore: init")
        self.wt = self.repo / ".claude" / "worktrees" / "w1"
        now = datetime.now(timezone.utc)
        self.span = (now - timedelta(hours=1), now + timedelta(hours=1))

    def worktree(self):
        _vcs(self.repo, "worktree", "add", "-q", "-b", "w1", str(self.wt))
        return self.wt

    def test_subdirectory_writes_to_the_checkout_log(self):
        sub = self.repo / "a" / "b"
        sub.mkdir(parents=True)
        runlog.append_event(sub, "gate", "pass", ref=1, session="s")
        self.assertEqual([e["ref"] for e in runlog.read_events(self.repo, "s", *self.span)], [1])
        self.assertFalse((sub / ".claude").exists())

    def test_worktree_pass_and_event_are_found_from_either_checkout(self):
        wt = self.worktree()
        runlog.append_pass(wt, "s", PASS)
        runlog.append_event(wt, "ci", "pass", ref=2, session="s")
        self.assertFalse((wt / runlog.PROFILES).exists())
        for where in (self.repo, wt):
            self.assertEqual([p["slots"] for p in runlog.read_passes(where, "s")], [2])
            self.assertEqual([e["ref"] for e in runlog.read_events(where, "s", *self.span)], [2])

    def test_a_wake_pass_keeps_its_scope_and_an_unchecked_idle_reason(self):
        runlog.append_pass(self.repo, "s", {"scope": "wake", "slots": 2, "free": 1, "idle": {"unchecked": 1}})
        (p,) = runlog.read_passes(self.repo, "s")
        self.assertEqual((p["scope"], p["idle"]), ("wake", {"unchecked": 1}))

    def test_append_pass_refuses_an_off_schema_record_and_writes_nothing(self):
        bad = [
            {"event": "pass-end", "slots": 2},  # no scope: invented under load
            {"pass": "wake", "slots": 2, "free": 0},
            {**PASS, "scope": "weekly"},
            {**PASS, "extra": 1},
            {"scope": "full", "slots": 2, "idle": {}},  # no free
            {**PASS, "free": 3, "idle": {"empty": 3}},  # more free than slots
            {**PASS, "free": 1, "idle": {"empty": 2}},  # idle does not sum to free
            {**PASS, "free": 1, "idle": {"bored": 1}},  # unknown reason
            {"scope": "full", "slots": 2, "free": 1},  # only a wake may omit idle
            {**PASS, "greedy": "yes"},
            {**PASS, "dispatched": ["7"]},
            {**PASS, "claimed": {"1": 2}},
            {"scope": "nudge", "nudged": []},
            {"scope": "nudge", "nudged": [3], "slots": 2},
            [1],
        ]
        for entry in bad:
            with self.assertRaises(runlog.BadPass, msg=entry):
                runlog.append_pass(self.repo, "s", entry)
        self.assertFalse(runlog.log_path(self.repo, runlog.PASS_LOG).exists())

    def test_append_pass_accepts_every_scope_a_wake_without_idle_and_a_nudge(self):
        for scope in ("full", "status", "#7", "parent #9"):
            runlog.append_pass(self.repo, "s", {**PASS, "scope": scope})
        runlog.append_pass(self.repo, "s", {"scope": "wake", "slots": 2, "free": 1})
        runlog.append_pass(self.repo, "s", {"scope": runlog.NUDGE_SCOPE, "nudged": [4]})
        self.assertEqual(len(runlog.read_passes(self.repo, "s")), 5)  # the nudge is not a pass

    def test_a_removed_worktree_still_resolves_to_the_main_checkout(self):
        # The harness removes a worktree when its agent ends, but a transcript still names its path.
        wt = self.worktree()
        runlog.append_pass(wt, "s", PASS)
        _vcs(self.repo, "worktree", "remove", str(wt))
        self.assertFalse(wt.exists())
        self.assertEqual(repo.main_checkout(wt), self.repo)
        self.assertEqual([p["slots"] for p in runlog.read_passes(wt, "s")], [2])

    def test_profile_built_for_a_worktree_session_finds_both_logs(self):
        wt = self.worktree()
        runlog.append_pass(wt, "sess", PASS)
        runlog.append_event(wt, "gate", "pass", ref=1, session="sess")
        transcript = self.repo.parent / "sess.jsonl"
        ts = datetime.now(timezone.utc).isoformat()
        transcript.write_text(json.dumps({"type": "user", "timestamp": ts, "cwd": str(wt), "message": {"content": "run"}}) + "\n")
        p = prof.profile(transcript, None, None, 50.0, datetime.now(timezone.utc))
        self.assertTrue(p["sources"]["pass_log"])
        self.assertEqual(p["slots"], 2)
        self.assertEqual(p["metrics"]["events"]["gate"]["pass"], 1)

    def stamp(self, where, session):
        """Write the state file rebuild-state would, in `where`'s main checkout."""
        (Path(where) / repo.STATE).parent.mkdir(parents=True, exist_ok=True)
        (Path(where) / repo.STATE).write_text(json.dumps({"session": session, "tickets": {}}))

    def test_outside_a_checkout_the_state_file_is_read_from_the_given_directory(self):
        with tempfile.TemporaryDirectory() as d:
            self.stamp(d, "sess")
            self.assertEqual(runlog.append_event(d, "gate", "pass")["session"], "sess")
            self.assertTrue(runlog.log_path(d, runlog.EVENTS_LOG).exists())

    def test_session_comes_from_the_state_file_from_a_linked_worktree(self):
        # The session is not in the environment: the state file, in the main checkout, is the only source.
        self.stamp(self.repo, "sess")
        wt = self.worktree()
        self.assertEqual(runlog.append_event(wt, "gate", "pass", ref=1)["session"], "sess")
        self.assertEqual([e["ref"] for e in runlog.read_events(self.repo, "sess", *self.span)], [1])

    def test_the_session_is_the_one_that_rebuilt_last_in_a_multi_session_file(self):
        (self.repo / repo.STATE).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / repo.STATE).write_text(json.dumps({"session": "b", "sessions": {"a": {"tickets": {}}, "b": {"tickets": {}}}}))
        self.assertEqual(runlog.current_session(self.repo), "b")

    def test_the_session_env_var_set_by_a_session_flag_overrides_the_last_rebuilder(self):
        self.stamp(self.repo, "last-rebuilder")
        with mock.patch.dict("os.environ", {runlog.SESSION_ENV: "mine"}):
            self.assertEqual(runlog.append_event(self.repo, "gate", "pass")["session"], "mine")

    def test_an_explicit_session_overrides_the_state_file(self):
        self.stamp(self.repo, "stamped")
        runlog.append_event(self.repo, "gate", "pass", session="given")
        self.assertEqual(runlog.read_events(self.repo, "given", *self.span)[0]["session"], "given")

    def test_no_state_file_and_no_session_raises_nosession_and_writes_nothing(self):
        with self.assertRaises(runlog.NoSession) as cm:
            runlog.append_event(self.repo, "gate", "pass", ref=1)
        self.assertEqual(str(cm.exception), "runlog: no session, gate event not written")
        self.assertFalse(runlog.log_path(self.repo, runlog.EVENTS_LOG).exists())

    def test_a_state_file_without_a_session_raises_nosession(self):
        for text in ("not json", json.dumps({"session": ""}), json.dumps({"session": None}), json.dumps([])):
            (self.repo / repo.STATE).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / repo.STATE).write_text(text)
            with self.assertRaises(runlog.NoSession, msg=text):
                runlog.append_event(self.repo, "gate", "pass")
        self.assertFalse(runlog.log_path(self.repo, runlog.EVENTS_LOG).exists())

    def test_append_pass_with_no_session_raises_nosession_and_writes_nothing(self):
        with self.assertRaises(runlog.NoSession):
            runlog.append_pass(self.repo, "", {"slots": 1})
        self.assertFalse(runlog.log_path(self.repo, runlog.PASS_LOG).exists())

    def test_unknown_outcome_or_kind_raises_and_writes_nothing(self):
        with self.assertRaises(ValueError):
            runlog.append_event(self.repo, "gate", "merged")
        with self.assertRaises(ValueError):
            runlog.append_event(self.repo, "nope", "pass")
        self.assertFalse(runlog.log_path(self.repo, runlog.EVENTS_LOG).exists())

    def test_every_kind_accepts_its_own_outcomes(self):
        for kind, outcomes in runlog.OUTCOMES.items():
            for outcome in outcomes:
                self.assertEqual(runlog.append_event(self.repo, kind, outcome, session="s")["outcome"], outcome)

    def test_replay_vocabulary_matches_replay_py(self):
        self.assertEqual(set(runlog.OUTCOMES[runlog.REPLAY]),
                         {replay.LINE_REPLAY, replay.WORD_MERGE, replay.REGENERATED, replay.HANDED_BACK})

    def test_a_legacy_sessionless_event_counts_inside_the_span_only(self):
        # Logs written before #341 hold events with session null: the time window still attributes them.
        legacy = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "session": None,
                  "kind": "gate", "ref": 1, "outcome": "pass"}
        path = runlog.log_path(self.repo, runlog.EVENTS_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(legacy) + "\n")
        self.assertEqual(len(runlog.read_events(self.repo, "s", *self.span)), 1)
        long_ago = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.assertIsNone(runlog.read_events(self.repo, "s", long_ago, long_ago + timedelta(days=1)))

    def test_passes_are_per_session(self):
        runlog.append_pass(self.repo, "a", {**PASS, "slots": 1})
        runlog.append_pass(self.repo, "b", {**PASS, "slots": 3})
        (p,) = runlog.read_passes(self.repo, "b")
        self.assertEqual((p["slots"], p["session"]), (3, "b"))
        self.assertIn("ts", p)


if __name__ == "__main__":
    unittest.main()
