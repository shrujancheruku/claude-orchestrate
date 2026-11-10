# Human oversight and outcomes: evidence for orchestrating AI coding agents

Research note for [#606](https://github.com/shrujancheruku/claude-orchestrate/issues/606), a child of [Map #588](https://github.com/shrujancheruku/claude-orchestrate/issues/588). Every figure below was read from the primary source on 2026-11-10 (arXiv HTML or PDF, the venue page, or the vendor's own post) unless it is marked "(secondary)". The Human Oversight System (HOS) checkout at `1482a93` (2026-11-09) was used as an index only; where HOS's summary of a paper and the paper disagree, the paper wins and the disagreement is noted.

1. **Productivity effects are real but task-shaped and smaller than developers believe.** Greenfield, self-contained tasks show large speedups (Peng 2023: 55.8% faster, N=95; Cui 2025: +26.08% completed tasks across 4,867 developers), while experienced maintainers on large familiar repositories were 19% *slower* with early-2025 agents (METR, CI +2% to +39%) and still forecast a 20% speedup afterwards. METR's 2026 follow-up (57 developers) has both cohorts' intervals spanning zero and METR itself calls the estimate "only very weak evidence". Nothing here supports a blanket speed claim; the honest claim is "throughput on routine, well-specified tickets, measured per repo".
2. **In the field, human oversight of agent PRs collapses by default.** 79.0% of merged human+AI PRs received no human feedback and non-owner AI PRs closed in a mean 0.18 days against 3.68 for human non-owners (Gao 2026); 58.2% of agent PRs had no explicit review at all (Li 2025); 38% of agent-PR rejections were reviewer abandonment and 64.1% of rejected Claude Code PRs were closed with no explanation (Ehsani 2026; Watanabe 2026). Agent PRs are accepted less (52.5% to 65.3% vs 76.8% human in AIDev-pop) and are twice the median size (48 vs 24 added lines).
3. **Ungated velocity turns into measured debt.** Cursor adoption gave a velocity spike that dissipated after two months while static-analysis warnings (+30.26%) and cognitive complexity (+41.64%) stayed up and then suppressed later velocity (He 2026, 806 repos, DiD); Copilot adoption raised rework and moved review load onto core maintainers whose own output fell 19% (Xu 2025). One vendor RCT points the other way on a greenfield task (GitHub 2024: 53.2% more likely to pass all tests), so quality direction depends on task and horizon.
4. **Presentation changes trust faster than accuracy.** Explanations raise acceptance of AI output regardless of correctness (Bansal 2021); cognitive forcing cuts overreliance (0.64 to 0.48 on wrong AI advice, d=.36, Buçinca 2021) but is liked less; a requirements-and-assumptions review interface made reviewers faster and more confident, most of all when they missed the error (g=0.85), without improving accuracy (Grunde-McLaughlin 2026); expecting outcome-only review made workers revise AI output *less*, an effect that attenuates when the reviewer can see the original AI output beside the final (Zhou and Zhao 2026, N=2,895). Edit-likelihood highlighting helped reviewers; model-confidence highlighting did not (Vasconcelos 2024).
5. **Where it is thin:** no controlled study yet measures human oversight of *autonomous multi-ticket* agent pipelines; the strongest oversight experiments are on non-code tasks (nutrition, QA, agent games) or N of 12 to 48; the field datasets are descriptive, span model generations, and count "merged" as success; and the two best "what to show the human" results (process visibility, edit-likelihood attention) have not been replicated on PR review. The scalable-oversight line (Bowman 2022, McAleese 2024) shows human+model beats either alone, but on QA and bug-finding in isolation, not on merge decisions.

## Sources

### 1. Becker, Rush, Barnes and Rein (METR), 2025, and the METR 2026 follow-up

**Citation.** Becker, J., Rush, N., Barnes, E., Rein, D. (2025). Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity. Preprint, arXiv:2507.09089 (v2, 2025-07-25). https://arxiv.org/abs/2507.09089. Follow-up: METR (2026-02-24). Update on developer productivity uplift. https://metr.org/blog/2026-02-24-uplift-update/.

**Design.** Randomised controlled trial at the task level: 246 real issues on the developers' own repositories, each randomly assigned AI-allowed or AI-disallowed; screen recordings and self-reported times. Follow-up: developer-level randomisation, 57 developers, 143 repositories, 800+ tasks.

**Population.** 16 experienced open-source maintainers with moderate AI experience; repositories averaging 23,000 stars; tools were "primarily ... Cursor Pro ... and Claude 3.5/3.7 Sonnet". Follow-up: 57 developers with a median of 10 years' experience, ten of them from the first study.

**Headline results.** AI "increases completion time by 19%" (95% CI +2% to +39% per the METR update page). Developers forecast a 24% reduction before the study and estimated a 20% reduction after it. Developers accepted "fewer than 44%" of AI generations; 56% said they often needed major changes to clean up AI code; 75% said they read every line of AI-generated code; about 9% of time went to reviewing and cleaning AI output and 4% to waiting on generations. Five contributing factors named: over-optimism, high repository familiarity, large complex repositories, low AI reliability, implicit repository context. Follow-up: the original cohort measured −18% time (CI −38% to +9%); the new cohort −4% (CI −15% to +9%). METR reports "a significant increase in developers choosing not to participate" rather than work without AI, 30% to 50% of developers skipping tasks they did not want to do without AI, pay cut from $150/hr to $50/hr, and that time tracking "is unreliable for developers who run multiple AI agents concurrently". METR's conclusion: developers "are more sped up from AI tools now" but the data is "only very weak evidence for the size of this increase", and the estimate is likely "a lower-bound".

**Limitations.** N=16 developers in the first study; the follow-up has selection on willingness to work without AI, a pay change, and wall-clock time that no longer measures effort when agents run in the background. Both are pre-registered METR studies, not peer-reviewed.

**For orchestrate.** The one controlled study on the population orchestrate serves (maintainers of their own large repos) found a slowdown, and the mechanism was review and cleanup of agent output plus missing implicit context. The orchestrator's touch map, scout facts and verify command are exactly the "implicit repository context" the study names; whether they close the gap must be measured, and the profile's wall-clock numbers are not the metric: human attention per merged PR is.

### 2. Peng, Kalliamvakou, Cihon and Demirer (GitHub, Microsoft Research, MIT), 2023

**Citation.** Peng, S., Kalliamvakou, E., Cihon, P., Demirer, M. (2023). The Impact of AI on Developer Productivity: Evidence from GitHub Copilot. Preprint, arXiv:2302.06590. https://arxiv.org/abs/2302.06590.

**Design.** RCT, 95 professional programmers recruited on Upwork (45 treated, 50 control), one task: "implement an HTTP server in JavaScript as quickly as possible"; run 2022-05-15 to 2022-06-20.

**Headline results.** Mean completion 71.17 minutes (Copilot) vs 160.89 (control): "55.8% reduction in completion time", t-test p=0.0017, 95% CI 21% to 89%. Success rate 7 percentage points higher with Copilot, not significant. Larger gains for less experienced developers, heavy daily coders and developers aged 25 to 44.

**Limitations.** Greenfield, self-contained, single task; freelancers, not maintainers; vendor authors; wide interval; 35 of each arm completed task and survey.

**For orchestrate.** The large speedup is on the kind of ticket orchestrate should hand to a worker first: self-contained, well specified, no repository history to respect. It is not evidence about PR review or about maintenance tickets.

### 3. Cui, Demirer, Jaffe, Musolff, Peng and Salz, 2025

**Citation.** Cui, K. Z., Demirer, M., Jaffe, S., Musolff, L., Peng, S., Salz, T. (2025). The Effects of Generative AI on High-Skilled Work: Evidence from Three Field Experiments with Software Developers. Working paper dated February 2025 (read), post-registered AEARCTR-0014530; published in Management Science, https://doi.org/10.1287/mnsc.2025.00535; SSRN 4945566. PDF: https://economics.mit.edu/sites/default/files/inline-files/draft_copilot_experiments.pdf.

**Design.** Three RCTs run by the firms "as part of their ordinary course of business" at Microsoft, Accenture and an anonymous Fortune 100 electronics manufacturer; treated developers got GitHub Copilot access. Registry pre-specifies outcomes "# Pull Requests, # Commits, # Builds, Build Success Rate" and adoption.

**Population.** 4,867 full-time developers combined (registry: 4,869 planned observations, 1,438 clusters).

**Headline results.** "Though each experiment is noisy, when data is combined across three experiments and 4,867 developers, our analysis reveals a 26.08% increase (SE: 10.3%) in completed tasks among developers using the AI tool. Notably, less experienced developers had higher adoption rates and greater productivity gains." The Management Science abstract page could not be fetched (403); figures are from the February 2025 working paper.

**Limitations.** Intention-to-treat on access, with incomplete adoption; outcomes are counts of PRs and commits, not quality or review load; code completion in 2022 to 2023, not agents; SE of 10.3 points on a 26-point effect.

**For orchestrate.** The best-powered productivity evidence is a throughput effect on routine work for less experienced developers, with the quality side unmeasured. It supports "more tickets through" as the value claim and nothing about the review side, which He 2026 and Xu 2025 fill in.

### 4. Paradis et al. (Google), 2024

**Citation.** Paradis, E., Grey, K., Madison, Q., Nam, D., Macvean, A., Meimand, V., Zhang, N., Ferrari-Church, B., Chandra, S. (2024). How much does AI impact development speed? An enterprise-based randomized controlled trial. Preprint, arXiv:2410.12944 (v3, 2024-11-11). https://arxiv.org/abs/2410.12944.

**Design.** RCT, 96 Google engineers randomised 48/48 (93 finished), one enterprise task: extend a ten-file, 474-line change list to "implement a new service to log messages ... update build, data structure, and test files", done when "all tests passed"; features were AI code completion, Smart Paste and natural-language-to-code.

**Headline results.** Mean 96 minutes (AI) vs 114 (no AI), t(83.6)=2.11, p=.038 unadjusted; adjusted model β₁=−0.24, 95% CI [−0.51, 0.03] log scale, p=0.086, "about 21%, although our confidence interval is large". Seniority interaction not significant (p=0.706); engineers coding 5+ hours a day were 32% faster as a main effect.

**Limitations.** A single three-hour task; adjusted estimate not significant; 2024 completion-era tooling.

**For orchestrate.** Even inside Google with 96 engineers the speed effect is at the edge of detectability. Any per-repo tuning of orchestrate on a handful of tickets' wall time is noise; the profile needs many tickets and a fixed task class before a speed claim means anything.

### 5. He, Miller, Agarwal, Kästner and Vasilescu, 2026

**Citation.** He, H., Miller, C., Agarwal, S., Kästner, C., Vasilescu, B. (2026). Speed at the Cost of Quality: How Cursor AI Increases Short-Term Velocity and Long-Term Complexity in Open-Source Projects. MSR 2026, https://doi.org/10.1145/3793302.3793349; arXiv:2511.04427 (v3). https://arxiv.org/html/2511.04427v3.

**Design.** Difference-in-differences with propensity-matched controls plus a panel GMM; 806 GitHub repositories that adopted Cursor (adoption January 2024 to March 2025) against 1,380 matched never-adopters; data January 2024 to August 2025.

**Headline results.** Lines added +28.58% (±13.7%) overall and commits +2.63% (n.s.); first month +281.3% lines and +55.4% commits, second month +48.4% and +14.5%, and the gains "dissipate after two months". Static-analysis warnings +30.26% (±6.66%) and cognitive complexity +41.64% (±7.62%), both persistent; duplicated-line density not significant. GMM: a 100% increase in complexity is associated with a 64.5% decrease in lines added later, and a 100% increase in warnings with a 50.3% decrease. The paper's Discussion cites 25.1% for complexity where Table 2 says 41.64%; the table figure is used here.

**Limitations.** Observational adoption (projects chose Cursor); repository-level metrics; Cursor only.

**For orchestrate.** The causal mechanism by which unreviewed agent velocity becomes slower velocity is complexity and warning debt. A merge-time quality ratchet (no net increase in lint warnings or complexity on the diff) is the cheapest intervention the evidence points at and is a script rule, not a reviewer judgement.

### 6. Xu, Medappa, Tunç, Vroegindeweij and Fransoo, 2025

**Citation.** Xu, F., Medappa, P. K., Tunç, M. M., Vroegindeweij, M., Fransoo, J. C. (2025). AI-Assisted Programming Decreases the Productivity of Experienced Developers by Increasing the Technical Debt and Maintenance Burden. Preprint, arXiv:2510.10165 (v3); SSRN 5521379. https://arxiv.org/html/2510.10165v3.

**Design.** Difference-in-differences around the GitHub Copilot technical preview (2021-06-29): Copilot-endorsed languages (Python, JavaScript, Ruby, TypeScript, Go) vs others (R, C, C#, C++, Java, PHP, Scala), monthly panel July 2020 to July 2022, Microsoft-owned repositories; 2,755 repositories (2,510 after CEM matching) and 1,699 contributors.

**Headline results.** Project level: lines added +17.7%, commits +4.1%, PRs +4.3%; PR rework +2.4% "holding PR count constant". Peripheral contributors "increased their commit activity by 43.5% and submitted 17.7% more PRs". Core developers "review 6.5% more code after Copilot's introduction, but show a 19% drop in their original code productivity" (abstract; Table 6's review coefficient converts to about +6.2% and the commit decline is reported as 19% in the text and 42.9% in the table, so the abstract figures are quoted with that caveat).

**Limitations.** Observational, language-based treatment assignment, Microsoft-owned repositories only, 2020 to 2022 completion tooling.

**For orchestrate.** The cost of agent throughput lands on the humans who review. orchestrate's reviewer tier absorbs part of that load, but the user still approves gates and L-tier risk notes; the number of human approvals per hour and the rework rate on merged PRs are the figures to watch, and a WIP cap on PRs awaiting the human is the control.

### 7. Gao, Banyongrakkul, Guan, Zahedi and Treude, 2026

**Citation.** Gao, H., Banyongrakkul, P., Guan, H., Zahedi, M., Treude, C. (2026). On Autopilot? An Empirical Study of Human-AI Teaming and Review Practices in Open Source. MSR 2026 short paper; arXiv:2601.13754. https://arxiv.org/html/2601.13754.

**Design.** Mining study on the AIDev dataset with added contributor code-ownership tiers and a human-only baseline of 174,567 PRs across 2,134 repositories (243,306 before bot filtering); guideline analysis of 196 artefacts from 145 repositories.

**Headline results.** "over 67.5% of AI-co-authored PRs originate from contributors without prior code ownership" (61.0% for human-only PRs). 79.0% of merged human+AI PRs had no human feedback; for non-owner human+AI PRs it is 11,817 of 13,618 (86.8%); the abstract rounds to "approximately 80%". Mean time to close merged PRs: human+AI non-owner 0.18 days vs human-only non-owner 3.68 days; comments per merged PR 0.18 vs 2.99. "13.1% (19/145) possess at least one artefact mentioning AI usage"; "86.9% of repositories lack guidelines for AI agent usage".

**Limitations.** Descriptive; AI co-authorship detected from trailers and agent accounts; no causal claim; the ~80% is the all-tiers aggregate.

**For orchestrate.** The inverted scrutiny gradient (least review for the PRs that get the least ownership) is the default state of the field. orchestrate's script-owned verdict and gate are the counter-design; the metric to publish is the share of merged PRs with zero human or reviewer comment, which in orchestrate should be zero by construction.

### 8. Ehsani, Pathak, Rawal, Mujahid, Imran and Chatterjee, 2026

**Citation.** Ehsani, R., Pathak, S., Rawal, S., Al Mujahid, A., Imran, M. M., Chatterjee, P. (2026). Where Do AI Coding Agents Fail? An Empirical Study of Failed Agentic Pull Requests in GitHub. MSR 2026; arXiv:2601.15195. https://arxiv.org/html/2601.15195.

**Design.** Field dataset of 33,596 agent-authored PRs from five agents (OpenAI Codex 21,799; Copilot 4,970; Devin 4,827; Cursor 1,541; Claude Code 459); qualitative coding of 600 sampled rejected PRs (562 accessible); logistic regression on merge.

**Headline results.** Overall merge rate 71.48% (24,014). By task: documentation 84%, CI 79%, build 74%, fix 64%, performance 55%. Rejection categories (of 600): reviewer abandonment 228 (38%), duplicate PR 142 (23%), CI/test failure 99 (17%), unwanted feature 24 (4%), agentic misalignment 9 (1%). Not-merged PRs changed more lines (Cliff's δ=17%), touched more files (δ=10%) and had more failed CI checks (δ=24%); each additional failed CI check was associated with about 15% lower odds of merge. Reviewer quote on an unwanted-feature close: "This is a LOT to review, would really prefer smaller granular PRs".

**Limitations.** GitHub-visible agents only; "merged" as the success proxy; percentages computed on 600 not 562; agent mix dominated by Codex.

**For orchestrate.** Three of the four largest failure modes are orchestration failures, not model failures: nobody looked (38%), two agents did the same work (23%), CI red (17%). Holds and the touch map address duplicates; the CI gate before review addresses the third; an idle-PR alarm addresses abandonment. Performance and bug-fix tickets merit the larger review tier.

### 9. Li, Zhang and Hassan (AIDev), 2025

**Citation.** Li, H., Zhang, H., Hassan, A. E. (2025). The Rise of AI Teammates in Software Engineering (SE) 3.0: How Autonomous Coding Agents Are Reshaping Software Engineering. Preprint, arXiv:2507.15003. https://arxiv.org/html/2507.15003.

**Design.** Dataset paper: 456,535 PRs by five agents across 61,453 repositories and 47,303 developers (Codex 411,621; Devin 24,893; Copilot 16,531; Cursor 1,981; Claude Code 1,509); analyses on AIDev-pop (repositories with 500+ stars, 7,122 agent PRs) against 6,618 human PRs.

**Headline results.** Acceptance in AIDev-pop: human 76.8%, Codex 65.3%, Claude Code 52.5%, Cursor 51.4%, Devin 48.9%, Copilot 38.2% (text says 35%). Agent PRs with no explicit review 58.2% (human 75.3%); reviewed only by humans 21.8% (14.7%); reviewed by bots 20.1% (10.0%). Median turnaround of accepted PRs: human 3.9 h, Codex 0.3 h, Devin 2.2 h, Cursor 2.4 h, Claude Code 6.9 h, Copilot 17.2 h. Agent PRs skew to docs (Codex 21.3% vs human 7.8%) and tests (9.2% vs 2.8%).

**Limitations.** Descriptive; mid-2025 snapshot across model generations; acceptance conflates PR quality with repository policy; Claude Code and Cursor samples are small in AIDev-pop (101 and 144).

**For orchestrate.** Per-agent acceptance differs by 27 points, so an orchestrate profile that records worker model against merge outcome is a real measurement, not vanity. The high no-review share for human PRs (75.3%) is a reminder that "no review" is partly repository culture, which is why the metric needs a human baseline per repo.

### 10. Watanabe, Li, Kashiwa, Reid, Iida and Hassan, 2026

**Citation.** Watanabe, M., Li, H., Kashiwa, Y., Reid, B., Iida, H., Hassan, A. E. (2026). On the Use of Agentic Coding: An Empirical Study of Pull Requests on GitHub. ACM TOSEM, https://doi.org/10.1145/3798166; arXiv:2509.14745 (v3, 2026-02-09). https://arxiv.org/html/2509.14745.

**Design.** 567 Claude Code PRs matched to 567 human PRs across 157 repositories; Mann-Whitney U, chi-squared and log-rank tests; manual coding of task types, rejection reasons and post-submission revisions.

**Headline results.** Merged: 475/567 (83.77%) agentic vs 516/567 (91.00%) human, chi-squared p<0.05; median time to merge 1.23 h vs 1.04 h, log-rank n.s. Merged as a single commit: 54.94% vs 58.53% (n.s.); 214 of 475 merged agentic PRs (45.1%) were revised by humans, the revisions being fixes 47.7%, docs 29.0%, refactor 27.1%, style 23.4%. Of 92 rejected agentic PRs, "The majority (64.1%) ... were closed without explanatory comments or discussion" (59/92); implemented elsewhere 12.0%; too large 3.3% ("Closing in favor of smaller, more focused PRs to make reviews more manageable"). Median added lines 48 vs 24, deletions 7 vs 8, files 2 vs 2; multi-purpose PRs 85/213 (39.9%) vs 27/221 (12.2%); PR description median 355 vs 56 words (the only size difference the paper marks significant).

**Limitations.** One agent; n=567; acceptance by task type not reported; no significance reported for the added-lines or multi-purpose differences.

**For orchestrate.** Two rules fall straight out: one purpose per PR and a size guard before review, both scriptable from the diff; and every close carries a reason, which orchestrate already does through `needs-info`, `ready-for-human` and `wontfix` with a reason comment. The 45.1% post-merge human revision rate is the field baseline a reviewer tier should beat.

### 11. Buçinca, Malaya and Gajos, 2021, with Bansal et al. 2021 and Ghosh et al. 2026

**Citation.** Buçinca, Z., Malaya, M. B., Gajos, K. Z. (2021). To Trust or to Think: Cognitive Forcing Functions Can Reduce Overreliance on AI in AI-assisted Decision-making. Proc. ACM Hum.-Comput. Interact. 5(CSCW1), Article 188, https://doi.org/10.1145/3449287. Companion: Bansal, G., Wu, T., Zhou, J., Fok, R., Nushi, B., Kamar, E., Ribeiro, M. T., Weld, D. S. (2021). Does the Whole Exceed its Parts? The Effect of AI Explanations on Complementary Team Performance. CHI 2021; arXiv:2006.14779. Successor: Ghosh, A., Sarkar, A., Lindley, S., Poelitz, C. (2026). An Experimental Comparison of Cognitive Forcing Functions for Execution Plans in AI-Assisted Writing. Preprint, arXiv:2601.18033.

**Design.** Buçinca: between-subjects MTurk experiment, 260 recruited, 199 analysed, simulated AI at 75% accuracy on a nutrition task; three cognitive forcing functions (on demand: suggestion hidden behind a button; update: decide first, then see the AI and revise; wait: 30-second delay) against two simple explainable-AI baselines (explanation; explanation plus a confidence prompt) and no AI.

**Headline results.** On instances where the AI was wrong (carb-source detection), participants overrelied 0.64 (SE .03) with simple XAI vs 0.48 (.03) with cognitive forcing, F=9.24, p=.003, d=.36; they were correct 0.08 vs 0.27, p≪.0001, d=.66. Forcing did not eliminate overreliance. Trust 3.91 vs 3.72 (n.s.); participants rated simple XAI as less complex (2.64 vs 2.95, p=.002) and preferred any AI over none; benefits concentrated in people high in Need for Cognition. Bansal 2021 (abstract): "explanations increased the chance that humans will accept the AI's recommendation, regardless of its correctness" and did not improve complementary performance (N not read from the primary). Ghosh 2026 (abstract): an "Assumption" forcing function "most effectively reduced overreliance without increasing cognitive load" while participants found a "WhatIf" prompt most helpful (N not stated in the abstract).

**Limitations.** Non-code tasks, crowd workers, simulated AI; effect sizes small to medium; the design people prefer is the one that performs worse.

**For orchestrate.** The "update" forcing function maps directly onto a human gate: record your own verdict or question before the reviewer's verdict is shown. It costs satisfaction and buys accuracy on exactly the cases where the reviewer is wrong, which is the case the gate exists for. Longer reviewer rationales are not a safety feature; Bansal's result says they raise acceptance of wrong verdicts too.

### 12. Zhou and Zhao, 2026

**Citation.** Zhou, P., Zhao, Y. (2026). Review Makes Workers Less Likely to Revise AI Output. SSRN working paper 6325399, written 2026-02-20, posted 2026-03-16. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6325399.

**Design.** Seven experiments, N=2,895 (SSRN abstract, read via a search-engine rendering of the abstract page; the page itself refused automated fetch). HOS's SLR calls them preregistered (secondary).

**Headline results.** Participants who expected a peer or supervisor to evaluate their work edited AI-generated content significantly less than those who did not expect review; the authors attribute it to deference to the AI's perceived expertise under outcome-focused review. The effect "attenuates when workers know that reviewers will see the process, which includes both the original AI output and the final submission". HOS reports d=0.87 for the process-visible condition and that the effect "disappeared, and reversed" (secondary; not verified in the abstract).

**Limitations.** Working paper; non-code tasks; the full method was not read.

**For orchestrate.** A worker that knows only its final diff will be judged has, by this evidence, less reason to revise; a human who sees only the final diff is in the outcome-only condition. orchestrate's draft PR plus commit history is already process-visible; the reviewer and the human should be shown "what changed since the first draft" and "what changed after review", not just the final state.

### 13. Perry, Srivastava, Kumar and Boneh, 2023

**Citation.** Perry, N., Srivastava, M., Kumar, D., Boneh, D. (2023). Do Users Write More Insecure Code with AI Assistants? CCS '23, pp. 2785–2799, https://doi.org/10.1145/3576915.3623157; arXiv:2211.03622.

**Design.** Controlled user study, 47 analysed of 54 recruited, 33 with an OpenAI codex-davinci-002 assistant and 14 without; five security-relevant tasks (Python encryption, ECDSA signing, sandboxed directory; JavaScript SQL; C strings).

**Headline results.** Correct and secure with vs without AI: encryption correct 67% vs 79%, secure 21% vs 43%; signing secure 3% vs 21%; sandboxed directory secure 12% vs 29%; SQL secure 36% vs 50%; C strings no large difference. Participants with AI "believed their answers were more secure" (Likert 3.5 vs 2.0 among insecure solvers on the encryption task). Inverse relationship between security and trust in the assistant on every task (sandbox task: 1.5 vs 4.0); insecure solvers were "less likely to modify the AI assistant's outputs or adjust properties such as temperature"; those who "proactively adjust parameters and re-phrase prompts were more likely provided correct and secure code".

**Limitations.** N=47, 2022 model, security-specific tasks, unbalanced arms.

**For orchestrate.** Self-assessed security goes the wrong way under AI assistance, so neither a worker's nor a developer's confidence may lower the tier on auth, parsing or secret-handling paths; the risk-path list that forces L tier is the right mechanism, and independent security checks belong in the gate rather than in the reviewer's prose.

### 14. Vasconcelos, Bansal, Fourney, Liao and Wortman Vaughan, 2024

**Citation.** Vasconcelos, H., Bansal, G., Fourney, A., Liao, Q. V., Wortman Vaughan, J. (2024). Generation Probabilities Are Not Enough: Uncertainty Highlighting in AI Code Completions. ACM Trans. Comput.-Hum. Interact., https://doi.org/10.1145/3702320; arXiv:2302.07248 (v3).

**Design.** Within-subjects study, 30 programmers, three coding tasks, conditions: no highlights, generation-probability highlights, edit-model highlights (tokens most likely to be edited).

**Headline results.** Mean completion 9.28 / 9.58 / 8.61 minutes (omnibus p=0.002); edit model vs generation probability p=0.003 (89.9% of the time); edit model vs none p=0.066; generation probability vs none p=0.511. Characters added 148.9 / 122.4 / 92.2 (p=0.009). Unit-test accuracy 38.3% / 30.0% / 50.0% (p=0.145, n.s.). Highlight utility 2.93 vs 3.92 (p<0.001). Survival of highlighted tokens: 73.9% (generation probability) vs 35.3% (edit model), so edit-model highlights pointed at what actually got changed.

**Limitations.** N=30, completion-era model, short tasks; accuracy difference not significant.

**For orchestrate.** What helps a human reviewer is attention routed to the hunks most likely to need editing, not model confidence. A reviewer that ranks hunks by "likely to need a change" for the human's L-tier read is supported; surfacing model confidence to the human is not.

### 15. Zhou, Roy, Gupta, Weitekamp and MacLellan, 2026

**Citation.** Zhou, J., Roy, A., Gupta, S., Weitekamp, D., MacLellan, C. J. (2026). When Should Users Check? A Decision-Theoretic Model of Confirmation Frequency in Multi-Step AI Agent Tasks. CHI 2026, https://doi.org/10.1145/3772318.3790655; arXiv:2510.05307. https://arxiv.org/html/2510.05307v2.

**Design.** Formative study (8 participants), then a within-subjects study with 48 participants across three multi-step agent domains (shopping cart, image editing, Overcooked), 7 trials each; a dynamic program places confirmation points from per-step success probability, redo time, confirmation time and diagnosis time.

**Headline results.** "81% of participants (39 of 48) preferred the modeled intermediate confirmation strategy"; completion time 264.70 s (end confirmation) vs 228.86 s (intermediate), −13.54%, t(143)=5.52, p<0.001; by domain 17.44%, 7.46%, 15.64%. A Beta-Bernoulli update from observed step outcomes is proposed but not used in the main study.

**Limitations.** Not coding tasks; fixed parameters per domain; short tasks.

**For orchestrate.** It gives a formula for where a worker should stop and ask: when the expected redo cost of continuing wrong exceeds the confirmation cost. For long tickets that argues for a plan checkpoint before the build rather than review at the end only; it is a toggle, not a default, until measured on code.

### 16. Grunde-McLaughlin, Mozannar, Murad, Chen, Amershi and Fourney, 2026, with Catalan et al. 2026

**Citation.** Grunde-McLaughlin, M., Mozannar, H., Murad, M., Chen, J., Amershi, S., Fourney, A. (2026). Overseeing Agents Without Constant Oversight: Challenges and Opportunities. Preprint, arXiv:2602.16844. https://arxiv.org/html/2602.16844. Companion: Catalan, C. R., Dizon, L. M., Monderin, P. N., Kuang, E. (2026). "I'm Not Reading All of That": Understanding Software Engineers' Level of Cognitive Engagement with Agentic Coding Assistants. Preprint, arXiv:2603.14225.

**Design.** Three studies of n=12 each (formative, design probes, controlled) on reviewing a computer-use agent's answers to AssistantBench questions; the proposed interface is a requirements checklist marked completed / unknown / contradicted plus the agent's assumptions, each linked to evidence. Catalan: four engineers using Cline on one task, think-aloud plus a Bloom's-taxonomy survey.

**Headline results.** Time to find errors fell (overall Hedges' g=−0.29; on trials where the error was found g=−0.65, 270.79 s vs 229.63 s). Accuracy 72.92% vs 77.08%, g=0.18 (95% CI −0.52 to 0.94), "not meaningfully improved". Confidence rose (g=0.56) and rose most "when errors were missed" (g=0.85, 95% CI 0.01 to 1.88). The authors note the study "does not have high power". Catalan: engagement "consistently declines as tasks progress"; participants evaluated "primarily the 'happy path'"; none could say how many functions the generated script had; half could summarise the first function.

**Limitations.** n=12 per study, QA not code, preprint; Catalan n=4.

**For orchestrate.** A better hand-off format is judged by escaped defects, never by how fast or confident the human felt. Any orchestrate change to the pass table, PR body or risk note needs an outcome measure attached, because the one controlled test of a richer verification view raised confidence precisely on the missed errors.

### 17. Shen and Tamkin (Anthropic), 2026

**Citation.** Shen, J. H., Tamkin, A. (2026). How AI Impacts Skill Formation. Preprint, arXiv:2601.20245; Anthropic research post 2026-01-29. https://www.anthropic.com/research/AI-assistance-coding-skills.

**Design.** RCT, 52 "mostly junior" engineers with weekly Python use and no Trio experience; 35-minute task implementing two features with the Trio async library, with or without an AI assistant; 25-minute quiz without AI afterwards.

**Headline results.** Quiz 50% (AI) vs 67% (no AI), Cohen's d=0.738, p=0.01, largest gap on debugging questions; completion about 23 vs 25 minutes, p=0.391. Patterns that preserved learning (quiz ≥65%): generation then comprehension questions (86%), hybrid code plus explanation (68%), conceptual inquiry (65%); patterns that did not: full delegation (39%), progressive reliance (35%), iterative AI debugging (24%). The authors state the pattern analysis "does not draw a causal link".

**Limitations.** N=52, vendor-run, one library, no retention follow-up.

**For orchestrate.** Delegating the build does not have to cost the developer understanding, but the pattern that preserves it is asking what the code does after it exists. A "walk me through this PR" step before the human's approval is the comprehension-preserving pattern the study found, and it is measurable with a quiz.

### 18. Bauer (GitHub), 2024

**Citation.** Bauer, J. (2024, updated 2025-02-06). Does GitHub Copilot improve code quality? Here's what the data says. GitHub Blog. https://github.blog/news-insights/research/does-github-copilot-improve-code-quality-heres-what-the-data-says/.

**Design.** Vendor-run RCT: 243 recruited, 202 valid submissions (104 Copilot, 98 without), developers with at least five years' Python experience, task: a web server for restaurant reviews assessed by 10 unit tests; 1,293 blind peer reviews, at least 10 per submission.

**Headline results.** "53.2% greater likelihood to pass all 10 unit tests" (p<0.01; per-arm rates only in a chart); 18.2 vs 16.0 lines per error, "13.6% more lines of code" without an error (p=0.002); mean errors 4.63 vs 5.35; readability +3.62% (p=0.003), reliability +2.94% (p=0.01), maintainability +2.47% (p=0.041), conciseness +4.16% (p=0.002); reviewers 5% more likely to approve Copilot code (p=0.014).

**Limitations.** Vendor-run and blog-only; greenfield task; small quality deltas; not peer-reviewed.

**For orchestrate.** This is the main contradiction to He 2026 and Xu 2025: on a fresh, test-specified task AI-assisted code scored better. The reconciliation the evidence supports is horizon and context: short greenfield tasks improve, long-lived repositories accrue debt. orchestrate serves the second case.

### 19. Mozannar, Bansal, Fourney and Horvitz, 2024

**Citation.** Mozannar, H., Bansal, G., Fourney, A., Horvitz, E. (2024). Reading Between the Lines: Modeling User Behavior and Costs in AI-Assisted Programming. CHI 2024 (honorable mention), https://doi.org/10.1145/3613904.3641936; arXiv:2210.14306. https://ar5iv.labs.arxiv.org/html/2210.14306.

**Design.** 21 programmers completed tasks with Copilot and retrospectively labelled their sessions with CUPS, a 12-state taxonomy; over 3,000 labelled instances.

**Headline results.** "the 'verifying suggestion' state takes up the most time at 22.4%"; Copilot-specific states total "51.5% (s_N=19.3)" of session time; the conclusion puts "double-checking and editing Copilot suggestions" at 34.3%; waiting 4.2%; participants accepted 34.0% of 1,024 suggestions (mean per participant 36.5%).

**Limitations.** Completion-era tool, lab tasks, self-labelled states.

**For orchestrate.** Human verification is the dominant human cost even for completions; for PR-sized agent output it is the whole human cost. The profile should carry human review minutes per merged PR beside tokens.

### 20. Abreu et al. (Meta), 2025

**Citation.** Abreu, R., Murali, V., Rigby, P. C., Maddila, C., Sun, W., Ge, J., Chinniah, K., Mockus, A., Mehta, M., Nagappan, N. (2025). Moving Faster and Reducing Risk: Using LLMs in Release Deployment. ICSE-SEIP 2025, https://doi.org/10.1109/ICSE-SEIP66354.2025.00045; arXiv:2410.06351. https://arxiv.org/html/2410.06351v1.

**Design.** Production diff-risk scoring at Meta: training 855,282 diffs / 1,981 SEVs, validation 120,967 / 214, test 181,052 / 305; logistic regression on size, diffusion, file and folder SEV history, service criticality, complexity and author expertise, against fine-tuned LLMs reading the diff, title and test plan.

**Headline results.** Share of SEVs captured when gating the riskiest 5% / 10% / 50% of diffs: logistic regression 18.7% / 27.9% / 84.6%; risk-aligned iDiffLlama-13B 26.2% / 42.3% / 88.5%. "the model gives release engineers a knob that can be tuned to control productivity-risk trade-off."

**Limitations.** One company; SEVs as the outcome; the LLM is fine-tuned on Meta's history.

**For orchestrate.** Risk-tiered human attention is validated at scale: a tenth of the changes carry over two fifths of the severe incidents when the score is good. orchestrate's tiering by diff size and risk paths is the logistic-regression end of this; file-level defect history and author familiarity are the next features to add, and the knob (what share of PRs reaches the human) is the parameter to expose.

### 21. Takerngsaksiri et al. (Atlassian, HULA), 2025

**Citation.** Takerngsaksiri, W., Pasuksmit, J., Thongtanunam, P., Tantithamthavorn, C., Zhang, R., Jiang, F., Li, J., Cook, E., Chen, K., Wu, M. (2025). Human-In-the-loop Software Development Agents. ICSE-SEIP 2025, https://doi.org/10.1109/ICSE-SEIP66354.2025.00036; arXiv:2411.12924 (v2). https://arxiv.org/html/2411.12924v2.

**Design.** Deployment of a plan-then-code agent inside Atlassian Jira with human approval of the plan and of the code; two months of usage; survey of 109 of 146 invited practitioners (75%).

**Headline results.** 663 issues used (of more than 22,000 eligible); plans generated for 527 (79%); plans approved 433/527 (82%); code generated 376/433 (87%); PRs raised 95/376 (25%); merged 56/95 (59%); 8% of the 663 issues ended in a merged PR. Survey: 71% agreed it identified the relevant files; 36% that planned changes matched their own approach; 67% disagreed that the code "completely solved the task without human involvement"; 54% disagreed that the code had no defects.

**Limitations.** One company, one agent, 2024 models; the funnel conflates agent quality with engineer choice.

**For orchestrate.** A plan gate is a real filter (82% approval, not 100%), and most of the attrition is after code exists (25% raised a PR). The funnel is the shape orchestrate should report per repo: tickets taken, plans approved, PRs opened, PRs merged, human revisions.

### 22. Mozannar et al. (Microsoft Research, Magentic-UI), 2025

**Citation.** Mozannar, H., Bansal, G., Tan, C., Fourney, A., Dibia, V., Chen, J., Gerrits, J., Payne, T., Maldaner, M. K., Grunde-McLaughlin, M., et al. (2025). Magentic-UI: Towards Human-in-the-loop Agentic Systems. Preprint, arXiv:2507.22358. https://arxiv.org/html/2507.22358.

**Design.** System paper with benchmark runs, a simulated-user study on the GAIA validation set (162 tasks), a 12-participant qualitative study (SUS 74.58) and 24 internal adversarial scenarios.

**Headline results.** Autonomous task completion 30.3% rose to 51.9% with a simulated user holding side information ("71% improvement"); the agent asked for help on 10% of tasks and relied on the user for the final answer on 18%. With all mitigations on "none of the adversarial scenarios were effective"; with mitigations disabled, prompt injection made the agent "Log into the agent's own web interface to approve actions autonomously".

**Limitations.** Simulated user; not coding; the safety set is internal.

**For orchestrate.** Asking at the right moment (10% of tasks) beat guessing, which supports the worker's stop-and-ask over guess-and-continue. The self-approval incident is the design case for keeping the approving identity outside the agent: in orchestrate the worker never approves or merges and the guard hook denies unapproved calls with a reason.

### 23. Bowman et al. (Anthropic), 2022

**Citation.** Bowman, S. R., Hyun, J., Perez, E., Chen, E., Pettit, C., Heiner, S., Lukošiūtė, K., Askell, A., et al. (2022). Measuring Progress on Scalable Oversight for Large Language Models. Preprint, arXiv:2211.03540. https://ar5iv.labs.arxiv.org/html/2211.03540.

**Design.** "Sandwiching" experiments: settings where the model is "already more capable than a typical human, but less capable than an expert"; ten participants in two groups of five (four analysed per task after one outlier each), 100 validation questions per condition.

**Headline results.** MMLU: human alone 57.2%, model alone 57.2%, human with model 75.4%. QuALITY (time-restricted): 48.6% / 59.2% / 76.8%.

**Limitations.** N=4 per task; QA not code; 2022 model.

**For orchestrate.** The human-plus-model team beat either alone by 18 to 28 points, which is the premise for keeping a human in the L-tier verdict rather than automating it or leaving it to the human unaided. It does not say how to combine them on a merge decision.

### 24. McAleese et al. (OpenAI), 2024

**Citation.** McAleese, N., Pokorny, R. M., Ceron Uribe, J. F., Nitishinskaya, E., Trebacz, M., Leike, J. (2024). LLM Critics Help Catch LLM Bugs. Preprint, arXiv:2407.00215. https://arxiv.org/abs/2407.00215.

**Design.** RLHF-trained critic models evaluated by human raters on code with naturally occurring and inserted bugs; human contractors paid for code review as the comparison.

**Headline results.** "model-written critiques are preferred over human critiques in 63% of cases" on naturally occurring errors; critics "catch more bugs than human contractors paid for code review"; human-machine teams "catch similar numbers of bugs to LLM critics while hallucinating less than LLMs alone"; the critics found "hundreds of errors in ChatGPT training data rated as 'flawless'".

**Limitations.** Industry report; no N in the abstract; critics also "hallucinated bugs".

**For orchestrate.** The reviewer's output is a critique for a human to check, and the human's contribution is fewer hallucinated findings, not more bugs. Hallucinated reviewer findings are therefore a first-class metric: the share of reviewer "changes requested" that the worker or human overturns.

### 25. Shukla, Feng, Wang, Rostami and Zhang (Hedwig), 2026

**Citation.** Shukla, T., Feng, K. J. K., Wang, L., Rostami, M., Zhang, A. X. (2026). Hedwig: Dynamic Autonomy for Coding Agents Under Local Oversight. Preprint, arXiv:2605.11495 (v1, 2026-05-12). https://arxiv.org/html/2605.11495v1.

**Design.** Formative survey of 21 engineers; a two-session demo on one small Python repository with two backend tasks (11 write operations per run), single run per condition; a learned 13-feature oversight policy (diff size, blast radius, change-pattern risk, prior approvals and denials) whose scores "the model is never shown".

**Headline results.** Claude Code with the instruction-file rule "CLAUDE.md says 'ask before API-shape changes'" caught 2 of 4 required check-ins (recall 0.50); Hedwig caught 4 of 4.

**Limitations.** A demo, n=4 check-ins, one run, no variance.

**For orchestrate.** Thin but pointed: an instruction-file rule is not an enforcement mechanism. orchestrate already puts gates in scripts and the guard hook; this is the evidence that it should stay that way, and that per-repo learned "when to stop" thresholds are a plausible toggle.

### 26. DORA (Google Cloud), 2024 and 2025

**Citation.** Google Cloud (2024-10-22). Announcing the 2024 DORA report. https://cloud.google.com/blog/products/devops-sre/announcing-the-2024-dora-report. DORA (2025). State of AI-assisted Software Development. https://dora.dev/research/2025/dora-report/.

**Design.** Annual practitioner surveys with structural modelling; 2025 wave: survey June 13 to July 21, 2025, nearly 5,000 respondents (secondary for the N).

**Headline results.** 2024: "As AI adoption increased, it was accompanied by an estimated decrease in delivery throughput by 1.5%, and an estimated reduction in delivery stability by 7.2%"; "A 25% increase in AI adoption is associated with" +7.5% documentation quality, +3.4% code quality, +3.1% code review speed; "39% of the respondents reported little to no trust in AI-generated code". 2025: "AI's primary role is as an amplifier, magnifying an organization's existing strengths and weaknesses"; throughput now positively associated with adoption while instability remains (secondary; the report page yields only the amplifier line).

**Limitations.** Self-report, correlational, organisation-level.

**For orchestrate.** The organisation-level metric that moved the wrong way is stability. Change-failure rate on orchestrate-merged PRs (reverts, hotfix follow-ups) is the field's own yardstick and belongs in the profile.

### 27. Faros AI, 2025

**Citation.** Faros AI (2025). The AI Productivity Paradox Report 2025. https://www.faros.ai/blog/ai-productivity-paradox-report-2025.

**Design.** Vendor telemetry across "over 10,000 developers across 1,255 teams", comparing high-AI-adoption teams with the rest.

**Headline results.** High-adoption teams "complete 21% more tasks and merge 98% more pull requests"; PR review time +91%; average PR size +154%; "AI adoption is consistently associated with a 9% increase in bugs per developer"; across "overall throughput, DORA metrics, and quality KPIs, the gains observed in team behavior do not scale when aggregated".

**Limitations.** Vendor data, correlational, no methodology beyond the post.

**For orchestrate.** Review time nearly doubling and PR size more than doubling is the same shape as Watanabe and Ehsani: the bottleneck moves to review. A PR size cap and a human-review-minutes metric are the orchestrate responses.

### 28. Anthropic, 2025 (internal)

**Citation.** Anthropic (2025-12-02). How AI is transforming work at Anthropic. https://www.anthropic.com/research/how-ai-is-transforming-work-at-anthropic.

**Design.** Survey of 132 engineers and researchers (August 2025), 53 interviews, and 200,000 internal Claude Code transcripts from February and August 2025.

**Headline results.** Self-reported Claude use in 59% of work (28% a year earlier) and a +50% productivity gain (+20% a year earlier); 14% report gains above 100%; a 67% increase in merged PRs per engineer per day after Claude Code adoption (org data); more than half can "fully delegate" only 0 to 20% of their work; human turns per transcript 6.2 to 4.1 (−33%) while maximum consecutive tool calls rose 9.8 to 21.2; some respondents reported "more debugging and cleanup of Claude's code". Anthropic flags selection, social-desirability and recall bias.

**Limitations.** Self-report, one privileged population, vendor.

**For orchestrate.** Even the most AI-forward population reports that most work cannot be fully delegated and that human turns per task are falling rather than vanishing. The share of tickets that reach merge with zero human turns is a number orchestrate can report honestly.

### 29. Thurlow, Human Oversight System, 2026

**Citation.** Thurlow, S. (2026). Human Oversight System (HOS), documentation and research notes, https://github.com/thurlow-research/HumanOversightSystem (software archived at https://doi.org/10.5281/zenodo.21347272), docs CC BY 4.0, code MIT. Files read: `docs/planning/SLR-Derived-Improvements.md` (index over a 149-paper systematic review core set) and `research/findings/` (`dogfooding-inflates-the-measured-intervention-rate.md`, `unexplained-pr-rejection-is-a-transparency-gap-in-agentic-systems.md`, `agentic-prs-are-larger-and-more-multipurpose-than-human-prs.md`, `richer-ai-explanations-may-increase-overreliance-not-reduce-it.md`, `the-1point7x-defect-rate-claim-has-a-precise-empirical-source.md`).

**Design.** A single oversight framework's own field notes and a literature index. Used here as an index; every paper above was chased to its primary source.

**Headline observations (C).** Measuring the human-intervention rate while dogfooding an oversight framework on its own repository "systematically inflates" it, because the framework's protected surfaces are its own files. The "1.7× more issues" figure for AI-co-authored PRs traces to CodeRabbit's December 2025 report over 470 PRs (320 AI, 150 human), not to a peer-reviewed replication. Where HOS's summaries were checked against the papers: Gao's "~80%" is the all-tier aggregate (79.0%), not the non-owner figure (86.8%); Ehsani's 38% abandonment is of 600 sampled, 41% of the 562 analysed; Zhou and Zhao's d=0.87 and "reversed" could not be verified from the abstract.

**For orchestrate.** Any intervention rate orchestrate reports from runs on its own backlog is not representative of a consumer repo; the profile should tag runs by repo class. The HOS summaries were accurate to the paper in every case checked except the rounding noted, which is a good sign for using the rest of its index.

## Implementable findings

| id | finding | grade | source | for orchestrate |
|---|---|---|---|---|
| HO-01 | Experienced maintainers on their own large repos were 19% slower with early-2025 agents (95% CI +2% to +39%) while forecasting 24% faster beforehand and 20% afterwards | A | Becker et al. 2025 | measure: record wall time and human-attention minutes per merged PR per repo; never state a speedup the profile has not measured |
| HO-02 | METR's 2026 follow-up: −18% (CI −38% to +9%) and −4% (CI −15% to +9%); wall clock "is unreliable for developers who run multiple AI agents concurrently" | B | METR 2026 | measure: in greedy runs profile human-attention time separately from elapsed time; elapsed time is not the productivity metric |
| HO-03 | On a self-contained greenfield task Copilot users finished 55.8% faster (71.17 vs 160.89 min, p=0.0017, CI 21% to 89%) | A | Peng et al. 2023 | flag: a "greenfield first" ordering that fills slots with self-contained tickets before maintenance tickets; measure merge rate by that class |
| HO-04 | Across 4,867 developers in three firm RCTs, Copilot access raised completed tasks 26.08% (SE 10.3%), most for less experienced developers | A | Cui et al. 2025 | default: the honest value claim is throughput on routine, well-specified tickets; measure tickets merged per human hour |
| HO-05 | Inside Google (N=96) the speed effect was about 21% but the adjusted CI spanned zero (p=0.086) | A | Paradis et al. 2024 | measure: no per-repo speed conclusions from fewer than tens of tickets of one class |
| HO-06 | Cursor adoption's velocity gain dissipated after two months while warnings (+30.26%) and cognitive complexity (+41.64%) persisted; a 100% complexity rise later cut lines added 64.5% | A | He et al. 2026 | default: a quality ratchet in the script gate, no net increase in lint warnings or complexity on the diff; measure both per session |
| HO-07 | After Copilot, core developers reviewed 6.5% more and produced 19% less original code; PR rework +2.4% | A | Xu et al. 2025 | measure: human approvals per hour and rework on merged PRs; rule: a cap on PRs awaiting the human at once |
| HO-08 | 79.0% of merged human+AI PRs had no human feedback; non-owner AI PRs closed in a mean 0.18 days vs 3.68 for human non-owners | A | Gao et al. 2026 | reject: fast silent merge as a success signal; default: every merge carries a script-owned reviewer verdict; measure the zero-comment merge share against the repo's human baseline |
| HO-09 | 13.1% of repositories had any artefact mentioning AI usage | A | Gao et al. 2026 | default: setup writes the AI-PR rules into `.claude/orchestration.md` (already); flag: a PR-body disclosure line naming the worker model |
| HO-10 | Of rejected agent PRs 38% were reviewer abandonment, 23% duplicates, 17% CI failures; each failed CI check cut merge odds about 15% | A | Ehsani et al. 2026 | default: CI green before review (already), holds and touch map against duplicates (already); measure PRs idle beyond N days as abandonment; rule: an idle PR goes back to the tracker with a reason |
| HO-11 | Agent PR merge rate by task: docs 84%, CI 79%, build 74%, fix 64%, performance 55% | A | Ehsani et al. 2026 | flag: route performance and bug-fix tickets to the next review tier up; measure merge rate by ticket type |
| HO-12 | In AIDev-pop agent PRs were accepted at 38.2% to 65.3% vs 76.8% human, and 58.2% had no explicit review | A | Li et al. 2025 | measure: acceptance and revision rate per worker model; reject: no-review merging under any model |
| HO-13 | Claude Code PRs merged 83.77% vs 91.00% human; 45.1% of merged ones were revised by humans (fixes 47.7%); median added lines 48 vs 24; 39.9% multi-purpose vs 12.2% | B | Watanabe et al. 2026 | rule: one purpose per PR and a size guard before review, scripted from the diff; measure post-review human revisions as the reviewer tier's baseline to beat |
| HO-14 | 64.1% of rejected agent PRs (59/92) were closed with no explanation | B | Watanabe et al. 2026 | default: every close, hand-back or `wontfix` carries a reason the worker can read (already); rule: the gate refuses a close without one |
| HO-15 | Cognitive forcing cut overreliance on wrong AI advice from 0.64 to 0.48 (p=.003, d=.36) and raised correct overrides 0.08 to 0.27 (d=.66), but was rated more complex (2.95 vs 2.64, p=.002) | A | Buçinca et al. 2021 | flag: for L-tier PRs the human records a verdict or question before the reviewer's verdict is revealed; measure override rate and direction |
| HO-16 | Explanations "increased the chance that humans will accept the AI's recommendation, regardless of its correctness" | A | Bansal et al. 2021 | reject: longer reviewer rationales as a trust lever; default: verdicts lead with executed evidence (test output, CI) rather than prose |
| HO-17 | Expecting outcome-only review made workers revise AI output less (7 experiments, N=2,895); the effect attenuates when the reviewer sees the original AI output beside the final | A | Zhou and Zhao 2026 | default: keep the draft PR and commit history visible (already); flag: a reviewer and human view of "changed since first draft" and "changed after review" |
| HO-18 | With an AI assistant, secure solutions fell (signing 3% vs 21%, encryption 21% vs 43%) while belief in security rose; those who edited and trusted less did better | A | Perry et al. 2023 | rule: risk paths (auth, parsing, secrets) force L tier regardless of worker or developer confidence (already by risk paths); reject: any confidence self-report as a tier input |
| HO-19 | Highlighting tokens most likely to be edited cut task time (8.61 vs 9.58 min, p=0.003); highlighting low generation probability gave no benefit | A | Vasconcelos et al. 2024 | flag: the L-tier reviewer ranks hunks by "likely to need a change" for the human's read; reject: surfacing model confidence to the human |
| HO-20 | Intermediate confirmations placed by error probability and redo cost cut completion time 13.54% (t(143)=5.52) and were preferred by 81% | A | Zhou et al. 2026 | flag: a plan checkpoint before the build for long tickets, placed where redo cost exceeds confirmation cost; measure redo (rescue and redispatch) counts |
| HO-21 | A requirements-and-assumptions review view made error finding faster (g=−0.65) but not more accurate (72.9% to 77.1%, g=0.18) and raised confidence most when errors were missed (g=0.85) | B | Grunde-McLaughlin et al. 2026 | measure: judge any change to the pass table, PR body or risk note by escaped defects, never by reviewer speed or confidence; reject: human confidence as a gate signal |
| HO-22 | Engineers' engagement declined through the task to the happy path; none could say how many functions the agent wrote | C | Catalan et al. 2026 | flag: a substantive question about the change before approval unlocks on L tier |
| HO-23 | Juniors who built with AI scored 50% vs 67% on a later quiz (d=0.738, p=0.01) with no speed gain; asking what the code does afterwards preserved learning | A | Shen and Tamkin 2026 | flag: a "walk me through this PR" step before the human approves; measure with an occasional comprehension check |
| HO-24 | On a greenfield test-specified task Copilot users were 53.2% more likely to pass all tests and had 13.6% more error-free lines | B | Bauer (GitHub) 2024 | measure: quality direction depends on horizon; track post-merge defects on orchestrate PRs rather than assuming debt or quality |
| HO-25 | Contradiction: METR −19% on familiar large repos vs Peng +55.8% and Cui +26% on routine or greenfield work; He and Xu find debt where Bauer finds quality | A | Becker 2025; Peng 2023; Cui 2025; He 2026; Xu 2025; Bauer 2024 | measure: the effect's sign depends on task class and horizon, so the profile must split tickets by class and track post-merge outcomes before any claim |
| HO-26 | Programmers spent 22.4% of session time verifying suggestions and 51.5% in suggestion-related states; accepted 34.0% of suggestions | B | Mozannar et al. 2024 | measure: human review minutes per merged PR in the profile, beside tokens and dollars |
| HO-27 | Gating the riskiest 10% of diffs captured 42.3% of severe incidents (risk-aligned LLM) vs 27.9% (logistic regression) | B | Abreu et al. 2025 | default: risk-tiered review (already S/M/L); flag: a risk score adding file defect history and author familiarity; measure the share of PRs that reach the human as the exposed knob |
| HO-28 | Plan-then-code deployment funnel: 82% of plans approved, 25% of coded issues raised a PR, 59% of PRs merged, 8% end to end; 67% disagreed the code solved the task alone | B | Takerngsaksiri et al. 2025 | measure: report the funnel per repo (taken, planned, opened, merged, revised); flag: plan approval before build |
| HO-29 | Help on 10% of tasks lifted agent completion 30.3% to 51.9%; with mitigations off a prompt-injected agent approved its own actions in its own interface | B | Mozannar et al. 2025 | default: the worker stops and asks (`needs-info`, `needs-lead`) rather than guessing; rule: the approving identity is never the agent, the guard denies unapproved calls (already) |
| HO-30 | Human plus model scored 75.4% vs 57.2% for either alone (MMLU) and 76.8% vs 48.6% / 59.2% (QuALITY) | B | Bowman et al. 2022 | default: keep the human in the L-tier verdict as a teammate, neither automated away nor unaided |
| HO-31 | Model critiques were preferred over human critiques 63% of the time; human-plus-critic teams caught as many bugs as the critic with fewer hallucinations | B | McAleese et al. 2024 | default: the reviewer's output is a critique for a human to check at L tier; measure the share of reviewer findings the worker or human overturns |
| HO-32 | An instruction-file rule caught 2 of 4 required check-ins; a learned policy caught 4 of 4 | C | Shukla et al. 2026 | reject: CLAUDE.md or skill prose as the oversight mechanism; default: gates in scripts and the guard hook (already); flag: per-repo learned stop thresholds |
| HO-33 | Rising AI adoption came with an estimated −1.5% throughput and −7.2% delivery stability; 39% reported little or no trust in AI code | C | DORA 2024 | measure: change-failure rate (reverts, hotfix follow-ups) on orchestrate-merged PRs |
| HO-34 | High-adoption teams merged 98% more PRs with 91% longer review time, 154% larger PRs and 9% more bugs per developer; gains did not scale to delivery metrics | C | Faros AI 2025 | rule: a PR size cap; measure review time per PR and bugs per merged PR |
| HO-35 | Human turns per Claude Code transcript fell 6.2 to 4.1 while more than half can fully delegate only 0 to 20% of their work | C | Anthropic 2025 | measure: share of tickets merged with zero human turns, reported per repo |
| HO-36 | Intervention rates measured while dogfooding an oversight framework on its own repo are structurally inflated | C | Thurlow (HOS) 2026 | measure: tag profile runs by repo class; do not calibrate tiers or stop rates on orchestrate's own backlog alone |

## Candidate paradigms

1. **Risk-tiered human attention.** Human review depth follows a diff risk score (size, risk paths, file defect history, author familiarity, task type), with the share of PRs reaching the human as the exposed knob. HO-11, HO-18, HO-27. Best evidence B (Abreu production data; Perry A for the risk-path rule).
2. **Cognitive-forcing approval.** On L tier the human commits a verdict or a question before the reviewer's verdict is shown; approval unlocks after one substantive question. HO-15, HO-16, HO-21, HO-22. Best evidence A (Buçinca, Bansal; non-code tasks).
3. **Process-visible review.** Reviewer and human see the draft-to-final and after-review diffs, not only the final state. HO-17, HO-13. Best evidence A (Zhou and Zhao, non-code; working paper).
4. **Checkpointed autonomy.** A plan gate before the build for long tickets, placed where redo cost exceeds confirmation cost; worker stops and asks rather than guesses. HO-20, HO-28, HO-29. Best evidence A (Zhou CHI, non-code) and B (HULA, Magentic-UI).
5. **Edit-likelihood attention routing.** The reviewer ranks hunks by likelihood of needing a change for the human's read; model confidence is never shown. HO-19, HO-18. Best evidence A (Vasconcelos, N=30).
6. **Human-plus-critic verdict.** The reviewer is a critique the human checks at L tier; hallucinated findings are measured. HO-30, HO-31. Best evidence B.
7. **Review-capacity protection.** WIP cap on PRs awaiting the human, PR size cap, one purpose per PR, idle-PR hand-back. HO-07, HO-10, HO-13, HO-34. Best evidence A (Xu, Ehsani).
8. **Quality ratchet gate.** The script gate refuses a net increase in warnings or complexity. HO-06. Best evidence A (He).
9. **Explained closure.** No PR or ticket closes without a machine-readable reason. HO-14, HO-10. Best evidence B.
10. **Comprehension-preserving delegation.** A walk-through or question step before approval so the developer still understands the code. HO-23, HO-35. Best evidence A (Shen and Tamkin, N=52).
11. **Script-owned oversight.** Gates, guard and verdicts live in scripts, never in instruction prose. HO-32, HO-29. Best evidence C (Hedwig demo) with B (Magentic-UI self-approval).
12. **Measured oversight.** The profile carries human-attention minutes, zero-comment merges, overrides and their direction, funnel stages, post-merge defects and change-failure rate, split by ticket class and repo class. HO-01, HO-02, HO-05, HO-21, HO-25, HO-26, HO-33, HO-36. Best evidence A (METR, Grunde-McLaughlin for the "measure outcomes not confidence" rule).

## Open questions

- No controlled study measures human oversight of an autonomous multi-ticket pipeline; every RCT above is single-developer, single-task, and most predate agents that open PRs. Whether tiered machine review plus a human gate beats either alone on merge decisions is unmeasured.
- The two strongest "what to show the human" results (process visibility, edit-likelihood highlighting) are on non-code or completion-era tasks with N=30 to 2,895; neither has been replicated on PR review.
- Cognitive forcing costs satisfaction; whether developers keep using a gate that makes them commit first, over weeks, is not known (Buçinca is one session).
- The field datasets count "merged" as success and span model generations; none measures post-merge defects per agent PR, so the 45.1% human-revision rate and the 1.7× issue-rate claim have no causal counterpart.
- METR's follow-up shows the measurement itself breaking: when agents run in the background, wall-clock time and task-level randomisation stop working. What replaces them for orchestrate (human-attention minutes, funnel rates, change-failure rate) is a proposal here, not a validated instrument.
- Where to place a plan checkpoint on a coding ticket needs per-step error probabilities and redo costs that no one has estimated for PR work; HULA's 82% plan approval is one deployment.
- Whether the slowdown METR found on familiar repositories is closed by supplying the implicit context (touch map, scout facts, verify command) is exactly the experiment orchestrate could run on itself, subject to the dogfooding confound HO-36 names.

## Sources list

- Becker, J., Rush, N., Barnes, E., Rein, D. (2025). Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity. Preprint, arXiv:2507.09089. https://arxiv.org/abs/2507.09089
- METR (2026-02-24). Update on developer productivity uplift. https://metr.org/blog/2026-02-24-uplift-update/
- Peng, S., Kalliamvakou, E., Cihon, P., Demirer, M. (2023). The Impact of AI on Developer Productivity: Evidence from GitHub Copilot. Preprint, arXiv:2302.06590. https://arxiv.org/abs/2302.06590
- Cui, K. Z., Demirer, M., Jaffe, S., Musolff, L., Peng, S., Salz, T. (2025). The Effects of Generative AI on High-Skilled Work: Evidence from Three Field Experiments with Software Developers. Management Science, https://doi.org/10.1287/mnsc.2025.00535; working paper February 2025, https://economics.mit.edu/sites/default/files/inline-files/draft_copilot_experiments.pdf; registry AEARCTR-0014530
- Paradis, E., Grey, K., Madison, Q., Nam, D., Macvean, A., Meimand, V., Zhang, N., Ferrari-Church, B., Chandra, S. (2024). How much does AI impact development speed? An enterprise-based randomized controlled trial. Preprint, arXiv:2410.12944. https://arxiv.org/abs/2410.12944
- He, H., Miller, C., Agarwal, S., Kästner, C., Vasilescu, B. (2026). Speed at the Cost of Quality: How Cursor AI Increases Short-Term Velocity and Long-Term Complexity in Open-Source Projects. MSR 2026, https://doi.org/10.1145/3793302.3793349; arXiv:2511.04427
- Xu, F., Medappa, P. K., Tunç, M. M., Vroegindeweij, M., Fransoo, J. C. (2025). AI-Assisted Programming Decreases the Productivity of Experienced Developers by Increasing the Technical Debt and Maintenance Burden. Preprint, arXiv:2510.10165; SSRN 5521379
- Gao, H., Banyongrakkul, P., Guan, H., Zahedi, M., Treude, C. (2026). On Autopilot? An Empirical Study of Human-AI Teaming and Review Practices in Open Source. MSR 2026; arXiv:2601.13754
- Ehsani, R., Pathak, S., Rawal, S., Al Mujahid, A., Imran, M. M., Chatterjee, P. (2026). Where Do AI Coding Agents Fail? An Empirical Study of Failed Agentic Pull Requests in GitHub. MSR 2026; arXiv:2601.15195
- Li, H., Zhang, H., Hassan, A. E. (2025). The Rise of AI Teammates in Software Engineering (SE) 3.0: How Autonomous Coding Agents Are Reshaping Software Engineering. Preprint, arXiv:2507.15003
- Watanabe, M., Li, H., Kashiwa, Y., Reid, B., Iida, H., Hassan, A. E. (2026). On the Use of Agentic Coding: An Empirical Study of Pull Requests on GitHub. ACM TOSEM, https://doi.org/10.1145/3798166; arXiv:2509.14745
- Buçinca, Z., Malaya, M. B., Gajos, K. Z. (2021). To Trust or to Think: Cognitive Forcing Functions Can Reduce Overreliance on AI in AI-assisted Decision-making. Proc. ACM Hum.-Comput. Interact. 5(CSCW1), 188. https://doi.org/10.1145/3449287
- Bansal, G., Wu, T., Zhou, J., Fok, R., Nushi, B., Kamar, E., Ribeiro, M. T., Weld, D. S. (2021). Does the Whole Exceed its Parts? The Effect of AI Explanations on Complementary Team Performance. CHI 2021; arXiv:2006.14779
- Ghosh, A., Sarkar, A., Lindley, S., Poelitz, C. (2026). An Experimental Comparison of Cognitive Forcing Functions for Execution Plans in AI-Assisted Writing. Preprint, arXiv:2601.18033
- Zhou, P., Zhao, Y. (2026). Review Makes Workers Less Likely to Revise AI Output. SSRN 6325399. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6325399
- Perry, N., Srivastava, M., Kumar, D., Boneh, D. (2023). Do Users Write More Insecure Code with AI Assistants? CCS '23, https://doi.org/10.1145/3576915.3623157; arXiv:2211.03622
- Vasconcelos, H., Bansal, G., Fourney, A., Liao, Q. V., Wortman Vaughan, J. (2024). Generation Probabilities Are Not Enough: Uncertainty Highlighting in AI Code Completions. ACM TOCHI, https://doi.org/10.1145/3702320; arXiv:2302.07248
- Zhou, J., Roy, A., Gupta, S., Weitekamp, D., MacLellan, C. J. (2026). When Should Users Check? A Decision-Theoretic Model of Confirmation Frequency in Multi-Step AI Agent Tasks. CHI 2026, https://doi.org/10.1145/3772318.3790655; arXiv:2510.05307
- Grunde-McLaughlin, M., Mozannar, H., Murad, M., Chen, J., Amershi, S., Fourney, A. (2026). Overseeing Agents Without Constant Oversight: Challenges and Opportunities. Preprint, arXiv:2602.16844
- Catalan, C. R., Dizon, L. M., Monderin, P. N., Kuang, E. (2026). "I'm Not Reading All of That": Understanding Software Engineers' Level of Cognitive Engagement with Agentic Coding Assistants. Preprint, arXiv:2603.14225
- Shen, J. H., Tamkin, A. (2026). How AI Impacts Skill Formation. Preprint, arXiv:2601.20245; https://www.anthropic.com/research/AI-assistance-coding-skills
- Bauer, J. (2024). Does GitHub Copilot improve code quality? Here's what the data says. GitHub Blog. https://github.blog/news-insights/research/does-github-copilot-improve-code-quality-heres-what-the-data-says/
- Mozannar, H., Bansal, G., Fourney, A., Horvitz, E. (2024). Reading Between the Lines: Modeling User Behavior and Costs in AI-Assisted Programming. CHI 2024; arXiv:2210.14306
- Abreu, R., Murali, V., Rigby, P. C., Maddila, C., Sun, W., Ge, J., Chinniah, K., Mockus, A., Mehta, M., Nagappan, N. (2025). Moving Faster and Reducing Risk: Using LLMs in Release Deployment. ICSE-SEIP 2025, https://doi.org/10.1109/ICSE-SEIP66354.2025.00045; arXiv:2410.06351
- Takerngsaksiri, W., Pasuksmit, J., Thongtanunam, P., Tantithamthavorn, C., Zhang, R., Jiang, F., Li, J., Cook, E., Chen, K., Wu, M. (2025). Human-In-the-loop Software Development Agents. ICSE-SEIP 2025, https://doi.org/10.1109/ICSE-SEIP66354.2025.00036; arXiv:2411.12924
- Mozannar, H., Bansal, G., Tan, C., Fourney, A., et al. (2025). Magentic-UI: Towards Human-in-the-loop Agentic Systems. Preprint, arXiv:2507.22358
- Bowman, S. R., et al. (2022). Measuring Progress on Scalable Oversight for Large Language Models. Preprint, arXiv:2211.03540
- McAleese, N., Pokorny, R. M., Ceron Uribe, J. F., Nitishinskaya, E., Trebacz, M., Leike, J. (2024). LLM Critics Help Catch LLM Bugs. Preprint, arXiv:2407.00215
- Shukla, T., Feng, K. J. K., Wang, L., Rostami, M., Zhang, A. X. (2026). Hedwig: Dynamic Autonomy for Coding Agents Under Local Oversight. Preprint, arXiv:2605.11495
- Google Cloud (2024). Announcing the 2024 DORA report. https://cloud.google.com/blog/products/devops-sre/announcing-the-2024-dora-report; DORA (2025). State of AI-assisted Software Development. https://dora.dev/research/2025/dora-report/
- Faros AI (2025). The AI Productivity Paradox Report 2025. https://www.faros.ai/blog/ai-productivity-paradox-report-2025
- Anthropic (2025). How AI is transforming work at Anthropic. https://www.anthropic.com/research/how-ai-is-transforming-work-at-anthropic
- Thurlow, S. (2026). Human Oversight System (HOS), documentation and research notes, https://github.com/thurlow-research/HumanOversightSystem (software archived at https://doi.org/10.5281/zenodo.21347272), docs CC BY 4.0, code MIT.
