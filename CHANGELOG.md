# Changelog

## [0.8.0](https://github.com/shrujancheruku/claude-orchestrate/compare/v0.7.0...v0.8.0) (2026-11-07)


### Features

* **board:** opt-in Projects board made by setup and synced every pass ([#244](https://github.com/shrujancheruku/claude-orchestrate/issues/244)) ([4986dff](https://github.com/shrujancheruku/claude-orchestrate/commit/4986dff5323fee971aca4439bc49c2f144eded58))
* **ci-times:** measure CI wait on merged PRs with an over-limit verdict ([#221](https://github.com/shrujancheruku/claude-orchestrate/issues/221)) ([d808984](https://github.com/shrujancheruku/claude-orchestrate/commit/d808984a9648f14ed41394a595b0ef747abcffe8))
* **gate:** the gate is the one check before review: overlap, dependencies, secrets and lockfiles, on a named head ([#306](https://github.com/shrujancheruku/claude-orchestrate/issues/306)) ([ece7fd0](https://github.com/shrujancheruku/claude-orchestrate/commit/ece7fd0243d3a88b503939ded8a141d54569e6c6))
* **lead-brief:** risk-note fallback comment and the lead's start-up index ([#233](https://github.com/shrujancheruku/claude-orchestrate/issues/233)) ([1c7dd3d](https://github.com/shrujancheruku/claude-orchestrate/commit/1c7dd3d631afdef6706c92f67b41f5c746d5dc73))
* **lead:** lead skill moves for stops, triage, risk notes, CI, publishing and decision PRs ([#245](https://github.com/shrujancheruku/claude-orchestrate/issues/245)) ([918492c](https://github.com/shrujancheruku/claude-orchestrate/commit/918492cd7ce1e775552c885aedb2e529ada67ffe))
* **lead:** lead skill start-up finds, launches or resumes the orchestrator ([#242](https://github.com/shrujancheruku/claude-orchestrate/issues/242)) ([d777440](https://github.com/shrujancheruku/claude-orchestrate/commit/d777440f44cc6e5050b4892f0271d7af71df36cd))
* **lead:** needs-lead and lead labels, glossary, lead ADR and Orchestrator model row ([#218](https://github.com/shrujancheruku/claude-orchestrate/issues/218)) ([49ec072](https://github.com/shrujancheruku/claude-orchestrate/commit/49ec07228880841163c39d41f40487052357b23d))
* **run:** a claims script decides each claimed ticket's state, and worker-live goes ([#302](https://github.com/shrujancheruku/claude-orchestrate/issues/302)) ([f44bd38](https://github.com/shrujancheruku/claude-orchestrate/commit/f44bd38925480197cb979715f7777f8a3c095d6b))
* **run:** a frontier script decides the takeable tickets from one paginated tracker snapshot ([#310](https://github.com/shrujancheruku/claude-orchestrate/issues/310)) ([f07136f](https://github.com/shrujancheruku/claude-orchestrate/commit/f07136f92b9edd3f9d3ae250f4b90052a705ac62))
* **run:** detect a live lead and route stops through lead-route ([#223](https://github.com/shrujancheruku/claude-orchestrate/issues/223)) ([c55a678](https://github.com/shrujancheruku/claude-orchestrate/commit/c55a6785636e6709cb25c7ca3897104e65e85a06))
* **run:** lead tickets and lead PRs join the pipeline ([#228](https://github.com/shrujancheruku/claude-orchestrate/issues/228)) ([ff650cc](https://github.com/shrujancheruku/claude-orchestrate/commit/ff650cc6927f3717a045b6da2818e8961be1d62f))
* **run:** lead-route hands back split tickets, abandons wontfix, holds ADR-conflict PRs ([#225](https://github.com/shrujancheruku/claude-orchestrate/issues/225)) ([3467740](https://github.com/shrujancheruku/claude-orchestrate/commit/3467740a6a7054333fb76bd27f6f7d02def66b14))
* **run:** worker-report classifies every worker completion before the orchestrator acts ([#295](https://github.com/shrujancheruku/claude-orchestrate/issues/295)) ([3f38738](https://github.com/shrujancheruku/claude-orchestrate/commit/3f38738bfa9ebb6db650c7ef6083cc89006f497f))
* **worker,reviewer:** record discovered work on PRs and reviews ([#222](https://github.com/shrujancheruku/claude-orchestrate/issues/222)) ([6a239a1](https://github.com/shrujancheruku/claude-orchestrate/commit/6a239a108eb56c586689a599a64f9ad15ba2e66a))


### Bug Fixes

* **gate:** pass a branch that is behind but mergeable, leaving the update to merge ([#251](https://github.com/shrujancheruku/claude-orchestrate/issues/251)) ([67c1895](https://github.com/shrujancheruku/claude-orchestrate/commit/67c18959fbeb982cb9e0b45391e52f80e89cdcb7))
* **guard:** a worker may not background any Bash command ([#300](https://github.com/shrujancheruku/claude-orchestrate/issues/300)) ([971f348](https://github.com/shrujancheruku/claude-orchestrate/commit/971f348b54008afbec7fcb76248c9434c334da00))
* **guard:** allow read-only gh api graphql queries, refuse mutations ([#290](https://github.com/shrujancheruku/claude-orchestrate/issues/290)) ([a083811](https://github.com/shrujancheruku/claude-orchestrate/commit/a083811acf46f20d00aad422d0791dcde3c477fc))
* **holds:** a merged or closed ticket's touch map no longer counts as building ([#234](https://github.com/shrujancheruku/claude-orchestrate/issues/234)) ([46d0f97](https://github.com/shrujancheruku/claude-orchestrate/commit/46d0f970c803935c403f79e1615332dabc6f1303))
* **holds:** hold the changes of an Elsewhere ticket's live worktree ([#252](https://github.com/shrujancheruku/claude-orchestrate/issues/252)) ([03dbce4](https://github.com/shrujancheruku/claude-orchestrate/commit/03dbce4c0cc392a2eab43ccceddaf245e7f9febc))
* **lead-route:** hold REDISPATCH while another session's worker is live, and rescue the old worktree first ([#249](https://github.com/shrujancheruku/claude-orchestrate/issues/249)) ([95ba98e](https://github.com/shrujancheruku/claude-orchestrate/commit/95ba98e1021f5b7d34bd998be3996b8dd6c4f0a0))
* **merge:** unlock and remove a harness-locked worktree after the merge ([#254](https://github.com/shrujancheruku/claude-orchestrate/issues/254)) ([c26220a](https://github.com/shrujancheruku/claude-orchestrate/commit/c26220a207007f122a94c60e1cd2c74b22392220))
* **profile:** a pass logged after the last record sets the slot count ([#232](https://github.com/shrujancheruku/claude-orchestrate/issues/232)) ([708bd57](https://github.com/shrujancheruku/claude-orchestrate/commit/708bd57fc735a35b5c75453cc36419a830b96bdf))
* **worker:** refuse backgrounded verify, treat status-less hand-backs as interim ([#246](https://github.com/shrujancheruku/claude-orchestrate/issues/246)) ([3df0247](https://github.com/shrujancheruku/claude-orchestrate/commit/3df0247a0ed69d1b94f6891f2719a61f3f6fca17))


### Performance Improvements

* **tests:** copy a per-process ScriptEnv template and add canned gh reply helpers ([#297](https://github.com/shrujancheruku/claude-orchestrate/issues/297)) ([282a0ed](https://github.com/shrujancheruku/claude-orchestrate/commit/282a0ed9f02191f8d67f45b1a0ce59167076e2ed))
* **tests:** guard tests call the rules in-process, with one subprocess test for the entry point ([#296](https://github.com/shrujancheruku/claude-orchestrate/issues/296)) ([334767e](https://github.com/shrujancheruku/claude-orchestrate/commit/334767e6425c3be6d23ad51458652029749d73f9))
* **tests:** split test_merge into four modules so no one module bounds the parallel run ([#298](https://github.com/shrujancheruku/claude-orchestrate/issues/298)) ([6608342](https://github.com/shrujancheruku/claude-orchestrate/commit/6608342b0f29105c00d93a30c3de47d16e794009))
* **verify:** run test modules in parallel with a stdlib runner ([#291](https://github.com/shrujancheruku/claude-orchestrate/issues/291)) ([325026a](https://github.com/shrujancheruku/claude-orchestrate/commit/325026a999a7cedb51dc1dd18a4d07a79eadb4f2))

## [0.7.0](https://github.com/shrujancheruku/claude-orchestrate/compare/v0.6.0...v0.7.0) (2026-11-05)


### Features

* **merge:** merge decides readiness against the approved head ([#186](https://github.com/shrujancheruku/claude-orchestrate/issues/186)) ([0f13267](https://github.com/shrujancheruku/claude-orchestrate/commit/0f13267aff19b1ed4773e0f1aa94585edd829e6f))
* **run:** a holds script decides held paths and overlap ([#183](https://github.com/shrujancheruku/claude-orchestrate/issues/183)) ([d4c3d49](https://github.com/shrujancheruku/claude-orchestrate/commit/d4c3d49d0bf188efb944be9155ef4ce93f07c1a0))


### Bug Fixes

* **run:** verify --config reads the commands from the config, unexpanded ([#185](https://github.com/shrujancheruku/claude-orchestrate/issues/185)) ([7f0a0a3](https://github.com/shrujancheruku/claude-orchestrate/commit/7f0a0a3fd6d296e85023b2abd5c97b79bacef3d2))
* **scripts:** one run log for events and passes, kept in the main checkout ([#176](https://github.com/shrujancheruku/claude-orchestrate/issues/176)) ([ce0be7d](https://github.com/shrujancheruku/claude-orchestrate/commit/ce0be7d0f7a3171aab445e0087a3c204694d76be))
* **watch-ci:** end a watch with an error line when gh keeps failing ([#187](https://github.com/shrujancheruku/claude-orchestrate/issues/187)) ([0d36251](https://github.com/shrujancheruku/claude-orchestrate/commit/0d362516d3b73ff80b96b4ffe58de49e05eae4a5))

## [0.6.0](https://github.com/shrujancheruku/claude-orchestrate/compare/v0.5.0...v0.6.0) (2026-11-05)


### Features

* **profile:** record greedy passes and measure occupancy against the cap ([#172](https://github.com/shrujancheruku/claude-orchestrate/issues/172)) ([51843c0](https://github.com/shrujancheruku/claude-orchestrate/commit/51843c0ffac7e6e10d748d6ceb6b6ca6088f2ad0))
* **run:** add --greedy to size slots from independent tickets ([#171](https://github.com/shrujancheruku/claude-orchestrate/issues/171)) ([7ff07ea](https://github.com/shrujancheruku/claude-orchestrate/commit/7ff07eae99eae6e96102c16737798b69d11d20af))

## [0.5.0](https://github.com/shrujancheruku/claude-orchestrate/compare/v0.4.1...v0.5.0) (2026-11-05)


### ⚠ BREAKING CHANGES

* **profile:** drop the status-line usage tap, state that headroom isn't measured ([#107](https://github.com/shrujancheruku/claude-orchestrate/issues/107))
* **worker:** the mechanic agent is removed. Configs with a mechanic Models row ("Scout, mechanic") should drop it.
* **run:** /loop /orchestrate:run is no longer supported. Plain /orchestrate:run wakes on worker and reviewer completions and watch-ci lines.

### Features

* **gate:** script gate before review ([#72](https://github.com/shrujancheruku/claude-orchestrate/issues/72)) ([50d3d74](https://github.com/shrujancheruku/claude-orchestrate/commit/50d3d74b9d506a3a01de1115757bda464f9b4c2d))
* **merge:** merge script that waits for MERGED, then cleans up ([#75](https://github.com/shrujancheruku/claude-orchestrate/issues/75)) ([79767b3](https://github.com/shrujancheruku/claude-orchestrate/commit/79767b378eed0650736613e7a9e92ed8d358f439))
* **merge:** regenerate conflicting generated files ([#79](https://github.com/shrujancheruku/claude-orchestrate/issues/79)) ([a7dfd42](https://github.com/shrujancheruku/claude-orchestrate/commit/a7dfd421f11fd0deb9c1842898219784659ce3c1))
* **merge:** shared-file replay and post-merge sweep ([#77](https://github.com/shrujancheruku/claude-orchestrate/issues/77)) ([c57a9f1](https://github.com/shrujancheruku/claude-orchestrate/commit/c57a9f1207ad17ebb0a7315474e0755f50cafdaf))
* **profile:** compare command and regenerated 44e00c3b baseline ([#78](https://github.com/shrujancheruku/claude-orchestrate/issues/78)) ([e06736c](https://github.com/shrujancheruku/claude-orchestrate/commit/e06736cee9cf66a8a4579347047dd23d0efc299e))
* **profile:** drop the status-line usage tap, state that headroom isn't measured ([#107](https://github.com/shrujancheruku/claude-orchestrate/issues/107)) ([d42663c](https://github.com/shrujancheruku/claude-orchestrate/commit/d42663c718e17d17607965d008d78c6316745279))
* **profile:** orchestrator turns by job, context over time and events log ([#76](https://github.com/shrujancheruku/claude-orchestrate/issues/76)) ([bb80a58](https://github.com/shrujancheruku/claude-orchestrate/commit/bb80a58b0da35238aeb8de785d3c76d0e04044f7))
* **profile:** proxy $ per merged ticket by model and role (schema 2) ([#49](https://github.com/shrujancheruku/claude-orchestrate/issues/49)) ([5d94193](https://github.com/shrujancheruku/claude-orchestrate/commit/5d94193aab3b3b61c27d85090b7c22a10299dbd2))
* **profile:** review quality floor, worker-model split and depth-2 check ([#71](https://github.com/shrujancheruku/claude-orchestrate/issues/71)) ([67dc824](https://github.com/shrujancheruku/claude-orchestrate/commit/67dc824bf49000b35ff9e4d62bdfb1a3fcd78008))
* **review:** approve-with-minors, post-approval risk guard, reviewer continuation ([#81](https://github.com/shrujancheruku/claude-orchestrate/issues/81)) ([239d1d9](https://github.com/shrujancheruku/claude-orchestrate/commit/239d1d94a1d20689dab38c5e29a86f30ff1403b8))
* **reviewer:** post the review and return a short hand-back ([#48](https://github.com/shrujancheruku/claude-orchestrate/issues/48)) ([0d07b5a](https://github.com/shrujancheruku/claude-orchestrate/commit/0d07b5abe65ffb3486dcfd72414e01a6cf1cf79d))
* **run:** event-driven orchestrator with a small context; remove /loop ([#86](https://github.com/shrujancheruku/claude-orchestrate/issues/86)) ([1ce5f22](https://github.com/shrujancheruku/claude-orchestrate/commit/1ce5f22f7d13540b8c8069d03619688d326490d7))
* **run:** haiku workers for S tickets with one escalation to sonnet ([#88](https://github.com/shrujancheruku/claude-orchestrate/issues/88)) ([b4c1822](https://github.com/shrujancheruku/claude-orchestrate/commit/b4c1822a60bdfb6a07e3aa160f0b94d7aff852c6))
* **run:** rebuild the state file from transcripts ([#69](https://github.com/shrujancheruku/claude-orchestrate/issues/69)) ([48f730a](https://github.com/shrujancheruku/claude-orchestrate/commit/48f730aeed09007e5575455dbafb5643dbd36114))
* **scout:** fixed report shapes and path:line touch maps ([#52](https://github.com/shrujancheruku/claude-orchestrate/issues/52)) ([d2f3845](https://github.com/shrujancheruku/claude-orchestrate/commit/d2f3845a3827100acc65c67d0576eb10ed685d9d))
* **scripts:** add shared plugin-script helpers and CLI test harness ([#47](https://github.com/shrujancheruku/claude-orchestrate/issues/47)) ([8f73496](https://github.com/shrujancheruku/claude-orchestrate/commit/8f73496045a70a12690e394ba84de3a94f254f18))
* **scripts:** add watch-ci Monitor script for CI waits ([#60](https://github.com/shrujancheruku/claude-orchestrate/issues/60)) ([90cdb76](https://github.com/shrujancheruku/claude-orchestrate/commit/90cdb766b972ce7f7f45604ac8660098da7b3c67))
* **setup:** infer shared files and advise on risk paths and autoCompactWindow ([#90](https://github.com/shrujancheruku/claude-orchestrate/issues/90)) ([7822871](https://github.com/shrujancheruku/claude-orchestrate/commit/78228718681c22f966deaf8269c967e9fcc2de0d))
* **verify:** check runner script the worker verifies through ([#62](https://github.com/shrujancheruku/claude-orchestrate/issues/62)) ([cad4bc2](https://github.com/shrujancheruku/claude-orchestrate/commit/cad4bc262ca5077699e0517bbf0232545af42a0c))
* **worker:** drop the Agent tool, add turn rules, retire the mechanic ([#101](https://github.com/shrujancheruku/claude-orchestrate/issues/101)) ([11fab65](https://github.com/shrujancheruku/claude-orchestrate/commit/11fab658bf140c055ff6ad8a282ebc3653744882))
* **worker:** write every PR body with the :pr skill when one is listed ([#93](https://github.com/shrujancheruku/claude-orchestrate/issues/93)) ([ed01143](https://github.com/shrujancheruku/claude-orchestrate/commit/ed011439b9a72cf0dc893127ed1ccc704d983a7f))


### Bug Fixes

* **gate:** only treat UNKNOWN as re-run when nothing else is wrong ([#97](https://github.com/shrujancheruku/claude-orchestrate/issues/97)) ([f05faeb](https://github.com/shrujancheruku/claude-orchestrate/commit/f05faebb23bac39a74522f8d4fca31480abcf2a2))
* **guard:** catch --method=POST and quoted gh subcommands ([#82](https://github.com/shrujancheruku/claude-orchestrate/issues/82)) ([9ca1e0f](https://github.com/shrujancheruku/claude-orchestrate/commit/9ca1e0f5a77a83911b7f7788ca7770fb0602d754))
* **guard:** deny refs/heads/&lt;default&gt; pushes and scout ref and config writes ([#136](https://github.com/shrujancheruku/claude-orchestrate/issues/136)) ([ac44586](https://github.com/shrujancheruku/claude-orchestrate/commit/ac445862ee7c53f0c2816ea1921167b0f2574a16))
* **guard:** fail closed for unknown orchestrate roles ([#120](https://github.com/shrujancheruku/claude-orchestrate/issues/120)) ([7ce0559](https://github.com/shrujancheruku/claude-orchestrate/commit/7ce055923b6948f47b05e9dd5cb97a6b2b01686b))
* **guard:** let reviewers post their review reliably ([#53](https://github.com/shrujancheruku/claude-orchestrate/issues/53)) ([0ef4188](https://github.com/shrujancheruku/claude-orchestrate/commit/0ef4188308e7456da7e85dba4cb855185f75ca67))
* **guard:** let the reviewer run read-only git to read a PR head ([#103](https://github.com/shrujancheruku/claude-orchestrate/issues/103)) ([8ce72f8](https://github.com/shrujancheruku/claude-orchestrate/commit/8ce72f844ebbf151f10b970a3ca679797b1cc6c3))
* **guard:** match gh and git on shell tokens, not command text ([#116](https://github.com/shrujancheruku/claude-orchestrate/issues/116)) ([d274be6](https://github.com/shrujancheruku/claude-orchestrate/commit/d274be668057a8cfa6b3eb274beda592cbd783b6))
* **plugin:** SSH push and fetch under the sandbox, with HTTPS fallback ([#80](https://github.com/shrujancheruku/claude-orchestrate/issues/80)) ([8f7df80](https://github.com/shrujancheruku/claude-orchestrate/commit/8f7df80a4fee0b848980fa601fb215af284c98e9))
* **profile:** count merges from merge events and measure occupancy per pass's slots ([#115](https://github.com/shrujancheruku/claude-orchestrate/issues/115)) ([cd3aa5e](https://github.com/shrujancheruku/claude-orchestrate/commit/cd3aa5ee98685199d10ca117fa974a3e586200ac))
* **profile:** exit 2 instead of 1 when profile.py can't run ([#151](https://github.com/shrujancheruku/claude-orchestrate/issues/151)) ([245a183](https://github.com/shrujancheruku/claude-orchestrate/commit/245a18312e79fbbebf8e06ae0c41a469a47cd163))
* **reviewer:** rely on CI for the suite and always report the posted review's URL ([#106](https://github.com/shrujancheruku/claude-orchestrate/issues/106)) ([5be51e1](https://github.com/shrujancheruku/claude-orchestrate/commit/5be51e158fd7769227f54c9ecf8eb147dc685049))
* **run:** compare touch maps by file path, ignoring :line ([#92](https://github.com/shrujancheruku/claude-orchestrate/issues/92)) ([a52bf0a](https://github.com/shrujancheruku/claude-orchestrate/commit/a52bf0a148500a63652036705d1625b76e35a2bc))
* **run:** write held paths into the brief as plain file paths ([#150](https://github.com/shrujancheruku/claude-orchestrate/issues/150)) ([94e9f82](https://github.com/shrujancheruku/claude-orchestrate/commit/94e9f82ad8c2574f8f7f7ffa0caa62bf5553e799))
* **scout:** treat a PR as up to date only when it is 0 commits behind its base ([#96](https://github.com/shrujancheruku/claude-orchestrate/issues/96)) ([883b3ea](https://github.com/shrujancheruku/claude-orchestrate/commit/883b3ea6b805d0cdaad06dcdff35ad251364f89f))
* **scripts:** report a missing gh or a hung check as 'could not run', not a failed check ([#137](https://github.com/shrujancheruku/claude-orchestrate/issues/137)) ([701a9d8](https://github.com/shrujancheruku/claude-orchestrate/commit/701a9d872d02c9ec5fecd5d2cb9d4e73415e85ce))
* **scripts:** robustness in sandbox-git and guard-corpus, and the sweep's 2.38 requirement ([#143](https://github.com/shrujancheruku/claude-orchestrate/issues/143)) ([84c3b22](https://github.com/shrujancheruku/claude-orchestrate/commit/84c3b22076ea0da91879db237f60df088b966076))
* **scripts:** write events to the repo's own log and skip stray JSONL lines ([#142](https://github.com/shrujancheruku/claude-orchestrate/issues/142)) ([4f9ac91](https://github.com/shrujancheruku/claude-orchestrate/commit/4f9ac9196c6fd2c9da5e45c0fb61046ef7051dbf))
* **worker:** check the default branch for a finished ticket before building ([#125](https://github.com/shrujancheruku/claude-orchestrate/issues/125)) ([7b2df03](https://github.com/shrujancheruku/claude-orchestrate/commit/7b2df033f0f3ec496e6eddc9b445de905aac0719))
* **worker:** commits carry no attribution lines or trailers ([#153](https://github.com/shrujancheruku/claude-orchestrate/issues/153)) ([a1d58bc](https://github.com/shrujancheruku/claude-orchestrate/commit/a1d58bc05ba586d426c437da9ace78ed8d3af44e))
* **worker:** compare held paths by file, ignoring :line ([#114](https://github.com/shrujancheruku/claude-orchestrate/issues/114)) ([0992832](https://github.com/shrujancheruku/claude-orchestrate/commit/0992832ff6306c31f777c1ae09159d76a865e1c4))
* **worker:** finish background commands before reporting ([#117](https://github.com/shrujancheruku/claude-orchestrate/issues/117)) ([5c7f9fd](https://github.com/shrujancheruku/claude-orchestrate/commit/5c7f9fd66774437371c2b2d1dc49427074a6939e))
* **worker:** one rule for conflicts, merge the default branch and never force ([#139](https://github.com/shrujancheruku/claude-orchestrate/issues/139)) ([9421eaf](https://github.com/shrujancheruku/claude-orchestrate/commit/9421eaf4ef27b3182dbfbb04b74735e53ac912d7))
* **worker:** read the ticket with the tracker doc's Read an issue command ([#152](https://github.com/shrujancheruku/claude-orchestrate/issues/152)) ([#154](https://github.com/shrujancheruku/claude-orchestrate/issues/154)) ([7679397](https://github.com/shrujancheruku/claude-orchestrate/commit/7679397fdb21356ac0c13adb826d8e043180ed1f))

## [0.4.1](https://github.com/shrujancheruku/claude-orchestrate/compare/v0.4.0...v0.4.1) (2026-11-04)


### Bug Fixes

* **profile:** count each agent once and join reviewers to their PRs ([#22](https://github.com/shrujancheruku/claude-orchestrate/issues/22)) ([7e9bef3](https://github.com/shrujancheruku/claude-orchestrate/commit/7e9bef3dc3ed7f48dabde93cb07d0d3df6c23f27))
