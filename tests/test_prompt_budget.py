"""A word budget per prompt file, a ratchet (ADR-0017).

`tests/prompt_budget.json` holds the budget of each prompt file. A prompt over its budget fails,
so a PR that grows one raises the budget in the same diff, where the reviewer sees it. A prompt
well under its budget fails too: lower the budget to the new count.
"""
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUDGET = ROOT / "tests" / "prompt_budget.json"
PATTERNS = ("skills/*/SKILL.md", "agents/*.md", "skills/run/brief.md")
SLACK = 0.05  # a count more than this fraction under budget is stale


def prompt_files(root):
    found = set()
    for pattern in PATTERNS:
        found.update(str(p.relative_to(root)) for p in root.glob(pattern))
    return sorted(found)


def check(root, budgets):
    """Return the problems: a prompt over or well under budget, unbudgeted, or a budget with no file."""
    problems = []
    files = prompt_files(root)
    for name in files:
        count = len((root / name).read_text().split())
        if name not in budgets:
            problems.append(f"{name}: {count} words and no budget; add it to tests/prompt_budget.json")
        elif count > budgets[name]:
            problems.append(f"{name}: {count} words, over its budget of {budgets[name]}; raise the budget and name the judgement (ADR-0017)")
        elif count < budgets[name] * (1 - SLACK):
            problems.append(f"{name}: {count} words, under its budget of {budgets[name]}; lower the budget to {count}")
    for name in sorted(set(budgets) - set(files)):
        problems.append(f"{name}: budgeted but not a prompt file; remove it from tests/prompt_budget.json")
    return problems


class PromptBudget(unittest.TestCase):
    def test_repo_prompts_are_within_budget(self):
        budgets = json.loads(BUDGET.read_text())
        self.assertEqual(check(ROOT, budgets), [])

    def fixture(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        for name, words in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(" ".join(["w"] * words))
        return root

    def test_over_budget_fails(self):
        root = self.fixture({"skills/run/SKILL.md": 101})
        problems = check(root, {"skills/run/SKILL.md": 100})
        self.assertEqual(len(problems), 1)
        self.assertIn("over its budget", problems[0])

    def test_at_budget_passes(self):
        root = self.fixture({"agents/worker.md": 100})
        self.assertEqual(check(root, {"agents/worker.md": 100}), [])

    def test_stale_budget_fails(self):
        root = self.fixture({"agents/worker.md": 80})
        self.assertIn("lower the budget to 80", check(root, {"agents/worker.md": 100})[0])

    def test_unbudgeted_prompt_fails(self):
        root = self.fixture({"skills/new/SKILL.md": 5})
        self.assertIn("no budget", check(root, {})[0])

    def test_budget_without_a_file_fails(self):
        root = self.fixture({})
        self.assertIn("not a prompt file", check(root, {"agents/gone.md": 10})[0])


if __name__ == "__main__":
    unittest.main()
