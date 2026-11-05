"""Unit tests for scripts/runlog.py: where both logs land, what they read back, and the vocabulary."""

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
        self.assertFalse((wt / ".claude" / "orchestrate-profiles").exists())
        for where in (self.repo, wt):
            self.assertEqual([p["slots"] for p in runlog.read_passes(where, "s")], [2])
            self.assertEqual([e["ref"] for e in runlog.read_events(where, "s", *self.span)], [2])

    def test_profile_reads_both_logs_with_a_worktree_cwd(self):
        wt = self.worktree()
        runlog.append_pass(wt, "s", {"slots": 2})
        runlog.append_event(wt, "gate", "pass", ref=1, session="s")
        # profile() resolves the transcript's cwd (here the worktree) through runlog.
        self.assertEqual(len(prof.runlog.read_passes(wt, "s")), 1)
        events = prof.runlog.read_events(wt, "s", *self.span)
        self.assertEqual(prof.event_metrics(events, {"rounds_total": 0})["gate"]["pass"], 1)

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

    def test_common_append_event_is_a_thin_call_into_the_log(self):
        common.append_event(self.repo, "merge", "merged", ref="#1", session="s")
        (e,) = runlog.read_events(self.repo, "s", *self.span)
        self.assertEqual((e["kind"], e["outcome"]), ("merge", "merged"))

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
