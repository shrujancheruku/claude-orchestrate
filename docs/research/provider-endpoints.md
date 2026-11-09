# Provider endpoints, prices and terms

Research for ticket #523 (map #514). Fetched 2026-11-09 from each provider's own documentation. A source marked *gateway* is not the model maker's own page.

**The named models are mostly superseded.** The ticket names DeepSeek V3.2, Qwen3-Coder, Kimi K2 and GLM-4.6. On 2026-11-09 the makers document DeepSeek V4.x, Qwen3.8, Kimi K3.x (K2 retires end of December 2026) and GLM-5.x. The sheet prices the current model per provider and, where the old name is still listed, that too.

## Cost model

Per merged ticket, about 2M tokens, assumed split: 1.7M cache reads, 0.2M uncached input (misses and cache writes), 0.1M output. The split is an assumption (no provider documents it); output price dominates the sensitivity. Ceiling: $0.45 metered per ticket.

`$ per ticket = 1.7 x cache-read + 0.2 x input + 0.1 x output` (prices per million tokens).

## Comparison sheet

| Provider / model | Anthropic Messages endpoint | Model name | Input / cache read / output ($/M) | Caching | Tool use | Context | Data terms | $ / ticket |
|---|---|---|---|---|---|---|---|---|
| DeepSeek direct, `deepseek-flash` (V4.1 Flash) | Yes. `https://api.deepseek.com/anthropic` [1] | `claude-sonnet*`/`claude-haiku*` map to `deepseek-flash`, `claude-opus*` to `deepseek-v4-pro`; other `claude*` names go to flash; non-`claude` names 400 [1] | Peak 0.30 / 0.006 / 1.20; off-peak half [2] | Automatic disk prefix cache; `cache_control` ignored; best effort, cleared in hours to days [3] | Client tools yes; `tool_choice` `auto`/`none` only (`any` becomes `auto`); thinking mode needs full `content` with `thinking` blocks passed back [1] | 1M, max output 384K [2] | China-hosted per older policy; no API-specific no-training promise found; see notes [4][5] | **0.19 peak, 0.10 off-peak** |
| DeepSeek direct, `deepseek-v4-pro` | Same as above | via `claude-opus*` | Peak 1.32 / 0.044 / 3.96; off-peak half [2] | Same | Same | 1M [2] | Same | 0.74 peak, 0.37 off-peak |
| Alibaba Model Studio, `qwen3-coder-flash` (Qwen3-Coder) | Yes. `https://{WorkspaceId}.<region>.maas.aliyuncs.com/apps/anthropic` (Singapore, Beijing, Frankfurt, Virginia, Tokyo) [6] | Set `model` directly; no mapping documented [6] | Singapore, by input size: 0-32K 0.30/1.50; 32-128K 0.50/2.50; 128-256K 0.80/4.00; 256K-1M 1.60/9.60. Cache read 10% explicit or 20% implicit ("typically") [7][8] | Explicit (`cache_control`, 1,024 token minimum, 5 min, writes 125%) or implicit (automatic, not guaranteed) [6][8] | `tools` and `tool_choice` supported [6] | Tiers to 1M [7] | Not used to train; default retention not found [9] | 0.44 at the 32-128K tier, 0.70 at 128-256K |
| Alibaba Model Studio, `qwen3.8-flash` | Same endpoint | Same | 0.15 / n.l. / 0.47 [7] | Supported; hit rate not listed ("differs") [8] | Same | 1M tier [7] | Same | about 0.10 if hits cost 10% (assumed) |
| Moonshot direct, `kimi-k3` / `kimi-k3.1-pro` | Yes. `https://api.moonshot.ai/anthropic` (`POST /anthropic/v1/messages`) [10][11] | Name the model directly; Claude Code guide sets `ANTHROPIC_DEFAULT_*_MODEL=kimi-k3[1m]`, Haiku slot `kimi-k2.7-code` [10] | `kimi-k3`: 3.00 / 0.30 / n.l. (output price table did not render) [12]; K3.1 Pro via OpenRouter 2.00 / 0.20 / 10.00 [13] | `cache_control` honoured at top level only (markers inside `messages` ignored), TTL 5m or 1h; 5m write 1x, 1h write 2x, hit 0.1x [11][12] | `tools`, `tool_choice` `auto`/`any`/`none`; `input_schema` must be object [11] | 1,048,576 (K3), 262,144 (K2) [14] | Contradictory: FAQ says no training; terms reserve improvement use; ZDR for enterprise; Singapore [15] | K3 about 2.6 (gateway output price 15); K3.1 Pro about 1.7 |
| Moonshot direct, `kimi-k2.7-code` / `kimi-k2.6` (K2 family) | Same | Same | K2.7 Code 0.95 / 0.19 / 4.00 (*gateway* [16]); OpenRouter 0.60 / 0.16 / 3.50 [13] | Same | Same; K2.7 Code thinking cannot be disabled [10] | 262,144 [14] | Same | 0.91 (gateway), 0.77 (OpenRouter); K2 retires Dec 2026 [14] |
| Z.ai direct, `glm-4.6` / `glm-4.7` | Yes. `https://api.z.ai/api/anthropic` [17] | Claude Code defaults Opus and Sonnet slots to GLM-4.7; Haiku mapping not documented; manual mapping "not recommended" [17] | GLM-4.6 and 4.7: 0.60 / 0.11 / 2.20; GLM-5.3: 1.40 / 0.26 / 4.40; GLM-5.3-Flash: 0.15 / 0.03 / 0.50 [18] | "Cached input" priced; caching not described on the GLM-4.6 page [18][19] | "supports tool use"; no limits documented [19] | GLM-4.6: 200K in, 128K out [19]; pricing page gives none [18] | DPA: content not stored; training not addressed; Singapore [20] | GLM-4.6/4.7 **0.53**; GLM-5.3 1.16; **GLM-5.3-Flash 0.13** |
| OpenRouter, any model | Yes. `https://openrouter.ai/api`, key in `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` empty [21] | Model slug through `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL`; the guide shows only `~anthropic/` slugs [21] | Per model: `deepseek/deepseek-v3.2` 0.26 / 0.13 / 0.38; `z-ai/glm-4.6` 0.50 / 0.10 / 2.00; `qwen/qwen3-coder` 0.30 / 0.10 / 1.00; `moonshotai/kimi-k2-thinking` 0.60 / 0.15 / 2.50 [13] | Automatic for DeepSeek (hit 0.1x), Moonshot (0.25x), Z.AI (about 0.2x); Qwen needs `cache_control` (hit 0.1x, write 1.25x) [22] | "passes through ... native tool use"; "may not work correctly with other providers" [21] | Per model, e.g. 163,840 to 1,048,576 [13] | No prompt storage unless opted in; training off by default; upstream provider terms still apply [23] | v3.2 0.31; GLM-4.6 0.47; qwen3-coder 0.33; kimi-k2-thinking 0.63 |
| OpenCode Zen (gateway) | Only for Claude, Qwen3.8 Flash, Qwen3.6 Plus, Qwen3.5 Plus: `https://opencode.ai/inference/anthropic/v1/messages`. DeepSeek, GLM, Kimi, MiniMax are chat-completions only [24] | Model ID in request, e.g. `qwen3.8-flash` [24] | Qwen3.8 Flash 0.15 / 0.016 / 0.47; Qwen3.6 Plus 0.50 / 0.05 / 3.00 [24] | Cached read priced [24] | Not documented on the page | Not on the page | Zero retention, no training at providers, except OpenAI and Anthropic (30 days); US and EU hosting [24] | Qwen3.8 Flash 0.10; Qwen3.6 Plus 0.49 |

n.l. = not listed on the fetched page.

## Sources

1. https://api-docs.deepseek.com/guides/anthropic_api
2. https://api-docs.deepseek.com/quick_start/pricing
3. https://api-docs.deepseek.com/guides/kv_cache
4. https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html (via search snippet; direct fetch failed)
5. https://cdn.deepseek.com/policies/en-US/model-algorithm-disclosure.html (via search snippet)
6. https://www.alibabacloud.com/help/en/model-studio/anthropic-api-messages
7. https://www.alibabacloud.com/help/en/model-studio/model-pricing (first 100,000 characters only)
8. https://www.alibabacloud.com/help/en/model-studio/context-cache
9. https://www.alibabacloud.com/help/en/model-studio/privacy-notice and https://www.alibabacloud.com/help/en/legal/latest/alibaba-cloud-international-website-product-terms-of-service-v-3-8-0 (via search snippet)
10. https://platform.kimi.ai/docs/guide/claude-code-kimi.md (platform.moonshot.ai 301-redirects to platform.kimi.ai)
11. https://platform.kimi.ai/docs/api/messages.md
12. https://platform.kimi.ai/docs/guide/context-caching.md
13. https://openrouter.ai/api/v1/models (first-party JSON, fetched 2026-11-09; per-token prices times 1e6)
14. https://platform.kimi.ai/docs/pricing/chat.md (tables are rendered by script and came back empty)
15. https://platform.kimi.ai/docs/agreement/userprivacy, https://platform.kimi.ai/docs/agreement/modeluse, https://platform.kimi.ai/docs/guide/zero-data-retention, https://www.kimi.ai/help/kimi-api/api-data-security (via search snippet)
16. https://opencode.ai/docs/zen/ (gateway, not Moonshot)
17. https://docs.z.ai/scenario-example/develop-tools/claude
18. https://docs.z.ai/guides/overview/pricing
19. https://docs.z.ai/guides/llm/glm-4.6
20. https://docs.z.ai/legal-agreement/privacy-policy (the page is the DPA)
21. https://openrouter.ai/docs/guides/guides/claude-code-integration
22. https://openrouter.ai/docs/guides/best-practices/prompt-caching
23. https://openrouter.ai/docs/guides/privacy/data-collection
24. https://opencode.ai/docs/zen/

## Notes per provider

**DeepSeek.** The only maker whose Anthropic endpoint maps Claude model names for you, so `--model sonnet` lands on `deepseek-flash` with no extra setting [1]. Documented gaps that touch Claude Code: `cache_control`, `mcp_servers`, `document` and `search_result` blocks are ignored or unsupported; `tool_choice` `tool` returns 400 in thinking mode; a custom-typed tool returns 422; images are replaced with `[Unsupported Image]` on `deepseek-v4-pro` [1]. Thinking is on by default and every tool turn must return the earlier `thinking` blocks [1][2]. Cache hits are best effort, so the cache-read share in the cost model is the main risk; the usage block reports `prompt_cache_hit_tokens` [3]. Peak is 01:00-04:00 and 06:00-10:00 UTC on weekdays; all other times cost half [2]. Marketing, not behaviour: none found on these pages. Data terms: the policy snippets say inputs are stored, a small part of optimisation data "may" come from user input, and an older platform policy puts servers in the People's Republic of China [4][5]. I could not fetch the Open Platform policy, so "no training on API data" is not established.

**Alibaba Model Studio (Qwen).** `qwen3-coder-plus` and the 480B model are not on the pricing page; `qwen3-coder-flash` is, and the Anthropic page lists it as supported [6][7]. Base URL carries a workspace ID and region, so the key alone is not enough: the config must also name the workspace [6]. A base URL ending `/v1/` breaks model discovery with a 404; add the model by hand [6]. `max_tokens` is required, and temperature range differs from Anthropic's [6]. Per-tier pricing means a long Claude Code context lands in the dearer 32K+ tiers, which is why the cost is a range [7]. Cache rates are stated as "typically" 10% and 20%, and qwen3.8-flash is flagged as an exception without a number [8]. Alibaba says it "will never use your data for model training" (privacy notice) and the International Product Terms promise no model improvement without consent; the Dedicated "ZDR" line is from a marketing page [9].

**Moonshot (Kimi).** Native Anthropic endpoint with the fullest documented compatibility of the four (tools, `tool_choice` `any`, `cache_control` with 5m and 1h TTL, `output_config`) [11]. Model names are passed straight through [10]. Pricing tables on the Moonshot page did not render in any fetch; the only maker-stated price is `kimi-k3` input 3.00, cache write 3.00 (5m) or 6.00 (1h), cache hit 0.30 [12]; output 15.00 is from the OpenCode gateway [16]. K2 models are closed to new users from 2026-10-31 and retire by end of December 2026 [14], so K2 is not a viable spike target. K3 busts the ceiling several times over. Effort defaults to `max` and changing it breaks prefix-cache hits [11]. Data terms contradict each other across the FAQ, the terms and the privacy policy [15]; the per-repo switch should treat Kimi as "may train" until the enterprise ZDR is in place.

**Z.ai (GLM).** Dedicated Claude Code page, which says "We have verified compatibility with Claude Code 2.0.14 and other versions" [17]. That is the only maker claim of tested compatibility, and it is a statement, not a test result. The page maps only the Opus and Sonnet slots (to GLM-4.7) and recommends not hand-editing the mapping, which clashes with the plugin's per-process environment [17]. Whether GLM-5.3-Flash is served on `/api/anthropic` is not documented on the pages read. Pricing page gives no context window; GLM-4.6 is 200K in and 128K out per its model page [18][19]. The GLM-4.6 page does not document caching, yet the pricing page lists a cached-input price [18][19]. The DPA says content is not stored and is processed in Singapore, and is silent on training [20].

**OpenRouter.** Documented Anthropic path for Claude Code at `https://openrouter.ai/api` [21]. The guide's own warning is the main finding: "Claude Code is optimized for Anthropic models and may not work correctly with other providers", and compatibility is guaranteed only with Anthropic 1P [21]. Non-Anthropic slugs are not shown in the guide, so using `z-ai/glm-4.6` as `ANTHROPIC_DEFAULT_SONNET_MODEL` is untested documentation-wise. Caching is automatic for DeepSeek, Moonshot and Z.AI, explicit for Qwen [22]. Prices are a per-model listing and the same model appears at several prices (the `/models` JSON has several rows where the cache-read price exceeds or equals the input price, e.g. `z-ai/glm-5.2`, so I priced only models whose rows are coherent) [13]. OpenRouter stores no prompts unless opted in and trains on none by default; the upstream provider's own terms still apply [23]. No OpenRouter fee on credits was read, so it is not in the figures.

**OpenCode Zen.** "OpenCode" here means the Zen gateway [24]. Only Claude and three Qwen models are served in Anthropic Messages format, so DeepSeek, GLM and Kimi cannot be reached from Claude Code through Zen [24]. Gateway prices are the maker's price plus Zen's own; the page states zero retention and no training at its providers, which is a gateway claim and cannot be checked from this page [24]. Not a cost saving over the makers: Qwen3.8 Flash is priced like the maker.

## Which two to spike

Judged on metered cost against $0.45 per ticket first, documented tool-use behaviour second. No provider publishes a tool-use reliability measure; "reliability" below means documented compatibility and documented limits only. A spike has to measure the real figures.

1. **DeepSeek direct, `deepseek-flash`.** About $0.19 peak and $0.10 off-peak, well under the ceiling even if cache hits come in at half the assumed share (uncached input costs only $0.30/M). It is the only endpoint that maps `claude-sonnet*` itself. Watch for: thinking-block passback on every tool turn, `tool_choice` `any` downgraded to `auto`, and best-effort caching [1][3]. `deepseek-v4-pro` is over the ceiling at peak ($0.74) and borderline off-peak ($0.37), so it is a second step only.
2. **Z.ai direct, GLM.** GLM-4.6/4.7 land at about $0.53, over the ceiling; GLM-5.3-Flash at about $0.13 is under it, and both use one base URL. It is the only maker with a Claude Code page and a stated version check [17]. Spike GLM-4.7 first for tool-use quality, then GLM-5.3-Flash for cost, confirming the Flash model is served on the Anthropic path.

Runner-up: Alibaba Model Studio `qwen3-coder-flash` ($0.44-$0.70, depends on context tier) or `qwen3.8-flash` (about $0.10, cache rate assumed). Better documented caching than the Chinese-hosted peers but a workspace-ID base URL. Rejected: Kimi K3 ($1.7-$2.6, 4-6x the ceiling) and K2 (retiring); OpenRouter (adds a hop, Claude Code warns about non-Anthropic providers, same models at similar or higher prices); Zen (no Anthropic path for the candidates).

## Open questions for the spike

- Actual cache-read share and uncached input per ticket on each provider, replacing the assumed 1.7M / 0.2M / 0.1M.
- Whether DeepSeek's `claude-sonnet*` mapping applies to a `claude -p --agent` worker's `--model` value, or needs `ANTHROPIC_DEFAULT_SONNET_MODEL`.
- DeepSeek and Z.ai training terms for API traffic (neither is established from the pages read), which sets the wording of the per-repo switch.
