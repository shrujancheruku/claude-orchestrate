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
            self.assertNotIn("opus (used", text)
            self.assertIsNone(config.Config.parse(text).orchestrator_model, new)
        self.assertEqual(config.Config.parse(filled().replace("opus (used", "sonnet (used")).orchestrator_model, "sonnet")

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


if __name__ == "__main__":
    unittest.main()
