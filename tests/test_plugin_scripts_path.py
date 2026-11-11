"""Agents call the plugin's scripts through the path the run skill passes, never a bare or checkout `scripts/` (#617)."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = re.compile(r"(?P<pre>[^`\s]*)scripts/(?P<name>[A-Za-z][A-Za-z0-9_-]*(?:\.py)?)")


class PluginScriptsPath(unittest.TestCase):
    def test_every_script_named_in_an_agent_doc_goes_through_the_plugin_root(self):
        for path in sorted((ROOT / "agents").glob("*.md")):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                for m in REF.finditer(line):
                    with self.subTest(ref=f"{path.name}:{n} scripts/{m['name']}"):
                        self.assertEqual(m["pre"], "<plugin>/", f"{path.name}:{n} names a bare or checkout scripts/{m['name']}")

    def test_agents_say_where_the_path_comes_from(self):
        for name in ("worker", "reviewer", "scout"):
            text = (ROOT / "agents" / f"{name}.md").read_text()
            self.assertIn("## Plugin scripts", text, name)
            self.assertIn("`Plugin root:`", text, name)
            self.assertIn("`<plugin>/scripts/<name>`", text, name)

    def test_run_skill_passes_the_plugin_root_to_each_role(self):
        self.assertRegex((ROOT / "skills/run/brief.md").read_text(), r"(?m)^Plugin root: ")
        skill = (ROOT / "skills/run/SKILL.md").read_text()
        self.assertGreaterEqual(skill.count("`Plugin root:"), 2)  # reviewer and scout; the worker's comes with the brief
        self.assertIn("it computes Branch, Verify and Plugin root", skill)  # scripts/brief fills the worker's line (#659)

    def test_no_prompt_builds_a_temp_directory_path_by_hand(self):
        # #659: agents built `/tmp/claude-<uid>/<project>/...` from the cwd and Read a path that does not exist.
        built = re.compile(r"(?:/private)?/tmp/claude-|<tmp>/claude-|claude-<uid>")
        for path in [*(ROOT / "skills").rglob("*.md"), *(ROOT / "agents").glob("*.md")]:
            self.assertIsNone(built.search(path.read_text()), f"{path.relative_to(ROOT)} builds a temp-directory path")

    def test_the_output_file_and_literal_scratch_rules_stay_in_the_prompts(self):
        skill = (ROOT / "skills/run/SKILL.md").read_text()
        self.assertIn("only from the `output-file` path its task notification gives", skill)
        self.assertIn("never build a temp-directory path from the cwd or the session id", skill)
        self.assertIn("copied exactly as your system prompt names it (never built from the cwd", skill)
        reviewer = (ROOT / "agents/reviewer.md").read_text()
        self.assertIn("written with the literal plugin root and scratchpad paths, never a shell variable", reviewer)


if __name__ == "__main__":
    unittest.main()
