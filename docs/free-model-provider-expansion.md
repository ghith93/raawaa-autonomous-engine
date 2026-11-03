# Free model provider expansion

## Decision

Use the existing Raawaa provider gateway as the policy and accounting authority. Do not deploy OmniRoute as a required production dependency yet.

OmniRoute release 3.8.53 is an MIT-licensed, standalone Node.js gateway (Node.js >=22.22.2) with a broad provider/free-tier catalog, quota-aware routing, and a dashboard. It is useful as a discovery and implementation reference, but it is not a free inference account or a source of guaranteed capacity. Its own documentation distinguishes recurring quotas, one-time credits, no-auth routes, and terms-risk providers. A fresh instance can have no eligible routes.

Reasons not to embed it in the current runtime:
- It is a full long-running Node.js service, not a Python library or Cloudflare Worker module.
- It introduces a separate service, authentication surface, storage/runtime dependencies, and operational burden.
- Some catalog entries use OAuth/session flows or providers with terms/privacy caveats; no-auth does not mean approved, reliable, or private.
- Its aggregate free-token headline represents independently eligible third-party accounts and is not a token grant.

## Implemented in this change

- Register OpenRouter as an OpenAI-compatible provider.
- Add a strict runtime allowlist for the exact OpenRouter endpoint and the `openrouter/free` model alias only. This prevents the route from being configured to call an arbitrary paid model through this adapter.
- Add an OpenRouter route profile in disabled evaluation state.
- Apply conservative internal request/token caps. Token caps are engine guardrails, not claims about OpenRouter's published token quota.
- Keep commercial eligibility and production use disabled because the dynamic free router can select models with different licenses and data-handling terms.
- Add tests for the permitted free alias and rejection of a paid/unpinned model.

## Activation requirements

The route must remain disabled until all of the following are completed:
1. Operator adds `OPENROUTER_API_KEY` as a GitHub Actions secret; never put it in source control or chat.
2. A specific model and its license, provider terms, data retention, and commercial-use permissions are pinned and reviewed. A dynamic alias alone is not enough for confidential or commercial workloads.
3. Provider account limits and an independently enforceable no-paid-route guarantee are verified.
4. Live inference policy, provider hard-stop evidence, durable D1 quota and monetary ledgers, and the explicit runtime kill switch are reviewed and enabled by the operator.
5. A real request is tested only with a public, non-sensitive prompt and an approved zero-cost route.

## Free-provider policy

- Groq: existing account-confirmed free GPT-OSS routes remain disabled until the live-inference gates are explicitly opened.
- Gemini: adapter support exists; free-tier limits and data policy must be verified for the specific account/model before activation.
- OpenRouter: free alias staged, disabled, and non-commercial pending model-specific review.
- Cloudflare Workers AI: listed for evaluation. Its published free allocation is measured in Neurons (10,000/day), not tokens; add a dedicated meter before routing production tasks.
- Other providers (Mistral, SambaNova, NVIDIA NIM, Cohere, etc.) remain discovery candidates until current account-level quotas, exact endpoints, terms, and commercial permissions are verified.

## Gateway implementation roadmap (planned; not installed)

OmniRoute and LiteLLM are both retained as explicit future evaluation/integration work. Neither is currently installed, running, or a runtime dependency.

### Phase 1 — capability and deployment fit
- Compare current upstream releases, supported providers, free-tier eligibility signals, model pinning, quota visibility, authentication, data handling, license/terms metadata, and maintenance activity.
- Verify whether a genuinely free, persistent, isolated cloud runtime is available. Do not assume Cloudflare Workers can host either standalone service unchanged.
- Define a common acceptance suite: provider allowlisting, zero-cost enforcement, quota hard stops, secret isolation, request logging/redaction, health checks, restart recovery, and latency/failure behavior.

### Phase 2 — isolated proof of concept
- Deploy OmniRoute and LiteLLM separately in disposable, non-production environments; do not chain them together initially.
- Use only operator-approved free provider accounts and public, non-sensitive prompts. No paid fallback, OAuth/session scraping, or unapproved no-auth endpoints.
- Measure actual eligible model routes and quotas rather than relying on catalog or aggregate free-token claims.

### Phase 3 — comparative decision
- Compare each gateway against the engine's existing provider gateway for reliability, operational cost, quota enforcement, security, and measurable task quality.
- Select one only if it provides a verified advantage and can preserve the engine's policy, quota ledger, monetary ledger, and kill switch. Otherwise retain the existing gateway and use the projects as references.

### Phase 4 — controlled integration
- Integrate the selected gateway behind an optional adapter and feature flag, with strict model/provider allowlists and hard zero-cost limits.
- Run automated tests and staging soak tests before any production consideration. Keep production disabled until explicit operator approval and all deployment/security gates pass.

### Current status
- OmniRoute: source reviewed; installation and runtime deployment not started.
- LiteLLM: candidate identified; installation and runtime deployment not started.
- Next action: complete Phase 1 and produce a side-by-side deployment/feature assessment before installing either service.

## References

- OmniRoute: https://github.com/cheaper-inference/OmniRoute
- OmniRoute free-tier guide: https://github.com/cheaper-inference/OmniRoute/blob/release/v3.8.53/docs/getting-started/FREE-TIERS-GUIDE.md
- OpenRouter free models: https://openrouter.ai/collections/free-models
- OpenRouter rate limits: https://openrouter.ai/docs/api-reference/limits
- Cloudflare Workers AI pricing: https://developers.cloudflare.com/workers-ai/platform/pricing/
- Groq rate limits: https://console.groq.com/docs/rate-limits
- Gemini rate limits: https://ai.google.dev/gemini-api/docs/rate-limits
