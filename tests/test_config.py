"""scripts/config.py over a filled-in copy of skills/setup/config-template.md, and the guard contract."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import config  # noqa: E402

TEMPLATE = (ROOT / "skills" / "setup" / "config-template.md").read_text()

FILL = [
    ("<n, default 2>", "3"),
    ("  - `<path>`: <per-PR rule, e.g. \"add one line under Unreleased\">",
     "  - `CHANGELOG.md`: add one line under Unreleased\n  - `docs/*.md`: one row"),
    ("  - `<glob>`: `<regenerate command, e.g. npm run codegen>`",
     "  - `gen/**`: `make gen`\n  - `api.json`: `sh api.sh`"),
    ("<globs>", "`hooks/**`, `scripts/gate`"),
    ("<50>", "50"), ("<3> files", "3 files"), ("<400>", "400"),
    ("<yes | no>. When yes", "yes. When yes"),
    ("<minutes>. Optional", "15. Optional"),
]
CHECKS = ("- **Repo-specific checks:** docs updated (run: `make codegen && make check`), see `README.md`, "
          "and lint (run: `sh lint.sh`)")


def filled(post="yes"):
    # The template's repo-specific line is prose about the grammar (its examples and its "must run: the
    # tests" counter-example are not entries), so a filled-in config replaces the line whole.
    text = "\n".join(CHECKS if l.startswith("- **Repo-specific checks") else l for l in TEMPLATE.splitlines()) + "\n"
    for old, new in FILL:
        assert old in text, old
        text = text.replace(old, new, 1)
    return text.replace("**Post reviews:** yes", f"**Post reviews:** {post}")


def load_guard():
    spec = importlib.util.spec_from_file_location("guard", ROOT / "hooks" / "guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard


class SharedRule(unittest.TestCase):
    """One match rule for holds and replay: equality or a glob, case-sensitive on every platform."""

    def test_table(self):
        cfg = config.Config(shared_files=("docs/*.md", "CHANGELOG.md", "Docs/*"))
        for path, want in [
            ("docs/guide.md", True),     # glob
            ("CHANGELOG.md", True),      # exact path
            ("changelog.md", False),     # case mismatch on an exact path
            ("DOCS/guide.md", False),    # case mismatch on a glob
            ("Docs/a.md", True),
            ("src/docs/guide.md", False),
        ]:
            with self.subTest(path=path):
                self.assertEqual(cfg.is_shared(path), want)


class MergeGates(unittest.TestCase):
    TEXT = ("## Gates\n\n- **Start gates:** none beyond the label filter.\n"
            "- **Merge gates:** PRs touching `hooks/**`, `release-please-config.json` or `docs/` are merged by the developer. "
            "release-please's `chore(main): release` PRs too.\n\n## Review\n\n- **Merge gates:** `nope`\n")

    def test_the_globs_come_from_the_first_sentence_of_the_gates_section(self):
        self.assertEqual(config.Config.parse(self.TEXT).merge_gates, ("hooks/**", "release-please-config.json", "docs/"))

    def test_merge_gate_for_names_the_first_glob_a_path_hits(self):
        cfg = config.Config.parse(self.TEXT)
        self.assertEqual(cfg.merge_gate_for(["a.py", "hooks/guard.py"]), "hooks/**")
        self.assertEqual(cfg.merge_gate_for(["release-please-config.json", "hooks/x"]), "hooks/**")
        self.assertEqual(cfg.merge_gate_for(["scripts/merge"]), None)
        self.assertEqual(cfg.merge_gate_for([]), None)

    def test_none_and_the_templates_placeholder_gate_nothing(self):
        for line in ("none", "<tickets, labels or paths that wait for the user's go-ahead to merge>"):
            cfg = config.Config.parse(f"## Gates\n\n- **Merge gates:** {line}\n")
            self.assertEqual((cfg.merge_gates, cfg.merge_gate_for(["hooks/x"])), ((), None))
        self.assertEqual(config.Config.parse(filled()).merge_gates, ())

    def test_the_repos_own_config_gates_the_guard(self):
        cfg = config.Config.load(ROOT)
        self.assertEqual(cfg.merge_gate_for(["hooks/guard.py"]), "hooks/**")
        self.assertEqual(cfg.merge_gate_for([".release-please-manifest.json"]), ".release-please-manifest.json")
        self.assertIsNone(cfg.merge_gate_for(["scripts/merge"]))


class MergeGates(unittest.TestCase):
    """#757: a gate entry that names no glob must fail loudly, and a cut-off file list must gate."""

    def parse(self, line):
        return config.Config.parse(f"## Gates\n\n- **Merge gates:** {line}\n")

    def test_a_line_with_globs_has_no_problem(self):
        cfg = self.parse("PRs touching `hooks/**` or `a.txt` are merged by the developer.")
        self.assertEqual((cfg.merge_gates, cfg.problems_of(config.GATES)), (("hooks/**", "a.txt"), []))

    def test_a_line_with_no_glob_is_a_problem_not_silence(self):
        for line in ("PRs touching the hooks directory are merged by the developer.", "hooks/** is gated.", ""):
            with self.subTest(line):
                cfg = self.parse(line)
                self.assertEqual(cfg.merge_gates, ())
                self.assertEqual(len(cfg.problems_of(config.GATES)), 1)

    def test_none_and_a_missing_line_are_not_problems(self):
        self.assertEqual(self.parse("none").problems_of(config.GATES), [])
        self.assertEqual(self.parse("None. Nothing is gated.").problems_of(config.GATES), [])
        self.assertEqual(config.Config.parse("## Gates\n\n- **Other:** x\n").problems_of(config.GATES), [])

    def test_a_truncated_file_list_is_gated_when_a_gate_is_configured(self):
        cfg = self.parse("PRs touching `hooks/**` are merged by the developer.")
        self.assertIsNone(cfg.merge_gate_for(["src/a.py"]))
        self.assertEqual(cfg.merge_gate_for(["src/a.py"], truncated=True), config.GATE_TRUNCATED)
        self.assertEqual(cfg.merge_gate_for(["hooks/x"], truncated=True), "hooks/**")  # a real hit names its glob
        self.assertIsNone(self.parse("none").merge_gate_for(["src/a.py"], truncated=True))


class TrustedAuthors(unittest.TestCase):
    def test_logins_on_the_backlog_line_are_read_lowercase(self):
        text = "## Backlog\n\n- **Trusted authors:** `Alice`, `@bob`, `ci-bot[bot]`\n\n## Slots\n- **Trusted authors:** `nope`\n"
        self.assertEqual(config.Config.parse(text).trusted_authors, ("alice", "bob", "ci-bot[bot]"))

    def test_the_templates_placeholder_names_no_one(self):
        text = "## Backlog\n\n<!--\n- **Trusted authors:** `<login>`, `<login>`\n-->\n"
        self.assertEqual(config.Config.parse(text).trusted_authors, ())
        self.assertEqual(config.Config.parse(filled()).trusted_authors, ())


class ConfigTemplate(unittest.TestCase):
    def test_every_field_of_the_filled_template(self):
        cfg = config.Config.parse(filled())
        self.assertEqual(cfg.slots, 3)
        self.assertEqual(cfg.shared_files, ("CHANGELOG.md", "docs/*.md"))
        self.assertEqual(cfg.generated, (("gen/**", "make gen"), ("api.json", "sh api.sh")))
        self.assertEqual(cfg.check_commands, ("make codegen && make check", "sh lint.sh"))
        self.assertTrue(cfg.post_reviews)
        self.assertEqual(cfg.risk_paths, ("hooks/**", "scripts/gate"))
        self.assertEqual((cfg.tier_s_lines, cfg.tier_s_files, cfg.tier_l_lines), (50, 3, 400))
        self.assertEqual(cfg.orchestrator_model, "opus")
        self.assertEqual(cfg.problems, ())

    def test_orchestrator_model_is_none_when_blank_old_or_missing(self):
        row = "| Orchestrator | opus (used when the lead launches it; a hand-started session keeps its own) | |"
        for new in ("| Orchestrator | | |", "| Orchestrator | the session's model | |", "| Orchestrator |", ""):
            text = filled().replace(row, new)
            self.assertNotIn("| Orchestrator | opus (used", text)
            self.assertIsNone(config.Config.parse(text).orchestrator_model, new)
        self.assertEqual(config.Config.parse(filled().replace("| Orchestrator | opus (used", "| Orchestrator | sonnet (used")).orchestrator_model, "sonnet")

    def test_lead_model_is_the_lead_row_and_none_when_blank_or_missing(self):
        row = "| Lead | opus (used when scripts/launch --lead starts it; a hand-started session keeps its own) | |"
        self.assertIn(row, filled())
        self.assertEqual(config.Config.parse(filled()).lead_model, "opus")
        for new in ("| Lead | | |", "| Lead |", ""):
            self.assertIsNone(config.Config.parse(filled().replace(row, new)).lead_model, new)
        self.assertEqual(config.Config.parse(filled().replace(row, "| Lead | `sonnet` | |")).lead_model, "sonnet")
        self.assertEqual(config.Config.parse(filled().replace(row, "")).orchestrator_model, "opus")

    def test_board_set_and_missing(self):
        url = "https://github.com/orgs/acme/projects/7"
        self.assertIsNone(config.Config.parse(filled()).board)  # the template's line is a comment
        self.assertIsNone(config.Config.parse(TEMPLATE).board)
        text = filled().replace("## Slots", f"- **Board:** {url}\n\n## Slots", 1)
        self.assertEqual(config.Config.parse(text).board, url)
        self.assertIsNone(config.Config.parse(text).board_bad)

    def test_board_line_as_an_autolink_is_the_url_without_brackets(self):
        url = "https://github.com/users/acme/projects/1"
        for line in (f"- **Board:** {url}", f"- **Board:** <{url}>", f"- **Board:**   <{url}>  "):
            parsed = config.Config.parse(filled().replace("## Slots", f"{line}\n\n## Slots", 1))
            with self.subTest(line):
                self.assertEqual((parsed.board, parsed.board_bad), (url, None))

    def test_a_board_line_that_names_no_url_is_bad_not_missing(self):
        missing = config.Config.parse(filled())
        self.assertEqual((missing.board, missing.board_bad), (None, None))
        for line in ("<project URL>", "TBD", ""):
            parsed = config.Config.parse(filled().replace("## Slots", f"- **Board:** {line}\n\n## Slots", 1))
            with self.subTest(line):
                self.assertIsNone(parsed.board)
                self.assertEqual(parsed.board_bad, line)

    def test_the_templates_board_example_is_a_comment_and_shows_the_bare_form(self):
        self.assertIn("- **Board:** https://github.com/users/<owner>/projects/<n>", TEMPLATE)
        parsed = config.Config.parse(TEMPLATE)
        self.assertEqual((parsed.board, parsed.board_bad), (None, None))

    def test_ci_wait_limit_set_missing_and_unfilled(self):
        self.assertEqual(config.Config.parse(filled()).ci_wait_limit, 15)
        line = next(l for l in filled().splitlines() if l.startswith("- **CI wait limit:**"))
        self.assertIsNone(config.Config.parse(filled().replace(line + "\n", "")).ci_wait_limit)
        self.assertIsNone(config.Config.parse(TEMPLATE).ci_wait_limit)  # the placeholder is not a number

    def test_the_unfilled_template_reads_without_raising(self):
        # Placeholders are entries, not crashes.
        cfg = config.Config.parse(TEMPLATE)
        self.assertEqual(cfg.shared_files, ("<path>",))
        self.assertIsNone(cfg.slots)

    def test_the_merge_line_reads_up_to_date_required_and_defaults_to_when_overlapping(self):
        cfg = config.Config.parse
        head = "## PRs and merging\n\n"
        self.assertTrue(cfg(head + "- **Merge:** up to date: required\n").update_required)
        self.assertFalse(cfg(head + "- **Merge:** up to date: when overlapping\n").update_required)
        self.assertFalse(cfg(head + "- **Merge:** up to date: when overlapping. `up to date: required` always updates.\n").update_required)
        self.assertFalse(cfg(head + "- **Method:** squash\n").update_required)  # a missing line is the default
        self.assertFalse(cfg("").update_required)
        self.assertFalse(config.Config.parse(TEMPLATE).update_required)

    def test_the_pr_title_ticket_suffix_flag(self):
        def cfg(line):
            return config.Config.parse(f"## PRs and merging\n\n- **Branch:** `<number>-<slug>`\n{line}\n- **Method:** squash\n")
        body = "**Body:** `Closes #<n>`"
        self.assertTrue(cfg(f"- **PR title:** Conventional Commits, ending in ` (#<n>)`. {body}").pr_title_ticket)
        self.assertFalse(cfg(f"- **PR title:** Conventional Commits, same as commits. {body}").pr_title_ticket)
        self.assertFalse(cfg("- **PR title:** Conventional Commits. **Body:** `Closes #<n>` (#<n>) works").pr_title_ticket)
        self.assertFalse(config.Config.parse("").pr_title_ticket)
        self.assertTrue(config.Config.parse(TEMPLATE).pr_title_ticket)  # the template's default asks for it

    def test_missing_config_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(config.Config.load(d), config.Config())

    def test_load_reads_the_repos_config(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".claude").mkdir()
            (Path(d) / ".claude" / "orchestration.md").write_text(filled())
            self.assertEqual(config.Config.load(d), config.Config.parse(filled()))

    def test_none_means_empty(self):
        text = filled().replace("`hooks/**`, `scripts/gate`", "none")
        text = text.replace("  - `gen/**`: `make gen`\n  - `api.json`: `sh api.sh`\n", "")
        cfg = config.Config.parse(text)
        self.assertEqual((cfg.risk_paths, cfg.generated), ((), ()))

    def test_malformed_shared_and_generated_entries_are_problems(self):
        text = filled().replace("  - `docs/*.md`: one row", "  - docs without backticks")
        text = text.replace("  - `api.json`: `sh api.sh`", "  - `api.json`: no command")
        cfg = config.Config.parse(text)
        self.assertEqual(cfg.shared_files, ("CHANGELOG.md",))
        self.assertEqual(cfg.generated, (("gen/**", "make gen"),))
        self.assertEqual([p.kind for p in cfg.problems], [config.SHARED, config.GENERATED])

    def test_malformed_run_markers_are_problems(self):
        for bad in ("must run: the tests", "run: ``"):
            with self.subTest(bad):
                cfg = config.Config.parse(f"## Review\n\n- **Repo-specific checks:** {bad}\n")
                self.assertTrue(cfg.problems_of(config.RUN), bad)
                self.assertEqual(cfg.check_commands, ())


class GuardContract(unittest.TestCase):
    """hooks/guard.py keeps its own post-reviews reader (ADR-0003); it must agree with Config."""

    def test_guard_and_config_agree(self):
        guard = load_guard()
        cases = [filled("yes"), filled("no"), filled("Yes"), "- **Post reviews:** yes\n", "* Post reviews: no\n",
                 "## Review\n\n- **Repo-specific checks:** x\n", TEMPLATE, ""]
        for text in cases:
            with self.subTest(text[-60:]), tempfile.TemporaryDirectory() as d:
                (Path(d) / ".claude").mkdir()
                (Path(d) / ".claude" / "orchestration.md").write_text(text)
                self.assertEqual(guard.posting_enabled(d), config.Config.load(d).post_reviews)


class DeveloperWaits(unittest.TestCase):
    """The `Developer waits` section (#706): present, missing (defaults), and a fault that falls back to the defaults."""
    SECTION = ("## Developer waits\n\n- enabled: yes\n- sections: lead-asks, ready-for-human, parked\n"
               "- colours: ready-for-human green, lead-asks magenta\n- orchestrator pass reports: yes\n")

    def test_a_missing_section_is_the_defaults_with_no_problem(self):
        cfg = config.Config.parse("## Slots\n\n- **Slots:** 2\n")
        self.assertTrue(cfg.waits_enabled)
        self.assertEqual(cfg.waits_sections, ("ready-for-human", "needs-triage", "lead-asks"))
        colours = dict(cfg.waits_colours)
        self.assertEqual((colours["ready-for-human"], colours["needs-triage"], colours["lead-asks"]), ("red", "yellow", "blue"))
        self.assertFalse(cfg.waits_pass_reports)
        self.assertEqual(cfg.problems, ())

    def test_a_present_section_sets_order_colours_and_flags(self):
        cfg = config.Config.parse(self.SECTION)
        self.assertEqual(cfg.waits_sections, ("lead-asks", "ready-for-human", "parked"))
        colours = dict(cfg.waits_colours)
        self.assertEqual((colours["ready-for-human"], colours["lead-asks"]), ("green", "magenta"))
        self.assertEqual(colours["needs-triage"], "yellow")  # unlisted: the default stays
        self.assertTrue(cfg.waits_pass_reports)
        self.assertEqual(cfg.problems, ())
        self.assertFalse(config.Config.parse("## Developer waits\n\n- enabled: no\n").waits_enabled)

    def test_a_bad_colour_or_unknown_section_falls_back_to_the_defaults_with_one_problem(self):
        for bad in ("- colours: lead-asks purple\n", "- sections: lead-asks, nonsense\n", "- enabled: maybe\n",
                    "- colours: lead-asks\n", "- sections:\n"):
            with self.subTest(bad):
                cfg = config.Config.parse("## Developer waits\n\n- orchestrator pass reports: yes\n" + bad)
                self.assertEqual(cfg.waits_sections, config.WAITS_DEFAULT_SECTIONS)
                self.assertFalse(cfg.waits_pass_reports)  # the whole section falls back
                problems = cfg.problems_of(config.WAITS)
                self.assertEqual(len(problems), 1)
                self.assertNotIn("\n", problems[0].message)


if __name__ == "__main__":
    unittest.main()
