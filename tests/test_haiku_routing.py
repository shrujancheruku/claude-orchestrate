"""Worker model, reviewer tier and haiku escalation are the scripts' answer (#543): tables over Config.model_for,
Config.tier_for and claims.escalation, and the skill only repeats what the scripts print."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import claims  # noqa: E402
import config  # noqa: E402

SMALL = "sonnet; haiku for S tickets (at most 3 files, the **Tiers** file limit)"
OFF_RISK = "sonnet; haiku off risk paths"


def cfg(worker=SMALL, s_files=3):
    return config.Config.parse(
        "## Models\n\n| Role | Model | Effort |\n|---|---|---|\n"
        f"| Worker | {worker} | medium |\n\n"
        "## Review\n\n"
        "- **Risk paths** (any PR touching them is reviewed as L): `hooks/**`, `scripts/gate`\n"
        f"- **Tiers:** S is at most 50 changed lines in at most {s_files} files, none on a risk path; "
        "L touches a risk path or changes more than 400 lines; M is everything else.\n")


class ModelFor(unittest.TestCase):
    def test_table(self):
        # (labels, touch map paths | None, investigate, config, expected)
        one, four = ["a.py"], ["a.py", "b.py", "c.py", "d.py"]
        risky = ["a.py", "scripts/gate"]
        rows = [
            ((), one, False, cfg(), "haiku"),              # unlabelled, small map, no risk path
            ((), four, False, cfg(), "sonnet"),            # unlabelled, over the S file limit
            ((), risky, False, cfg(), "sonnet"),           # unlabelled, risk path
            ((), ["hooks/"], False, cfg(), "sonnet"),      # a directory entry stands for the files under it
            ((), None, False, cfg(), "sonnet"),            # unlabelled, the scout has not run: the default
            ((), [], False, cfg(), "haiku"),               # a `none` map is a known, empty one
            (("size:S",), one, False, cfg(), "haiku"),
            (("size:S",), None, False, cfg(), "haiku"),    # the label alone, before the map
            (("size:S",), risky, False, cfg(), "sonnet"),  # a risk path beats the label
            (("size:S",), four, False, cfg(), "sonnet"),   # so does a map over the limit
            (("size:S", "size:M"), one, False, cfg(), "sonnet"),  # S only when it is the only size label
            (("size:M",), one, False, cfg(), "sonnet"),
            (("size:L",), one, False, cfg(), "sonnet"),
            ((), one, True, cfg(), "sonnet"),              # investigate / design
            (("size:S",), one, True, cfg(), "sonnet"),
            ((), four, False, cfg(s_files=5), "haiku"),    # the limit comes from the config
            ((), one, False, cfg(worker="opus"), "opus"),  # no S clause: the default for every ticket
            (("size:S",), one, False, cfg(worker="opus"), "opus"),
            ((), one, False, cfg(worker="opus; haiku for S tickets"), "haiku"),
            ((), one, False, cfg(worker="opus; the small ones go cheap"), "opus"),  # an unparsed clause is never haiku
            ((), one, False, cfg(worker=""), "sonnet"),    # a blank cell
            ((), one, False, config.Config(), "sonnet"),   # no config at all
            # the `off risk paths` clause: every ticket whose map hits no risk path, whatever its size
            ((), four, False, cfg(worker=OFF_RISK), "haiku"),           # over the S file limit
            ((), one, False, cfg(worker=OFF_RISK), "haiku"),
            ((), [], False, cfg(worker=OFF_RISK), "haiku"),
            (("size:M",), four, False, cfg(worker=OFF_RISK), "haiku"),  # a size label is no bar
            (("size:L",), four, False, cfg(worker=OFF_RISK), "haiku"),
            (("size:S",), one, False, cfg(worker=OFF_RISK), "haiku"),
            ((), risky, False, cfg(worker=OFF_RISK), "sonnet"),         # a risk path takes the default
            ((), ["hooks/"], False, cfg(worker=OFF_RISK), "sonnet"),
            (("size:L",), risky, False, cfg(worker=OFF_RISK), "sonnet"),
            ((), None, False, cfg(worker=OFF_RISK), "sonnet"),          # no map yet: the default
            (("size:S",), None, False, cfg(worker=OFF_RISK), "sonnet"),  # nor does the S label stand in for one
            ((), one, True, cfg(worker=OFF_RISK), "sonnet"),            # investigate / design
            ((), four, False, cfg(worker="opus; `haiku` off risk paths"), "haiku"),
            (("size:L",), risky, False, cfg(worker="opus; haiku off risk paths"), "opus"),
        ]
        for labels, paths, investigate, c, want in rows:
            with self.subTest(labels=labels, paths=paths, investigate=investigate, worker=c.worker_model,
                              small=c.worker_small_model, rule=c.worker_small_rule):
                self.assertEqual(c.model_for(labels, paths, investigate), want)

    def test_the_clause_names_its_rule(self):
        # (Worker cell, (small model, rule))
        rows = [
            (SMALL, ("haiku", "S")),
            (OFF_RISK, ("haiku", "off-risk")),
            ("sonnet; Haiku Off Risk Paths", ("haiku", "off-risk")),
            ("sonnet", (None, None)),
            ("sonnet; haiku off the risky bits", (None, None)),  # neither clause: no small model
        ]
        for cell, want in rows:
            with self.subTest(cell=cell):
                c = cfg(worker=cell)
                self.assertEqual((c.worker_small_model, c.worker_small_rule), want)


class TierFor(unittest.TestCase):
    def test_table(self):
        # (lines, files, labels, risk_hit, expected)
        rows = [
            (10, 1, (), False, "S"),
            (50, 3, (), False, "S"),
            (51, 3, (), False, "M"),
            (50, 4, (), False, "M"),
            (400, 5, (), False, "M"),
            (401, 5, (), False, "L"),
            (10, 1, (), True, "L"),                  # a risk path is L
            (10, 1, ("size:M",), False, "M"),        # a label above the computed tier raises it
            (10, 1, ("size:L",), False, "L"),
            (500, 9, ("size:S",), False, "L"),       # a label below is ignored
            (100, 2, ("size:S",), False, "M"),
            (10, 1, ("size:S",), True, "L"),
            (10, 1, ("size:S", "bug"), False, "S"),
        ]
        c = cfg()
        for lines, files, labels, risk, want in rows:
            with self.subTest(lines=lines, files=files, labels=labels, risk=risk):
                self.assertEqual(c.tier_for(lines, files, labels, risk), want)

    def test_unset_thresholds_never_make_s_or_l(self):
        self.assertEqual(config.Config().tier_for(1, 1), "M")
        self.assertEqual(config.Config().tier_for(10 ** 6, 99), "M")
        self.assertEqual(config.Config().tier_for(1, 1, risk_hit=True), "L")


class Escalation(unittest.TestCase):
    HAIKU = {"model": "claude-haiku-4-5"}

    def ci(self, pr, head, outcome="fail"):
        return {"kind": "ci", "outcome": outcome, "ref": pr, "head": head}

    def review(self, pr, round_, outcome):
        return {"kind": "review", "outcome": outcome, "ref": pr, "round": round_}

    def test_table(self):
        c = cfg()
        rows = [
            ([self.ci(7, "a")], 7, self.HAIKU, None),                       # one failure is not a second
            ([self.ci(7, "a"), self.ci(7, "b")], 7, self.HAIKU, "sonnet"),
            ([self.ci(7, "a"), self.ci(7, "a")], 7, self.HAIKU, None),      # the same head twice is one failure
            ([self.ci(7, "a"), self.ci(7, "b", "pass")], 7, self.HAIKU, None),
            ([self.ci(8, "a"), self.ci(8, "b")], 7, self.HAIKU, None),      # another PR's failures
            ([self.review(7, 1, "request-changes")], 7, self.HAIKU, None),
            ([self.review(7, 2, "request-changes")], 7, self.HAIKU, "sonnet"),
            ([self.review(7, 2, "approve")], 7, self.HAIKU, None),
            ([self.review(7, 3, "incomplete")], 7, self.HAIKU, None),
            ([self.ci(7, "a"), self.ci(7, "b")], 7, {"model": "claude-sonnet-4-5"}, None),  # never a sonnet worker
            ([self.ci(7, "a"), self.ci(7, "b")], 7, {}, None),              # a missing model counts as not haiku
            ([self.ci(7, "a"), self.ci(7, "b")], 7, None, None),
            ([self.ci(7, "a"), self.ci(7, "b")], None, self.HAIKU, None),   # no PR yet
        ]
        for events, pr, worker, want in rows:
            with self.subTest(events=events, pr=pr, worker=worker):
                self.assertEqual(claims.escalation(events, pr, worker, c), want)

    def test_escalates_to_the_configs_default_model(self):
        events = [self.review(7, 2, "request-changes")]
        self.assertEqual(claims.escalation(events, 7, self.HAIKU, cfg(worker="opus; haiku for S tickets")), "opus")


class SkillRepeatsTheScripts(unittest.TestCase):
    SKILL = (ROOT / "skills/run/SKILL.md").read_text()

    def section(self):
        return self.SKILL[self.SKILL.index("## Choosing the worker model"):self.SKILL.index("## Rate limits")]

    def test_worker_model_and_escalation_come_from_the_scripts(self):
        s = self.section()
        self.assertIn("`model <name>`", s)
        self.assertIn("--touch-map", s)
        self.assertIn("`escalate <model>`", s)
        # the rules themselves are gone from the prose
        self.assertNotIn("`size:S` label routes", s)
        self.assertNotIn("a second CI failure on the PR;", s)

    def test_review_step_uses_the_gates_tier_line(self):
        step = next(l for l in self.SKILL.splitlines() if l.startswith("4. **Review**"))
        self.assertIn("`tier S|M|L`", step)
        self.assertNotIn("overrides them", step)

    def test_ci_and_review_steps_route_to_escalation_by_the_claims_word(self):
        for start in ("2. **CI failed**", "5. **Changes requested**"):
            step = next(l for l in self.SKILL.splitlines() if l.startswith(start))
            self.assertIn("escalate", step)
            self.assertIn("`escalate <model>`", step)

    def test_models_row(self):
        row = next(l for l in (ROOT / "skills/setup/config-template.md").read_text().splitlines() if "haiku" in l)
        self.assertIn("S tickets", row)
        c = config.Config.parse((ROOT / "skills/setup/config-template.md").read_text())
        self.assertEqual((c.worker_model, c.worker_small_model), ("sonnet", "haiku"))


if __name__ == "__main__":
    unittest.main()
