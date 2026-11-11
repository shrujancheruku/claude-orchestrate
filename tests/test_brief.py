"""scripts/brief writes the worker's brief with the modules that parse it (#659)."""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ScriptEnv, gh_repo_view  # noqa: E402
import github  # noqa: E402
import holds  # noqa: E402
import session  # noqa: E402
import touchmap  # noqa: E402
from test_issue import node  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = "/plugins/r-1/current"
REQUIRED = ["--shared", "README.md: edit only its section", "--commits", "Conventional Commits",
            "--pr-title", "Conventional Commits ending in ` (#7)`", "--human-only", "merging release PRs",
            "--plugin-root", PLUGIN]
SLOT = re.compile(r"<[^<>\n]*>")
FIXED_SLOTS = "`<Plugin root>/scripts/<name>`"  # the preamble's own wording, not a slot


class Brief(unittest.TestCase):
    def setUp(self):
        self.env = ScriptEnv()
        self.addCleanup(self.env.close)

    def brief(self, touch_map, *extra, issue=None, check=True):
        reply = json.dumps({"data": {"repository": {"issue": issue or node(title="feat: a thing, with a long title in it")}}})
        self.env.set_gh([gh_repo_view(), {"args": ["api", "graphql", "-f", "query=" + github.ISSUE_QUERY], "stdout": reply}])
        return self.env.run("brief", 7, "--touch-map", *touch_map, *REQUIRED, *extra, check=check)

    def test_branch_and_verify_are_computed_from_the_ticket_and_the_plugin_root(self):
        out = self.brief(["scripts/a.py"]).stdout
        self.assertEqual(session.branch_of(out), "7-a-thing-with-a-long")
        self.assertIn(f"Verify: '{PLUGIN}/scripts/verify' --config --branch 7-a-thing-with-a-long", out.splitlines())
        self.assertIn(f"Plugin root: {PLUGIN}.", out)
        self.assertIn('body includes "Closes #7"', out)
        self.assertEqual(SLOT.findall(out.replace(FIXED_SLOTS, "")), [])

    def test_a_path_only_touch_map_round_trips_to_rebuild_state_and_holds(self):
        out = self.brief(["scripts/a.py:10-20 (the join)", "tests/b.py, docs/c.md"]).stdout
        want = touchmap.parse("scripts/a.py:10-20 (the join), tests/b.py, docs/c.md")
        self.assertEqual(session.touch_map(out), want)  # what rebuild-state reads
        paths, ignored = holds.candidate_paths([session.touch_map_text(out)])  # what holds reads
        self.assertEqual(paths, ["scripts/a.py", "tests/b.py", "docs/c.md"])
        self.assertEqual(ignored, [])

    def test_a_none_touch_map_is_none(self):
        out = self.brief(["none"]).stdout
        self.assertIn("Touch map: none\n", out)
        self.assertEqual(session.touch_map(out), [])

    def test_a_prose_touch_map_entry_is_refused_naming_it_and_prints_nothing(self):
        r = self.brief(["scripts/a.py", "and also the docs"], check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn("and also the docs", r.stderr)

    def test_a_leftover_template_slot_is_refused_naming_the_slot(self):
        r = self.brief(["a.py"], "--notes", "branch 7-<slug from the ticket title>", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertIn("<slug from the ticket title>", r.stderr)

    def test_a_slot_in_a_required_line_is_refused_too(self):
        r = self.brief(["a.py"], "--held", "<held-path lines>", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("<held-path lines>", r.stderr)

    def test_a_body_an_untrusted_author_edited_gives_no_brief(self):
        r = self.brief(["a.py"], issue=node(editor="eve", author={"login": "eve"}, authorAssociation="NONE"), check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn("untrusted", r.stderr)

    def test_lead_mode_and_dependencies_land_in_their_lines(self):
        out = self.brief(["a.py"], "--lead", "--dep", "#5 → PR 9", "--dep", "#6 → PR 10").stdout.splitlines()
        self.assertIn("Merged dependencies: #5 → PR 9", out)
        self.assertIn("#6 → PR 10", out)
        self.assertIn("Notes: Lead mode: yes.", out)

    def test_the_output_has_the_templates_fields_in_its_order_and_first_line(self):
        block = re.search(r"```\n(.*?)\n```", (ROOT / "skills/run/brief.md").read_text(), re.S).group(1)
        field = re.compile(r"^([A-Z][A-Za-z\- ]*?): ", re.M)
        out = self.brief(["a.py"]).stdout
        self.assertEqual(field.findall(out), field.findall(block))
        self.assertEqual(out.splitlines()[0], block.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
