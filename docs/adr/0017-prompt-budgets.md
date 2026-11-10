# A ticket names the module that owns new logic, and CI budgets the prompt prose

Extends [ADR-0006](0006-mechanical-rules-become-scripts.md).

## Context

ADR-0006 moves a rule from the prompts into a script once it needs no judgement and more than one prompt states it. That is a rule applied after the fact: by the time a rule has been restated twice, the copies have drifted.

- Tickets are written in terms of what an agent must do ("the run skill says..."), so building one adds prose to a prompt. `skills/run/SKILL.md` grew by about 900 words since #533 while scripts took over rules in the same period.
- The guard's forms are hand-copied into prompts. `skills/run/SKILL.md` restates `CHAIN_JOINERS` and `CHAIN_PLAIN` from `hooks/guard.py`; `agents/reviewer.md` quotes the review post shape; the two have already drifted (the guard accepts `gh pr review --comment` and a file-comment shape that the reviewer prompt says do not exist).
- Two rules were kept only in the lead's private memory: the grooming habit (below) and crediting the research a rule comes from.

## Decision

- **A ticket names the module that owns new logic before it is built.** Orchestration logic lands in a script or library first; the prompt gets at most one sentence that applies the script's word. Prose is allowed for judgement, and the ticket names that judgement.
- **Ticket template.** A ticket carries a `Lands in:` section: the owning module, its verdict words or actions, and a prose budget (words added to which prompt file). The interface stays under **Decide in the PR**. See `docs/agents/issue-tracker.md`.
- **Triage test.** A ticket that adds prompt prose names the judgement the prose carries, or it is not `ready-for-agent`. The lead applies it on each grooming pass, and does not fold an ungated ticket into one gated on `hooks/**`, or the reverse.
- **The word budget is a ratchet.** `tests/prompt_budget.json` holds a word budget per prompt file (`skills/*/SKILL.md`, `agents/*.md`, `skills/run/brief.md`), set at the count when this ADR was written. A PR that grows a prompt past its budget fails `tests/test_prompt_budget.py` and raises the budget in the same diff, where the reviewer sees it; the ticket's `Lands in:` names the judgement behind the raise. A count well below budget also fails, so the budget is lowered on the next edit.
- **Prompts quote no literal guard forms** once #624's per-form refusal reasons land: they say the guard's reason names the approved form. Until then `tests/test_guard_forms_in_prompts.py` pins each form a prompt still quotes against the guard's constants, so a change to `CHAIN_JOINERS`, `CHAIN_PLAIN` or the review post shape fails unless the prompt follows. The reviewer drift is fixed by narrowing the guard in #624, not here.
- **Glossary sources exist.** `tests/test_glossary_sources.py` fails when a `Source:` path in `GLOSSARY.md` is missing, unless the entry names a ticket (`#<n>`) that will create it.
- **Credit what we take.** A PR that takes a rule or mechanism from the Human Oversight System (CC BY 4.0: attribution, license link, note of changes) or from a paper names it in the doc, ADR or script it changes and adds it to `docs/sources.md`. The same test fails an ADR or doc whose `## Sources` section names an author and year that `docs/sources.md` lacks.

## Consequences

- Prompts stop growing silently; growth is a reviewed edit with a stated reason.
- A ticket costs a few more lines to write, and an agent building it knows where the logic goes.
- The budget is a count of words, not of rules: it cannot tell judgement from restated logic, so the reviewer and the `Lands in:` line still do.
- A PR in flight that grows a prompt fails after another PR lowers or fixes the budget; it raises the budget on its next push.
- Rejected: a budget as a warning (nothing forces the reason to be written); moving the budget into the config (it is the repo's, not each user's); a parser for `Lands in:` in CI (tickets live in the tracker, not the tree).

## Sources

This ADR takes no rule from outside research. The credit rule above applies to later PRs.
