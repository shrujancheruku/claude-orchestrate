"""Each guard form a prompt still quotes is pinned to the guard's constants (ADR-0017, C7 of map #655).

Once the guard's refusal reasons name the approved form (#624), prompts stop quoting forms and
this file shrinks to what remains quoted.
"""
import importlib.util
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_guard():
    spec = importlib.util.spec_from_file_location("guard_for_prompt_pins", ROOT / "hooks" / "guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chain_forms(text):
    """The joiners and plain commands the run skill quotes for a chain of script calls, or None."""
    joined = re.search(r"joined only by ((?:`[^`]+`(?:, | or )?)+)", text)
    ending = re.search(r"ending only in ((?:`[a-z]+`(?:, | or )?)+)", text)
    if not joined or not ending:
        return None
    # `cat` of a `.claude/` file is a separate rule in the guard, not a CHAIN_PLAIN command.
    plain = set(re.findall(r"`([^`]+)`", ending.group(1))) - {"cat"}
    return set(re.findall(r"`([^`]+)`", joined.group(1))), plain


class GuardFormsInPrompts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = load_guard()

    def test_run_skill_chain_forms_match_the_guard(self):
        forms = chain_forms((ROOT / "skills/run/SKILL.md").read_text())
        self.assertIsNotNone(forms, "skills/run/SKILL.md no longer quotes the chain forms; update this pin")
        joiners, plain = forms
        # None is a chain's first or last command and "\n" a line break; neither is quoted as a joiner.
        guard_joiners = {j for j in self.guard.CHAIN_JOINERS if j and j != "\n"}
        self.assertEqual(joiners, guard_joiners, "CHAIN_JOINERS and skills/run/SKILL.md disagree; change both")
        self.assertEqual(plain, set(self.guard.CHAIN_PLAIN), "CHAIN_PLAIN and skills/run/SKILL.md disagree; change both")

    def test_chain_forms_pin_goes_red_when_a_joiner_changes(self):
        stale = "joined only by `;`, `&&` or `|`, so the guard can approve; ending only in `tail`, `head` or `wc`"
        joiners, plain = chain_forms(stale)
        self.assertEqual(joiners, {";", "&&", "|"})
        self.assertEqual(plain, {"tail", "head", "wc"})
        self.assertNotEqual(plain, set(self.guard.CHAIN_PLAIN))  # a prompt that lags the guard is what the pin catches

    def test_reviewer_post_shape_is_a_guard_shape(self):
        text = (ROOT / "agents/reviewer.md").read_text()
        command = re.search(r"(?m)^(gh api --method POST repos/\{owner\}/\{repo\}/pulls/<n>/reviews --input - <<'EOF')$", text)
        self.assertIsNotNone(command, "agents/reviewer.md no longer quotes the review post; update this pin")
        self.assertIn(command.group(1), self.guard.SHAPES)
        event = re.search(r'(?m)^\{"event": "(\w+)"', text)
        self.assertIsNotNone(event)
        self.assertIn(f'"event": "{event.group(1)}"', self.guard.SHAPES)


if __name__ == "__main__":
    unittest.main()
