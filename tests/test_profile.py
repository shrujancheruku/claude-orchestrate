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

    def test_an_idle_claimed_ticket_counts_its_slot_seconds_as_worker_idle(self):
        self.fx.passes([
            {"ts": at(1), "slots": 2, "free": 1, "claimed": {"1": "building", "2": "idle"}, "idle": {"empty": 1}},
            {"ts": at(31), "slots": 2, "free": 2, "claimed": {}, "idle": {"empty": 2}},
        ])
        idle = self.fx.run()["metrics"]["idle_s"]
        self.assertEqual(idle["worker-idle"], 1800)

    def test_a_nudge_record_is_not_a_pass(self):
        import subprocess
        self.fx.passes([{"ts": at(1), "slots": 2, "free": 0, "idle": {}}])
        subprocess.run([sys.executable, str(SCRIPTS / "profile.py"), "record", "--repo", str(self.fx.repo),
                        "--session", SESSION, "--nudged", "7"], check=True)
        self.assertEqual(len(runlog.read_passes(self.fx.repo, SESSION)), 1)
        raw = [json.loads(l) for l in runlog.log_path(self.fx.repo, runlog.PASS_LOG).read_text().splitlines()]
        self.assertEqual((raw[-1]["scope"], raw[-1]["nudged"]), ("nudge", [7]))

    def test_an_unchecked_idle_reason_round_trips_through_record_and_shows_in_the_summary(self):
        # A slot left empty because holds exited 2 is `unchecked`, not `limit` (which means a usage limit only).
        for ts, idle in ((at(1), {}), (at(31), {"unchecked": 1, "empty": 1})):
            subprocess.run([sys.executable, str(SCRIPTS / "profile.py"), "record", "--repo", str(self.fx.repo), "--session", SESSION,
                            json.dumps({"ts": ts, "scope": "wake", "slots": 2, "free": len(idle) and 2, "idle": idle})], check=True)
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
        # `record` merges the caller's JSON over ts/session, so a naive ts lands in the pass log verbatim.
        naive = at(5).replace("Z", "")
        self.assertNotIn("+", naive)
        subprocess.run([sys.executable, str(SCRIPTS / "profile.py"), "record", "--repo", str(self.fx.repo),
                        "--session", SESSION, json.dumps({"ts": naive, "slots": 2, "free": 0})], check=True)
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

    def test_schema_2_keeps_slot_efficiency_fields(self):
        p = self.fx.run()
        self.assertEqual(p["schema"], 2)
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

    def test_viewer_survives_a_profile_without_the_new_metrics(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        old = json.loads(json.dumps(self.p))
        for k in ("review", "worker_models", "escalations", "depth2"):
            del old["metrics"][k]
        path = Path(self.tmp.name) / "old.json"
        path.write_text(json.dumps(old))
        run = lambda f: subprocess.run(["node", str(Path(__file__).parent / "viewer_quality.js"), str(prof.VIEWER), str(f)],
                                       capture_output=True, text=True)
        out = run(path)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("predates", out.stdout)
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
        old = json.loads(json.dumps(p))
        for k in ("jobs", "context", "events"):
            del old["metrics"][k]
        path.write_text(json.dumps(old))
        out = run()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("predates", out.stdout)
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

    def test_record_appends_a_stamped_pass(self):
        repo = self.root / "repo"
        self.script("profile.py", "record", "--repo", str(repo), "--session", "s", '{"slots": 2, "free": 1}')
        self.script("profile.py", "record", "--repo", str(repo), "--session", "s", '{"slots": 2, "free": 0}')
        rows = [json.loads(line) for line in (runlog.log_path(repo, runlog.PASS_LOG)).read_text().splitlines()]
        self.assertEqual([(r["session"], r["free"]) for r in rows], [("s", 1), ("s", 0)])
        self.assertIn("ts", rows[0])
        self.script("profile.py", "record", "--repo", str(repo), "--session", "s", '{"slots": 5, "free": 2, "greedy": true}')
        self.assertIs(json.loads((runlog.log_path(repo, runlog.PASS_LOG)).read_text().splitlines()[-1])["greedy"], True)

    def run_profile(self, *args):
        return subprocess.run([sys.executable, "-I", str(SCRIPTS / "profile.py"), *args],
                              capture_output=True, text=True, env=self.env)

    def test_record_with_bad_json_could_not_run(self):
        repo = str(self.root / "repo")
        for bad in ("{nope", "[1]", '{"slots": 5, "greedy": "yes"}'):
            r = self.run_profile("record", "--repo", repo, "--session", "s", bad)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("profile record", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

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

    def test_viewer_shows_a_banner_for_rejected_files(self):
        html = prof.VIEWER.read_text()
        self.assertIn("regenerate it with scripts/profile.py", html)
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

    def profile(self, name, tiers, orchestrator=0.5, changes=(10, 20), outright=5, rounds=1.5):
        """tiers: {tier: (merged tickets, $ per merged ticket)}. The orchestrator's $ is the only overhead."""
        merged = sum(n for n, _ in tiers.values())
        tier_rows = {t: {"merged": n, "cost": n * c, "reviewed_prs": n, "rounds": round(n * rounds),
                         "round1": n, "round1_changes": round(n * changes[0] / changes[1]),
                         "round1_outright": round(n * outright / changes[1])}
                     for t, (n, c) in tiers.items()}
        per = sum(r["cost"] for r in tier_rows.values()) / merged + orchestrator
        path = self.root / name
        path.write_text(json.dumps({"schema": 2, "metrics": {
            "tickets_merged": merged,
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
        self.assertIn("regenerate it", r.stderr)


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


if __name__ == "__main__":
    unittest.main()
