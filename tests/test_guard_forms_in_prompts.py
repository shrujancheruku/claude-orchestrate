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


def chain_mismatches(text, guard):
    """Where the run skill's quoted chain forms and the guard's constants disagree."""
    forms = chain_forms(text)
    if forms is None:
        return ["skills/run/SKILL.md no longer quotes the chain forms; update this pin"]
    joiners, plain = forms
    problems = []
    # None is a chain's first or last command and "\n" a line break; neither is quoted as a joiner.
    if joiners != {j for j in guard.CHAIN_JOINERS if j and j != "\n"}:
        problems.append("CHAIN_JOINERS and skills/run/SKILL.md disagree; change both")
    if plain != set(guard.CHAIN_PLAIN):
        problems.append("CHAIN_PLAIN and skills/run/SKILL.md disagree; change both")
    return problems


class GuardFormsInPrompts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = load_guard()

    def test_run_skill_chain_forms_match_the_guard(self):
        text = (ROOT / "skills/run/SKILL.md").read_text()
        self.assertEqual(chain_mismatches(text, self.guard), [])

    def test_chain_pin_reports_a_changed_guard(self):
        prompt = "joined only by `;`, `&&` or `|` and ending only in `tail`, `head` or `wc`"

        class FakeGuard:
            CHAIN_JOINERS = (None, ";", "&&", "||", "|", "\n")  # a joiner the prompt lacks
            CHAIN_PLAIN = frozenset(("tail", "head", "wc"))

        problems = chain_mismatches(prompt, FakeGuard)
        self.assertEqual(len(problems), 1)
        self.assertIn("CHAIN_JOINERS", problems[0])
        FakeGuard.CHAIN_PLAIN = frozenset(("tail", "head", "wc", "sort"))  # and a plain command it lacks
        self.assertEqual(len(chain_mismatches(prompt, FakeGuard)), 2)
        self.assertEqual(len(chain_mismatches("no forms here", FakeGuard)), 1)

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
