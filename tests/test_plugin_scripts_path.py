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


if __name__ == "__main__":
    unittest.main()
