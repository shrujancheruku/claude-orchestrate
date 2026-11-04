# How the plan usage meters weight models and token types

Research for [#11](https://github.com/shrujancheruku/claude-orchestrate/issues/11) (map [#9](https://github.com/shrujancheruku/claude-orchestrate/issues/9)). Sources read 2026-11-04.

## Answer

- **Anthropic does not publish how the five-hour and seven-day meters weight models or token types.** The help center says only that usage depends on "which Claude model you're chatting with", conversation length, features and effort. It gives no multipliers and says nothing either way about caching.
- **Cache reads do count against plan limits.** The Claude Code docs say so directly: in a long session, "Claude Code re-reads that history at the [cached token rate], so a one-line question in a session that has been open all day still draws usage for the whole conversation". The link points to the API pricing page.
- **Best available proxy: API list prices.** First-party docs repeatedly link plan usage to the API's cached-token rate, and usage past the plan limit is billed "at standard API rates". The local data below fits API-price weighting better than any other weighting tested. Use the ratios in [API price ratios](#api-price-ratios-the-proxy) as the weights, and treat them as a proxy, not a published fact.

## What the primary sources say

### Plan limits (help center)

[How do usage and length limits work?](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work)

- "Your usage is affected by several factors, including the length and complexity of your conversations, the features you use, which Claude model you're chatting with, and the effort level you've selected."
- "your usage of all different Claude product surfaces (claude.ai, Claude Code, Claude Desktop) counts towards the same usage limit."
- "Weekly limits apply across all models, and some plans also have a separate weekly limit for Fable."
- "Longer conversations that trigger automatic context management consume more of your usage limit." "Higher effort uses more tokens."
- No per-model multipliers, no token-type weights, and nothing on caching.

[Use Claude Code with your Pro, Max, or Ultra plan](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan) and [What is the Max plan?](https://support.claude.com/en/articles/11049741-what-is-the-max-plan)

- Limits are shared between Claude and Claude Code. Max gets "five times the Pro plan's usage every five hours and every week". Anthropic "may limit your usage in other ways, such as weekly and monthly caps or model and feature usage".
- Neither article gives any weighting.

[Manage usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans)

- "Usage credits are billed at standard API rates" and "apply to all tokens processed". Past the plan limit, the exchange rate is the API price list. This is the only published link between plan usage and a per-token rate.

### Claude Code docs

[Manage costs effectively](https://code.claude.com/docs/en/costs)

- "Why usage climbs in a long session": "Claude Code sends your full conversation with every request … With prompt caching, Claude Code re-reads that history at the cached token rate, so a one-line question in a session that has been open all day still draws usage for the whole conversation." This is the clearest first-party statement that cache reads count toward plan limits, at a reduced rate.
- "Subagents and workflows: every subagent … sends its own requests on top of the main conversation's." "The subagent's own requests still draw on your usage. To spend less on them, choose a smaller model for a subagent."
- "Sonnet handles most coding tasks well and costs less than Opus … For simple subagent tasks, specify `model: haiku`." This is written as cost advice, not as a plan-meter weight.
- Model-specific limits exist as messages: after "You've hit your Opus limit" or "You've hit your Sonnet limit", switching models keeps you working. The session and weekly limits are "shared across all models".
- The `/usage` plan breakdown attributes usage to subagents, skills and MCP servers as percentages. The docs don't say how it weights tokens.
- "Thinking tokens are billed as output tokens." Thinking can't be turned off on the 5.5 models.

[How Claude Code uses prompt caching](https://code.claude.com/docs/en/prompt-caching)

- Caching "bills the re-read at the cached token rate". `cache_read_input_tokens` is "billed at the model's cached token rate, below the standard input rate".
- **TTL on a subscription within plan usage:** the main conversation requests a **one-hour** TTL. Subagents, compaction and other requests get **five minutes**. Usage credits, an API key or a cloud provider get five minutes everywhere. The one-hour TTL "charges a higher rate for cache writes". The Issho data matches this: every Opus orchestrator cache write was 1h, and every Sonnet/Haiku subagent write was 5m.
- Each model has its own cache, so a model switch re-reads the whole context uncached. A subagent's first request doesn't read the parent's cache.

[Status line](https://code.claude.com/docs/en/statusline)

- `rate_limits.five_hour.used_percentage` / `seven_day.used_percentage` is the "Percentage of the 5-hour or 7-day rate limit consumed, from 0 to 100". It appears only for Pro/Max/Ultra subscribers. The docs describe no formula. In practice the values arrive as integers.

### Launch post

[Introducing Claude Opus 5.5](https://www.anthropic.com/claude-opus-5-5)

- "Cache reads (which make up the majority of agentic and coding work costs) are $0.20 per million tokens". Alongside the price drop, "we're increasing five-hour usage limits on Pro, Max, Team, and seat-based Enterprise plans". The post links the price cut and the plan-limit raise, but doesn't state an exchange rate between them.

## API price ratios (the proxy)

From [platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing), $/MTok, global endpoint, standard speed:

| Model | Input | 5m cache write | 1h cache write | Cache read | Output |
| :- | -: | -: | -: | -: | -: |
| Opus 5.5 | 4 | 5 | 8 | 0.20 | 20 |
| Sonnet 5.5 | 2 | 2.50 | 4 | 0.10 | 10 |
| Haiku 5.5 (prompt ≤100K) | 0.10 | 0.125 | 0.20 | 0.01 | 0.50 |
| Haiku 5.5 (prompt >100K) | 0.50 | 0.625 | 1 | 0.05 | 2.50 |

- **Token-type multipliers** (relative to the model's input price): 5m write 1.25x, 1h write 2x, output 5x. Cache read is **0.05x on Opus 5.5 and Sonnet 5.5** ("Cache hits and refreshes on Claude Opus 5.5 and Claude Sonnet 5.5 are priced at 0.05x the base input price") and the standard 0.1x on Haiku 5.5. The page's generic prose still says "10%", but the 5.5 footnote overrides it.
- **Model ratios:** Opus = 2x Sonnet on every token type. Haiku 5.5 = 1/20 of Sonnet when the prompt is ≤100K, and 1/4 of Sonnet when it's over. The tier is decided per request and counts cache reads and writes toward the prompt length.
- **Other modifiers:** fast mode on Opus 5.5 costs $8/$40 (2x). US-only `inference_geo` costs 1.1x. Neither applied in the Issho run (all requests were `speed: standard`).

**The map's snapshot in proxy dollars:** 161M Opus cache reads = $32. 240M Sonnet cache reads = $24. 1.5M output = $15 on Sonnet, or $30 on Opus. Cache reads stop dominating once weighted: in the Issho window below, cache reads were 48% of Opus spend and 43% of Sonnet spend. Cache writes were 29% and 27%, output 22% and 30%. Uncached input was about 0%.

**Haiku caveat:** the cheap rate holds only for prompts ≤100K. In the Issho data, Haiku requests had a median prompt of 19K and never went over 100K. Sonnet requests had a median of 80K and a p90 of 162K, and 35% of them were over 100K. A Haiku worker carrying Sonnet-sized contexts would pay the 5x tier on about a third of its requests, which is still 1/4 of Sonnet.

## Local evidence: does meter movement fit API-price weighting?

**Data**

- Meter: `~/.claude/orchestrate/usage.jsonl` (written by `scripts/usage-tap.py`). 251 rows from 2026-11-03 16:43Z to 11-04 05:09Z, covering three five-hour windows and one seven-day window (seven-day went from 10% to 24%).
- Tokens: every assistant message with `usage` in `~/.claude/projects/**/*.jsonl` in that span, deduplicated by message id and request id. That gives 3,897 requests: Issho (Opus orchestrator, Sonnet workers and reviewers, Haiku scouts), plus a concurrent `look-together` Opus session and this repo.
- I used raw transcripts rather than the saved profile `2026-11-03-44e00c3b.json`. The profile double-counts agents (61 entries, 41 unique) and covers only 17:21–19:05.

**Method**

- Smooth out meter flicker by keeping only the first time each integer level is reached in each window.
- Fit `meter = a + k · cumulative_weighted_tokens` under each weighting.
- A correct weighting should fit well, and its dollars-per-point should stay stable across windows with different model mixes.

| Weighting | R² (window 1 / 2 / 3, 7-day) | Units per meter point (window 1 / 2 / 3) | Spread |
| :- | :- | :- | :- |
| **API list prices** | 0.990 / 0.999 / 0.976, 0.989 | $1.32 / $1.43 / $1.50 | ±6% |
| API, cache read 0.1x | 0.989 / 0.999 / 0.971, 0.988 | $1.92 / $2.22 / $1.97 | ±8% |
| API, all models at Opus price | 0.980 / 0.999 / 0.984, 0.984 | $1.88 / $2.13 / $1.87 | ±7% |
| API, cache reads free | 0.989 / 0.999 / 0.980, 0.991 | $0.72 / $0.65 / $1.04 | ±25% |
| Raw tokens, equal weight | 0.980 / 0.998 / 0.964, 0.979 | 4.2M / 5.6M / 3.1M | ±29% |
| Tokens excluding cache reads | 0.977 / 0.999 / 0.985, 0.984 | 114K / 110K / 155K | ±19% |
| Output tokens only | 0.976 / 0.998 / 0.958, 0.983 | 27K / 29K / 22K | ±14% |

In those windows Opus took 63%, 51% and 81% of the proxy dollars; Sonnet took the rest, and Haiku about 0%.

**Two-regressor fits** (no intercept, meter points per segment of 3 or 5 levels, n = 37 or 23):

- Sonnet $ vs Opus $: about 0.72 points per $ each, a ratio of **1.0 ± ~0.25**. That's consistent with Opus = 2x Sonnet per token, as on the API.
- Cache-read $ vs other $: a ratio of **about 0.7 ± 0.4**. The cache-read coefficient is about 2σ above zero, so cache reads are not free on the meter. The data can't tell 0.05x from 0.1x.

**Implied plan size (this account, this week, before any later limit changes):** one five-hour window ≈ $130–150 of API-list usage, and the seven-day meter ≈ $10 per point (≈ $1,000 per week).

**Why this inference is weak**

- **R² can't discriminate.** Every weighting fits at R² above 0.96, because all usage grows together over time. Only the stability across windows and the two-regressor split carry signal, and both are noisy.
- **Little variation in the mix.** There were only three five-hour windows, and Opus's share ranged from 51% to 81%. Haiku was too small (<0.1% of proxy dollars) to test at all.
- **Coarse, lagged meter readings.** The meter is an integer percentage, and the tap logs it only when the status line refreshes. Segments aren't independent, and the ± values above are optimistic.
- **Possible unseen usage.** Any claude.ai or other-device usage in the window counts against the meter but isn't in local transcripts. None is known, but I can't rule it out.
- **Unverified transcript counts.** I assumed the transcripts' `usage` counts (especially `output_tokens` on streamed messages) are complete.

**Conclusion:** the data is consistent with API-price weighting, including cache reads at a reduced but non-zero rate and Opus at 2x Sonnet. It rules out raw-token counting and cache-reads-free counting fairly convincingly. It cannot confirm the exact multipliers.

## For pricing delegation trade-offs (#9)

- **Weight by API list price.** Use plan cost per merged ticket = Σ tokens × the API rate for that model and token type, with the Haiku tier decided per request. Flag it as a proxy everywhere it's reported.
- **What drives orchestrator cost.** An Opus orchestrator turn costs about `context × $0.20/MTok` in cache reads, plus 1h-TTL writes at $8/MTok for each new chunk of context. The same context on Sonnet costs half as much. On Haiku it costs 1/20 while under 100K, and 1/4 above.
- **Fresh subagents pay to warm their own cache.** A model switch or a fresh subagent pays cache writes for its whole prefix, because there's no shared cache across models or prefixes. Subagents get the 5m TTL, so a subagent idle for more than 5 minutes (CI waits, for example) re-writes its context on the next turn.
- **The profiler** should store tokens split by type, model and cache TTL (`ephemeral_1h` vs `ephemeral_5m`), plus the prompt length per Haiku request. That is the minimum needed to apply these weights.
