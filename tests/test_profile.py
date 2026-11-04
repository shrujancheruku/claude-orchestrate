"""Tests for scripts/profile.py and scripts/usage-tap.py on fixture transcripts. Run: python3 -m unittest discover tests"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import profile as prof  # noqa: E402

T0 = datetime(2026, 11, 3, 16, 0, tzinfo=timezone.utc)
SESSION = "sess-1"


def at(minutes):
    return prof.iso(T0 + timedelta(minutes=minutes))


def assistant(minutes, msg_id, model, blocks, usage=None):
    return {
        "type": "assistant",
        "timestamp": at(minutes),
        "message": {"id": msg_id, "model": model, "content": blocks,
                    "usage": usage or {"input_tokens": 10, "output_tokens": 100,
                                       "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50}},
    }


def tool_use(use_id, name, **inp):
    return {"type": "tool_use", "id": use_id, "name": name, "input": inp}


def tool_result(minutes, use_id, text):
    return {"type": "user", "timestamp": at(minutes),
            "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]}}


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


class Fixture:
    """A session that dispatches two workers and a reviewer, merges one PR and hands one ticket back."""

    def __init__(self, root: Path):
        self.repo = root / "repo"
        (self.repo / ".claude").mkdir(parents=True)
        (self.repo / ".claude" / "orchestration.md").write_text("## Slots\n\n- **Slots:** 2.\n")
        self.main = root / "projects" / "-repo" / f"{SESSION}.jsonl"
        sub = self.main.with_suffix("") / "subagents"
        opus = "claude-opus-5-5"
        write_jsonl(self.main, [
            {"type": "user", "timestamp": at(0), "cwd": str(self.repo), "message": {"content": "run"}},
            assistant(1, "m1", opus, [tool_use("u1", "Agent", subagent_type="orchestrate:worker", description="Build #10 thing")]),
            assistant(1, "m2", opus, [tool_use("u2", "Agent", subagent_type="orchestrate:worker", description="Build #11 other")]),
            assistant(1, "m3", opus, [tool_use("u0", "Agent", subagent_type="general-purpose", description="not ours")]),
            assistant(20, "m4", opus, [tool_use("u3", "Agent", subagent_type="orchestrate:reviewer", description="Review PR 50 (L)")]),
            assistant(30, "m5", opus, [tool_use("u4", "Bash", command="gh pr merge 50 --squash")]),
            assistant(31, "m6", opus, [tool_use("u5", "Bash", command="gh issue edit 11 --add-label needs-info --remove-assignee @me")]),
            # The harness echoes request bodies, tool calls included, without a timestamp.
            {"type": "api-request-blob", "message": {"content": [tool_use("u3", "Agent", subagent_type="orchestrate:reviewer", description="Review PR #50 round 1")]}},
            {"type": "system", "subtype": "compact_boundary", "timestamp": at(35)},
            {"type": "user", "timestamp": at(41)},
        ])
        sonnet = "claude-sonnet-5-5"
        # Worker on #10: builds 1-11, waits on review, fixes 25-27. Its first response is logged twice.
        w1 = [assistant(1, "w1a", sonnet, [{"type": "text", "text": "a"}]),
              assistant(1, "w1a", sonnet, [{"type": "text", "text": "b"}])]
        w1 += [assistant(m, f"w1-{m}", sonnet, [{"type": "text", "text": "x"}]) for m in range(2, 11)]
        w1 += [assistant(11, "w1pr", sonnet, [tool_use("b1", "Bash", command="gh pr create --fill")]),
               tool_result(11, "b1", "https://github.com/o/r/pull/50")]
        w1 += [assistant(m, f"w1-{m}", sonnet, [{"type": "text", "text": "y"}]) for m in (25, 26, 27)]
        write_jsonl(sub / "agent-aaa.jsonl", w1)
        (sub / "agent-aaa.meta.json").write_text(json.dumps({"agentType": "orchestrate:worker", "toolUseId": "u1"}))
        write_jsonl(sub / "agent-bbb.jsonl", [assistant(m, f"w2-{m}", sonnet, []) for m in range(1, 6)])
        (sub / "agent-bbb.meta.json").write_text(json.dumps({"agentType": "orchestrate:worker", "toolUseId": "u2"}))
        write_jsonl(sub / "agent-ccc.jsonl", [assistant(20, "r1", sonnet, [])])
        (sub / "agent-ccc.meta.json").write_text(json.dumps({"agentType": "orchestrate:reviewer", "toolUseId": "u3"}))
        self.usage = root / "usage.jsonl"
        reset = at(60)
        write_jsonl(self.usage, [
            {"ts": at(2), "five_hour": 20, "seven_day": 40, "five_hour_resets_at": reset},
            {"ts": at(40), "five_hour": 35, "seven_day": 44, "five_hour_resets_at": reset},
            {"ts": at(500), "five_hour": 90, "five_hour_resets_at": at(600)},  # outside the session
        ])

    def passes(self, rows):
        write_jsonl(self.repo / prof.PROFILES / prof.PASS_LOG, [{"session": SESSION, **r} for r in rows])

    def run(self, now_minutes=120, **kw):
        os.environ["ORCHESTRATE_USAGE_LOG"] = str(self.usage)
        return prof.profile(self.main, None, kw.get("slots"), kw.get("threshold", 50.0), T0 + timedelta(minutes=now_minutes))


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.tmp.name))

    def tearDown(self):
        os.environ.pop("ORCHESTRATE_USAGE_LOG", None)
        self.tmp.cleanup()

    def test_tokens_count_each_message_once(self):
        p = self.fx.run()
        worker = next(a for a in p["agents"] if a["ticket"] == 10 and a["role"] == "worker")
        self.assertEqual(worker["tokens"]["output"], 100 * 14)  # 14 distinct messages, one logged twice
        self.assertEqual(p["orchestrator"]["tokens"]["output"], 100 * 6)
        self.assertEqual(p["orchestrator"]["compactions"], 1)

    def test_only_plugin_agents_are_profiled(self):
        self.assertEqual(sorted(a["role"] for a in self.fx.run()["agents"]), ["reviewer", "worker", "worker"])

    def test_tickets_join_prs_merges_and_handbacks(self):
        tickets = {t["ticket"]: t for t in self.fx.run()["tickets"]}
        self.assertEqual((tickets[10]["pr"], tickets[10]["outcome"], tickets[10]["end"]), (50, "merged", at(30)))
        self.assertEqual((tickets[11]["pr"], tickets[11]["outcome"], tickets[11]["end"]), (None, "handed-back", at(31)))
        self.assertEqual({tickets[10]["slot"], tickets[11]["slot"]}, {0, 1})
        # The reviewer's tokens count toward the ticket its PR belongs to.
        self.assertEqual(tickets[10]["tokens"], 14 * 1160 + 1160)

    def test_building_splits_on_gaps(self):
        t10 = next(t for t in self.fx.run()["tickets"] if t["ticket"] == 10)
        self.assertEqual(t10["building"], [[at(1), at(11)], [at(25), at(27)]])

    def test_occupancy_against_the_config_slot_count(self):
        m = self.fx.run()["metrics"]
        span = 40 * 60  # first dispatch at 1, last record at 41
        self.assertAlmostEqual(m["span_s"], span)
        self.assertAlmostEqual(m["occupancy"], (29 + 30) * 60 / (2 * span))
        self.assertAlmostEqual(m["building_share"], (12 + 4) * 60 / (2 * span))

    def test_idle_reasons_come_from_the_pass_log(self):
        self.fx.passes([
            {"ts": at(1), "slots": 2, "free": 0, "idle": {}},
            {"ts": at(31), "slots": 2, "free": 2, "idle": {"overlap": 1, "empty": 1}},
        ])
        idle = self.fx.run()["metrics"]["idle_s"]
        self.assertEqual((idle["overlap"], idle["empty"]), (600, 600))

    def test_usage_windows_and_underuse(self):
        self.fx.passes([{"ts": at(5), "slots": 2, "free": 0, "takeable_waiting": 3}])
        p = self.fx.run()
        self.assertEqual(len(p["usage"]), 2)
        (w,) = p["metrics"]["windows"]
        self.assertEqual((w["peak"], w["closed"], w["pressure"], w["underused"]), (35.0, True, True, True))
        # Still open: can't be underused yet.
        self.assertFalse(self.fx.run(now_minutes=50)["metrics"]["windows"][0]["underused"])

    def test_no_pressure_without_a_pass_log(self):
        (w,) = self.fx.run()["metrics"]["windows"]
        self.assertEqual((w["pressure"], w["underused"]), (None, False))

    def test_limit_hits_recommend_fewer_slots(self):
        self.fx.passes([{"ts": at(5), "slots": 2, "free": 0, "limit_hit": True, "reset_at": at(60)}])
        p = self.fx.run()
        self.assertEqual(p["metrics"]["limit_hits"], 1)
        self.assertEqual(p["recommendation"], "Try slots 2 → 1: usage limits hit.")

    def test_slots_rise_to_the_observed_peak(self):
        p = self.fx.run(slots=1)
        self.assertEqual((p["slots"], p["slots_raised"]), (2, True))
        self.assertLessEqual(p["metrics"]["occupancy"], 1)

    def test_pass_log_slots_win_over_config(self):
        self.fx.passes([{"ts": at(5), "slots": 3, "free": 1}])
        self.assertEqual(self.fx.run()["slots"], 3)
        self.assertEqual(self.fx.run(slots=4)["slots"], 4)

    def test_proxy_cost_per_merged_ticket_by_model_and_role(self):
        c = self.fx.run()["metrics"]["cost"]
        opus, sonnet = 0.0100875, 0.0020175  # one default request each, at the PRICES rates
        self.assertAlmostEqual(c["by_role"]["orchestrator"], 6 * opus)
        self.assertAlmostEqual(c["by_role"]["worker"], 19 * sonnet)
        self.assertAlmostEqual(c["by_role"]["reviewer"], sonnet)
        self.assertAlmostEqual(c["by_model"]["opus"], 6 * opus)
        self.assertAlmostEqual(c["by_model"]["sonnet"], 20 * sonnet)
        self.assertAlmostEqual(c["by_role_model"]["reviewer"]["sonnet"], sonnet)
        self.assertAlmostEqual(c["total"], 6 * opus + 20 * sonnet)
        # One ticket merged, so per-ticket figures equal the totals.
        self.assertAlmostEqual(c["per_merged_ticket"], c["total"])
        self.assertAlmostEqual(c["per_merged_ticket_by_model"]["opus"], 6 * opus)
        self.assertAlmostEqual(c["per_merged_ticket_by_role"]["worker"], 19 * sonnet)
        self.assertEqual(c["unpriced_models"], [])

    def test_per_ticket_table_joins_worker_and_reviewer_cost(self):
        tickets = {t["ticket"]: t for t in self.fx.run()["tickets"]}
        sonnet = 0.0020175
        self.assertAlmostEqual(tickets[10]["cost"]["worker"], 14 * sonnet)
        self.assertAlmostEqual(tickets[10]["cost"]["reviewer"], sonnet)
        self.assertAlmostEqual(tickets[10]["cost"]["total"], 15 * sonnet)
        self.assertAlmostEqual(tickets[11]["cost"]["total"], 5 * sonnet)
        self.assertEqual(tickets[10]["worker_models"], ["sonnet"])
        self.assertIsNone(tickets[10]["gate_runs"])  # filled once events.jsonl is read

    def test_prices_carry_a_date_source_and_haiku_long_context_rate(self):
        self.assertRegex(prof.PRICES_AS_OF, r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(prof.PRICES_SOURCE.startswith("https://"))
        for family in ("opus", "sonnet", "haiku"):
            self.assertEqual(set(prof.PRICES[family]) >= {"input", "output", "cache_read", "cache_write"}, True)
        self.assertEqual(prof.PRICES["haiku"]["long_over"], 100_000)
        short = prof.request_cost("claude-haiku-5", {"input_tokens": 90_000})
        long = prof.request_cost("claude-haiku-5", {"input_tokens": 110_000})
        self.assertAlmostEqual(short, 90_000 * prof.PRICES["haiku"]["input"] / 1e6)
        self.assertAlmostEqual(long, 110_000 * prof.PRICES["haiku"]["long"]["input"] / 1e6)
        self.assertIsNone(prof.request_cost("some-other-model", {"input_tokens": 1}))

    def test_unknown_models_are_flagged_not_priced(self):
        self.assertEqual(self.fx.run()["warnings"], [])
        path = Path(self.tmp.name) / "mystery.jsonl"
        write_jsonl(path, [assistant(21, "x1", "mystery-1", [])])
        t = prof.Transcript(path)
        self.assertEqual(t.cost(), {"unpriced": 0.0})
        self.assertEqual(prof.cost_metrics(t, [], 1)["unpriced_models"], ["mystery-1"])

    def test_meter_percent_per_merged_ticket_is_unweighted(self):
        meters = self.fx.run()["metrics"]["meters"]
        # Samples inside the session: 20 -> 35 (five-hour), 40 -> 44 (seven-day); one ticket merged.
        self.assertEqual((meters["five_hour"]["used"], meters["five_hour"]["per_merged_ticket"]), (15.0, 15.0))
        self.assertEqual((meters["seven_day"]["used"], meters["seven_day"]["per_merged_ticket"]), (4.0, 4.0))

    def test_meter_reset_adds_nothing(self):
        self.assertEqual(prof.meter_use([{"five_hour": 80}, {"five_hour": 10}, {"five_hour": 25}], "five_hour"), 15.0)
        self.assertIsNone(prof.meter_use([{"x": 1}], "five_hour"))

    def test_span_is_reported_and_a_later_record_warns(self):
        p = self.fx.run()
        self.assertEqual((p["start"], p["end"], p["warnings"]), (at(1), at(41), []))
        # A reviewer wrote after the orchestrator's last record: the profile is partial.
        late = self.fx.main.with_suffix("") / "subagents" / "agent-ccc.jsonl"
        late.write_text(late.read_text() + json.dumps(assistant(55, "r2", "claude-sonnet-5-5", [])) + "\n")
        p = self.fx.run()
        self.assertEqual(p["last_record"], at(55))
        self.assertEqual(len(p["warnings"]), 1)
        self.assertIn("partial", p["warnings"][0])
        self.assertIn("WARNING", prof.summary(p, Path("x.json")))

    def test_schema_2_keeps_slot_efficiency_fields(self):
        p = self.fx.run()
        self.assertEqual(p["schema"], 2)
        for key in ("occupancy", "building_share", "review_share", "idle_s", "windows"):
            self.assertIn(key, p["metrics"])

    def test_summary_is_about_five_lines(self):
        self.fx.passes([{"ts": at(5), "slots": 2, "free": 1}])
        out = prof.summary(self.fx.run(), Path("x.json"))
        self.assertEqual(len(out.splitlines()), 5)
        self.assertIn("per merged ticket (1 merged)", out)
        self.assertIn("opus", out)


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.root), "ORCHESTRATE_USAGE_LOG": str(self.root / "u.jsonl")}

    def tearDown(self):
        self.tmp.cleanup()

    def script(self, name, *args, stdin=""):
        return subprocess.run([sys.executable, "-I", str(SCRIPTS / name), *args], input=stdin,
                              capture_output=True, text=True, env=self.env, check=True)

    def test_record_appends_a_stamped_pass(self):
        repo = self.root / "repo"
        self.script("profile.py", "record", "--repo", str(repo), "--session", "s", '{"slots": 2, "free": 1}')
        self.script("profile.py", "record", "--repo", str(repo), "--session", "s", '{"slots": 2, "free": 0}')
        rows = [json.loads(line) for line in (repo / prof.PROFILES / prof.PASS_LOG).read_text().splitlines()]
        self.assertEqual([(r["session"], r["free"]) for r in rows], [("s", 1), ("s", 0)])
        self.assertIn("ts", rows[0])

    def test_profile_by_session_id_saves_json_and_html(self):
        fx = Fixture(self.root)
        self.env["ORCHESTRATE_USAGE_LOG"] = str(fx.usage)
        out = self.script("profile.py", SESSION).stdout
        self.assertIn("2 slots", out)
        saved = fx.repo / prof.PROFILES / f"{at(1)[:10]}-{SESSION[:8]}.json"
        data = json.loads(saved.read_text())
        self.assertEqual(data["schema"], prof.SCHEMA)
        html = saved.with_suffix(".html").read_text()
        self.assertIn('"session": "sess-1"', html)
        self.assertNotIn('type="application/json">null<', html)

    def test_html_escapes_script_close(self):
        out = self.root / "x.html"
        prof.write_html({"description": "</script><b>"}, out)
        self.assertNotIn("</script><b>", out.read_text())

    def test_tap_logs_changes_only_and_stays_silent(self):
        payload = {"rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": 1793750400},
                                   "seven_day": {"used_percentage": 40, "resets_at": 1794000000}}}
        for pct in (12, 12, 13):
            payload["rate_limits"]["five_hour"]["used_percentage"] = pct
            self.assertEqual(self.script("usage-tap.py", stdin=json.dumps(payload)).stdout, "")
        rows = [json.loads(line) for line in (self.root / "u.jsonl").read_text().splitlines()]
        self.assertEqual([r["five_hour"] for r in rows], [12, 13])

    def test_tap_ignores_sessions_without_limits_and_bad_input(self):
        self.script("usage-tap.py", stdin=json.dumps({"model": {}}))
        self.script("usage-tap.py", stdin="not json")
        self.assertFalse((self.root / "u.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
