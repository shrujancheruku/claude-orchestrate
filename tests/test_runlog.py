"""Unit tests for scripts/runlog.py: where both logs land, what they read back, and the vocabulary."""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common  # noqa: E402
import profile as prof  # noqa: E402
import replay  # noqa: E402
import repo  # noqa: E402
import runlog  # noqa: E402


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
        runlog.append_pass(wt, "s", {"slots": 2})
        runlog.append_event(wt, "ci", "pass", ref=2, session="s")
        self.assertFalse((wt / runlog.PROFILES).exists())
        for where in (self.repo, wt):
            self.assertEqual([p["slots"] for p in runlog.read_passes(where, "s")], [2])
            self.assertEqual([e["ref"] for e in runlog.read_events(where, "s", *self.span)], [2])

    def test_a_removed_worktree_still_resolves_to_the_main_checkout(self):
        # The harness removes a worktree when its agent ends, but a transcript still names its path.
        wt = self.worktree()
        runlog.append_pass(wt, "s", {"slots": 2})
        _vcs(self.repo, "worktree", "remove", str(wt))
        self.assertFalse(wt.exists())
        self.assertEqual(repo.main_checkout(wt), self.repo)
        self.assertEqual([p["slots"] for p in runlog.read_passes(wt, "s")], [2])

    def test_profile_built_for_a_worktree_session_finds_both_logs(self):
        wt = self.worktree()
        runlog.append_pass(wt, "sess", {"slots": 2})
        runlog.append_event(wt, "gate", "pass", ref=1, session="sess")
        transcript = self.repo.parent / "sess.jsonl"
        ts = datetime.now(timezone.utc).isoformat()
        transcript.write_text(json.dumps({"type": "user", "timestamp": ts, "cwd": str(wt), "message": {"content": "run"}}) + "\n")
        p = prof.profile(transcript, None, None, 50.0, datetime.now(timezone.utc))
        self.assertTrue(p["sources"]["pass_log"])
        self.assertEqual(p["slots"], 2)
        self.assertEqual(p["metrics"]["events"]["gate"]["pass"], 1)

    def test_outside_a_checkout_falls_back_to_the_given_directory(self):
        with tempfile.TemporaryDirectory() as d:
            runlog.append_event(d, "gate", "pass")
            self.assertTrue(runlog.log_path(d, runlog.EVENTS_LOG).exists())

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

    def test_sessionless_event_counts_inside_the_span_only(self):
        runlog.append_event(self.repo, "gate", "pass", session="")
        self.assertEqual(len(runlog.read_events(self.repo, "s", *self.span)), 1)
        long_ago = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.assertIsNone(runlog.read_events(self.repo, "s", long_ago, long_ago + timedelta(days=1)))

    def test_passes_are_per_session(self):
        runlog.append_pass(self.repo, "a", {"slots": 1})
        runlog.append_pass(self.repo, "b", {"slots": 3})
        (p,) = runlog.read_passes(self.repo, "b")
        self.assertEqual((p["slots"], p["session"]), (3, "b"))
        self.assertIn("ts", p)


if __name__ == "__main__":
    unittest.main()
