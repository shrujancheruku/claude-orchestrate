"""Prompts and the README must not name a plugin script or flag that doesn't exist (#134).

Every `scripts/<name>` reference (including the `../../scripts/<name>` and
`${CLAUDE_SKILL_DIR}/../../scripts/<name>` forms) in agents/*.md, skills/**, README.md and docs/guide.md
must be a file in scripts/. Every `--flag` written after a reference, up to the end of its
backtick span or line, must appear in that script's `--help`.

The flag check covers scripts that parse arguments with argparse (so `--help` is safe and
real). A script without argparse is skipped rather than run: running it with `--help` could
act on the repo. As a script gains a parser it is picked up automatically.
"""
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

# Prompts name `scripts/<name>`; the name may end in .py but never in sentence punctuation.
REF = re.compile(r"scripts/([A-Za-z][A-Za-z0-9_-]*(?:\.py)?)")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
# Scripts with subcommands whose flags live in the subcommand's own --help.
SUBCOMMANDS = {"profile.py": ("compare", "record")}


def prompt_files():
    files = sorted((ROOT / "agents").glob("*.md")) + sorted((ROOT / "skills").rglob("*.md"))
    return files + [ROOT / "README.md", ROOT / "docs/guide.md"]


def references():
    """Yield (file, line number, script name, flags written after the reference, that span)."""
    for path in prompt_files():
        for n, line in enumerate(path.read_text().splitlines(), 1):
            for m in REF.finditer(line):
                # The span runs to the closing backtick (inline code) or end of line (fence),
                # and stops at the next script reference so flags aren't blamed on the wrong one.
                rest = line[m.end():]
                end = len(rest)
                for stop in ("`", "scripts/"):
                    i = rest.find(stop)
                    if i != -1:
                        end = min(end, i)
                yield path.relative_to(ROOT), n, m.group(1), FLAG.findall(rest[:end]), rest[:end]


def help_text(name, span):
    script = SCRIPTS / name
    if "argparse" not in script.read_text():
        return None
    argvs = [[]] + [[s] for s in SUBCOMMANDS.get(name, ()) if re.search(rf"\b{s}\b", span)]
    out = []
    for argv in argvs:
        r = subprocess.run([sys.executable, str(script), *argv, "--help"],
                           capture_output=True, text=True, timeout=30)
        out.append(declared_flags(r.stdout + r.stderr))
    return set().union(*out)


def declared_flags(text):
    """Flags argparse itself declares: the usage block and the option lines.

    Not the description: scripts reuse their docstring there, and it can name a flag
    (`bad --cwd`) after the real option is gone.
    """
    usage = text.split("\n\n", 1)[0]
    options = [re.match(r"\s+(-[^ ].*?)(?:  |$)", l).group(1) for l in text.splitlines() if re.match(r"\s{1,4}-", l)]
    return set(FLAG.findall(usage + "\n" + "\n".join(options)))


class PromptReferences(unittest.TestCase):
    def test_references_found(self):
        # Guards the extractor itself: if the regex rots, the checks below pass vacuously.
        names = {r[2] for r in references()}
        self.assertTrue({"gate", "merge", "verify", "watch-ci", "rebuild-state", "profile.py", "ci-times", "lead-brief"} <= names, names)

    def test_every_referenced_script_exists(self):
        for path, n, name, _, _ in references():
            with self.subTest(ref=f"{path}:{n} scripts/{name}"):
                self.assertTrue((SCRIPTS / name).is_file(), f"{path}:{n} names scripts/{name}, which doesn't exist")

    def test_every_flag_a_prompt_passes_is_in_help(self):
        for path, n, name, flags, span in references():
            if not flags or not (SCRIPTS / name).is_file():
                continue
            text = help_text(name, span)
            if text is None:
                continue  # no argparse, so no --help to check against
            for flag in flags:
                with self.subTest(ref=f"{path}:{n} scripts/{name} {flag}"):
                    self.assertIn(flag, text, f"{path}:{n} passes {flag} to {name}, but its --help doesn't list it")


GUIDE = ROOT / "docs/guide.md"


def anchor(title):
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to hyphens."""
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", title.strip().lower()))


def guide_anchors():
    return {anchor(m.group(1)) for m in re.finditer(r"^#{2,4} (.+)$", GUIDE.read_text(), re.M)}


def lead_section():
    """The guide's Lead mode section, up to the next level-2 heading."""
    text = GUIDE.read_text()
    start = text.index("## Lead mode")
    return text[start:text.index("\n## ", start + 1)]


class LeadDocs(unittest.TestCase):
    """The README and guide explain the lead: both starts, what it degrades to, ci-times and the board (#216)."""

    def test_readme_starts_the_lead_in_one_command_and_links_the_guide(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("scripts/launch --lead", readme)
        self.assertNotIn("claude --name lead", readme)
        self.assertIn("docs/guide.md#lead-mode", readme)

    def test_every_readme_link_into_the_guide_resolves(self):
        readme = (ROOT / "README.md").read_text()
        anchors = guide_anchors()
        for m in re.finditer(r"docs/guide\.md#([\w-]+)", readme):
            with self.subTest(anchor=m.group(1)):
                self.assertIn(m.group(1), anchors)

    def test_guide_covers_both_starts_and_the_fallbacks(self):
        section = lead_section()
        self.assertIn("scripts/launch --lead", section)
        self.assertNotIn("claude --name lead", section)
        self.assertIn("**Two terminals.** Start the orchestrator yourself in one terminal with `scripts/launch`", section)
        self.assertNotIn("claude --name orchestrator", section)
        # Without a lead, each part falls back: stops to needs-info, discovered work and risk notes to the summary.
        # Assert on text only the new paragraph has: "discovered" and "risk notes" predate this PR.
        self.assertIn("**Without a lead.**", section)
        self.assertIn("**Two terminals.**", section)
        self.assertIn("Each pass report says `Lead: none`", section)

    def test_guide_has_ci_times_and_board_sections(self):
        anchors = guide_anchors()
        self.assertIn("measuring-ci-wait", anchors)
        self.assertIn("projects-board", anchors)


def reconcile_step():
    """The run skill's step 1 (Reconcile), up to step 2."""
    text = (ROOT / "skills/run/SKILL.md").read_text()
    start = text.index("1. **Reconcile.**")
    return text[start:text.index("\n2. **", start)]


def scout_reconcile_line():
    """The scout's Reconcile report line, the one that names the shape."""
    return next(l for l in (ROOT / "agents/scout.md").read_text().splitlines() if l.startswith("- **Reconcile:**"))


class ReconcileReadsClaims(unittest.TestCase):
    """The claims script carries each claimed ticket's state, worktree and change, so the scout reports none of it (#266)."""

    def test_scout_reconcile_has_no_per_claimed_ticket_line(self):
        line = scout_reconcile_line()
        self.assertNotIn("claimed ticket", line)
        self.assertNotIn("dirty", line)
        self.assertNotIn("worktree", line)

    def test_scout_reconcile_reports_check_states_of_in_review_tickets(self):
        line = scout_reconcile_line()
        self.assertIn("in-review", line)
        self.assertIn("check states", line)
        self.assertIn("`#<n> <PR> <check states>`", line)

    def test_scout_reconcile_leaves_the_frontier_to_the_script(self):
        line = scout_reconcile_line()
        for part in ("`takeable:`", "`ready-for-human:`", "parents whose sub-issues are all closed"):
            with self.subTest(part=part):
                self.assertNotIn(part, line)

    def test_run_reads_frontier_in_step_one_and_reports_its_sections_in_step_five(self):
        text = (ROOT / "skills/run/SKILL.md").read_text()
        self.assertIn('scripts/frontier" --all', text)
        step5 = text[text.index("5. **Report and yield.**"):]
        for part in ("`ready-for-human:`", "`parents-closed:`", "`drift:`"):
            with self.subTest(part=part):
                self.assertIn(part, step5)

    def test_scout_hygiene_no_longer_lists_claims_facts(self):
        text = (ROOT / "agents/scout.md").read_text()
        hygiene = next(l for l in text.splitlines() if l.startswith("- **Hygiene:**"))
        self.assertNotIn("merged PRs whose tickets", hygiene)
        self.assertNotIn("state files out of step", hygiene)

    def test_step_one_runs_rebuild_then_claims_then_the_scout(self):
        step = reconcile_step()
        order = [step.index(s) for s in ("rebuild-state", "scripts/claims", "One scout call")]
        self.assertEqual(order, sorted(order), "step 1 must rebuild, then read claims, then call the scout")

    def test_step_one_asks_the_scout_for_nothing_a_claim_line_carries(self):
        # The scout call's sentence only: the rest of step 1 (lead routing) may name a worktree's unpushed work.
        step = reconcile_step()
        call = step[step.index("One scout call"):step.index("Done when every claimed ticket has a state.")]
        for phrase in ("merged PRs whose tickets", "branch's last pushed commit", "dirty", "unpushed"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, call)

    def test_haiku_hand_over_reads_the_claim_change_field(self):
        text = (ROOT / "skills/run/SKILL.md").read_text()
        self.assertNotIn("as the scout does for no-PR tickets", text)


class GlossaryTerms(unittest.TestCase):
    """The two mechanical check points have their own terms, and the retired "Pre-merge check" is gone (#272)."""

    def glossary(self):
        return (ROOT / "GLOSSARY.md").read_text()

    def test_script_gate_and_merge_readiness_are_defined(self):
        text = self.glossary()
        self.assertIn("**Script gate**:", text)
        self.assertIn("**Merge readiness**:", text)

    def test_pre_merge_check_is_retired(self):
        self.assertNotIn("**Pre-merge check**", self.glossary())
        self.assertNotIn("Pre-merge check", (ROOT / "docs/guide.md").read_text())


class VerdictLines(unittest.TestCase):
    """`gate` and `merge` end with one `next:` verdict line; the run skill names every word and no exit code (#347)."""

    GATE = ("review <head>", "send-back <pr>", "rerun-after-ci", "could-not-run")
    MERGE = ("merged", "wait-ci <pr> <head>", "re-review <pr>", "send-back <pr>", "ask-user <why>", "retry",
             "could-not-run")
    ALSO = ("wait-ci <pr> <head>", "send-back <pr>", "left <pr> <why>")

    def skill(self):
        return (ROOT / "skills/run/SKILL.md").read_text()

    def step(self, text, start, end):
        i = text.index(start)
        return text[i:text.index(end, i)]

    def test_the_skill_names_every_verdict_word(self):
        text = self.skill()
        gate = self.step(text, "1. **Gate and CI:**", "\n2. ")
        merge = self.step(text, "6. **Approved**", "7. **After the merge:**")
        for word in self.GATE:
            self.assertIn(f"`{word}`", gate)
        for word in self.MERGE + self.ALSO:
            self.assertIn(f"`{word}`", merge)
        self.assertIn("`also: <verdict> <pr> ...`", merge)

    def test_the_merge_step_names_no_exit_code(self):
        merge = self.step(self.skill(), "6. **Approved**", "7. **After the merge:**")
        self.assertNotRegex(merge, r"(?i)\bexits? \d")

    def test_the_guide_lists_the_vocabulary(self):
        guide = (ROOT / "docs/guide.md").read_text()
        for word in set(self.GATE + self.MERGE + self.ALSO):
            self.assertIn(f"`{word}`", guide)

    def test_the_scripts_document_the_same_words(self):
        for name, words in (("gate", self.GATE), ("merge", self.MERGE)):
            doc = (ROOT / "scripts" / name).read_text().split('"""')[1]
            for word in words:
                self.assertIn(word, doc)


class ReviewerChecksOutByHash(unittest.TestCase):
    """#416: the reviewer reads the PR head by hash and runs the suite in the foreground, the way verify does."""

    REVIEWER = (ROOT / "agents" / "reviewer.md").read_text()
    RUN = (ROOT / "skills" / "run" / "SKILL.md").read_text()

    def test_head_is_fetched_by_hash_never_by_the_pull_ref(self):
        self.assertIn("headRefOid", self.REVIEWER)
        self.assertIn("git fetch origin <hash>", self.REVIEWER)
        self.assertNotIn("git fetch origin pull/", self.REVIEWER)
        self.assertIn("read `headRefOid` again just before you post", self.REVIEWER)

    def test_suite_runs_in_the_pr_tree_through_verify_in_the_foreground(self):
        self.assertIn("scripts/verify --config --cwd <scratch>/pr-<n>", self.REVIEWER)
        self.assertIn("never `run_in_background`", self.REVIEWER)
        self.assertIn("600000", self.REVIEWER)

    def test_reports_carry_head_and_suite(self):
        self.assertEqual(self.REVIEWER.count("suite: pass | fail <n> | not run (<reason>)"), 2)
        self.assertIn("head: <hash reviewed>", self.REVIEWER)

    def test_run_skill_reruns_a_stale_head_and_owns_the_reviewer_worktree(self):
        self.assertIn("is stale: run it again", self.RUN)
        self.assertIn("detached `pr-<n>` worktree under its scratchpad", self.RUN)
        # `merge` removes the scratch trees when the PR lands; the orchestrator never does (#540).
        self.assertNotIn("remove the `pr-<n>` trees", self.RUN)

    def test_run_skill_classifies_every_reviewer_report_with_the_script(self):
        self.assertIn('scripts/worker-report" <pr> --reviewer', self.RUN)

    def test_run_skill_sends_back_a_report_without_a_suite_reason(self):
        self.assertIn("(no `suite:` line, or `not run` with no reason) is sent back to the same reviewer once", self.RUN)
        self.assertIn("incomplete <why>", self.RUN)


if __name__ == "__main__":
    unittest.main()
