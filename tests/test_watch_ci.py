import json
import unittest

from harness import ScriptEnv

# A gh that serves each PR a sequence of replies: the Nth `pr view <pr>` call gets
# step min(N, last). Steps live in <state>.seq as {"<pr>": [<json view>, ...]}.
SEQ_GH = '''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_GH_STATE"]
args = sys.argv[1:]
with open(state + ".calls", "a") as f:
    f.write(json.dumps(args) + "\\n")
seq = json.load(open(state + ".seq"))
n = sum(1 for l in open(state + ".calls") if json.loads(l)[:3] == args[:3]) - 1
steps = seq[args[2]]
print(json.dumps(steps[min(n, len(steps) - 1)]))
'''


def view(result, merge="CLEAN", head="abcdef1234567", state="OPEN"):
    if result is None:
        checks = [{"status": "IN_PROGRESS", "conclusion": ""}]
    else:
        checks = [{"status": "COMPLETED", "conclusion": result}]
    return {"statusCheckRollup": checks, "mergeStateStatus": merge, "headRefOid": head, "state": state}


class WatchCi(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)
        (self.env.bin / "gh").write_text(SEQ_GH)

    def seq(self, steps):
        self.env.gh_state.with_name("gh-state.json.seq").write_text(json.dumps(steps))

    def watch(self, *prs):
        return self.env.run("watch-ci", "--interval", "0", *prs)

    def test_settle_order_and_events(self):
        self.seq({
            "1": [view(None), view(None), view(None), view("SUCCESS", head="1111111aaa")],
            "2": [view(None), view("FAILURE", merge="BLOCKED", head="2222222bbb")],
            "3": [view("SUCCESS", head="3333333ccc")],
        })
        out = self.watch("1", "2", "3").stdout.splitlines()
        self.assertEqual(out, [
            "PR #3 pass CLEAN 3333333",
            "PR #2 fail BLOCKED 2222222",
            "PR #1 pass CLEAN 1111111",
        ])
        events = self.env.events()
        self.assertEqual([(e["kind"], e["ref"], e["outcome"]) for e in events],
                         [("ci", 3, "pass"), ("ci", 2, "fail"), ("ci", 1, "pass")])
        self.assertEqual(events[1]["merge_state"], "BLOCKED")
        self.assertEqual(events[1]["head"], "2222222")

    def test_empty_rollup_is_pending_until_checks_appear(self):
        empty = {"statusCheckRollup": [], "mergeStateStatus": "CLEAN", "headRefOid": "abc1234", "state": "OPEN"}
        self.seq({"7": [empty, view("SUCCESS", head="abc1234")]})
        self.assertEqual(self.watch("7").stdout.splitlines(), ["PR #7 pass CLEAN abc1234"])

    def test_status_context_failure(self):
        v = {"statusCheckRollup": [{"state": "ERROR"}], "mergeStateStatus": "CLEAN", "headRefOid": "abc1234", "state": "OPEN"}
        self.seq({"8": [v]})
        self.assertEqual(self.watch("8").stdout.splitlines(), ["PR #8 fail CLEAN abc1234"])


if __name__ == "__main__":
    unittest.main()
