"""Tests for scripts/profile.py on fixture transcripts. Run: python3 -m unittest discover tests"""

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import profile as prof  # noqa: E402
import runlog  # noqa: E402
from transcripts import write_agent, write_jsonl  # noqa: E402

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


MERGED_50 = {"ts": at(30), "session": SESSION, "kind": "merge", "ref": 50, "outcome": "merged"}


class Fixture:
    """A session that dispatches two workers and a reviewer, merges one PR and hands one ticket back."""

    def __init__(self, root: Path):
        self.repo = root / "repo"
        (self.repo / ".claude").mkdir(parents=True, exist_ok=True)
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
        write_agent(sub, "aaa", w1, {"agentType": "orchestrate:worker", "toolUseId": "u1"})
        write_agent(sub, "bbb", [assistant(m, f"w2-{m}", sonnet, []) for m in range(1, 6)],
                    {"agentType": "orchestrate:worker", "toolUseId": "u2"})
        write_agent(sub, "ccc", [assistant(20, "r1", sonnet, [])], {"agentType": "orchestrate:reviewer", "toolUseId": "u3"})
        self.usage = root / "usage.jsonl"
        reset = at(60)
        write_jsonl(self.usage, [
            {"ts": at(2), "five_hour": 20, "seven_day": 40, "five_hour_resets_at": reset},
            {"ts": at(40), "five_hour": 35, "seven_day": 44, "five_hour_resets_at": reset},
            {"ts": at(500), "five_hour": 90, "five_hour_resets_at": at(600)},  # outside the session
        ])

    def passes(self, rows):
        write_jsonl(runlog.log_path(self.repo, runlog.PASS_LOG), [{"session": SESSION, **r} for r in rows])

    def events(self, rows):
        write_jsonl(runlog.log_path(self.repo, runlog.EVENTS_LOG), rows)

    def run(self, now_minutes=120, **kw):
        os.environ["ORCHESTRATE_USAGE_LOG"] = str(self.usage)
        return prof.profile(self.main, None, kw.get("slots"), kw.get("threshold", 50.0), T0 + timedelta(minutes=now_minutes), kw.get("lead"))


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

    def test_an_idle_claimed_ticket_counts_its_slot_seconds_as_worker_idle(self):
        self.fx.passes([
            {"ts": at(1), "slots": 2, "free": 1, "claimed": {"1": "building", "2": "idle"}, "idle": {"empty": 1}},
            {"ts": at(31), "slots": 2, "free": 2, "claimed": {}, "idle": {"empty": 2}},
        ])
        idle = self.fx.run()["metrics"]["idle_s"]
        self.assertEqual(idle["worker-idle"], 1800)

    def test_record_with_an_empty_session_exits_2_in_one_line_and_writes_no_pass(self):
        import subprocess
        for extra in ([], ["--nudged", "5"]):
            r = subprocess.run([sys.executable, str(SCRIPTS / "profile.py"), "record", "--repo", str(self.fx.repo),
                                "--session", "", *extra], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
            self.assertIn("runlog: no session, pass not written", r.stderr)
        self.assertFalse(runlog.log_path(self.fx.repo, runlog.PASS_LOG).exists())

    def test_a_nudge_record_is_not_a_pass(self):
        import subprocess
        self.fx.passes([{"ts": at(1), "slots": 2, "free": 0, "idle": {}}])
        subprocess.run([sys.executable, str(SCRIPTS / "profile.py"), "record", "--repo", str(self.fx.repo),
                        "--session", SESSION, "--nudged", "7"], check=True)
        self.assertEqual(len(runlog.read_passes(self.fx.repo, SESSION)), 1)
        raw = [json.loads(l) for l in runlog.log_path(self.fx.repo, runlog.PASS_LOG).read_text().splitlines()]
        self.assertEqual((raw[-1]["scope"], raw[-1]["nudged"]), ("nudge", [7]))

    def test_an_unchecked_idle_reason_round_trips_through_the_pass_log_and_shows_in_the_summary(self):
        # A slot left empty because holds exited 2 is `unchecked`, not `limit` (which means a usage limit only).
        for ts, idle in ((at(1), {}), (at(31), {"unchecked": 1, "empty": 1})):
            runlog.append_pass(self.fx.repo, SESSION, {"ts": ts, "scope": "wake", "slots": 2, "free": len(idle) and 2, "idle": idle})
        p = self.fx.run()
        self.assertEqual((p["metrics"]["idle_s"]["unchecked"], p["metrics"]["idle_s"]["empty"]), (600, 600))
        self.assertNotIn("limit", p["metrics"]["idle_s"])
        self.assertIn("unchecked 10m", prof.summary(p, Path("x.json")))
        self.assertEqual(runlog.read_passes(self.fx.repo, SESSION)[1]["scope"], "wake")

    def test_an_unknown_idle_reason_is_kept(self):
        self.fx.passes([{"ts": at(1), "slots": 2, "free": 1, "idle": {"mystery": 1}}])
        self.assertEqual(self.fx.run()["metrics"]["idle_s"]["mystery"], 2400)

    def test_a_pass_record_at_the_edge_of_the_wake_grace_ends_the_window_inside_it_does_but_outside_it_does_not(self):
        # The transcript's last record is at minute 41.
        grace = prof.WAKE_GRACE.total_seconds() / 60
        for record_at, window_end in ((41 + grace - 1 / 60, 41 + grace - 1 / 60), (41 + grace, 41 + grace), (41 + grace + 1 / 60, 41)):
            self.fx.passes([{"ts": at(record_at), "slots": 2, "free": 1}])
            self.assertEqual(self.fx.run()["end"], at(window_end), record_at)

    def test_stray_jsonl_lines_and_naive_timestamps_do_not_crash_the_profile(self):
        # `append_pass` lets the caller's ts win over its own stamp, so a naive ts lands in the pass log verbatim.
        naive = at(5).replace("Z", "")
        self.assertNotIn("+", naive)
        runlog.append_pass(self.fx.repo, SESSION, {"ts": naive, "scope": "full", "slots": 2, "free": 0, "idle": {}})
        log = runlog.log_path(self.fx.repo, runlog.PASS_LOG)
        with open(log, "a") as f:
            f.write("[]\n3\n\n\"text\"\nnot json\n")
        with open(self.fx.usage, "a") as f:
            f.write("[]\n3\n\n" + json.dumps({"ts": naive, "five_hour": 50}) + "\n")
        p = self.fx.run()
        self.assertEqual(len(p["usage"]), 3)  # the naive-ts usage row is read as UTC, inside the session
        self.assertEqual(p["metrics"]["limit_hits"], 0)

    def test_parse_ts_reads_a_naive_timestamp_as_utc(self):
        self.assertEqual(prof.parse_ts("2026-11-03T16:00:00"), T0)

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
        opus, sonnet = 0.00249, 0.001245  # one default request each (10 in, 100 out, 1000 read, 50 written) at Opus 5.5 and Sonnet 5.5 rates
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
        sonnet = 0.001245
        self.assertAlmostEqual(tickets[10]["cost"]["worker"], 14 * sonnet)
        self.assertAlmostEqual(tickets[10]["cost"]["reviewer"], sonnet)
        self.assertAlmostEqual(tickets[10]["cost"]["total"], 15 * sonnet)
        self.assertAlmostEqual(tickets[11]["cost"]["total"], 5 * sonnet)
        self.assertEqual(tickets[10]["worker_models"], ["sonnet"])
        self.assertIsNone(tickets[10]["gate_runs"])  # filled once events.jsonl is read

    def test_prices_carry_a_date_source_and_haiku_long_context_rate(self):
        self.assertRegex(prof.PRICES_AS_OF, r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(prof.PRICES_SOURCE.startswith("https://"))
        for key, price in prof.PRICES.items():
            self.assertTrue(set(price) >= {"input", "output", "cache_read", "cache_write"}, key)
        # Rates as listed on the pricing page on PRICES_AS_OF.
        self.assertEqual(prof.PRICES["claude-opus-5-5"], {"input": 4, "output": 20, "cache_read": 0.2, "cache_write": 5})
        self.assertEqual(prof.PRICES["claude-sonnet-5-5"]["cache_read"], 0.1)
        self.assertEqual(prof.price_key("claude-opus-5-5-x"), "claude-opus-5-5")  # longest key wins
        self.assertEqual(prof.price_key("claude-opus-5"), "claude-opus-5")
        self.assertEqual(prof.PRICES["claude-haiku-5-5"]["long_over"], 100_000)
        short = prof.request_cost("claude-haiku-5-5", {"input_tokens": 90_000})
        long = prof.request_cost("claude-haiku-5-5", {"input_tokens": 110_000})
        self.assertAlmostEqual(short, 90_000 * 0.10 / 1e6)
        self.assertAlmostEqual(long, 110_000 * 0.50 / 1e6)
        # The prompt counts cache reads: 60K fresh + 60K cached is over the line.
        over = prof.request_cost("claude-haiku-5-5", {"input_tokens": 60_000, "cache_read_input_tokens": 60_000})
        self.assertAlmostEqual(over, (60_000 * 0.50 + 60_000 * 0.05) / 1e6)
        self.assertIsNone(prof.request_cost("some-other-model", {"input_tokens": 1}))

    def test_unlisted_newer_version_is_flagged_and_zero_usage_is_skipped(self):
        self.assertTrue(prof.is_exact_version("claude-opus-4-8-20260101"))
        self.assertTrue(prof.is_exact_version("claude-opus-4"))
        self.assertFalse(prof.is_exact_version("claude-opus-4-9"))
        self.assertEqual(prof.price_key("claude-3-5-haiku-20241022"), "claude-3-5-haiku")
        self.assertEqual(prof.model_family("claude-3-5-haiku-20241022"), "haiku")
        path = Path(self.tmp.name) / "newer.jsonl"
        zero = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        write_jsonl(path, [assistant(1, "n1", "claude-opus-4-9", []), assistant(2, "z1", "<synthetic>", [], zero)])
        c = prof.cost_metrics(prof.Transcript(path), [], 1)
        self.assertEqual((c["approximate_models"], c["unpriced_models"]), (["claude-opus-4-9"], []))

    def test_unknown_models_are_flagged_not_priced(self):
        self.assertEqual(self.fx.run()["warnings"], [])
        path = Path(self.tmp.name) / "mystery.jsonl"
        write_jsonl(path, [assistant(21, "x1", "mystery-1", [])])
        t = prof.Transcript(path)
        self.assertEqual(prof.cost_of(t), {"unpriced": 0.0})
        self.assertEqual(prof.cost_metrics(t, [], 1)["unpriced_models"], ["mystery-1"])

    def test_unpriced_and_approximate_models_each_warn_once_in_the_profile(self):
        # End to end: both messages reach profile["warnings"], and a model used in
        # several requests is named once.
        with open(self.fx.main, "a") as f:
            for i, (mid, model) in enumerate([("u1", "mystery-1"), ("u2", "mystery-1"), ("a1", "claude-opus-4-9")]):
                f.write(json.dumps(assistant(30 + i, mid, model, [])) + "\n")
        warnings = self.fx.run()["warnings"]
        unpriced = [w for w in warnings if w.startswith("no price for model(s)")]
        approx = [w for w in warnings if w.startswith("version not in PRICES")]
        self.assertEqual(unpriced, ["no price for model(s) mystery-1: their cost counts as $0"])
        self.assertEqual(approx, ["version not in PRICES for claude-opus-4-9: priced at the nearest older version's rates"])

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

    def late_entry(self, minutes):
        """The orchestrator's transcript writes one more record `minutes` after the start."""
        self.fx.main.write_text(self.fx.main.read_text() + json.dumps(assistant(minutes, f"late{minutes}", "claude-opus-5-5", [])) + "\n")

    def resume(self, minutes):
        return {"ts": at(minutes), "session": SESSION, "kind": "resume", "ref": None, "outcome": "resumed"}

    def test_a_resume_event_reports_the_session_gap_and_leaves_it_out_of_the_span(self):
        self.late_entry(80)
        self.fx.events([self.resume(79)])
        m = self.fx.run()["metrics"]
        self.assertEqual(m["session_gap"], {"total_s": 38 * 60, "intervals": [[at(41), at(79)]]})
        self.assertEqual(m["span_s"], (79 - 1) * 60 - 38 * 60 + 60)  # at(1) to at(80), minus the gap
        self.assertEqual(m["unexplained_silences"], [])

    def test_throughput_is_merged_tickets_per_hour_of_the_span_and_a_resume_gap_shortens_it(self):
        plain = self.fx.run()["metrics"]
        self.assertGreater(plain["tickets_merged"], 0)
        self.assertAlmostEqual(plain["throughput_per_hour"], plain["tickets_merged"] / (plain["span_s"] / 3600))
        self.late_entry(80)
        ungapped = self.fx.run()["metrics"]["throughput_per_hour"]
        self.fx.events([MERGED_50, self.resume(79)])
        m = self.fx.run()["metrics"]
        self.assertAlmostEqual(m["throughput_per_hour"], m["tickets_merged"] / (m["span_s"] / 3600))
        self.assertGreater(m["throughput_per_hour"], ungapped)  # the gap is out of the denominator
        self.assertIn("merged/hour", prof.summary(self.fx.run(), Path("x.json")))

    def test_viewer_renders_the_throughput_tile(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        p = self.fx.run()
        path = Path(self.tmp.name) / "t.json"
        path.write_text(json.dumps(p))
        out = subprocess.run(["node", str(Path(__file__).parent / "viewer_quality.js"), str(prof.VIEWER), str(path), "throughputTile"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Throughput", out.stdout)
        self.assertIn(f"{p['metrics']['throughput_per_hour']:.2f} merged/hour", out.stdout)
        self.assertIn("merged over", out.stdout)
        cmp = subprocess.run(["node", str(Path(__file__).parent / "viewer_quality.js"), str(prof.VIEWER), str(path), "compare"],
                             capture_output=True, text=True)
        self.assertEqual(cmp.returncode, 0, cmp.stderr)
        self.assertIn("Merged / hour", cmp.stdout)
        self.assertIn(f"{p['metrics']['throughput_per_hour']:.2f}", cmp.stdout)

    def test_merges_per_hour_net_of_prompt_stalls_counts_overlapping_stalls_once(self):
        records = [json.loads(l) for l in self.fx.main.read_text().splitlines()]
        records += [tool_result(38, "u4", "merged"), tool_result(36, "u5", "ok")]
        write_jsonl(self.fx.main, records)
        plain = self.fx.run()["metrics"]
        self.assertEqual(plain["prompt_stall_s"], 0)
        self.assertEqual(plain["throughput_net_per_hour"], plain["throughput_per_hour"])
        prompt = lambda minutes, tool, command, **kw: {"time": at(minutes), "session_id": SESSION, "agent_type": None, "source": "request",
                                                        "tool_name": tool, "tool_input": {"command": command}, **kw}
        write_jsonl(self.fx.repo / ".claude" / "orchestrate-prompts.jsonl", [
            prompt(30.1, "Bash", "gh pr merge 50 --squash"),   # waits until minute 38
            prompt(31.2, "Bash", "gh issue edit 11 --add-label needs-info --remove-assignee @me"),  # inside the first: counted once
            prompt(35, "Bash", "gh pr merge 50 --squash", agent_type="worker"),  # a worker's prompt is not the orchestrator's
            prompt(33, "Bash", "something never run"),  # no matching call
        ])
        m = self.fx.run()["metrics"]
        self.assertAlmostEqual(m["prompt_stall_s"], (38 - 30.1) * 60)
        self.assertGreater(m["throughput_net_per_hour"], m["throughput_per_hour"])
        self.assertAlmostEqual(m["throughput_net_per_hour"], m["tickets_merged"] / ((m["span_s"] - m["prompt_stall_s"]) / 3600))
        self.assertEqual(m["stops"]["by_kind"]["prompt"]["count"], 1)
        self.assertEqual(m["stops"]["count"], 0)  # the stall is the run's, not a ticket's

    def test_a_silence_over_30_minutes_with_no_event_is_noted_and_not_excluded(self):
        self.late_entry(80)
        p = self.fx.run()
        m = p["metrics"]
        self.assertEqual(m["unexplained_silences"], [[at(41), at(80)]])
        self.assertEqual(m["session_gap"], {"total_s": 0, "intervals": []})
        self.assertEqual(m["span_s"], 79 * 60)
        self.assertIn("no resume event", prof.summary(p, Path("x.json")))

    def test_a_silence_of_30_minutes_or_less_is_not_noted(self):
        self.late_entry(71)
        self.assertEqual(self.fx.run()["metrics"]["unexplained_silences"], [])

    def test_outside_gaps_is_the_window_minus_the_gaps(self):
        t = lambda m: T0 + timedelta(minutes=m)
        got = prof.outside_gaps(t(0), t(100), [[t(10), t(20)], [t(15), t(30)], [t(90), t(120)]])
        self.assertEqual(got, [[t(0), t(10)], [t(30), t(90)]])

    def test_viewer_shows_the_gap_in_the_subtitle_the_bands_and_the_silence_banner(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        self.late_entry(80)
        self.fx.events([self.resume(60)])
        p = self.fx.run()
        self.late_entry(130)
        silent = self.fx.run()
        path = Path(self.tmp.name) / "p.json"
        run = lambda prof_, card: subprocess.run(["node", str(Path(__file__).parent / "viewer_quality.js"), str(prof.VIEWER), str(path), card],
                                                 capture_output=True, text=True)
        path.write_text(json.dumps(p))
        sub = run(p, "subtitleText")
        self.assertEqual(sub.returncode, 0, sub.stderr)
        self.assertIn("session gap 19m, outside the span", sub.stdout)
        for card in ("timeline", "usage"):
            out = run(p, card)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("session gap", out.stdout)
        self.assertNotIn("No transcript entry", run(p, "silenceBanners").stdout)
        path.write_text(json.dumps(silent))
        banner = run(silent, "silenceBanners")
        self.assertEqual(banner.returncode, 0, banner.stderr)
        self.assertIn("no resume event", banner.stdout)

    def test_schema_3_keeps_slot_efficiency_fields(self):
        p = self.fx.run()
        self.assertEqual(p["schema"], 3)
        for key in ("occupancy", "building_share", "review_share", "idle_s", "windows"):
            self.assertIn(key, p["metrics"])

    def test_merges_count_from_merge_events_not_from_gh_pr_merge_text(self):
        ev = lambda outcome, ref, minute=30: {"ts": at(minute), "session": SESSION, "kind": "merge", "ref": ref, "outcome": outcome}
        self.fx.events([ev("timeout", 50)])  # the transcript has `gh pr merge 50`, but the script saw no merge
        p = self.fx.run()
        self.assertEqual(p["metrics"]["tickets_merged"], 0)
        self.fx.events([ev("closed", 50)])
        self.assertEqual(self.fx.run()["metrics"]["tickets_merged"], 0)
        self.fx.events([ev("merged", 50, 33)])
        p = self.fx.run()
        t10 = next(t for t in p["tickets"] if t["ticket"] == 10)
        self.assertEqual((t10["outcome"], t10["end"], t10["merge_unverified"]), ("merged", at(33), False))
        self.assertEqual(p["metrics"]["merges_unverified"], 0)

    def test_a_merge_logged_in_the_wake_of_the_pass_record_is_counted_and_not_partial(self):
        # The transcript's last line is at minute 41. scripts/merge logs at 50, the pass record lands a second
        # later, and the transcript's last line is a second after that. The old window ended at the transcript's
        # last line before the record's wake, and printed "no tickets merged" with a partial warning.
        records = [json.loads(l) for l in self.fx.main.read_text().splitlines()]
        records.append({"type": "user", "timestamp": at(50 + 2 / 60)})
        write_jsonl(self.fx.main, records)
        self.fx.events([{"ts": at(50), "kind": "merge", "ref": 50, "outcome": "merged"}])
        self.fx.passes([{"ts": at(50 + 1 / 60), "slots": 2, "free": 1}])
        p = self.fx.run()
        self.assertEqual(p["metrics"]["tickets_merged"], 1)
        self.assertEqual(p["metrics"]["merges_unverified"], 0)
        self.assertEqual([w for w in p["warnings"] if "partial" in w], [])
        self.assertNotIn("no tickets merged", prof.summary(p, Path("x.json")))

    def test_the_window_ends_at_the_last_pass_record_when_it_follows_the_transcript(self):
        # The record and the merge event are written after the transcript's last line (minute 41).
        self.fx.passes([{"ts": at(43), "slots": 2, "free": 1}])
        self.fx.events([{"ts": at(43), "kind": "merge", "ref": 50, "outcome": "merged"}])
        p = self.fx.run()
        self.assertEqual(p["metrics"]["tickets_merged"], 1)
        t10 = next(t for t in p["tickets"] if t["ticket"] == 10)
        self.assertEqual(t10["end"], at(43))
        self.assertEqual([w for w in p["warnings"] if "partial" in w], [])

    def test_without_events_both_merge_commands_count_but_are_unverified(self):
        for cmd in ("gh pr merge 50 --squash", '"${CLAUDE_PLUGIN_ROOT}/scripts/merge" 50 --method squash', "~/p/scripts/merge 50 --worktree w"):
            records = [json.loads(l) for l in self.fx.main.read_text().splitlines()]
            for r in records:
                for b in (r.get("message") or {}).get("content", []) if isinstance((r.get("message") or {}).get("content"), list) else []:
                    if b.get("id") == "u4":
                        b["input"]["command"] = cmd
            write_jsonl(self.fx.main, records)
            p = self.fx.run()
            self.assertEqual(p["metrics"]["tickets_merged"], 1, cmd)
            self.assertEqual(p["metrics"]["merges_unverified"], 1, cmd)
            self.assertIn("unverified", prof.summary(p, Path("x.json")))

    def test_events_log_without_a_merge_ignores_gh_pr_merge_text(self):
        self.fx.events([{"ts": at(5), "session": SESSION, "kind": "gate", "ref": 50, "outcome": "pass"}])
        self.assertEqual(self.fx.run()["metrics"]["tickets_merged"], 0)

    def test_slots_follow_each_pass_and_the_raise_note_uses_the_count_in_force(self):
        # Two tickets run 1-30 and 1-31 (two at once); the count drops 3 -> 1 only after both ended.
        self.fx.passes([{"ts": at(1), "slots": 3, "free": 1}, {"ts": at(35), "slots": 1, "free": 1}])
        self.fx.events([MERGED_50])
        p = self.fx.run()
        self.assertFalse(p["slots_raised"])
        self.assertEqual(p["slots"], 1)
        self.assertEqual([n for _, n in p["slots_timeline"]], [3, 1])
        capacity = 3 * 34 * 60 + 1 * 6 * 60  # 1 -> 35 on 3 slots, 35 -> 41 on 1
        self.assertAlmostEqual(p["metrics"]["occupancy"], (29 + 30) * 60 / capacity)
        self.assertIn("3→1 slots", prof.summary(p, Path("x.json")))
        # The same two tickets under a count of 1 in force the whole time do raise.
        self.fx.passes([{"ts": at(1), "slots": 1, "free": 1}])
        self.assertTrue(self.fx.run()["slots_raised"])

    def test_a_greedy_session_is_measured_against_its_cap(self):
        # Two tickets run 1-30 and 1-31 under a greedy cap of 5: occupancy is against the cap, not the count run.
        self.fx.passes([{"ts": at(1), "slots": 5, "free": 3, "greedy": True, "idle": {"overlap": 2, "empty": 1}}])
        self.fx.events([MERGED_50])
        p = self.fx.run()
        self.assertTrue(p["greedy"])
        self.assertFalse(p["slots_raised"])
        self.assertAlmostEqual(p["metrics"]["occupancy"], (29 + 30) * 60 / (5 * 40 * 60))
        self.assertIn("5 slots (greedy)", prof.summary(p, Path("x.json")))

    def test_a_pass_log_without_greedy_reads_as_not_greedy(self):
        self.fx.passes([{"ts": at(1), "slots": 2, "free": 1}])
        p = self.fx.run()
        self.assertFalse(p["greedy"])
        self.assertNotIn("greedy", prof.summary(p, Path("x.json")))

    def test_a_log_holding_only_other_sessions_events_falls_back_to_the_transcript(self):
        self.fx.events([{"ts": at(30), "session": "other", "kind": "merge", "ref": 50, "outcome": "merged"}])
        p = self.fx.run()
        self.assertEqual((p["metrics"]["tickets_merged"], p["metrics"]["merges_unverified"]), (1, 1))

    def test_a_pass_after_the_last_record_adds_no_negative_capacity(self):
        self.fx.passes([{"ts": at(1), "slots": 2, "free": 1}, {"ts": at(90), "slots": 1, "free": 1}])
        p = self.fx.run()
        # The late pass sets the final count but has no time inside the profile, so capacity is unchanged.
        self.assertEqual([n for _, n in p["slots_timeline"]], [2, 1])
        self.assertAlmostEqual(p["metrics"]["occupancy"], (29 + 30) * 60 / (2 * 40 * 60))

    def test_a_pass_logged_after_the_last_record_sets_the_slot_count(self):
        # The run records its pass and builds the profile at the end of that same pass, so the pass is the newest record.
        self.fx.passes([{"ts": at(90), "slots": 5, "free": 3, "greedy": True}])
        self.fx.events([MERGED_50])
        p = self.fx.run()
        self.assertEqual(p["slots"], 5)
        self.assertEqual([n for _, n in p["slots_timeline"]], [5])
        self.assertEqual(p["end"], at(41))
        self.assertIn("5 slots (greedy)", prof.summary(p, Path("x.json")))

    def test_an_earlier_pass_then_a_late_one_reads_3_to_5(self):
        self.fx.passes([{"ts": at(1), "slots": 3, "free": 1}, {"ts": at(90), "slots": 5, "free": 1}])
        self.fx.events([MERGED_50])
        p = self.fx.run()
        self.assertEqual(p["slots"], 5)
        self.assertEqual([n for _, n in p["slots_timeline"]], [3, 5])
        self.assertIn("3→5 slots", prof.summary(p, Path("x.json")))

    def test_forced_slots_override_the_pass_log(self):
        self.fx.passes([{"ts": at(1), "slots": 3, "free": 1}, {"ts": at(35), "slots": 2, "free": 1}])
        p = self.fx.run(slots=4)
        self.assertEqual(p["slots"], 4)
        self.assertEqual(len(p["slots_timeline"]), 1)

    def test_summary_is_about_five_lines(self):
        self.fx.passes([{"ts": at(5), "slots": 2, "free": 1}])
        self.fx.events([MERGED_50])
        out = prof.summary(self.fx.run(), Path("x.json"))
        self.assertEqual(len(out.splitlines()), 6)  # header, $, slots, plan, GitHub calls, saved
        self.assertIn("per merged ticket (1 merged)", out)
        self.assertIn("opus", out)

    def test_summary_says_headroom_is_not_measured_without_usage_data(self):
        self.fx.usage.write_text("")
        out = prof.summary(self.fx.run(), Path("x.json"))
        self.assertIn("headroom not measured", out)
        self.assertNotIn("status", out.lower().split("plan", 1)[1].splitlines()[0])


def verdict_text(verdict, minors=0, blocking=0):
    """A reviewer's full verdict block, as returned when it didn't post."""
    findings = "\n".join(["- [blocking] [spec] a.py:1 - x -> y"] * blocking + ["- [minor] [standards] a.py:2 - x -> y"] * minors)
    return f"verdict: {verdict}\ntier: M\nspec: a -> met\nfindings:\n{findings}\nposted: off"


class QualityFixture:
    """Four tickets: #20 escalates Haiku to Sonnet and its reviewer is continued; #21 is a Haiku ticket approved
    with two minors and merged without a second round; #22 gets a fresh reviewer for round 2 and its worker
    spawns an agent (depth 2); #23 is a Sonnet ticket approved in round 1."""

    def __init__(self, root: Path):
        self.main = root / "projects" / "-repo" / f"{SESSION}.jsonl"
        sub = self.main.with_suffix("") / "subagents"
        opus, sonnet, haiku = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"
        dispatch = [("u20h", "worker", "Build #20 a"), ("u20s", "worker", "Build #20 a again"), ("u21", "worker", "Build #21 b"),
                    ("u22", "worker", "Build #22 c"), ("u23", "worker", "Build #23 d"),
                    ("r20", "reviewer", "Review PR 60"), ("r21", "reviewer", "Review PR 61 round 1"),
                    ("r22a", "reviewer", "Review PR 62"), ("r22b", "reviewer", "Review PR 62 round 2"), ("r23", "reviewer", "Review PR 63")]
        records = [{"type": "user", "timestamp": at(0), "cwd": str(root), "message": {"content": "run"}}]
        for i, (use_id, role, desc) in enumerate(dispatch):
            records.append(assistant(1 + i, f"m{i}", opus, [tool_use(use_id, "Agent", subagent_type=f"orchestrate:{role}", description=desc)]))
        for n, pr in ((20, 60), (21, 61), (22, 62), (23, 63)):
            records.append(assistant(30 + n, f"mg{n}", opus, [tool_use(f"g{n}", "Bash", command=f"gh pr merge {pr} --squash")]))
        write_jsonl(self.main, records)

        def worker(name, use_id, model, pr=None, extra=(), contexts=(1000,), out_chars=400):
            usage = lambda c: {"input_tokens": c, "output_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
            recs = [assistant(2 + k, f"{name}-{k}", model, [], usage(c)) for k, c in enumerate(contexts)]
            recs.append(tool_result(2, f"{name}-t", "x" * out_chars))
            if pr:
                recs += [assistant(3, f"{name}-pr", model, [tool_use(f"{name}-b", "Bash", command="gh pr create --fill")], usage(500)),
                         tool_result(3, f"{name}-b", f"https://github.com/o/r/pull/{pr}")]  # 30 characters
            write_agent(sub, name, recs + list(extra), {"agentType": "orchestrate:worker", "toolUseId": use_id, "model": model})

        worker("w20h", "u20h", haiku, 60, contexts=(1000, 3000, 2000), out_chars=4000)
        worker("w20s", "u20s", sonnet, contexts=(5000,))
        worker("w21", "u21", haiku, 61, contexts=(100_000, 200_000), out_chars=800)
        spawn = [assistant(9, "w22-spawn", sonnet, [tool_use("d1", "Agent", subagent_type="orchestrate:scout", description="find callers")])]
        worker("w22", "u22", sonnet, 62, extra=spawn)
        write_agent(sub, "d1x", [assistant(10, "d1-a", haiku, [])], {"agentType": "orchestrate:scout", "toolUseId": "d1"})
        worker("w23", "u23", sonnet, 63)

        def reviewer(name, use_id, texts):
            write_agent(sub, name, [assistant(20 + k, f"{name}-{k}", sonnet, [{"type": "text", "text": t}]) for k, t in enumerate(texts)],
                        {"agentType": "orchestrate:reviewer", "toolUseId": use_id})

        reviewer("r20", "r20", [verdict_text("changes", blocking=1), verdict_text("approve")])  # one agent, two verdicts: continued
        reviewer("r21", "r21", ["verdict: approve\nround: 1\nblocking: 0\nnon-blocking: 2\nreview: https://github.com/o/r/pull/61#r"])
        reviewer("r22a", "r22a", [verdict_text("changes", blocking=2)])
        reviewer("r22b", "r22b", [verdict_text("approve", minors=1)])  # fresh: dispatched for round 2
        reviewer("r23", "r23", [verdict_text("approve")])

    def run(self):
        return prof.profile(self.main, None, 4, 50.0, T0 + timedelta(minutes=300))


class QualityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = QualityFixture(Path(self.tmp.name))
        self.p = self.fx.run()
        self.m = self.p["metrics"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_rounds_per_pr_and_round_1_outcomes(self):
        r = self.m["review"]
        self.assertEqual({x["pr"]: x["rounds"] for x in r["prs"]}, {60: 2, 61: 1, 62: 2, 63: 1})
        self.assertEqual((r["reviewed_prs"], r["rounds_total"], r["rounds_per_pr"]), (4, 6, 1.5))
        self.assertEqual((r["round1_approvals"], r["round1_changes"], r["round1_changes_rate"]), (2, 2, 0.5))

    def test_continued_and_fresh_reviewer_rounds(self):
        r = self.m["review"]
        self.assertEqual((r["continued_rounds"], r["fresh_rounds"]), (1, 1))  # #60 continued, #62 fresh

    def test_approved_with_minors_merged_without_round_2(self):
        r = self.m["review"]
        self.assertEqual(r["minors_merged_no_round2"], 1)  # #61 only: #62's minors came after a round 1 of changes
        self.assertTrue({x["pr"]: x for x in r["prs"]}[61]["minors_merged_no_round2"])

    def test_escalation_counts_haiku_then_sonnet(self):
        self.assertEqual(self.m["escalations"], {"count": 1, "tickets": [20]})
        self.assertEqual({t["ticket"]: t["worker_models"] for t in self.p["tickets"]}[20], ["haiku", "sonnet"])

    def test_alias_metadata_names_the_worker_model_but_not_the_model_rollup(self):
        # Real metadata holds the dispatch alias; the transcript holds the full id. One model, one row.
        sub = self.fx.main.with_suffix("") / "subagents"
        for meta in sub.glob("agent-w*.meta.json"):
            data = json.loads(meta.read_text())
            data["model"] = "haiku" if "haiku" in data["model"] else "sonnet"
            meta.write_text(json.dumps(data))
        p = self.fx.run()
        self.assertEqual(set(p["metrics"]["tokens_by_model"]) - {"claude-opus-5-5"}, {"claude-haiku-4-5", "claude-sonnet-5-5"})
        agents = {a["id"]: a for a in p["agents"]}
        self.assertEqual(agents["w23"]["model"], "claude-sonnet-5-5")
        self.assertEqual(set(p["metrics"]["worker_models"]), {"haiku", "sonnet"})
        self.assertEqual(p["metrics"]["escalations"]["tickets"], [20])

    def test_dispatched_round_prefers_the_tier_round_and_repeats_follow_the_highest(self):
        self.assertEqual(prof.dispatched_round("Review PR 5. Tier M, round 2. Check against your round 1 findings"), 2)
        self.assertEqual(prof.dispatched_round("against your round 1 findings, round 2"), 2)
        self.assertEqual(prof.dispatched_round("Review PR 5"), 1)
        sub = self.fx.main.with_suffix("") / "subagents"
        agent = lambda name: {"role": "reviewer", "id": name, "pr": 9, "ticket": None, "round": 1,
                              "transcript": prof.Transcript(sub / f"agent-{name}.jsonl")}
        r = prof.review_metrics([agent("r22a"), agent("r22b")], [], set())  # both briefs parse to round 1
        self.assertEqual([v["round"] for v in r["prs"][0]["verdicts"]], [1, 2])

    def test_review_events_are_read_in_place_of_the_reviewer_transcripts(self):
        ev = lambda pr, outcome, **kw: {"kind": "review", "ref": pr, "outcome": outcome, **kw}
        events = [ev(60, "request-changes", round=1, blocking=2, non_blocking=1, suite="pass"),
                  ev(60, "incomplete", round=1), ev(60, "approve", round=2, blocking=0, non_blocking=0, suite="pass 12/12"),
                  ev(61, "approve", blocking=0, non_blocking=3)]
        sub = self.fx.main.with_suffix("") / "subagents"
        # The transcript of #60's reviewer says approve in one round; the log says two rounds and wins. #62 has no events.
        agent = lambda name, pr: {"role": "reviewer", "id": name, "pr": pr, "ticket": None, "round": 1,
                                  "transcript": prof.Transcript(sub / f"agent-{name}.jsonl")}
        r = prof.review_metrics([agent("r21", 60), agent("r22a", 62)], [], {60, 61}, events)
        prs = {x["pr"]: x for x in r["prs"]}
        self.assertEqual((prs[60]["rounds"], prs[60]["round1"]), (2, "changes"))
        self.assertEqual([v["suite"] for v in prs[60]["verdicts"]], ["pass", "pass 12/12"])
        self.assertEqual((prs[61]["rounds"], prs[61]["verdicts"][0]["minor"]), (1, 3))  # a PR with only events, no reviewer agent
        self.assertEqual(prs[62]["rounds"], 1)  # no events: read from its transcript
        self.assertEqual(r["unparsed_reviewers"], 0)

    def test_usage_rounds_and_round_1_approvals_split_by_worker_model(self):
        wm = self.m["worker_models"]
        self.assertEqual((wm["haiku"]["workers"], wm["sonnet"]["workers"]), (2, 3))
        # A ticket is credited to the model that finished it: #20 to Sonnet, #21 to Haiku.
        self.assertEqual((wm["haiku"]["tickets"], wm["sonnet"]["tickets"]), (1, 3))
        # Rounds go to the finishing model; round 1 to the model that started the ticket (#60 began on Haiku).
        self.assertEqual((wm["haiku"]["rounds"], wm["haiku"]["round1_approvals"], wm["haiku"]["round1_changes"]), (1, 1, 1))
        self.assertEqual((wm["sonnet"]["rounds"], wm["sonnet"]["round1_approvals"], wm["sonnet"]["round1_changes"]), (5, 1, 1))
        self.assertGreater(wm["haiku"]["cost"], 0)
        self.assertEqual(wm["haiku"]["tokens"], (6000 + 30 + 500 + 10) + (300_000 + 20 + 500 + 10))

    def test_requests_context_and_tool_output_per_worker_split_by_model(self):
        h = self.m["worker_models"]["haiku"]
        self.assertEqual((h["requests"], h["requests_median"]), (4 + 3, 3.5))  # each worker: its requests plus the one that opened the PR
        self.assertEqual(h["context_median"], statistics.median([1000, 3000, 2000, 500, 100_000, 200_000, 500]))
        self.assertEqual(h["context_p90"], 200_000)
        # Tool output is estimated at 4 characters per token, the PR URL's 30 characters included.
        self.assertEqual(h["tool_output_tokens"], (4030 // 4) + (830 // 4))
        w21 = {a["id"]: a for a in self.p["agents"]}["w21"]
        self.assertEqual((w21["requests"], w21["context_median"], w21["tool_output_tokens"]), (3, 100_000, 830 // 4))

    def test_depth_2_agent_joined_by_tool_use_id_is_a_defect(self):
        (d,) = self.m["depth2"]
        self.assertEqual((d["parent"], d["ticket"], d["tool_use_id"], d["agent"], d["subagent_type"]),
                         ("w22", 22, "d1", "d1x", "orchestrate:scout"))
        self.assertGreater(d["tokens"], 0)
        self.assertTrue(any("depth 2" in w for w in self.p["warnings"]))

    def test_viewer_skips_depth_2_without_the_key(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        path = Path(self.tmp.name) / "old.json"
        run = lambda f: subprocess.run(["node", str(Path(__file__).parent / "viewer_quality.js"), str(prof.VIEWER), str(f)],
                                       capture_output=True, text=True)
        # Reviews present but no depth-2 key: that section is skipped, not reported as clean.
        del self.p["metrics"]["depth2"]
        path.write_text(json.dumps(self.p))
        out = run(path)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("Depth-2", out.stdout)

    def test_clean_session_has_no_depth_2_escalations_or_reviews(self):
        with tempfile.TemporaryDirectory() as d:
            m = Fixture(Path(d)).run()["metrics"]
        self.assertEqual((m["depth2"], m["escalations"]["count"], m["review"]["reviewed_prs"]), ([], 0, 0))
        self.assertIsNone(m["review"]["round1_changes_rate"])


class OrchestratorFixture(Fixture):
    """The base session plus one orchestrator turn for each job, compactions, hand-backs and an events log."""

    def __init__(self, root: Path):
        super().__init__(root)
        opus = "claude-opus-5-5"
        big = {"input_tokens": 0, "output_tokens": 100, "cache_read_input_tokens": 99_000, "cache_creation_input_tokens": 1_000}
        bash = lambda i, cmd: [tool_use(i, "Bash", command=cmd)]
        extra = [
            assistant(42, "a1", opus, [tool_use("m1", "Monitor", command="anything")], big),
            assistant(42, "a2", opus, bash("m2", '"${CLAUDE_SKILL_DIR}/../../scripts/watch-ci" 50')),
            assistant(43, "a3", opus, bash("m3", "python3 ~/.claude/plugins/orchestrate/scripts/gate 50")),
            assistant(43, "a4", opus, bash("m4", "gh pr diff 50")),  # regex fallback
            assistant(43, "a5", opus, bash("m5", "scripts/rebuild-state sess --repo .")),
            assistant(44, "a6", opus, bash("m6", "scripts/merge 50 10")),
            assistant(44, "a7", opus, bash("m7", "git worktree remove ../w")),  # regex fallback
            assistant(45, "a8", opus, [tool_use("m8", "Agent", subagent_type="orchestrate:scout", description="where is X")]),
            assistant(45, "a9", opus, [tool_use("m9", "SendMessage", to="w1", message="fix")]),
            assistant(45, "a10", opus, bash("m10", "gh issue list --label ready-for-agent")),
            assistant(46, "a11", opus, [{"type": "text", "text": "x" * 40}]),
            assistant(46, "a12", opus, [{"type": "thinking", "thinking": "..."}]),
            assistant(46, "a13", opus, bash("m13", "ls")),
            assistant(47, "a14", opus, [tool_use("m14", "Write", file_path=str(self.repo / ".claude" / "orchestrate-state.json"), content="{}")]),
            tool_result(46, "m13", "o" * 80),
            tool_result(46, "u3", "r" * 400),
            # Real shape: the report arrives as a message from the agent's ID; a continued agent sends more than one.
            {"type": "user", "timestamp": at(47), "message": {"content": 'Another Claude session sent a message:\n<agent-message from="aaa">\n' + "w" * 770 + "\n</agent-message>"}},
            {"type": "user", "timestamp": at(48), "message": {"content": '<agent-message from="aaa">' + "w" * 400}},
            # The completion notice has only a stub, and an unknown agent is not ours: neither counts.
            {"type": "user", "timestamp": at(47), "message": {"content": "<task-notification><tool-use-id>u1</tool-use-id><result>delivered as a message</result></task-notification>"}},
            {"type": "user", "timestamp": at(47), "message": {"content": '<agent-message from="zzz">' + "w" * 4000}},
            {"type": "system", "subtype": "compact_boundary", "timestamp": at(44), "compactMetadata": {"trigger": "auto", "preTokens": 150_000}},
            {"type": "system", "subtype": "compact_boundary", "timestamp": at(46), "compactMetadata": {"trigger": "manual", "preTokens": 90_000}},
            {"type": "system", "subtype": "compact_boundary", "timestamp": at(48), "compactMetadata": {"trigger": "auto", "preTokens": 170_000}},
        ]
        with open(self.main, "a") as f:
            f.writelines(json.dumps(r) + "\n" for r in extra)


class OrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = OrchestratorFixture(Path(self.tmp.name))

    def tearDown(self):
        os.environ.pop("ORCHESTRATE_USAGE_LOG", None)
        self.tmp.cleanup()

    def test_turns_by_job_keyed_by_tool_and_plugin_script(self):
        jobs = self.fx.run()["metrics"]["jobs"]["by_job"]
        turns = {k: v["turns"] for k, v in jobs.items()}
        # Base session: three worker dispatches and a reviewer (dispatch), `gh pr merge`, `gh issue edit`.
        self.assertEqual(turns, {
            "ci_wait": 2, "review_posting": 2, "state_file": 2, "merge_cleanup": 3, "dispatch": 5, "send_message": 1,
            "tracker": 2, "end_of_wake": 1, "no_tool_call": 1, "other": 1})

    def test_jobs_report_turns_and_proxy_usd_per_merged_ticket(self):
        m = self.fx.run()["metrics"]
        job = m["jobs"]["by_job"]["dispatch"]
        self.assertEqual(m["tickets_merged"], 1)
        self.assertEqual(job["turns_per_merged_ticket"], 5)
        # Each default turn is 10 input, 100 output, 1000 cache read and 50 cache write tokens at Opus 5.5 rates.
        one = (10 * 4 + 100 * 20 + 1000 * 0.20 + 50 * 5) / 1e6
        self.assertAlmostEqual(job["usd_per_merged_ticket"], 5 * one)
        # The 100k-context Monitor turn costs more than a default one.
        self.assertGreater(m["jobs"]["by_job"]["ci_wait"]["usd"], 2 * one)
        self.assertAlmostEqual(sum(j["usd"] for j in m["jobs"]["by_job"].values()), m["jobs"]["usd"])
        self.assertEqual(sum(j["turns"] for j in m["jobs"]["by_job"].values()), m["jobs"]["turns"])

    def test_context_per_turn_with_compactions_and_the_effective_window(self):
        c = self.fx.run()["metrics"]["context"]
        self.assertEqual(c["turns"][7]["context"], 1060)  # a default turn: 10 + 1000 + 50
        self.assertEqual(max(t["context"] for t in c["turns"]), 100_000)
        self.assertEqual([x["trigger"] for x in c["compactions"]], [None, "auto", "manual", "auto"])
        self.assertEqual([x["pre_tokens"] for x in c["compactions"]], [None, 150_000, 90_000, 170_000])
        # Only auto compactions count, and one with no recorded size is skipped.
        self.assertEqual(c["effective_window"], 160_000)

    def test_no_auto_compaction_means_no_effective_window(self):
        with tempfile.TemporaryDirectory() as d:
            c = Fixture(Path(d)).run()["metrics"]["context"]
        self.assertIsNone(c["effective_window"])

    def test_handback_own_text_and_bash_output_tokens_are_char_estimates(self):
        c = self.fx.run()["metrics"]["context"]
        self.assertEqual(c["handback_tokens"], {"reviewer": {"messages": 1, "tokens": 100}, "worker": {"messages": 2, "tokens": (788 + 400) // 4}})
        self.assertEqual((c["own_text_tokens"], c["bash_output_tokens"]), (10, 20))
        self.assertEqual(c["estimate"], "chars / 4")

    def test_idle_wakes_are_passes_that_did_nothing(self):
        self.fx.passes([
            {"ts": at(5), "dispatched": [10, 11]},
            {"ts": at(21)},   # dispatched a reviewer at 20
            {"ts": at(32)},   # merged PR 50 at 30
            {"ts": at(40)},   # nothing since 32
            {"ts": at(50), "dispatched": [], "merged": []},
        ])
        self.assertEqual(self.fx.run()["metrics"]["context"]["idle_wakes"], {"passes": 5, "idle": 2})

    def test_gate_failures_ci_merges_and_handback_rate_from_events(self):
        ev = lambda kind, outcome, ref=None, session=SESSION, **kw: {"ts": at(10), "session": session, "kind": kind, "ref": ref, "outcome": outcome, **kw}
        self.fx.events([
            ev("gate", "fail", 50, failed=["title follows Conventional Commits"]),
            ev("gate", "unknown", 50, failed=[], behind_by=None),
            ev("gate", "pass", 50),
            ev("gate", "fail", 99, failed=["verify"]),
            ev("gate", "pass", 50, session="other"),
            ev("gate", "pass", 50, session=None),  # no session, inside the span: counts
            ev("ci", "fail", 50), ev("ci", "pass", 50),
            ev("merge", "merged", 50), ev("merge", "timeout", 51), ev("merge", "closed", 52),
            ev("replay", "line-replay", 50), ev("replay", "word-merge", 50),
            ev("replay", "regenerated", 50), ev("replay", "handed-back", 50),
        ])
        p = self.fx.run()
        e = p["metrics"]["events"]
        self.assertTrue(e["available"])
        # Gate failures are their own count: the review round count is untouched.
        self.assertEqual((e["gate"]["runs"], e["gate"]["pass"], e["gate"]["fail"], e["gate"]["unknown"]), (5, 2, 2, 1))
        self.assertEqual(e["gate"]["failed_checks"], {"title follows Conventional Commits": 1, "verify": 1})
        self.assertEqual(e["gate"]["review_rounds"], 0)
        self.assertEqual(p["metrics"]["review"]["rounds_total"], 0)
        self.assertEqual((e["ci"]["runs"], e["ci"]["by_outcome"]), (2, {"fail": 1, "pass": 1}))
        self.assertEqual((e["merges"], e["merges_by_outcome"]), (1, {"merged": 1, "timeout": 1, "closed": 1}))
        self.assertEqual((e["replay"]["events"], e["replay"]["handed_back"], e["replay"]["handback_rate"]), (4, 1, 0.25))
        tickets = {t["ticket"]: t for t in p["tickets"]}
        self.assertEqual((tickets[10]["gate_runs"], tickets[10]["gate_failed"]), (4, 1))  # PR 50
        self.assertEqual((tickets[11]["gate_runs"], tickets[11]["gate_failed"]), (0, 0))

    def test_no_events_log_is_reported_not_zero(self):
        p = self.fx.run()
        self.assertEqual(p["metrics"]["events"], {"available": False})
        self.assertTrue(all(t["gate_runs"] is None for t in p["tickets"]))

    def test_viewer_draws_the_orchestrator_card_and_survives_old_profiles(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        self.fx.events([{"ts": at(10), "session": SESSION, "kind": "replay", "ref": 50, "outcome": "handed-back"}])
        p = self.fx.run()
        path = Path(self.tmp.name) / "p.json"
        run = lambda: subprocess.run(["node", str(Path(__file__).parent / "viewer_quality.js"), str(prof.VIEWER), str(path), "orchestrator"],
                                     capture_output=True, text=True)
        path.write_text(json.dumps(p))
        out = run()
        self.assertEqual(out.returncode, 0, out.stderr)
        for want in ("Orchestrator turns by job", "Context per turn", "160k", "Shared-file hand-back rate", "100%"):
            self.assertIn(want, out.stdout)
        # Events missing alone, or no compactions, must not crash either.
        partial = json.loads(json.dumps(p))
        partial["metrics"]["events"] = {"available": False}
        partial["metrics"]["context"]["compactions"] = []
        partial["metrics"]["context"]["effective_window"] = None
        path.write_text(json.dumps(partial))
        out = run()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("events log", out.stdout)

    def gh_events(self):
        call = lambda minute, script, **kw: {"ts": at(minute), "session": SESSION, "kind": "gh_call", "ref": None,
                                             "script": script, "method": "GET", "write": False, "path": "graphql",
                                             "graphql": True, "attempt": 1, "outcome": "ok", "status": 200, "ms": 5, **kw}
        return [
            call(1, "gate", remaining=4000),
            call(2, "merge", write=True, method="PUT", graphql=False, path="repos/{owner}/{repo}/pulls/1/merge", remaining=3990),
            call(3, "merge", write=True, method="PUT", graphql=False, outcome="ratelimited", status=403, remaining=0, retry_after=7),
            call(3, "merge", write=True, method="PUT", graphql=False, attempt=2),
            {"ts": at(3), "session": SESSION, "kind": "gh_call", "script": "merge", "outcome": "wait", "seconds": 7.0, "shared": True},
            call(9, "watch-ci", remaining=3000),
        ]

    def test_github_calls_block_counts_per_pass_and_for_the_run(self):
        self.fx.passes([{"ts": at(5), "slots": 2, "free": 1}, {"ts": at(10), "slots": 2, "free": 1}])
        self.fx.events(self.gh_events())
        g = self.fx.run()["metrics"]["gh_calls"]
        self.assertTrue(g["available"])
        t = g["total"]
        self.assertEqual((t["calls"], t["writes"], t["graphql"], t["retries"], t["rate_limited"]), (5, 3, 2, 1, 1))
        self.assertEqual((t["by_script"], t["lowest_remaining"]), ({"merge": 3, "gate": 1, "watch-ci": 1}, 0))
        self.assertEqual((t["peak_writes_per_minute"], t["waited_s"], t["waited_shared_s"]), (2, 7.0, 7.0))  # the minute-2 and minute-3 writes are 60 s apart
        first, second = g["passes"]
        self.assertEqual((first["pass"], first["calls"], first["writes"]), (1, 4, 3))
        self.assertEqual((second["pass"], second["calls"], second["writes"]), (2, 1, 0))

    def test_agent_rows_are_split_from_script_rows(self):
        self.fx.passes([{"ts": at(5), "slots": 2, "free": 1}])
        agent = {"ts": at(2), "session": SESSION, "kind": "gh_call", "script": "agent:worker", "role": "worker", "write": False, "graphql": True}
        self.fx.events(self.gh_events() + [agent, dict(agent, role="reviewer", script="agent:reviewer"), dict(agent)])
        t = self.fx.run()["metrics"]["gh_calls"]["total"]
        self.assertEqual((t["agent_calls"], t["script_calls"], t["by_role"]), (3, 5, {"worker": 2, "reviewer": 1}))

    def test_github_calls_appear_in_the_summary(self):
        self.fx.passes([{"ts": at(5), "slots": 2, "free": 1}, {"ts": at(10), "slots": 2, "free": 1}])
        self.fx.events(self.gh_events())
        out = prof.summary(self.fx.run(), Path("x.json"))
        self.assertIn("GitHub calls: 5 calls, 3 writes", out)
        self.assertIn("top: merge 3, gate 1, watch-ci 1", out)

    def test_the_summary_stays_six_lines_however_many_passes_there_are(self):
        self.fx.passes([{"ts": at(m), "slots": 2, "free": 1} for m in range(2, 40)])
        self.fx.events(self.gh_events())
        out = prof.summary(self.fx.run(), Path("x.json"))
        self.assertEqual(len(out.splitlines()), 6)
        self.assertGreater(len(self.fx.run()["metrics"]["gh_calls"]["passes"]), 1)

    def test_graphql_points_are_summed_per_pass_and_printed(self):
        # A third pass made no calls (it counts in N); a call after the last record is not a pass; `True` is not a cost.
        self.fx.passes([{"ts": at(5), "slots": 2, "free": 1}, {"ts": at(10), "slots": 2, "free": 1}, {"ts": at(20), "slots": 2, "free": 1}])
        events = self.gh_events()
        events.append({"ts": at(30), "session": SESSION, "kind": "gh_call", "script": "gate", "graphql": True, "cost": 50})
        events.append({"ts": at(31), "session": SESSION, "kind": "gh_call", "script": "gate", "graphql": True, "cost": True})
        events[0]["cost"] = 7
        events[5]["cost"] = 3
        events[1]["cost"] = 99  # a REST call: not counted
        self.fx.events(events)
        p = self.fx.run()
        g = p["metrics"]["gh_calls"]
        self.assertEqual([r["graphql_points"] for r in g["passes"]], [7, 3, 50])
        self.assertEqual(g["total"]["graphql_points"], 60)
        self.assertIn("graphql points per pass: avg 3, max 7 (total 10 over 3 passes)", prof.summary(p, Path("x.json")))

    def test_no_points_line_when_the_log_has_no_costs(self):
        self.fx.passes([{"ts": at(5), "slots": 2, "free": 1}])
        self.fx.events(self.gh_events())
        self.assertNotIn("graphql points", prof.summary(self.fx.run(), Path("x.json")))

    def test_an_older_events_log_without_gh_calls_is_not_recorded(self):
        self.fx.events([MERGED_50])
        p = self.fx.run()
        self.assertEqual(p["metrics"]["gh_calls"], {"available": False})
        self.assertIn("GitHub calls: not recorded", prof.summary(p, Path("x.json")))


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

    def run_profile(self, *args):
        return subprocess.run([sys.executable, "-I", str(SCRIPTS / "profile.py"), *args],
                              capture_output=True, text=True, env=self.env)

    def record_repo(self, slots=2):
        """A git repo whose config has `slots` slots and whose snapshot holds #1 (claimed, no worker: stalled) and the
        takeable #2 and #3."""
        repo = self.root / "repo"
        (repo / ".claude").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        (repo / ".claude" / "orchestration.md").write_text(f"## Slots\n\n- **Slots:** {slots}.\n")

        def issue(n, claimed):
            return {"number": n, "title": f"t{n}", "labels": ["ready-for-agent"], "claimed": claimed, "blockers": [],
                    "sub_issues": {"total": 0, "completed": 0}, "body": "", "parent": None}
        (repo / ".claude" / "orchestrate-snapshot.json").write_text(json.dumps(
            {"repo": {}, "issues": [issue(1, True), issue(2, False), issue(3, False)], "prs": [], "merged": [], "abandoned": []}))
        return repo

    def record(self, repo, *args):
        return self.run_profile("record", "--repo", str(repo), "--session", "s", *args)

    def rows(self, repo):
        path = runlog.log_path(repo, runlog.PASS_LOG)
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def record_with_lookup(self, repo, lookup):
        from unittest import mock
        import repo as repo_lib
        with mock.patch.object(repo_lib, "lead_session", lookup):
            prof.cmd_record(["--repo", str(repo), "--session", "s", "--idle", "overlap=1"])
        return self.rows(repo)[-1]

    def test_record_writes_the_lead_session_or_null(self):
        repo = self.record_repo()
        self.assertEqual(self.record_with_lookup(repo, lambda r: ("lead-uuid", "one live lead"))["lead"], "lead-uuid")
        self.assertIsNone(self.record_with_lookup(repo, lambda r: (None, "no live session named lead"))["lead"])

        def boom(r):
            raise OSError("claude agents failed")
        self.assertIsNone(self.record_with_lookup(repo, boom)["lead"])  # a failed lookup is lead mode off, never a stop

    def test_record_names_the_waiting_reason_in_claimed(self):
        repo = self.record_repo()
        runlog.append_event(repo, runlog.WORKER_REPORT, "valid", ref="1", session="s", status="stopped",
                            stop_reason="permission-denied", denied_command="gh pr create")
        r = self.record(repo, "--idle", "overlap=1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.rows(repo)[-1]["claimed"], {"1": "waiting permission-denied"})

    def test_record_derives_claimed_slots_free_takeable_and_greedy(self):
        repo = self.record_repo()
        r = self.record(repo, "--idle", "overlap=1", "--dispatched", "2", "--merged", "9", "--suggestion", "2 → 3")
        self.assertEqual((r.returncode, r.stdout), (0, "next: recorded\n"), r.stderr)
        (row,) = self.rows(repo)
        self.assertEqual({k: row[k] for k in ("scope", "slots", "free", "claimed", "greedy", "idle", "dispatched", "merged", "suggestion",
                                              "takeable_waiting", "limit_hit", "reset_at")},
                         {"scope": "full", "slots": 2, "free": 1, "claimed": {"1": "stalled"}, "greedy": False, "idle": {"overlap": 1},
                          "dispatched": [2], "merged": [9], "suggestion": "2 → 3",
                          "takeable_waiting": 1,  # #3; #2 was dispatched this pass, though the snapshot still shows it
                          "limit_hit": False, "reset_at": None})
        self.assertEqual((row["session"], "ts" in row), ("s", True))
        # Flags the config does not hold: the greedy cap and the usage limit.
        self.assertEqual(self.record(repo, "--greedy", "--slots", "4", "--idle", "empty=3", "--limit-hit",
                                     "--reset-at", "2026-11-10T15:00:00Z").returncode, 0)
        row = self.rows(repo)[-1]
        self.assertEqual((row["slots"], row["free"], row["greedy"], row["limit_hit"], row["reset_at"]),
                         (4, 3, True, True, "2026-11-10T15:00:00Z"))

    def test_a_wake_may_omit_idle_and_a_nudge_rides_on_the_record(self):
        repo = self.record_repo()
        self.assertEqual(self.record(repo, "--scope", "wake", "--nudged", "1").returncode, 0)
        (row,) = self.rows(repo)
        self.assertNotIn("idle", row)
        self.assertEqual((row["scope"], row["nudged"]), ("wake", [1]))

    def test_record_nudged_alone_writes_only_the_nudge(self):
        repo = self.record_repo()
        self.assertEqual(self.record(repo, "--nudged", "5").returncode, 0)
        (row,) = self.rows(repo)
        self.assertEqual((row["scope"], row["nudged"]), ("nudge", [5]))

    def test_record_refuses_what_it_does_not_understand_in_one_line_and_appends_nothing(self):
        repo = self.record_repo()
        for args in (["--idle", "overlap=2"],  # one free slot, two idle
                     [],  # a full pass names why its free slot is free
                     ["--idle", "bored=1"],
                     ["--idle", "overlap"],
                     ["--idle", "overlap=1", "overlap=1"],
                     ["--scope", "weekly", "--idle", "overlap=1"],
                     ["--greedy", "--idle", "overlap=1"],  # the cap is in no file
                     ['{"scope": "full"}']):  # the JSON positional is gone
            r = self.record(repo, *args)
            self.assertEqual(r.returncode, 2, (args, r.stderr))
            self.assertEqual(r.stdout, "next: could-not-run\n", args)  # a program error is never a decision
            self.assertIn("profile", r.stderr.strip().splitlines()[-1], args)  # ours, or argparse's usage error
            self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.rows(repo), [])

    def test_record_on_a_rate_limit_ends_retry_after_with_the_reset_time(self):
        repo = self.record_repo()
        (repo / ".claude" / "orchestrate-snapshot.json").unlink()  # no snapshot: the tracker is read, and gh is limited
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text("#!/bin/sh\necho 'gh: You have exceeded a secondary rate limit. (HTTP 403)' >&2\n"
                      "echo 'retry-after: 300' >&2\nexit 1\n")
        gh.chmod(0o755)
        self.env.update(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", XDG_CACHE_HOME=str(self.root / "cache"))
        r = self.record(repo, "--idle", "overlap=1")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertRegex(r.stdout, r"^next: retry-after \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n$")
        self.assertEqual(self.rows(repo), [])

    def test_record_refuses_when_there_is_no_slot_count(self):
        repo = self.record_repo()
        (repo / ".claude" / "orchestration.md").unlink()
        r = self.record(repo, "--idle", "empty=0")
        self.assertEqual((r.returncode, len(r.stderr.strip().splitlines())), (2, 1), r.stderr)
        self.assertIn("no slot count", r.stderr)
        self.assertEqual(self.record(repo, "--slots", "2", "--idle", "empty=1").returncode, 0)

    def test_unknown_session_could_not_run(self):
        r = self.run_profile("no-such-session")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("no transcript", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

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

    def test_missing_viewer_writes_only_the_json_with_one_stderr_line(self):
        # A plugin copy without viewer/ (#628): the scripts copied beside no viewer directory.
        fx = Fixture(self.root)
        self.env["ORCHESTRATE_USAGE_LOG"] = str(fx.usage)
        bare = self.root / "bare-plugin"
        shutil.copytree(SCRIPTS, bare / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        r = subprocess.run([sys.executable, "-I", str(bare / "scripts" / "profile.py"), SESSION],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, r.stderr)
        self.assertIn(str(bare / "viewer" / "profile.html"), lines[0])
        self.assertIn("only the JSON", lines[0])
        self.assertIn("2 slots", r.stdout)  # the summary still prints
        saved = fx.repo / prof.PROFILES / f"{at(1)[:10]}-{SESSION[:8]}.json"
        self.assertTrue(saved.is_file())
        self.assertFalse(saved.with_suffix(".html").exists())

    def test_viewer_shows_a_banner_for_rejected_files(self):
        html = prof.VIEWER.read_text()
        self.assertIn("regenerate it with scripts/profile.py", html)
        self.assertIn(f"data.schema !== {prof.SCHEMA}", html)
        self.assertNotIn("This profile predates", html)
        self.assertNotIn("console.warn", html)

    def test_html_escapes_script_close(self):
        out = self.root / "x.html"
        prof.write_html({"description": "</script><b>"}, out)
        self.assertNotIn("</script><b>", out.read_text())


class CompareTest(unittest.TestCase):
    """`profile.py compare` on hand-made profiles (the saved JSON is its input)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def profile(self, name, tiers, orchestrator=0.5, changes=(10, 20), outright=5, rounds=1.5, schema=3, passes=3, span_h=4.0):
        """tiers: {tier: (merged tickets, $ per merged ticket)}. The orchestrator's $ is the only overhead."""
        merged = sum(n for n, _ in tiers.values())
        tier_rows = {t: {"merged": n, "cost": n * c, "reviewed_prs": n, "rounds": round(n * rounds),
                         "round1": n, "round1_changes": round(n * changes[0] / changes[1]),
                         "round1_outright": round(n * outright / changes[1])}
                     for t, (n, c) in tiers.items()}
        per = sum(r["cost"] for r in tier_rows.values()) / merged + orchestrator
        path = self.root / name
        path.write_text(json.dumps({"schema": schema, "metrics": {
            "context": {"idle_wakes": {"passes": passes, "idle": 0}},
            "tickets_merged": merged,
            "throughput_per_hour": merged / span_h,
            "cost": {"per_merged_ticket": per, "per_merged_ticket_by_role": {"orchestrator": orchestrator}},
            "review": {"round1_changes_rate": changes[0] / changes[1], "round1_outright_rate": outright / changes[1],
                       "rounds_per_pr": rounds},
            "tiers": tier_rows}}))
        return path

    def compare(self, before, after, *args):
        return subprocess.run([sys.executable, "-I", str(SCRIPTS / "profile.py"), "compare", str(before), str(after), *args],
                              capture_output=True, text=True)

    def line(self, out, start):
        return next(l for l in out.splitlines() if l.strip().startswith(start))

    def gate(self, out, start):
        """The row under the gating header: the figure as measured, its verdict, then mix-adjusted."""
        return self.line(out[out.index("gating,"):], start)

    def with_stops(self, path, median, count, open_=0, oldest=None):
        data = json.loads(path.read_text())
        data["metrics"]["stops"] = {"count": count, "open": open_, "median_s": median, "total_s": 0, "oldest_open_s": oldest}
        path.write_text(json.dumps(data))
        return path

    def with_lead(self, path, lead_id, per_ticket, outside):
        data = json.loads(path.read_text())
        c = data["metrics"]["cost"]
        c["per_merged_ticket"] += per_ticket
        c["per_merged_ticket_by_role"]["lead"] = per_ticket
        data["lead"] = lead_id
        data["metrics"]["lead"] = {"outside_window_usd": outside}
        path.write_text(json.dumps(data))
        return path

    def test_compare_of_a_lead_run_against_a_lead_null_run_prints_the_lead_row_and_both_modes(self):
        before = self.profile("b.json", {"M": (20, 1.0)})
        after = self.with_lead(self.profile("a.json", {"M": (20, 1.0)}), "abcdef1234", 0.4, 7.5)
        r = self.compare(before, after)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(no lead) → after a.json (lead abcdef12)", r.stdout)
        row = self.line(r.stdout, "lead $ per merged ticket")
        self.assertEqual(row.split()[-3:], ["$0.00", "$0.40", "+$0.40"])
        self.assertLess(r.stdout.index("orchestrator $ per merged ticket"), r.stdout.index("lead $ per merged ticket"))
        self.assertIn("after: lead outside the window: $7.50, not in $ per merged ticket", r.stdout)
        self.assertNotIn("before: lead outside", r.stdout)
        self.assertNotIn("INCONCLUSIVE", r.stdout)  # a mode mismatch is not inconclusive

    def test_the_orchestrator_gate_ignores_lead_cost_and_the_cost_gate_includes_it(self):
        before = self.profile("b.json", {"M": (20, 1.0)})
        after = self.with_lead(self.profile("a.json", {"M": (20, 1.0)}), "abcdef1234", 0.4, 0.0)  # orchestrator $0.50, total $1.90
        r = self.compare(before, after, "--max-orchestrator-cost", "0.60", "--max-cost", "1.60")
        self.assertIn("pass", self.gate(r.stdout, "orchestrator $ per merged ticket"))
        self.assertIn("FAIL", self.gate(r.stdout, "proxy $ per merged ticket"))
        # The lead's cost stays in overhead, which the mix adjustment carries over unchanged.
        res = prof.compare(prof.load_profile(before), prof.load_profile(after))
        self.assertAlmostEqual(res["after"]["overhead"], 0.9)

    def test_compare_prints_the_median_stop_wait_row_and_a_stop_line_per_side(self):
        before = self.with_stops(self.profile("b.json", {"M": (20, 1.0)}), 1800, 4)
        after = self.with_stops(self.profile("a.json", {"M": (20, 1.0)}), 600, 3, 1, 7200)
        r = self.compare(before, after)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(self.line(r.stdout, "median stop wait"), r"30m\s+10m\s+-20m")
        self.assertIn("before: 4 stops", r.stdout)
        self.assertIn("after: 3 stops · 1 open at end, oldest 2.0h", r.stdout)

    def test_a_profile_saved_before_stops_were_measured_reads_as_no_stop_data(self):
        r = self.compare(self.profile("b.json", {"M": (20, 1.0)}), self.profile("a.json", {"M": (20, 1.0)}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(self.line(r.stdout, "median stop wait"), r"–\s+–\s+–")
        self.assertIn("before: no stop data", r.stdout)

    def test_headline_figures_side_by_side_with_deltas(self):
        before = self.profile("b.json", {"L": (20, 4.0), "M": (20, 1.0)}, orchestrator=1.0, changes=(10, 20), outright=6)
        after = self.profile("a.json", {"L": (20, 2.0), "M": (20, 0.5)}, orchestrator=0.5, changes=(6, 20), outright=8, rounds=1.25)
        r = self.compare(before, after)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(self.line(r.stdout, "proxy $ per merged ticket"), r"\$3\.50\s+\$1\.75\s+-\$1\.75")
        self.assertRegex(self.line(r.stdout, "orchestrator $ per merged ticket"), r"\$1\.00\s+\$0\.50\s+-\$0\.50")
        self.assertRegex(self.line(r.stdout, 'round-1 "changes" rate'), r"50%\s+30%\s+-20 pts")
        self.assertRegex(self.line(r.stdout, "round-1 approvals (outright)"), r"30%\s+40%\s+\+10 pts")
        self.assertRegex(self.line(r.stdout, "rounds per PR"), r"1\.50\s+1\.25\s+-0\.25")
        self.assertRegex(self.line(r.stdout, "L-tier share"), r"50%\s+50%\s+\+0 pts")
        self.assertRegex(self.line(r.stdout, "tier L"), r"20\s+\$4\.00\s+20\s+\$2\.00")
        self.assertNotIn("INCONCLUSIVE", r.stdout)

    def test_throughput_row_is_informational_with_no_gate(self):
        before = self.profile("b.json", {"L": (20, 4.0), "M": (20, 1.0)}, span_h=8.0)
        after = self.profile("a.json", {"L": (20, 4.0), "M": (20, 1.0)}, span_h=4.0)
        out = self.compare(before, after).stdout
        self.assertRegex(self.line(out, "merges/h raw"), r"5\.00\s+10\.00\s+\+5\.00")
        self.assertNotIn("throughput", out[out.index("gating,"):])

    def test_targets_come_from_the_flags_not_from_code(self):
        before = self.profile("b.json", {"L": (20, 4.0), "M": (20, 1.0)})
        after = self.profile("a.json", {"L": (20, 2.0), "M": (20, 0.5)})
        none = self.compare(before, after).stdout
        self.assertIn("--max-cost", none)
        met = self.compare(before, after, "--max-cost", "2.30", "--max-orchestrator-cost", "0.75").stdout
        self.assertRegex(self.gate(met, "proxy $ per merged ticket"), r"pass\s+→.*pass")
        missed = self.compare(before, after, "--max-cost", "1.50").stdout
        self.assertRegex(self.gate(missed, "proxy $ per merged ticket"), r"FAIL")
        self.assertIn("gating", missed)

    def test_review_floor_is_judged_against_the_baseline(self):
        before = self.profile("b.json", {"L": (20, 4.0), "M": (20, 1.0)}, changes=(10, 20), outright=6, rounds=1.5)
        worse = self.profile("a.json", {"L": (20, 4.0), "M": (20, 1.0)}, changes=(14, 20), outright=3, rounds=1.8)
        out = self.compare(before, worse).stdout
        for label in ('round-1 "changes" rate', "round-1 approvals (counting", "rounds per PR"):
            self.assertIn("FAIL", self.gate(out, label))

    def test_fewer_than_20_merged_tickets_is_inconclusive(self):
        before = self.profile("b.json", {"L": (10, 4.0), "M": (10, 1.0)})
        after = self.profile("a.json", {"L": (9, 4.0), "M": (10, 1.0)})
        self.assertIn("INCONCLUSIVE", self.compare(before, after).stdout)
        after = self.profile("a.json", {"L": (10, 4.0), "M": (10, 1.0)})
        self.assertNotIn("INCONCLUSIVE", self.compare(before, after).stdout)

    def test_l_share_gap_over_25_points_is_inconclusive(self):
        before = self.profile("b.json", {"L": (20, 4.0), "M": (20, 1.0)})  # 50%
        near = self.profile("a.json", {"L": (30, 4.0), "M": (10, 1.0)})  # 75%: exactly 25 points
        self.assertNotIn("INCONCLUSIVE", self.compare(before, near).stdout)
        far = self.profile("a.json", {"L": (31, 4.0), "M": (9, 1.0)})  # 77.5%
        self.assertIn("25 points", self.compare(before, far).stdout)

    def test_mix_adjustment_reweights_to_the_baseline_mix_and_flags_a_flipped_verdict(self):
        before = self.profile("b.json", {"L": (10, 4.0), "M": (10, 1.0)})
        # 65% L against 50%: dearer overall only because of the mix. L $3.00, M $0.50 -> raw 2.625, mixed 2.25.
        after = self.profile("a.json", {"L": (13, 3.0), "M": (7, 0.5)})
        out = self.compare(before, after, "--max-cost", "2.30").stdout
        row = self.gate(out, "proxy $ per merged ticket")
        self.assertRegex(row, r"\$2\.62\s+FAIL\s+→\s+\$2\.25\s+pass")
        self.assertIn("INCONCLUSIVE", out)
        self.assertIn("mix", out)
        # With the same mix the adjustment changes nothing and nothing flips.
        same = self.profile("s.json", {"L": (10, 3.0), "M": (10, 0.5)})
        self.assertNotIn("INCONCLUSIVE", self.compare(before, same, "--max-cost", "2.30").stdout)

    def test_without_dollar_targets_the_conclusion_says_they_were_not_judged(self):
        before = self.profile("b.json", {"L": (20, 4.0), "M": (20, 1.0)})
        after = self.profile("a.json", {"L": (20, 4.0), "M": (20, 1.0)})
        out = self.compare(before, after).stdout
        self.assertIn("not judged: no target given", out)
        self.assertNotIn("not judged", self.compare(before, after, "--max-cost", "9", "--max-orchestrator-cost", "9").stdout)

    def test_untiered_tickets_and_unparsed_reviewers_are_printed(self):
        before = self.profile("b.json", {"L": (20, 4.0), "?": (5, 1.0)})
        out = self.compare(before, before).stdout
        self.assertIn("no tier", out)
        self.assertIn("parseable", out)

    def test_a_profile_without_tier_figures_is_refused(self):
        old = self.root / "old.json"
        old.write_text(json.dumps({"schema": 1, "metrics": {}}))
        new = self.profile("a.json", {"L": (20, 1.0)})
        r = self.compare(old, new)
        self.assertEqual(r.returncode, 2)
        self.assertIn("schema 1", r.stderr)

    def test_a_schema_2_profile_is_refused_on_either_side(self):
        old = self.profile("old.json", {"L": (20, 1.0)}, schema=2)
        new = self.profile("a.json", {"L": (20, 1.0)})
        for args in ((old, new), (new, old)):
            r = self.compare(*args)
            self.assertEqual(r.returncode, 2)
            self.assertIn("schema 2", r.stderr)
            self.assertIn("re-profile", r.stderr)

    def test_a_profile_with_no_pass_log_is_refused(self):
        bare = self.profile("bare.json", {"L": (20, 1.0)}, passes=0)
        new = self.profile("a.json", {"L": (20, 1.0)})
        r = self.compare(new, bare)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a profiled run", r.stderr)


class TierTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.tmp.name))

    def tearDown(self):
        os.environ.pop("ORCHESTRATE_USAGE_LOG", None)
        self.tmp.cleanup()

    def test_tier_comes_from_the_reviewer_dispatch_and_verdict_from_a_handback_call(self):
        sub = self.fx.main.with_suffix("") / "subagents"
        sonnet = "claude-sonnet-5-5"
        write_jsonl(sub / "agent-ccc.jsonl", [
            assistant(20, "r1", sonnet, [{"type": "text", "text": "verdict: approve (looking)"}]),
            assistant(21, "r2", sonnet, [tool_use("h1", "SubagentHandback", message="verdict: changes\ntier: L\n[blocking] x\n[minor] y")])])
        p = self.fx.run()
        tickets = {t["ticket"]: t for t in p["tickets"]}
        self.assertEqual(tickets[10]["tier"], "L")  # "Review PR 50 (L)"
        self.assertIsNone(tickets[11]["tier"])
        review = p["metrics"]["review"]
        self.assertEqual((review["round1_changes"], review["round1_outright"]), (1, 0))  # the call counts, the text doesn't
        self.assertEqual(p["metrics"]["tiers"]["L"]["merged"], 1)
        self.assertEqual(p["metrics"]["tiers"]["L"]["round1_changes"], 1)

    def handback(self, message):
        sub = self.fx.main.with_suffix("") / "subagents"
        write_jsonl(sub / "agent-ccc.jsonl", [assistant(20, "r1", "claude-sonnet-5-5", [tool_use("h1", "SubagentHandback", message=message)])])
        return self.fx.run()["metrics"]["review"]

    def test_a_posted_review_is_read_from_its_json_body_and_findings_count_once(self):
        # The shape agents/reviewer.md has a posted review hand back: the JSON that was posted, the
        # findings in the body and again, inline, in `comments`.
        body = ("Round 1 \u00b7 verdict: approve \u00b7 tier: L\n\nspec:\n- thing \u2192 met\n\nfindings:\n"
                "- [minor] [standards] a.ts:5 \u2014 add a why comment\n- [minor] [standards] b.ts:9 \u2014 rename\n\nposted: ok")
        message = json.dumps({"event": "COMMENT", "body": body, "comments": [
            {"path": "a.ts", "line": 5, "side": "RIGHT", "body": "[minor] add a why comment"},
            {"path": "b.ts", "line": 9, "side": "RIGHT", "body": "[minor] rename"}]})
        review = self.handback(message)
        pr = review["prs"][0]
        self.assertEqual([(v["round"], v["verdict"], v["blocking"], v["minor"]) for v in pr["verdicts"]], [(1, "approve", 0, 2)])
        self.assertEqual((review["unparsed_reviewers"], review["round1_outright"]), (0, 0))

    def test_posted_json_after_prose_is_still_read(self):
        message = 'Posted. Here is what I sent: {"event":"COMMENT","body":"Round 2 \\u00b7 verdict: changes \\u00b7 tier: L\\n\\nfindings:\\n- [blocking] a.ts:1 \\u2014 x","comments":[]}'
        v = self.handback(message)["prs"][0]["verdicts"][0]
        self.assertEqual((v["verdict"], v["blocking"]), ("changes", 1))

    def test_a_malformed_payload_does_not_fall_through_to_an_inline_comment(self):
        message = ('```json\n{"event":"COMMENT","body":"Round 1 · verdict: approve · tier: L\\n\\nfindings:\\n- [minor] a.ts:1 x","comments":'
                   '[{"path":"a.ts","line":1,"body":"[minor] x"}] oops')
        v = self.handback(message)["prs"][0]["verdicts"][0]
        self.assertEqual((v["verdict"], v["round"]), ("approve", 1))

    def test_a_posted_review_with_no_findings_is_an_outright_approval(self):
        review = self.handback(json.dumps({"event": "COMMENT", "body": "Round 1 \u00b7 verdict: approve \u00b7 tier: S\n\nfindings: none", "comments": []}))
        self.assertEqual(review["round1_outright"], 1)

    def test_a_reviewer_with_no_readable_verdict_is_counted(self):
        review = self.handback("I looked at it and it seems fine.")
        self.assertEqual((review["reviewed_prs"], review["unparsed_reviewers"]), (0, 1))

    def test_tier_is_read_from_prompts_and_descriptions(self):
        self.assertEqual(prof.dispatched_tier("Review PR 5. Tier: L (touches proto). Round: 1."), "L")
        self.assertEqual(prof.dispatched_tier("Tier M, round 2"), "M")
        self.assertEqual(prof.dispatched_tier("Review PR 5 (S, proto)"), "S")
        self.assertIsNone(prof.dispatched_tier("Review PR 5 round 2"))


class StopWaitTest(unittest.TestCase):
    """Stop wait by kind (#641). The base fixture: #10 (worker at 1, reviewer at 20, PR 50 merged at 30), #11 (worker at 1,
    handed back at 31), and the transcript ends at 41. `extra_dispatch` adds worker dispatches to the main transcript."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.tmp.name))

    def tearDown(self):
        os.environ.pop("ORCHESTRATE_USAGE_LOG", None)
        self.tmp.cleanup()

    def dispatch(self, minutes, description):
        with open(self.fx.main, "a") as f:
            f.write(json.dumps(assistant(minutes, f"d{minutes}", "claude-opus-5-5", [
                tool_use(f"ud{minutes}", "Agent", subagent_type="orchestrate:worker", description=description)])) + "\n")
            f.write(json.dumps({"type": "user", "timestamp": at(41)}) + "\n")  # the transcript still ends at 41

    def report(self, minutes, ticket, reason, **extra):
        return {"ts": at(minutes), "session": SESSION, "kind": "worker-report", "ref": ticket, "outcome": "valid",
                "status": "stopped", "stop_reason": reason, **extra}

    def stops(self, events=(), passes=()):
        self.fx.events([MERGED_50, *events])
        if passes:
            self.fx.passes(passes)
        return self.fx.run()["metrics"]["stops"]

    def only(self, st):
        self.assertEqual(len(st["items"]), 1, st["items"])
        return st["items"][0]

    def test_a_worker_report_starts_a_stop_that_a_reviewer_dispatch_ends(self):
        s = self.only(self.stops([self.report(5, 10, "held-path")]))
        self.assertEqual((s["ticket"], s["kind"], s["start"], s["end"], s["open"], s["wait_s"]), (10, "question", at(5), at(20), False, 900))

    def test_a_worker_dispatch_ends_a_stop(self):
        self.dispatch(15, "Build #11 again")
        s = self.only(self.stops([self.report(8, 11, "adr-conflict")]))
        self.assertEqual((s["kind"], s["end"], s["wait_s"]), ("adr-conflict", at(15), 420))

    def test_a_merge_ends_a_stop(self):
        s = self.only(self.stops([self.report(25, 10, "permission-denied")]))
        self.assertEqual((s["end"], s["wait_s"]), (at(30), 300))

    def test_a_close_ends_a_stop(self):
        s = self.only(self.stops([self.report(20, 11, "human-only")]))
        self.assertEqual((s["end"], s["wait_s"]), (at(31), 660))

    def test_a_waiting_pass_starts_a_stop_with_no_worker_report(self):
        st = self.stops(passes=[{"ts": at(22), "slots": 2, "free": 0, "idle": {}, "claimed": {"10": "waiting round-3"}}])
        s = self.only(st)
        self.assertEqual((s["kind"], s["start"], s["end"]), ("third-review-round", at(22), at(30)))

    def test_a_with_lead_pass_starts_a_question_stop(self):
        st = self.stops(passes=[{"ts": at(12), "slots": 2, "free": 1, "idle": {"empty": 1}, "claimed": {"11": "with-lead"}}])
        s = self.only(st)
        self.assertEqual((s["kind"], s["start"], s["end"]), ("question", at(12), at(31)))

    def test_later_signs_of_the_same_stop_are_one_stop(self):
        st = self.stops([self.report(5, 10, "ambiguous")], [
            {"ts": at(6), "slots": 2, "free": 0, "idle": {}, "claimed": {"10": "with-lead"}},
            {"ts": at(10), "slots": 2, "free": 0, "idle": {}, "claimed": {"10": "waiting question"}}])
        self.assertEqual(self.only(st)["start"], at(5))

    def test_a_second_stop_after_the_first_ended_counts_again(self):
        st = self.stops([self.report(5, 10, "held-path"), self.report(25, 10, "no-spec")])
        self.assertEqual([(s["start"], s["end"]) for s in st["items"]], [(at(5), at(20)), (at(25), at(30))])
        self.assertEqual((st["by_kind"]["question"]["count"], st["by_kind"]["question"]["median_s"]), (2, 600))

    def test_a_question_stop_that_unassigns_the_ticket_still_counts(self):
        # The pass shows no claim for #10 (a no-lead question unassigns it); the worker report is matched by ticket number.
        st = self.stops([self.report(5, 10, "no-primary-source")], [{"ts": at(6), "slots": 2, "free": 2, "idle": {"empty": 2}, "claimed": {}}])
        self.assertEqual(self.only(st)["wait_s"], 900)

    def test_each_kind_is_produced_and_a_plain_transient_or_worktree_gone_is_not_a_stop(self):
        events = [self.report(2, 10, "held-path"), self.report(22, 10, "permission-denied"),
                  self.report(3, 11, "transient", exhausted=True), self.report(5, 11, "worktree-gone"),
                  self.report(7, 11, "transient"), self.report(9, 11, "adr-conflict")]
        passes = [{"ts": at(26), "slots": 2, "free": 0, "idle": {}, "claimed": {"10": "waiting round-3"}},
                  {"ts": at(24), "slots": 2, "free": 0, "idle": {}, "claimed": {"10": "waiting gate"}}]
        st = self.stops(events, passes)
        kinds = {k: v["count"] for k, v in st["by_kind"].items()}
        # #10: question 2-20, permission-denied 22-30 (the passes at 24 and 26 are inside it); #11: transient exhausted 3-31
        # (the adr-conflict report at 9 is inside it).
        self.assertEqual(kinds, {"question": 1, "permission-denied": 1, "transient-exhausted": 1, "adr-conflict": 0,
                                 "third-review-round": 0, "gate": 0, "prompt": 0})
        events = [self.report(5, 10, "worktree-gone"), self.report(6, 10, "transient"), self.report(7, 11, "adr-conflict")]
        passes = [{"ts": at(21), "slots": 2, "free": 0, "idle": {}, "claimed": {"10": "waiting gate"}},
                  {"ts": at(31), "slots": 2, "free": 0, "idle": {}, "claimed": {}}]
        st = self.stops(events, passes)
        self.assertEqual({k: v["count"] for k, v in st["by_kind"].items() if v["count"]}, {"adr-conflict": 1, "gate": 1})

    def test_an_open_stop_is_out_of_the_median_and_in_the_total(self):
        self.dispatch(36, "Build #12 new")
        st = self.stops([self.report(5, 10, "held-path"), self.report(38, 12, "permission-denied")])
        self.assertEqual((st["count"], st["open"], st["median_s"]), (2, 1, 900))
        self.assertEqual((st["total_s"], st["oldest_open_s"]), (900 + 180, 180))  # the transcript ends at 41
        self.assertEqual(st["by_kind"]["permission-denied"], {"count": 1, "open": 1, "median_s": None, "total_s": 180})
        self.assertIn("1 open at end, oldest 3m", prof.summary(self.fx.run(), Path("x.json")))

    def test_no_stops_gives_an_empty_block(self):
        st = self.stops()
        self.assertEqual((st["count"], st["median_s"], st["items"]), (0, None, []))

    def viewer_card(self, profile):
        path = Path(self.tmp.name) / "q.json"
        path.write_text(json.dumps(profile))
        out = subprocess.run(["node", str(Path(__file__).parent / "viewer_quality.js"), str(prof.VIEWER), str(path), "stopWait"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_viewer_card_shows_a_prompt_only_run_and_keeps_the_empty_state(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        self.fx.events([MERGED_50])
        profile = self.fx.run()
        st = profile["metrics"]["stops"]
        self.assertEqual(st["count"], 0)
        self.assertIn("No stops in this session.", self.viewer_card(profile))
        st["by_kind"]["prompt"] = {"count": 1, "open": 0, "median_s": 474, "total_s": 474}
        card = self.viewer_card(profile)
        self.assertIn("Permission prompt", card)
        self.assertNotIn("No stops in this session.", card)

    def test_viewer_renders_the_tile_and_card_with_zero_stop_kinds_hidden(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        self.dispatch(36, "Build #12 new")
        self.stops([self.report(5, 10, "held-path"), self.report(38, 12, "permission-denied")])
        path = Path(self.tmp.name) / "p.json"
        path.write_text(json.dumps(self.fx.run()))
        run = lambda card: subprocess.run(["node", str(Path(__file__).parent / "viewer_quality.js"), str(prof.VIEWER), str(path), card],
                                          capture_output=True, text=True)
        tile, card = run("stopWaitTile"), run("stopWait")
        self.assertEqual((tile.returncode, card.returncode), (0, 0), tile.stderr + card.stderr)
        self.assertIn("Stop wait", tile.stdout)
        self.assertIn("15m", tile.stdout)
        self.assertIn("2 stops · 1 open at end", tile.stdout)
        self.assertIn("Question", card.stdout)
        self.assertIn("Permission denied", card.stdout)
        self.assertIn("Overall", card.stdout)
        self.assertIn("1 open at end, oldest 3m", card.stdout)
        for hidden in ("Gate", "ADR conflict", "Third review round", "Transient exhausted"):
            self.assertNotIn(hidden, card.stdout)
        cmp = run("compare")
        self.assertIn("Median stop wait", cmp.stdout)
        self.assertIn("2 stops · 1 open at end, oldest 3m", cmp.stdout)


class ClaimedStopKindTest(unittest.TestCase):
    def test_words(self):
        self.assertEqual([prof.claimed_stop_kind(w) for w in ("with-lead", "waiting gate", "waiting info", "waiting exhausted", "building", "paused limit", None)],
                         ["question", "gate", "question", "transient-exhausted", None, None, None])


class LeadTest(unittest.TestCase):
    """The lead and its scouts folded into the run's profile (#638). The run's window is minute 1 to 41."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fx = Fixture(self.root)
        self.lead = self.root / "projects" / "-repo" / "lead-1.jsonl"
        sonnet = "claude-sonnet-5-5"
        # Lead requests at minutes 0 and 45 are outside the window; 5 and 10 inside. Its scout: 8 inside, 50 outside.
        write_jsonl(self.lead, [assistant(m, f"l{m}", sonnet, []) for m in (0, 5, 10, 45)])
        write_agent(self.lead.with_suffix("") / "subagents", "sc1", [assistant(m, f"s{m}", sonnet, []) for m in (8, 50)],
                    {"agentType": "general-purpose", "toolUseId": "x"})
        self.one = prof.request_cost(sonnet, assistant(0, "x", sonnet, [])["message"]["usage"])

    def tearDown(self):
        os.environ.pop("ORCHESTRATE_USAGE_LOG", None)
        self.tmp.cleanup()

    def test_lead_cost_inside_the_window_is_in_per_ticket_and_the_rest_is_the_outside_figure(self):
        p = self.fx.run(lead=str(self.lead))
        c, lead = p["metrics"]["cost"], p["metrics"]["lead"]
        self.assertAlmostEqual(c["by_role"]["lead"], 2 * self.one)
        self.assertAlmostEqual(c["by_role"]["lead-scout"], self.one)
        self.assertAlmostEqual(lead["outside_window_usd"], 3 * self.one)
        # $ per merged ticket counts the inside cost and never the outside figure.
        base = self.fx.run()["metrics"]["cost"]
        self.assertAlmostEqual(c["total"] - base["total"], 3 * self.one)
        self.assertAlmostEqual(c["per_merged_ticket"] - base["per_merged_ticket"], 3 * self.one / p["metrics"]["tickets_merged"])
        self.assertAlmostEqual(lead["share"], 3 * self.one / c["total"])
        self.assertEqual(p["lead"], str(self.lead))
        self.assertEqual(p["metrics"]["tokens_by_role"]["lead"]["output"], 200)
        self.assertEqual(p["metrics"]["tokens_by_role"]["lead-scout"]["output"], 100)
        self.assertEqual(sorted(p["metrics"]["tokens_by_model"]), sorted(self.fx.run()["metrics"]["tokens_by_model"]))
        self.assertEqual(p["lead_layer"]["segments"], [[at(5), at(5)], [at(10), at(10)]])  # the request at 0 and 45 are clipped out
        self.assertEqual(len(p["lead_layer"]["scouts"]), 1)

    def test_a_resume_gap_inside_the_span_is_out_of_the_lead_window(self):
        self.fx.main.write_text(self.fx.main.read_text() + json.dumps(assistant(80, "late", "claude-opus-5-5", [])) + "\n")
        self.fx.events([{"ts": at(79), "session": SESSION, "kind": "resume", "ref": None, "outcome": "resumed"}])
        # The gap is minute 41 to 79: a lead request at 60 is in the span but not in the window.
        write_jsonl(self.lead, [assistant(m, f"l{m}", "claude-sonnet-5-5", []) for m in (5, 60)])
        p = self.fx.run(lead=str(self.lead))
        self.assertAlmostEqual(p["metrics"]["cost"]["by_role"]["lead"], self.one)
        self.assertAlmostEqual(p["metrics"]["lead"]["outside_window_usd"], 2 * self.one)  # the lead's 60 and the scout's 50, both in the gap
        self.assertEqual(p["metrics"]["lead"]["requests"], 2)  # the lead's 5 and the scout's 8, not the 60

    def test_the_lead_comes_from_the_pass_log_and_lead_null_records_no_lead_cost(self):
        self.fx.passes([{"ts": at(10), "slots": 2, "free": 1, "idle": {"empty": 1}, "lead": str(self.lead)}])
        named = self.fx.run()
        self.assertEqual(named["lead"], str(self.lead))
        self.assertGreater(named["metrics"]["cost"]["by_role"]["lead"], 0)
        self.fx.passes([{"ts": at(10), "slots": 2, "free": 1, "idle": {"empty": 1}, "lead": None}])
        none = self.fx.run()
        self.assertIsNone(none["lead"])
        self.assertIsNone(none["lead_layer"])
        self.assertNotIn("lead", none["metrics"]["cost"]["by_role"])
        self.assertEqual(none["metrics"]["lead"]["cost"], 0)
        self.assertEqual(none["metrics"]["lead"]["outside_window_usd"], 0)
        # The unmeasured-time statement is there in both modes.
        for p in (named, none):
            self.assertEqual(p["metrics"]["lead"]["developer_time"], "unmeasured")
            self.assertIn("not measured and is never estimated", prof.summary(p, Path("x.json")))

    def test_profiling_by_hand_with_lead_and_no_pass_log_folds_the_lead_in(self):
        out = subprocess.run([sys.executable, "-I", str(SCRIPTS / "profile.py"), str(self.fx.main), "--lead", str(self.lead), "--out", str(self.root / "o")],
                             capture_output=True, text=True, env={**os.environ, "ORCHESTRATE_USAGE_LOG": str(self.fx.usage)})
        self.assertEqual(out.returncode, 0, out.stderr)
        saved = json.loads(next((self.root / "o").glob("*.json")).read_text())
        self.assertGreater(saved["metrics"]["cost"]["by_role"]["lead"], 0)
        self.assertIn("outside it", out.stdout)

    def test_a_lead_without_a_transcript_warns_and_costs_nothing(self):
        p = self.fx.run(lead="no-such-lead")
        self.assertTrue(any("no transcript for lead" in w for w in p["warnings"]))
        self.assertNotIn("lead", p["metrics"]["cost"]["by_role"])

    def viewer(self, p, card):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        path = self.root / "p.json"
        path.write_text(json.dumps(p))
        out = subprocess.run(["node", str(Path(__file__).parent / "viewer_quality.js"), str(prof.VIEWER), str(path), card],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_the_viewer_renders_the_lead_layer_and_leaves_it_out_for_no_lead(self):
        p = self.fx.run(lead=str(self.lead))
        p["lead"] = "abcdef123456"
        self.assertIn("lead abcdef12", self.viewer(p, "subtitleText"))
        self.assertRegex(self.viewer(p, "costTile"), r"lead \d+%")
        cost = self.viewer(p, "cost")
        self.assertIn("Lead outside the window", cost)
        self.assertIn("not in $ per merged ticket", cost)
        self.assertIn("Lead layer", cost)
        self.assertIn("lead-scout", cost)
        self.assertIn("Lead layer", self.viewer(p, "tokens"))
        timeline = self.viewer(p, "timeline")
        self.assertIn("Lead layer", timeline)
        compared = self.viewer(p, "compare")
        self.assertIn("lead abcdef12", compared)
        self.assertIn("Lead $ / ticket", compared)
        self.assertIn("lead outside the window", compared)
        none = self.fx.run()
        self.assertIn("no lead", self.viewer(none, "compare"))
        self.assertNotIn("lead outside", self.viewer(none, "compare"))
        self.assertIn("no lead", self.viewer(none, "subtitleText"))
        self.assertNotIn("lead", self.viewer(none, "costTile"))
        for card in ("cost", "tokens", "timeline"):
            out = self.viewer(none, card)
            self.assertNotIn("outside the window", out)
            self.assertNotIn("Lead layer", out)
        self.assertIn("never estimated", self.viewer(none, "cost"))


if __name__ == "__main__":
    unittest.main()
