# Headless worker on a third-party endpoint

Research for #524 (map #514). Question: how does a `claude -p --agent orchestrate:worker` process reach another Anthropic-compatible endpoint with its own key, without touching the orchestrator session's plan login? All facts are from the Claude Code docs, read 2026-11-09. Each fact carries its URL.

## Variables that point the process at another endpoint

| Need | Variable | Source |
| - | - | - |
| Base URL | `ANTHROPIC_BASE_URL` ("Override the API endpoint to route requests through a proxy or gateway") | https://code.claude.com/docs/en/env-vars.md |
| Credential, bearer | `ANTHROPIC_AUTH_TOKEN` (sent as `Authorization: Bearer`) | https://code.claude.com/docs/en/llm-gateway-connect.md |
| Credential, key | `ANTHROPIC_API_KEY` (sent as `x-api-key`) | https://code.claude.com/docs/en/llm-gateway-connect.md |
| Credential, rotating | `apiKeyHelper` setting (sent in both headers) | https://code.claude.com/docs/en/llm-gateway-connect.md |
| Model for the session | `ANTHROPIC_MODEL`, or `--model` (the flag wins) | https://code.claude.com/docs/en/model-config.md |
| `sonnet` alias target | `ANTHROPIC_DEFAULT_SONNET_MODEL` | https://code.claude.com/docs/en/env-vars.md |
| `haiku` alias target (also background tasks) | `ANTHROPIC_DEFAULT_HAIKU_MODEL` | https://code.claude.com/docs/en/env-vars.md |
| `opus` alias target | `ANTHROPIC_DEFAULT_OPUS_MODEL` (a `FABLE` counterpart exists) | https://code.claude.com/docs/en/env-vars.md , https://code.claude.com/docs/en/model-config.md |
| Subagent default model | `CLAUDE_CODE_SUBAGENT_MODEL` (alias or full name; frontmatter and per-call model beat it unless `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`) | https://code.claude.com/docs/en/sub-agents.md |
| Extra headers | `ANTHROPIC_CUSTOM_HEADERS` (`Name: Value` per line) | https://code.claude.com/docs/en/llm-gateway-connect.md |
| Gateway model list in `/model` | `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` (picker only; not needed for `-p`) | https://code.claude.com/docs/en/llm-gateway-connect.md |

Which credential variable: use `ANTHROPIC_AUTH_TOKEN` if the endpoint says bearer/Authorization, `ANTHROPIC_API_KEY` if it says API key/`x-api-key`; a wrong choice gives `401`. Source: https://code.claude.com/docs/en/llm-gateway-connect.md

## Per-process scoping and the plan login

- The docs never use the phrase "per process", but they say shell exports "apply only to that terminal session and programs started from it". So variables set on the command line of one `claude -p` invocation reach only that child. Source: https://code.claude.com/docs/en/llm-gateway-connect.md
- The Agent SDK passes an `env` option to the process it spawns, the documented programmatic form of the same thing. Source: https://code.claude.com/docs/en/llm-gateway-connect.md
- Trap: a settings-file `env` block beats a shell variable ("the settings file value applies in most sessions"). Any `env` block in `~/.claude/settings.json` that sets these names would override the per-process values. `--setting-sources project,local` (already in the #111 line) keeps the user file out. Sources: https://code.claude.com/docs/en/env-vars.md (Precedence), https://code.claude.com/docs/en/cli-reference.md (`--setting-sources`)
- Trap: `--settings '<json>'` can also carry an `env` block for one session, above user, project and local files; it is the other per-session route. Source: https://code.claude.com/docs/en/settings.md
- Plan login interaction: "A gateway credential variable takes precedence over a saved claude.ai login or Console key. Your claude.ai login stays saved and unused while the variable is set; unset the variable and Claude Code goes back to it." With `ANTHROPIC_AUTH_TOKEN` this is immediate; with `ANTHROPIC_API_KEY` interactive mode asks once, but "In non-interactive mode (`-p`), the key is always used when present". Sources: https://code.claude.com/docs/en/llm-gateway-connect.md , https://code.claude.com/docs/en/env-vars.md
- Requests with a gateway credential "carry that credential in place of a developer's claude.ai subscription login, and the subscription's usage limits don't apply". Claude Code "doesn't send" the saved login. Source: https://code.claude.com/docs/en/llm-gateway.md
- Trap: setting only `ANTHROPIC_BASE_URL` without a credential variable does not leave the plan: "a saved claude.ai login remains the active credential", so the plan's limits and billing apply and the login's OAuth header goes to the other host. Always set a credential with the base URL. Source: https://code.claude.com/docs/en/llm-gateway.md
- Startup warns about a conflict when both a credential variable and a saved login exist ("auth may not work as expected"); the docs say the variable is still used for requests. Source: https://code.claude.com/docs/en/llm-gateway-connect.md (troubleshooting table)
- Do not use `--bare` for this worker: bare mode "never reads OAuth credentials or the system keychain" (fine) but "Hooks still don't run in bare mode when you pass them ... in the `hooks/hooks.json` ... of a plugin you load with a flag", and the orchestrate guard is a plugin hook. Source: https://code.claude.com/docs/en/headless.md
- Managed settings with `forceLoginMethod` or `forceLoginOrgUUID` cannot coexist with these credential variables. Source: https://code.claude.com/docs/en/llm-gateway-connect.md

## Model ids

- `--model` accepts "a model alias ... or a model's full name". Claude Code "doesn't check it up front, and a mistyped value produces There's an issue with the selected model on the first request". So an arbitrary id is accepted by the client; the endpoint decides. Sources: https://code.claude.com/docs/en/cli-reference.md , https://code.claude.com/docs/en/model-config.md
- Agent frontmatter `model` accepts `sonnet`, `opus`, `haiku`, `fable`, a full model ID, or `inherit`; the full ID "accepts the same values as the `--model` flag". Source: https://code.claude.com/docs/en/sub-agents.md
- The worker uses `model: sonnet`, so the least invasive route is to leave frontmatter alone and set `ANTHROPIC_DEFAULT_SONNET_MODEL=<endpoint model id>`; the alias then resolves to it. `--model <id>` for the session also wins over frontmatter for the main thread? The docs state `--model` overrides the `model` setting and `ANTHROPIC_MODEL`; they do not say it overrides an agent's frontmatter when the agent is the main thread. Unresolved, so the spike should check `system/init.model`. Sources: https://code.claude.com/docs/en/env-vars.md , https://code.claude.com/docs/en/cli-reference.md
- Behind an `ANTHROPIC_BASE_URL` gateway, the `ANTHROPIC_DEFAULT_*_MODEL_SUPPORTED_CAPABILITIES` variables "have no effect" (they work only with the `CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY/MANTLE` configurations). For an id Claude Code does not recognise, use a `modelOverrides` entry. Source: https://code.claude.com/docs/en/llm-gateway-protocol.md

## Support status of third-party gateways

- "Any gateway that exposes a supported API format works" (Anthropic Messages at `/v1/messages`, Bedrock InvokeModel, Vertex rawPredict). Source: https://code.claude.com/docs/en/llm-gateway.md , https://code.claude.com/docs/en/llm-gateway-protocol.md
- "Anthropic doesn't endorse, maintain, or audit third-party gateway products, and doesn't support routing Claude Code to non-Claude models through any gateway." Source: https://code.claude.com/docs/en/llm-gateway.md
- "A gateway that doesn't forward [new capabilities] breaks the corresponding features." Source: https://code.claude.com/docs/en/llm-gateway.md
- Unrecognised model ids (gateway aliases) are treated as current Claude models: Claude Code sends adaptive `thinking`, `context_management`, `output_config`/effort and mid-conversation `role: "system"` messages, and an endpoint that rejects them returns `400`. Source: https://code.claude.com/docs/en/llm-gateway-protocol.md

## What stops working or degrades off Anthropic

- Prompt caching: needs `cache_control` forwarded unchanged. Otherwise "no error: the conversation bills as uncached input on every turn". Off switch: `DISABLE_PROMPT_CACHING=1`. Sources: https://code.claude.com/docs/en/llm-gateway-protocol.md , https://code.claude.com/docs/en/env-vars.md
- 1M context: assumed 200K for an unrecognised id (or 1M if the id carries `[1m]`); extended context is a beta header that is "silently unavailable when the header is stripped". If the endpoint rejects over-200K requests, set `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000`; `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` holds default-1M models to 200K. Sources: https://code.claude.com/docs/en/llm-gateway-protocol.md , https://code.claude.com/docs/en/model-config.md
- WebSearch: "runs a query against Anthropic's web search backend ... The search backend is not configurable"; it is listed as available on the Claude API, Claude Platform on AWS and Microsoft Foundry, partly on Vertex, not on Bedrock. The docs do not say what it does behind an arbitrary `ANTHROPIC_BASE_URL`; treat as unavailable unless the endpoint implements the server-side tool. Source: https://code.claude.com/docs/en/tools-reference.md
- WebFetch: a client-side fetch, but its domain safety check calls `api.anthropic.com` directly regardless of the base URL; `skipWebFetchPreflight: true` in settings turns that off. Also runs its extraction prompt as a model call. Sources: https://code.claude.com/docs/en/tools-reference.md , https://code.claude.com/docs/en/llm-gateway-connect.md
- Beta fields: `400` on `context_management`, `output_config`, `thinking: adaptive`, `strict`/`defer_loading` tool fields when the upstream rejects them. `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` strips most; `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` works only for Opus 4.6 and Sonnet 4.6, and the capability variables do not apply behind a base URL. Claude Code retries on its own without `context_management`, `effort`, the advisor tool, system messages and tool search (versions noted on the page). MCP tool search is switched off by the experimental-betas variable. Sources: https://code.claude.com/docs/en/llm-gateway-protocol.md , https://code.claude.com/docs/en/llm-gateway-connect.md
- Token counting falls back to a character estimate unless `/v1/messages/count_tokens` exists. Source: https://code.claude.com/docs/en/llm-gateway-protocol.md
- Fast mode, Remote Control and voice dictation: unavailable or checked against `api.anthropic.com` directly. Irrelevant to a headless worker. Source: https://code.claude.com/docs/en/llm-gateway-connect.md
- Background traffic (version check, telemetry) still goes to Anthropic; `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` turns it off (also disables auto-update). Source: https://code.claude.com/docs/en/llm-gateway-connect.md
- Cost figures in `--output-format json|stream-json` (`total_cost_usd`) are "client-side estimates", so `--max-budget-usd` caps against Anthropic list prices, not the endpoint's. Source: https://code.claude.com/docs/en/headless.md

## Not covered by the docs (leave to the spike)

- Whether `--model` overrides an agent's `model:` frontmatter when `--agent` runs it as the main thread.
- WebSearch behind an arbitrary `ANTHROPIC_BASE_URL`.
- Whether the worker's tool-use loop works on a non-Claude model (explicitly unsupported by Anthropic).

## Candidate command line for the spike

The #111 line plus endpoint variables. Run from the temp repo; the variables sit on the command so they reach this child only.

```sh
PATH="$FAKE_BIN:$PATH" \
CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
ENABLE_CLAUDEAI_MCP_SERVERS=false \
ANTHROPIC_BASE_URL="$ENDPOINT_URL" \
ANTHROPIC_AUTH_TOKEN="$ENDPOINT_KEY" \
ANTHROPIC_DEFAULT_SONNET_MODEL="$ENDPOINT_MODEL" \
ANTHROPIC_DEFAULT_HAIKU_MODEL="$ENDPOINT_MODEL" \
CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1 \
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
claude -p --agent orchestrate:<role> --plugin-dir <plugin repo> \
  --setting-sources project,local --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  --allowedTools Bash \
  --output-format stream-json --verbose --max-turns <n> --max-budget-usd <usd> \
  "<brief>"
```

Swap `ANTHROPIC_AUTH_TOKEN` for `ANTHROPIC_API_KEY` if the endpoint wants `x-api-key`. Assert in `system/init` that `model` is `$ENDPOINT_MODEL`, and in `/status`-equivalent output or the endpoint's logs that requests hit `$ENDPOINT_URL`. Confirm the orchestrator's own login is untouched by running `claude auth status` in the parent shell afterwards.
