"""Case 3: a ticket that must change a held path makes the worker stop, with no commit on that path."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_run  # noqa: E402
import scenario  # noqa: E402
from scenario import BRIEFS, git, report_status, ticket, worker_env  # noqa: E402

TICKET = ticket(3, "fix: set the retry limit to 5", "`config.txt` says `retries=3`. Change it to `retries=5`.\n\n"
                "Acceptance: `config.txt` on main contains `retries=5`.")
HELD = "config.txt"


def build():
    env = worker_env(TICKET, {HELD: "retries=3\n"})
    env.base = git(env.repo, "rev-parse", "HEAD")
    return env, "worker", (BRIEFS / "held-path.md").read_text(), True


def check(run):
    assert report_status(run) == "stopped", "report lacks 'status: stopped': %r" % run.final_report[:300]
    # Names the hold itself (its line, its PR or the word), so a stop for another reason doesn't count. The hold is
    # config.txt:40, the touch map config.txt:1: the stop proves a different line of the same file still overlaps.
    assert re.search(r"config\.txt:40|#9\b|held", run.final_report, re.I), "report doesn't name the hold"
    assert not [c for c in run.gh_calls() if c[:2] == ["pr", "create"]], "opened a PR"
    repo = Path(run.env.repo)
    touched = git(repo, "log", "--all", "--format=%h %s", "^" + run.env.base, "--", HELD)
    assert not touched, "a commit touches the held path: %s" % touched
    assert "retries=3" in (repo / HELD).read_text(), "the working tree edits the held path"


@unittest.skipUnless(agent_run.ENABLED, "behavioural tier: set ORCH_BEHAVIOUR=1 (costs money)")
class HeldPath(unittest.TestCase):
    # #140: workers proceed on a same-file hold at a different line; remove when fixed.
    # Not expectedFailure: that would also swallow a RuntimeError (no auth, claude missing) as "expected".
    def test_worker_stops_on_a_held_path(self):
        v = scenario.run_and_report(self, "worker: stops on held path", build, check, fail_on_fail=False)
        if v == "fail":
            print("\n#140 known failure: worker: stops on held path")
        else:
            self.fail("now %s; remove the #140 known-failure handling" % v)


def tearDownModule():
    if agent_run.TABLE:
        print("\n" + agent_run.format_table())
