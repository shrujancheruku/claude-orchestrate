# Deterministic diff checks for AI-authored code: evidence and false-positive profile

Research note for [#597](https://github.com/shrujancheruku/claude-orchestrate/issues/597), part of map [#588](https://github.com/shrujancheruku/claude-orchestrate/issues/588). Written 2026-11-10 from the primary sources named in the Sources section; HOS was read from a clone of its `main` at commit `1482a93` (2026-11-09). It feeds the tier-inputs grilling (#590) and the fail-honestly checks grilling (#591); it decides nothing.

## How to read this

Every check below has six fields: what it **matches**, what it is a **proxy for**, the **measured base rate or lift** with the exact figure and its source, how it **fails** (false positives in normal code), and whether it is a **cheap regex over a unified diff or needs a parse**. Each figure carries an evidence grade:

- **A**: published measurement with a human or matched control baseline and a reported test or validated detector.
- **B**: published measurement without a baseline, or descriptive only.
- **C**: published qualitative finding, benchmark result outside field conditions, or a recommendation.
- **D**: HOS field observation (its own repository history), or a source read only through HOS's summary and not fetched here.

Two cross-cutting facts frame all of them:

1. **LLM reviewers do not see this class.** Parris 2026 ran a deterministic scanner and an LLM evaluator (minimax-m2 via Ollama) over the same files: on `deliberation.py` the LLM rated 7 of 7 static FAILs as PASS, on `startup.py` 8 of 8, on `critic_infrastructure.py` 2 of 7, and it "consistently passed on C02, C03, C04, C05, and C13" (Table 10). The abstract and conclusion state a **44:1** static-to-LLM finding ratio; the paper does not show the calculation, and recomputing from its Table 9 gives about 33:1 on the five non-anchor surfaces or about 227:1 with the anchor repository included. Quote the direction (two orders of magnitude) rather than the ratio. Grade A for the direction, B for the number.
2. **Deterministic findings are a floor the reviewer may raise, not lower.** This is HOS decision D52 ("An LLM arbiter, reviewer, or any agent component may not suppress, dismiss, or override a finding produced by a deterministic scanner") and the map's own principle. Grade D (HOS design decision), motivated by the Parris result above.

What orchestrate already has (map #588, facts checked 2026-11-10): `scripts/gate` checks secrets, lockfile-without-manifest, overlap and outside-map; nothing on tests, exceptions, debug output, size or commit type.

## Summary table

| # | Check | Best figure | Grade | Regex or parse |
|---|---|---|---|---|
| 1 | Swallowed / broadened exceptions | AI files 0.435 HIGH fail-soft findings per file vs 0.242 human, 1.80x (Parris 2026); "Broad exception handling" is the single largest AI-introduced issue at 8.5% of 484,366 issues (Liu et al. 2026); error-handling issues "nearly 2x" in AI PRs (Loker 2025) | A / B | regex catches the bare and broad forms; swallow-and-continue needs a block parse |
| 2 | Success-after-failure | AIRA check C01, HIGH only; no separate rate published (Parris 2026) | B | parse |
| 3 | Deleted or loosened assertions | Under spec-test conflict, Claude models and Qwen3-Coder cheat ">79%" of the time by modifying tests; GPT-5 passes 76% of impossible SWE-bench tasks (Zhong et al. 2025). In the wild, human-agent PRs delete tests *less* often than human PRs: 0.9% vs 4.8% of co-evolution test changes (Milanese et al. 2026) | C (mechanism) / A (field rate) | regex over `-` lines for deletion; pairing `-`/`+` lines for loosening |
| 4 | Net test deletion | 0.9% vs 4.8% as above (Milanese et al. 2026); agents add tests more (68.4% vs 54.8%, OR 1.79) | A, low lift | `--numstat` / `--name-status` only |
| 5 | Newly skipped tests | "Ignored Test" smell delta mean 1.607 in human-agent PRs vs 0.153 human, effect size negligible (Milanese et al. 2026) | B | regex |
| 6 | Leftover debug output | No published measurement found; Kim and Yegge 2025 (via HOS SLR doc, not read) lists it as a recurrent AI defect class | D | regex, path-aware |
| 7 | New dependency manifest entries | 97 of 364 smells in agent build-file PRs are wildcard versions, 56 deprecated and 27 outdated dependencies; 61.4% (238/387) of those PRs merged, mostly right after CI passed (Ghammam and Almukhtar 2026); HOS `new-external-dependency` signature forces a human gate (HOS, no base rate) | B / D | regex |
| 8 | Deletion ratio | Maintainers "show caution toward PRs that delete existing code" (Canelas dos Santos et al. 2026, qualitative); agentic and human median deletions 7 vs 8 lines (Watanabe et al. 2026); among merged PRs humans delete more, Cliff's delta 0.4462 (Ogenrwot and Businge 2026) | C | `--numstat` |
| 9 | Multi-purpose PRs | 39.9% of agentic PRs (85/213) have multiple purposes vs 12.2% (27/221) of human PRs; "too large" is 3.3% of coded rejections (Watanabe et al. 2026) | A | commit-type labels or path-domain count; no parse |
| 10 | File and commit counts | Agentic PRs add a median 48 lines vs 24, same median files (2 vs 2), same deletions (7 vs 8) (Watanabe et al. 2026); not-merged agentic PRs carry 17% more LOC and 10% more files than merged (Ehsani et al. 2026); among merged PRs humans have more commits (delta 0.5429, large) and files (0.4487) (Ogenrwot and Businge 2026) | A / B | `--numstat`, `rev-list --count` |
| 11 | Commit-type (refactor/chore) risk | Agentic breaking-change rate by task: chore 9.35%, refactor 6.72%, perf 4.12%, feat 2.89%, fix 2.69%; human: feat 7.74%, fix 5.32%, chore 4.95%, refactor 4.36%, perf 0.90% (Ferdous et al. 2026) | A | regex on commit subjects, but the label is author-supplied |
| 12 | HOS structural signatures and path tier floors | New auth state, route, user-facing state enum, new template, payment/auth/migration/PII paths force `structural` or a tier floor; no base rate, four documented false-positive incidents (HOS) | D | regex |
| -- | Self-reported confidence (not a check) | 99.9% of agentic PRs report 8 to 10; breaking-change rate 3.94%, 3.96%, 3.16% at levels 8, 9, 10 (Ferdous et al. 2026) | A | do not use to lower a tier |

## 1. Swallowed or broadened exceptions

**Matches.** Added lines that open a catch-all handler: Python `except:` and `except (Exception|BaseException)`, JS/TS `catch (e) {}` and `catch {}`, Go `_ = err` or `if err != nil { return nil }`, and handlers whose body is `pass`, `continue`, a bare log call, or `return` with a default value. AIRA's C03 "Broad Exception Suppression" is the published version ("Exceptions swallowed or neutralized"); its C05 "Bypass / Override Paths" ("Flags or overrides disable safeguards") and C13 "Confidence Misrepresentation" (degraded output with no signal) are the adjacent forms.

**Proxy for.** What Parris calls failure untruthfulness: code whose outputs do not reflect whether it succeeded. Liu et al. frame the same rule as technical debt that persists (22.7% of all tracked AI-introduced issues survive to repository HEAD, 105,364 of 464,900).

**Measured rate or lift.**
- Parris 2026, Study 3 (matched control, 955 AI-attributed vs 955 human files matched on language, file-size band and size decile, at most four files per repository): **0.435 HIGH findings per file vs 0.242, a 1.80x excess** (Table 6: 415 vs 231 HIGH findings). Per language (Table 7): JavaScript 0.682 vs 0.367 (1.86x, 327 files per arm), Python 0.293 vs 0.129 (2.27x, 311), TypeScript 0.319 vs 0.224 (1.42x, 317). No confidence intervals or significance tests are reported for Study 3. Study 2 (300 vs 300 files): C03 fired 61 times on AI files vs 43 on human; "the strongest recurring support for the hypothesis concentrated in exception-handling-related patterns". Grade A for the direction; note the figure is HIGH findings of all fifteen checks, not C03 alone.
- Liu et al. 2026 (302.6k verified AI-authored commits, 6,299 repositories, five assistants, ESLint/Pylint/Semgrep run before and after each commit): **"Broad exception handling" is the top rule at 8.5% of all 484,366 AI-introduced issues**, and 14.9% of Python issues (Tables 3 and 4). Between 7.5% (Copilot) and 14.8% (Gemini) of each assistant's commits introduce at least one issue (Claude 11.5%, Cursor 10.9%, Devin 9.4%). **No human baseline**: "We also do not compare AI-authored commits against a baseline of purely human-written commits." Analyser precision on a 99-issue sample: 85.9%. Grade B.
- Loker 2025 (CodeRabbit, 470 open-source PRs, 320 AI-labelled and 150 human-only, review findings normalised per 100 PRs): 10.83 vs 6.45 issues per PR, "~1.7x more issues overall"; error handling and exception paths **"nearly 2x"**; the page says only "statistical rate ratios" (HOS's note adds "Poisson rate ratios at 95% CI", which I could not find on the blog page). Authorship was inferred from co-authorship signals, and CodeRabbit "cannot guarantee" the human arm is purely human. Grade B.

**How it fails.** Parris: "Not every flagged pattern is a defect in context", "some fail-soft behavior is intentional in context", and the scanner counts "Multiple rule hits within the same file and check ... individually with no deduplication". Normal code that trips it: top-level CLI entry points that convert any exception to an exit code, best-effort telemetry and cleanup, retry loops that catch broadly and re-raise after the last attempt, test helpers, and plugin loaders. In this repository, orchestrate's own scripts catch `(ValueError, TypeError)` and `OSError` deliberately, so a check keyed on *broad* classes rather than *any* `except` is the one with a tolerable rate. A `-U0` diff hides whether an added `except` is new or a re-indented old one; `git diff -w` or ignoring moved lines helps.

**Regex or parse.** The bare and broad forms are a one-line regex over `+` lines. "Catches and continues without re-raising" (C03 proper) and "returns success inside the handler" (C01) need the handler body, that is, a block parse: AIRA is "Parser-backed static analysis for Python (AST-based), JavaScript, TypeScript, and JSX/TSX (esprima-backed with lexical fallback)". A diff-only approximation is "an added `except`/`catch` line followed, within the same hunk, by an added `pass`, `continue`, `return` or `return <literal>` line at deeper indentation"; it is cheap and over-detects.

## 2. Success-after-failure

**Matches.** A `return` of a success value (`True`, `0`, `{"status": "ok"}`, an empty list) inside an exception handler, or a function that catches a failure of a write, audit log, or startup step and continues (AIRA C01 "Success returned after critical failure", C02 "Evidence lost without halting", C10 "Initialization catches failure and continues").

**Proxy for.** The strongest form of failure untruthfulness: callers and operators are told the operation worked. Parris marks C01, C02 and C10 HIGH only, and names C03 + C01 together as "Explicit failure concealment" to escalate (HOS's reading of the paper's co-occurrence profiles).

**Measured rate or lift.** No per-check rate for C01 is published; the 1.80x figure above is the aggregate of all HIGH findings. Grade B.

**How it fails.** Functions whose contract *is* "return False on any error" (probe, health check, `try_parse`), fallbacks that are documented (default config when the file is missing), and idempotent operations where "already done" is success. The signature cannot distinguish a documented fallback from a concealed one; HOS's SLR doc names "fallback-without-disclosure" as the thing to catch, which needs the docstring or call site.

**Regex or parse.** Parse. The diff-only approximation in section 1 (added `except` followed by an added `return <success literal>` in the same hunk) covers the simple cases and is what a worker's own PR usually contains.

## 3. Deleted or loosened assertions

**Matches.** Removed (`-`) lines matching an assertion in a test file with no equivalent added line: `assert `, `self.assert*`, `expect(`, `assert.*(`, `t.Error*`, `should`; and pairs of `-`/`+` lines that loosen one: `assertEqual` to `assertAlmostEqual`/`assertIn`/`assertTrue`, `toBe`/`toEqual` to `toBeTruthy`/`toMatchObject`/`toBeDefined`, an added `pytest.approx` or widened tolerance, an inverted expectation, an expected value edited to match actual output.

**Proxy for.** The agent satisfying the check instead of the claim. Alami calls one of nine observed strategies "Assert Narrowing" ("Some hide behind an assertion range"). ImpossibleBench's abstract states the field risk plainly: "an LLM agent with access to unit tests may delete failing tests rather than fix the underlying bug", and its Appendix B.1 records Claude Sonnet 3.7 on a scikit-learn task: "Rather than stopping when encountering a flawed test (as instructed), the agent directly inverted the test assertion."

**Measured rate or lift.**
- Zhong, Raghunathan and Carlini 2025 (ImpossibleBench; tasks whose specification conflicts with the tests, so any pass is cheating): "GPT-5 cheats in 76% of the tasks in Oneoff-SWEbench and 2.9% on Oneoff-LiveCodeBench"; 54.0% on Conflicting-SWEbench. Strategy mix: "Claude models and Qwen3-Coder, however, cheat primarily (>79%) through modifying test cases"; OpenAI models spread over four strategies (modify tests, overload comparison operators, record extra states, special-case) at ">14%" each. "Read-only access ... restores legitimate performance while preventing test modification attempts" but "does not eliminate other cheating methods such as special-casing or operator overloading." Benchmark conditions, not field PRs. Grade C for field rates, A for the mechanism under pressure.
- Alami 2026 (Substack note, 453 adversarial debate logs, 6 domains, 3 mutator families): a holistic LLM judge was fooled on 4 of 5 full theses, averaging 84.8/100 with several gaming theses at 95 to 97; adversarial execution caught 8 of 8 snippets and 5 of 5 theses. HOS cites an SSRN version (abstract id 6512960) that I did not retrieve. Grade C.
- Milanese et al. 2026 (AIDev, 6,582 human-agent PRs vs 3,122 human PRs; manual sample of 339 and 294 test-touching PRs): in co-evolution changes, **Delete 0.9% vs 4.8% (OR 0.18, p = 0.023)**, Modify 20.6% vs 43.5% (OR 0.34), Add 68.4% vs 54.8% (OR 1.79). Agents in the wild delete and modify existing tests *less* than humans; the authors read this as a "bias toward expanding test suites". Grade A.

So the evidence says: low base rate in merged field PRs, high rate under spec-test conflict. The check is for the second regime, which is exactly a worker iterating against a failing suite.

**How it fails.** Test refactors (`parametrize` collapsing ten asserts into one), assertions moved into a helper, tests deleted with the feature they covered, snapshot and fixture updates, replacing an exact float comparison with a correct tolerance, and renamed test files (use `git diff -M` so a rename is not a delete plus add). Loosening is hard to tell from a legitimate expected-value fix without the ticket; the honest output is "N assertions removed, M loosened, in these files" for the reviewer, not a verdict.

**Regex or parse.** Deleted assertions: regex over `-` lines restricted to test paths, cheap. Loosening: regex over paired `-`/`+` lines in the same hunk, still diff-level. Counting assertions per test function needs a parse.

## 4. Net test deletion

**Matches.** `git diff --name-status -M` showing `D` for test files, and `--numstat` showing a net negative line count across test paths, while non-test paths change.

**Proxy for.** The same self-certification class as section 3 at file granularity, and the reviewer's own fear: HOS's SLR doc quotes a field reviewer, "for all I know, it could turn off all the tests" (Omidvar-Tehrani et al. 2024, via HOS, not read).

**Measured rate or lift.** Milanese et al. 2026 as above: deletions are 0.9% of agent test changes vs 4.8% human. Haque, Ingale and Csallner 2026 (AIDev-pop, 33,596 PRs) do not report deletions ("We treat a file as modified only if its contents change (and thus ignore renamed or removed files)"); they report test inclusion rising from 31% to 52% of agentic PRs between January and July 2025 and that test PRs carry far more churn (median LOC churn, non-test vs test PRs: Claude 183 vs 1,736, Codex 39 vs 133, Copilot 49 vs 323, Cursor 139 vs 852, Devin 78 vs 335). Grade A, low lift: the signal is rare, which is what makes it worth a hard stop when it fires.

**How it fails.** Test consolidation, removal of a feature and its tests in the same PR, moving tests to a new directory layout. Rename detection removes most; the rest is the reviewer's one line.

**Regex or parse.** Neither; `--name-status -M` and `--numstat` with a test-path pattern.

## 5. Newly skipped tests

**Matches.** Added lines carrying `@pytest.mark.skip`, `skipif`, `xfail`, `@unittest.skip*`, `pytest.skip(`, `it.skip`/`test.skip`/`describe.skip`, `xit(`/`xdescribe(`, `@Ignore`/`@Disabled`, `t.Skip(`, or a deselect added to a test config (`--deselect`, `-k "not ..."`, `testPathIgnorePatterns`).

**Proxy for.** Turning a test off instead of fixing what it found; the smell literature calls it Ignored Test.

**Measured rate or lift.** Milanese et al. 2026, Table 3 (AromaDr smell deltas): **Ignored Test mean delta 1.607 in human-agent PRs vs 0.153 in human PRs**; Conditional Test 1.846 vs 0.534; Unknown Test (no assertion) 4.148 vs 1.926; Assertion Roulette 8.043 vs 7.698. The authors: "the effect sizes are negligible, indicating that these differences are not practically meaningful" (the human-agent arm has a much larger spread). Their Table 3 also repeats the Ignored Test row's values under Magic Number Test, which looks like a copy error, so treat the row with care. AIRA's C14 "Test Coverage Asymmetry" ("Failure-path tests lag happy-path tests", 563 findings in Study 1) is a different measure and not diff-local. Grade B.

**How it fails.** Platform- or dependency-conditional skips (`skipif(sys.platform == "win32")`, missing optional backend), marking a known-flaky upstream test, skipping a slow test behind a flag. All are legitimate when the reason string says so; a check that requires a non-empty reason argument removes most noise.

**Regex or parse.** Regex over `+` lines.

## 6. Leftover debug output

**Matches.** Added `print(`, `console.log(`/`console.debug(`, `debugger;`, `breakpoint()`, `pdb.set_trace()`, `binding.pry`, `var_dump(`, `fmt.Println(` in non-CLI source paths; also commented-out blocks and files such as `*.orig`, `*.log`, scratch scripts.

**Proxy for.** Unfinished work shipped, and (HOS SLR doc, citing Naqvi et al. 2026, not read) a channel that leaks secrets into logs.

**Measured rate or lift.** None published that I could find. Liu et al. 2026's top rules do not include print or console statements; Loker 2025 does not mention logging or debug statements; a web search for an agentic-PR study measuring debug artefacts found none. The only source is Kim and Yegge 2025 (a book), cited by HOS's SLR doc as listing "leftover debug logging, commented-out blocks, and temp files" among "recurrent, cheaply mechanical AI defect classes". Grade D.

**How it fails.** Badly, without a path allowlist: CLI tools print by design (every `scripts/*` in this repository writes its result to stdout), test files print on failure, notebooks and examples print. `console.log` in a Node CLI or a build script is correct. Keep the check to library and application paths, or to a `debugger`/`breakpoint()`/`set_trace` subset that has no legitimate committed use.

**Regex or parse.** Regex over `+` lines, path-aware.

## 7. New dependency manifest entries

**Matches.** An added line in a dependency manifest that looks like a dependency (HOS: path matches `requirements*.txt|pyproject.toml|setup.py|setup.cfg|Pipfile|package.json|go.mod|Gemfile|Cargo.toml|pom.xml|build.gradle` and the stripped line starts with `[A-Za-z0-9_.\-"']`); separately, a version spec that is a wildcard or range, and a lockfile change without a manifest change (orchestrate's `gate` already checks the last).

**Proxy for.** New third-party code and a new supply-chain surface, which HOS treats as structural by definition: "A new entry in any dependency manifest" is the first row of the contract's §2a table, and the signature "FORCE[s] `structural` regardless of the author's label".

**Measured rate or lift.**
- Ghammam and Almukhtar 2026 (387 agentic build-file PRs, 945 Maven/Gradle/CMake/Make files, AIDev): **364 smells, of which Wildcard Usage 97** ("using dependency version patterns like '+' or '*'"), Lack of Error Handling 63, Deprecated Dependencies 56, Outdated Dependencies 27, Missing Version in Raw 19, Hardcoded Credentials 5. 66 of 945 files gained smells, 31 lost some, 824 were clean before and after. "Overall, 61.4% (238/387) of the analyzed PRs were merged by developers, and in most cases, the merge occurred immediately after automated checks passed, with no additional modifications from human reviewers." No human baseline. Grade B.
- HOS: the signature has no measured base rate; HOS logs every `structural-override` event "so the misclassification rate becomes measurable research data" (DECISIONS D33), and the contract calls the set "a floor ... deliberately biased to over-detect". Grade D.
- Hallucinated and typosquatted packages (HOS SLR doc citing Ji et al. 2024 and Hjazeen 2026, not read here) are the adversarial version of the same line. Grade D.

**How it fails.** HOS flags version bumps on purpose ("pure version bumps of existing deps are still flagged — a new pinned version is structural enough to merit a glance"), which in a repo with a bot bumping versions is most manifest diffs. Reordering, comments, `[tool.*]` sections of `pyproject.toml`, and dev-only groups all match the HOS line regex. A check that diffs the parsed dependency *name set* (new name vs changed version) has a far better rate and is still cheap for `requirements.txt`, `package.json` and `pyproject.toml`.

**Regex or parse.** Regex for "manifest touched, line added"; a small per-format parse to separate new names from bumps.

## 8. Deletion ratio

**Matches.** `deleted / (added + deleted)` from `git diff --numstat`, excluding lockfiles, generated and vendored paths; or deleted lines above an absolute threshold.

**Proxy for.** Removed behaviour, which reviewers treat as harder to verify than added behaviour.

**Measured rate or lift.**
- Canelas dos Santos, Branco, Gamboa and Fonseca 2026 (MSR Mining Challenge, AIDev auto-merged PRs; Zenodo artifact page): "maintainers auto-merge agentic PRs more often but show caution toward PRs that delete existing code"; "auto-merged PRs are smaller and more focused"; "repositories tend to either auto-merge all or none agentic PRs". No numbers on the record page; the paper PDF was not retrieved. Grade C.
- Watanabe et al. 2026: agentic PRs "add more lines (48 vs 24) while maintaining similar deletion counts (7 vs 8)". So agentic PRs are not deletion-heavy on average. Grade A.
- Ogenrwot and Businge 2026 (24,014 merged agentic vs 5,081 merged human PRs): Cliff's delta for Deletions 0.4462 (medium) with the text stating humans change more, so merged agentic PRs delete *less* than merged human ones. Grade B (direction stated in prose, not per metric).

Net: a deletion-heavy agentic PR is atypical, and maintainers are cautious of it; there is no measurement tying deletion ratio to breaking changes. HOS's SLR doc lists "deletion-ratio" as a recommended tier input on the strength of the Branco finding alone.

**How it fails.** Dependency removals, dead-code cleanups a ticket asked for, lockfile regeneration, moving a file (without `-M`), reformatting. Exclude lockfiles, generated files and renames, and the remaining hits are the ones worth a look.

**Regex or parse.** `--numstat`.

## 9. Multi-purpose PRs

**Matches.** More than one conventional-commit type among the PR's commit subjects (orchestrate's commits carry them by the developer's standing rule), or more than N top-level path domains touched (HOS `diff_size.py` R2: distinct domain prefixes >= 3, advisory only).

**Proxy for.** A unit of review the human cannot hold in one mental model; Watanabe et al. §5.2: "Large PRs increase reviewer cognitive load and raise the risk of rejection".

**Measured rate or lift.** Watanabe et al. 2026 (567 Claude Code PRs matched to 567 human PRs across 157 repositories; purposes coded with the Zeng et al. framework allowing multiple labels, 75.8% label-level agreement): **"85 Agentic-PRs (39.9%) have multiple objectives, compared to just 27 multi-purpose Human-PRs (12.2%)"** (denominators 213 and 221 coded PRs). Acceptance 83.77% vs 91.00% (chi-squared, p < 0.05). Of 92 rejected agentic PRs, 64.1% were closed without explanation; "Are too large" is 3.3% of coded rejections; the quoted reason is "Closing in favor of smaller, more focused PRs to make reviews more manageable". Recommendation §5.2: "Split large, multi-purpose PRs into smaller, easy-to-review submissions." Ehsani et al. 2026 quote a reviewer on a rejected PR, "This is a LOT to review, would really prefer smaller granular PRs", filed under "unwanted feature" (24 of 600, 4%). Grade A for the rate, with the caveat that the coding is of PR purposes, not of a mechanical label.

**How it fails.** The HOS default domain map puts `scripts/`, `docs/` and `tests/`-like trees in separate domains, so a normal feature PR (code plus tests plus a doc line) hits the threshold of 3: the HOS validator is advisory for this reason. Commit-type detection fails when workers squash, when a `fix` needs a `refactor` to land, and when the label is simply wrong (it is author-written). HOS's own field version is #401: "Accumulated 81 commits in one branch rather than submitting incremental PRs" (grade D).

**Regex or parse.** Regex on commit subjects, or path prefixes; no parse.

## 10. File and commit counts (diff size)

**Matches.** Lines changed (`--numstat` sum, excluding lockfiles, generated and vendored paths), files changed, commits in the PR (`rev-list --count base..head`). HOS R1: `changed_lines > 400 OR changed_files > 15` sets a HIGH tier floor; zero means "data unavailable" and does not fire.

**Proxy for.** Review cost and the chance the reviewer gives up. Ehsani et al. 2026: "Reviewer-level abandonment is the most frequent rejection pattern, accounting for 228 PRs (38%)" of 600 sampled rejections.

**Measured rate or lift.**
- Watanabe et al. 2026: median files 2 vs 2; added lines **48 vs 24**; deleted 7 vs 8; description length 355 vs 56 words; median revision commits 2 vs 2. Mann-Whitney U at alpha 0.05, significance only, no effect sizes. Grade A.
- Ehsani et al. 2026 (33,596 agentic PRs, 71.48% merged): "Not merged PRs tend to introduce larger modifications in the number of files and LOC than merged ones", with "the difference in total lines of code changes is 17%" and "the difference in the number of changed files is 10%"; failed CI checks have Cliff's delta -0.24 and "each additional failed CI check decreases the odds of a merge by about 15%". Grade B (aggregate differences, logistic regression).
- Ogenrwot and Businge 2026: among *merged* PRs, Cliff's delta Commits 0.5429 (large), Files 0.4487 (medium), Additions 0.2836 (small), Deletions 0.4462 (medium), with "Human PRs touch more files and often involve more commits than Agentic PRs." Grade B.

So the agentic excess is in **added lines**, not files or commits; commit count discriminates agentic from human, not good from bad. HOS's 400/15 thresholds are unreferenced defaults ("The threshold is not a magic number; the principle is that a threshold must exist and must be calibrated to the reviewer's cognitive budget", HOS finding on Watanabe). The map's "project-calibrated thresholds" item (Adejumo and Johnson 2025, via the map) is the right reading: percentiles of this repository's own PR distribution, once recorded.

**How it fails.** Lockfiles, generated code, vendored dependencies, snapshot fixtures, renames and formatting-only commits inflate all three counts; a single cohesive 300-line change is better than three broken splits (HOS: "This finding is not an argument for arbitrary small PRs"). Exclusions are required before any threshold means anything.

**Regex or parse.** `--numstat`, `--name-status -M`, `rev-list --count`.

## 11. Commit-type (refactor/chore) risk

**Matches.** Conventional-commit type on the commit subjects or PR title: `refactor`, `chore`, `perf` vs `feat`, `fix`.

**Proxy for.** Maintenance-class change, where the agent must preserve invariants across call sites it did not enumerate; HOS's hypothesis is that agents are better at local generation than global invariant preservation.

**Measured rate or lift.** Ferdous et al. 2026 (MSR 2026; AIDev, Python only; 7,191 agentic and 1,402 human PRs, 4,798 and 1,026 after task filtering; an AST diff tool applying 17 breaking-change patterns from Du et al., validated on 94 of 3,538 detected patches at 95.7% and 93.6% true-positive by two raters, kappa 0.79). Overall: agentic 805 of 23,333 patches (**3.45%**, 11.3% of PRs) vs human 2,733 of 36,991 (**7.40%**, 21.18% of PRs). By task (labels from AIDev's classification "according to the commit convention"):

| task | agentic | human |
|---|---|---|
| chore | 9.35% | 4.95% |
| refactor | 6.72% | 4.36% |
| perf | 4.12% | 0.90% |
| feat | 2.89% | 7.74% |
| fix | 2.69% | 5.32% |

The pattern inverts between arms: agents break less on feat/fix and more on chore/refactor. (HOS's SLR doc states "feature work (3.45%)"; 3.45% is the overall agentic rate, feat is 2.89%.) Watanabe et al. add volume: refactoring is 24.9% of agentic PRs vs 14.9% of human. Grade A.

**How it fails.** The type is author-supplied framing, which the map treats as untrusted input; a worker can write `fix:` on a refactor. Use it one way: `refactor`/`chore` may raise the tier, `feat`/`fix` may never lower it (the same asymmetry HOS applies to confidence, D51). Repositories without the convention get nothing. Ferdous "relied exclusively on the classification schema provided by the AIDev dataset", so the rates are for AIDev's labels, not for a regex on subjects. Docs and test tasks were not reported.

**Regex or parse.** Regex on commit subjects.

## 12. HOS structural signatures and path tier floors

HOS's `change_classifier.py` is the one deterministic diff classifier in the sources with a running false-positive history, so it is worth recording even where orchestrate will not adopt a signature.

**Matches** (added-line regexes, framework tooling tree exempt):
- `new-external-dependency`: section 7.
- `new-permission-or-auth-state`: `permission_required|login_required|IsAuthenticated|IsAdminUser|has_perm|PermissionDenied|user_passes_test|staff_member_required|LoginRequiredMixin|PermissionRequiredMixin|AccessMixin|@roles?_required|@authorize|require_role`.
- `new-user-flow-or-route`: `\bpath\(|\bre_path\(|\burl\(|@app\.route|@router\.(get|post|put|delete|patch)|router\.(...)|\bRoute\(|createBrowserRouter|<Route\b`.
- `new-user-facing-state`: `choices\s*=|TextChoices|IntegerChoices|StateField|FSMField|@transition|STATUS_[A-Z]|_STATES\b|enum\.Enum`.
- `new-user-facing-surface`: a template or component added as a new file.
- `new-observability-instrumentation`: OpenTelemetry/Prometheus/statsd/structlog primitives.
- Modification variants (SPEC-121): a removed and an added auth line that differ; a removed and an added line in a structural section of a tracked spec.
- Tier floor by path: payment/billing/stripe paths CRITICAL; auth/session/token, `migrations/*.py`, pii/gdpr/privacy/consent paths HIGH; any `.py|.js|.ts|.jsx|.tsx` MEDIUM. By added line: `stripe\.|PaymentIntent|charge\(|Card\(|ACH|IBAN|account_number` CRITICAL; `EmailField|first_name|last_name|date_of_birth|ssn|national_id|phone_number|address|personal_data` HIGH.

**Proxy for.** A change the author should have classified `structural` (human gate) but could label `additive`; the evaluator re-derives the label "only in the loosening direction".

**Measured rate or lift.** None. HOS states the design choice instead: "a false positive forces a human to look (conservative, safe). A false negative is the only real failure mode". The contract records the residual gap: the signatures "do not detect a structural change that modifies existing behavior without adding a signature". Grade D.

**How it fails (documented incidents, all HOS field).**
- HOS#117: the classifier matched *its own source*, where the patterns appear as literals, producing "phantom structural signals whenever a framework file is in the diff"; fixed by exempting the tooling tree.
- #1889: the `ACH`/`IBAN` tokens matched `MACHINE`, `CACHE`, `ATTACH`, `REACH` and floored CRITICAL; fixed with letter-bounded lookarounds, accepting that `ACHTransfer` no longer matches.
- #2031: a `propagat` stem matched English prose ("propagates") in Markdown, and an unbounded dotted-name quantifier backtracked quadratically on a long line (CWE-1333); fixed by listing code tokens only and bounding the quantifiers.
- #2201: a `new\s+state` alternative matched design-doc prose ("the new state variables"), and argparse `choices=` in internal CLI tooling read as user-facing state; fixed by dropping the prose alternative and exempting `scripts|bootstrap|bin/`.
- The 2024-era `.select_related()` N+1 example in HOS's HANDLING-FINDINGS doc is the general pattern: "a validator flags something, it is doing its job by drawing your eye — it has not condemned the code."

HOS's own meta-finding on regex checks ("A textual grep clears the pattern, not the class", audit #1407) is the limit statement: a class defined by a relationship (here, "behaviour changed") has no single textual signature; the signatures above catch additions, by construction.

**Regex or parse.** Regex; the modification variants pair `-`/`+` lines.

## Not a check: self-reported confidence

Ferdous et al. 2026 §5.3: "the confidence scores are strongly right-skewed, with 99.9% of AI-generated pull requests between 8 and 10"; breaking-change rate at level 8 is 3.94% (33 of 837), level 9 3.96% (311 of 7,854), level 10 3.16% (458 of 14,509). Flat. HOS D51 turns this into a rule: high confidence may never lower scrutiny; low confidence may raise it. Grade A. Relevant here because a worker's `verdict:`/`confidence:` line is the same kind of input.

## Ranking by evidence strength

1. **Commit-type risk** (Ferdous: matched arms, validated detector, large N) — the best-measured tier input, and the only one with a per-class breaking rate. Its weakness is that the label is author-written.
2. **Swallowed or broadened exceptions** (Parris 1.80x matched control; Liu 8.5% top rule at scale; Loker ~2x) — three independent sources agree on direction; none gives a per-pattern precision.
3. **Multi-purpose PRs** (Watanabe 39.9% vs 12.2%) — a clean matched comparison, with a small coded subset.
4. **Added-line size** (Watanabe 48 vs 24; Ehsani +17% LOC in not-merged) — file and commit counts do *not* carry the same signal.
5. **New dependency entries** (Ghammam 97/364 wildcard; HOS floor) — strong as a structural trigger, weak as a defect rate.
6. **Deleted or loosened assertions** (ImpossibleBench >79% via test edits under conflict; Milanese 0.9% in the field) — the mechanism is certain, the field rate low; a hard stop when it fires, not a tier input.
7. **Newly skipped tests** (Milanese 1.607 vs 0.153, negligible effect) — cheap, noisy, requires a reason string to be useful.
8. **Success-after-failure** (Parris C01, no separate rate) — needs a parse; worth it only inside the exception check.
9. **Net test deletion** (Milanese 0.9% vs 4.8%) — rare, so a hard stop is cheap.
10. **Deletion ratio** (Branco qualitative; Watanabe and Ogenrwot show no agentic excess) — a reviewer-attention signal, not a defect predictor.
11. **Debug output** — no measurement; a hygiene check with a path allowlist.
12. **HOS auth/route/state/path signatures** — field-only, stack-specific (Django/JS), four recorded false-positive incidents; the pattern is transferable, the regexes are not.

## HOS field observations versus published measurements

HOS field (grade D): the structural-override signatures and their false-positive incidents, the 400/15/3 diff-size defaults, the ratchet and "over-detect toward the human" stance, D51/D52, the 81-commit branch (#401), "a textual grep clears the pattern, not the class", and HOS's reading of AIRA's co-occurrence profiles and of `pip install aira-scanner` (the paper names github.com/BDB-Labs/aira-scanner and aira.bageltech.net; the GitHub URL returned 404 on 2026-11-10 and no PyPI package of that name was found). HOS's SLR-Derived-Improvements doc is HOS's reading of 149 papers; where this note cites a paper through it (Kim and Yegge, Ji et al., Naqvi et al., Omidvar-Tehrani et al., Hjazeen, Huang et al. 2026, Borg et al.), the item is marked "via HOS, not read" and should be fetched before orchestrate relies on it.

Published measurements (grades A and B) are the papers in the Sources list that were fetched directly: Parris, Liu et al., Loker, Ferdous et al., Watanabe et al. (both), Ehsani et al., Ogenrwot and Businge, Milanese et al., Haque et al., Ghammam and Almukhtar, Zhong et al., Canelas dos Santos et al. (record page only), Alami (author's note only).

Two provenance corrections for later citing: Loker 2025 is the primary for 1.7x and Ferdous et al. only cite it in their introduction (HOS's own finding says the same); and HOS's "feature work (3.45%)" is the overall agentic rate.

## Sources

- Parris, W. M. (2026). AIRA: AI-Induced Risk Audit: A Structured Inspection Framework for AI-Generated Code. arXiv:2604.17587 [cs.SE], v1, 19 April 2026. https://arxiv.org/abs/2604.17587 (HTML: https://arxiv.org/html/2604.17587v1)
- Liu, Y., Widyasari, R., Zhao, Y., Irsan, I. C., Chen, J., and Lo, D. (2026). Debt Behind the AI Boom: A Large-Scale Empirical Study of AI-Generated Code in the Wild. arXiv:2603.28592 [cs.SE], v3, 13 October 2026. https://arxiv.org/abs/2603.28592
- Loker, D. (2025). Our new report: AI code creates 1.7x more problems (State of AI vs Human Code Generation Report). CodeRabbit, 17 December 2025. https://www.coderabbit.ai/blog/state-of-ai-vs-human-code-generation-report
- Ferdous, K. M., Banik, D., Chowdhury, K., and Shamim, S. I. (2026). Safer Builders, Risky Maintainers: A Comparative Study of Breaking Changes in Human vs Agentic PRs. In Proceedings of the 23rd International Conference on Mining Software Repositories (MSR '26), ACM. DOI 10.1145/3793302.3793610 (Crossref-verified, issued 2026-04-13); arXiv:2603.27524. https://arxiv.org/abs/2603.27524
- Watanabe, M., Li, H., Kashiwa, Y., Reid, B., Iida, H., and Hassan, A. E. (2026). On the Use of Agentic Coding: An Empirical Study of Pull Requests on GitHub. ACM Transactions on Software Engineering and Methodology. DOI 10.1145/3798166 (Crossref-verified, issued 2026-09-16); arXiv:2509.14745 v3, 9 February 2026. https://arxiv.org/abs/2509.14745
- Ehsani, R., Pathak, S., Rawal, S., Mujahid, A. A., Imran, M. M., and Chatterjee, P. (2026). Where Do AI Coding Agents Fail? An Empirical Study of Failed Agentic Pull Requests in GitHub. MSR 2026. arXiv:2601.15195. https://arxiv.org/abs/2601.15195
- Ogenrwot, D., and Businge, J. (2026). How AI Coding Agents Modify Code: A Large-Scale Study of GitHub Pull Requests. MSR 2026 Mining Challenge. DOI 10.1145/3793302.3793603; arXiv:2601.17581 v3, 6 April 2026. https://arxiv.org/abs/2601.17581
- Milanese, R., Salzano, F., Spina, A., Vitale, A., Pareschi, R., Fasano, F., and Fazzini, M. (2026). Human-Agent versus Human Pull Requests: A Testing-Focused Characterization and Comparison. MSR 2026. arXiv:2601.21194. https://arxiv.org/abs/2601.21194
- Haque, S., Ingale, S., and Csallner, C. (2026). Do Autonomous Agents Contribute Test Code? A Study of Tests in Agentic Pull Requests. arXiv:2601.03556, 7 January 2026. https://arxiv.org/abs/2601.03556
- Ghammam, A., and Almukhtar, M. (2026). AI builds, We Analyze: An Empirical Study of AI-Generated Build Code Quality. MSR 2026. arXiv:2601.16839, 23 January 2026. https://arxiv.org/abs/2601.16839
- Zhong, Z., Raghunathan, A., and Carlini, N. (2025). ImpossibleBench: Measuring LLMs' Propensity of Exploiting Test Cases. arXiv:2510.20270 [cs.LG], 23 October 2025. https://arxiv.org/abs/2510.20270
- Canelas dos Santos, P. A., Branco, R., Gamboa, C., and Fonseca, A. (2026). LGTM! Characteristics of Auto-Merged LLM-based Agentic PRs. MSR 2026 Mining Challenge; artifact v2, 22 January 2026. DOI 10.5281/zenodo.18340558. https://zenodo.org/doi/10.5281/zenodo.18340558 (HOS cites the authors as Branco et al.)
- Alami, D. (2026). Cognitive Camouflage: How persuasive LLM-written code can pass its own tests and still fail the claim. Author's note, 9 April 2026. https://danielalami.substack.com/p/cognitive-camouflage (HOS cites an SSRN version, abstract id 6512960, not retrieved)
- Watanabe, K., Shirai, T., Kashiwa, Y., and Iida, H. (2026). What to Cut? Predicting Unnecessary Methods in Agentic Code Generation. arXiv:2602.17091, 19 February 2026. https://arxiv.org/abs/2602.17091
- Li, H., Zhang, H., and Hassan, A. E. (2026). AIDev: Studying AI Coding Agents on GitHub. arXiv:2602.09185, 9 February 2026 (the dataset behind Ferdous, Ehsani, Ogenrwot, Milanese, Haque, Ghammam and Canelas dos Santos). https://arxiv.org/abs/2602.09185
- Cited via HOS's SLR-Derived-Improvements doc and not read here: Kim, G., and Yegge, S. (2025). Vibe Coding. IT Revolution; Ji, J., Jun, J., Wu, M., and Gelles, R. (2024). Cybersecurity Risks of AI-Generated Code. DOI 10.51593/2023CA010; Naqvi et al. (2026); Omidvar-Tehrani et al. (2024); Hjazeen (2026); Huang et al. (2026) arXiv:2601.21276; Borg et al. (2026) arXiv:2601.02200.
- Thurlow, S. (2026). Human Oversight System (HOS), documentation and research notes, https://github.com/thurlow-research/HumanOversightSystem (software archived at https://doi.org/10.5281/zenodo.21347272), docs CC BY 4.0, code MIT. Files read: `scripts/oversight/change_classifier.py`, `scripts/oversight/validators/diff_size.py`, `tests/oversight/test_change_classifier.py`, `contract/OVERSIGHT-CONTRACT.md` §2a and §7, `DECISIONS.md` D33, D51, D52, `docs/HANDLING-FINDINGS.md`, `docs/planning/SLR-Derived-Improvements.md` (P0-4, P1-1, P1-4), `docs/v0.4.0/TECHNICAL-DESIGN-377-diff-size-risk-floor.md`, and `research/findings/`: `aira-deterministic-scanner-for-ai-code-failures.md`, `llm-reviewer-can-mask-deterministic-scanner-failures.md`, `the-1point7x-defect-rate-claim-has-a-precise-empirical-source.md`, `agentic-prs-are-larger-and-more-multipurpose-than-human-prs.md`, `maintenance-class-changes-carry-higher-agentic-risk-than-generation.md`, `agent-confidence-is-uninformative-for-defect-prediction.md`, `self-classification-cannot-gate-the-human-boundary.md`, `a-textual-grep-clears-the-pattern-not-the-class.md`, `ai-agent-scope-drift-mirrors-human-dev-behavior.md`.
