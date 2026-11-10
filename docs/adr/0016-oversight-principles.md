# Oversight only tightens, human attention has a cost, author framing is untrusted, and verdicts are computed

## Context

Orchestrate decides, without a human in the loop, how closely each PR is reviewed and when a ticket needs a person. While charting map #588 (2026-11-10) the developer read the Human Oversight System (HOS), a Purdue doctoral framework for scaling human oversight of AI-written code (a 149-paper literature review plus five months of field findings), and settled four principles that every later ticket of the map builds against. A check of the repo that day found each one only partly true:

- the `size:S|M|L` label overrode the computed tier in both directions, so a label could lower a review;
- stops, hand-backs and lead asks were free text, some of them bare questions;
- ADR-0015 keeps untrusted comments out of a spec, but a reviewer still read the PR title, body and commit messages as fact;
- `scripts/gate` checked secrets, lockfiles, manifests, overlap and outside-map, and `worker-report --reviewer` checked only the `suite:` line, while the orchestrator read the reviewer's own `verdict:` word.

The evidence behind each principle:

- **Ratchet.** HOS's ratchet principle (a field finding): oversight set by automation may only go up. Ferdous et al. 2026 find a model's self-reported confidence uninformative, so nothing the agent says should lower a check.
- **Attention has a cost.** HOS notes its own ratchet counts human attention as free. Buçinca, Malaya and Gajos 2021 find explanations raise overreliance and forcing functions lower it; Adejumo and Johnson 2025 find descriptive, contextual and actionable explanations drive action.
- **Framing.** Alexopoulos et al. 2026 find an LLM-written PR description flips reviewers and that redacting it recovers the baseline catches; Kumar 2026 (SWE-PRBench) and Charoenwet et al. 2026 (AgenticSCR) find author framing flips reviewers and more context degrades review.
- **Deterministic first, verdicts computed.** Parris 2026 (AIRA) finds LLM reviewers suppress fail-soft findings 44:1 against a scanner, and AI code swallows exceptions 1.8x as often. HOS's field findings say to gate on computed signals, not self-reported verdicts, and that reviewer agents file confident reports that do not reproduce.

Full citations are in `docs/sources.md`.

## Decision

- **The ratchet.** Automation may only tighten oversight. Nothing an agent sets (a label, a PR body, a report line, a reviewer's tier note) lowers a review tier or removes a review. Only the config, which the developer writes, loosens anything. The `size:S|M|L` label therefore only raises the computed tier; a label below it is ignored.
- **Human attention has a cost.** Every item routed to a human (a stop comment, a hand-back, a lead ask, a third-round ask, a risk note) is a decision carrying a recommendation, the options and what each costs. A bare question is a defect; if the system can pick a safe default, it handles the item itself. In lead mode this binds both hops: the orchestrator's ask to the lead and the lead's relay to the developer.
- **Author framing is untrusted input to a reviewer.** The ticket is the spec. The PR title, body and commit messages are the author's claims and are read as claims, to be checked against the diff. ADR-0015 already applies this to ticket comments; this extends it to the PR.
- **Deterministic checks first, verdicts computed.** Script checks run before any model reviewer and set a **floor** the reviewer can only raise. A review's verdict is computed from its blocking findings and open threads, never read from the reviewer's own `verdict:` word, which stays as a cross-check that must agree.

## Consequences

- Builds under map #588 (tier inputs, fail-honestly checks, reviewer inputs, computed verdict, human-facing surfaces) take these as given and cite this ADR. #543 enforces the one-way label rule by moving tier computation onto `Config`.
- The config template and `.claude/orchestration.md` say a `size:` label only raises the tier.
- A reviewer can no longer be talked down by an author, and cannot talk the run down by its own verdict. A real reviewer miss costs a rerun only when a floor check disagrees with it.
- Stop and ask text gets longer and more structured; the lead and orchestrator prompts follow in the map's human-facing surfaces ticket.
- The glossary gains **Ratchet** and **Floor**.
- Rejected: letting a label lower the tier (a collaborator-only label is trusted by ADR-0015, but the one-way rule is simpler and an over-tiered S ticket costs one review, not a miss); a reviewer self-reported confidence or verdict as the gate (uninformative per Ferdous et al.); a model reviewer first with scripts as a second opinion (the reviewer's suppressed findings are then never checked, per Parris); passing the PR body to the reviewer with a warning (redaction recovers catches, a warning is advice); HOS's sign-off registers and scored risk dimensions (process weight, out of scope in map #588).

## Sources

- Thurlow, S. (2026). Human Oversight System (HOS): the ratchet principle, counting human attention as free, gating on computed signals, non-reproducing reviewer reports. See `docs/sources.md`.
- Ferdous et al. 2026; Alexopoulos et al. 2026; Kumar 2026; Charoenwet et al. 2026; Parris 2026; Buçinca, Malaya and Gajos 2021; Adejumo and Johnson 2025. See `docs/sources.md`.
- Map #588 and its research tickets #597, #598, #599, #606.
