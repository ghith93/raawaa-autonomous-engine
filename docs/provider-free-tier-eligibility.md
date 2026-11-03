# Provider free-tier eligibility review

**Reviewed:** 2026-11-03 17:00 UTC  
**Purpose:** Identify candidate inference routes for a zero-spend, business-use agent system. This is a public-documentation review, not an account verification. No provider profile or credential is enabled by this document.

## Decision

Keep live inference disabled. A provider is eligible for the engine only after its exact model ID, current account quota, endpoint, price, commercial-use/model terms, data handling, and verification date are recorded in a provider profile. Publicly advertised free access does not prove that this account has access or that every model on a gateway is free.

## Candidate comparison

| Provider | Public free access | Quota / availability | Data and commercial-use considerations | Current decision |
|---|---|---|---|---|
| Gemini Developer API | Free tier offers free input/output tokens for selected models. | Limits vary by model and project; RPM, input TPM, and RPD are enforced. Exact account limits are shown in AI Studio. | The API terms allow professional/business development, but unpaid-service prompts and responses may be used to improve Google products and may be read/annotated by human reviewers. Do not send confidential formulae, customer data, credentials, or private business material through unpaid quota. | Candidate only for public/non-confidential tasks, after account and region eligibility checks. |
| GroqCloud Free Plan | Official docs expose a Free Plan and a separate Developer Plan; do not confuse the Developer Plan table with free limits. | Limits are organization-level and model-specific. RPM, RPD, TPM, TPD and optional input/output token limits may apply. Exact account limits must be read from the organization/project Limits page and response headers. | Groq's Services Agreement permits API integration into customer applications and access for end users, subject to model-specific terms. Inference inputs/outputs are not retained by default, but may be temporarily logged for reliability/abuse investigations (up to 30 days); eligible accounts can enable Zero Data Retention. Third-party model terms still apply. | Best initial candidate for non-confidential business workloads, subject to verifying the exact free model IDs, model terms, and this account's free limits. |
| OpenRouter Free | Free plan lists 25+ free models and 4 free providers. | Free plan is limited to 50 requests/day. Free model variants ending in `:free` have 20 RPM and 50 RPD; failed attempts count. The higher 1,000 RPD allowance requires at least $10 in purchased credits and is not a zero-spend option. | Provider/model data policies vary. Free plan has no budgets/spend controls, no preferred vendor auto-routing, and no data-policy-based routing. Dynamic free-model availability is not a stable production dependency. | Suitable only for constrained experiments with explicitly pinned free model IDs and provider-specific data review; do not use paid fallback or funded-credit upgrades. |
| Cerebras Inference | Documentation describes a $0 Free tier with lower rate limits and an 8,192-token context. | Current account entitlement and exact limits must be confirmed in the console. The main pricing page describes the Developer tier as pay-as-you-go; its one-time $5 promotional credit requires a valid payment method and expires after 30 days. | The promotional credit is not recurring free capacity. The Developer tier is described for development/evaluation, not production. | Do not treat as a persistent zero-cost provider. Reconsider only if the account shows a genuine no-card Free tier with suitable terms and limits. |

## Provider-specific notes

### Gemini API

- Pricing page: https://ai.google.dev/gemini-api/docs/pricing
- Rate limits: https://ai.google.dev/gemini-api/docs/rate-limits
- Additional terms: https://ai.google.dev/gemini-api/terms
- Free-tier quotas are model- and project-specific; the active values are in AI Studio. Do not assume a public quota applies to this account.
- Unpaid-service prompts and responses may be used to improve Google products and may be reviewed by people. Exclude all confidential Raawaa formula details, customer/order information, credentials, and private business documents.

### GroqCloud

- Rate limits: https://console.groq.com/docs/rate-limits
- Model catalog: https://console.groq.com/docs/models
- Data handling: https://console.groq.com/docs/your-data
- Services Agreement: https://console.groq.com/docs/legal/services-agreement
- Limits apply at organization level, not per API key. Store exact account limits rather than copying the Developer Plan table into a Free Plan profile.
- The Agreement allows API integration into customer applications, but each model's AI Model Terms must also be checked. Do not assume all third-party models share the same licence or commercial terms.
- Default inference retention is limited; reliability/abuse logs may retain inputs/outputs for up to 30 days unless eligible Zero Data Retention is enabled.

### OpenRouter

- Free models: https://openrouter.ai/collections/free-models
- Pricing and plan limits: https://openrouter.ai/pricing
- Free variants are explicitly marked with `:free`. Do not use a generic model alias that can route to a paid model unless the selected route is explicitly constrained to free models.
- The free plan has no budget/spend controls. Engine-side hard-zero cost enforcement remains necessary.

### Cerebras

- Inference pricing: https://inference-docs.cerebras.ai/support/pricing
- Main pricing: https://www.cerebras.ai/pricing
- The documentation surfaces both a $0 Free tier and a paid Developer tier with a one-time $5 credit requiring a payment method. Treat the latter as promotional paid-plan credit, not a recurring free tier. Confirm the current account's actual plan before considering a profile.


## Account verification update (2026-11-01)

The operator confirmed that the connected Groq organization is on the **Free** plan and supplied the account console's allowed-model list and rate-limit table. The repository's scheduled model-catalog credential check also completed successfully without making any inference/completion request.

### Confirmed account-visible candidate

- Plan: Groq Free (operator-confirmed in the account console).
- Candidate model: qwen/qwen3.8-27b (present in the supplied allowed-model list).
- Account console limits supplied for this model: 30 requests/minute, 1,000 requests/day, 8,000 tokens/minute, and 200,000 tokens/day.
- Other visible allowed model IDs included openai/gpt-oss-20b, openai/gpt-oss-120b, allam-2-7b, Prompt Guard models, Whisper models, and Orpheus TTS models. Do not assume the same quota or task suitability across these model families.
- The connection check verifies API-key authentication and model-catalog access only. It does not establish inference billing behavior, model-specific commercial terms, or data-handling suitability.

### Current decision

Groq Free is now **account-plan and model-catalog verified**, not merely a public candidate. It is not yet an enabled inference route. The quota ledger must enforce the supplied rolling minute limits atomically before any provider request. Daily limits must also be configured conservatively from the console. Monthly and per-task limits remain engine-side safety caps and must be explicitly chosen; they must not be represented as provider-published limits.

Before any live call, review the current model-specific profile and data classification for the intended task. The operator confirmed the Groq Free plan and supplied account-visible model availability and quotas. Rolling-minute enforcement is merged and tested. All routes remain staged-disabled, and no inference request has been made.



## Production-grade Groq candidates: OpenAI GPT-OSS (2026-11-01)

The operator's account-visible model list includes both OpenAI open-weight models. Groq's supported-models page classifies GPT-OSS 20B and GPT-OSS 120B as **Production Models** (unlike Qwen 3.8-27B, which is Preview/evaluation-only).

- Preferred first candidate: openai/gpt-oss-20b — Groq reports ~1,000 tokens/second, 131,072 context, tool use, browser search, code execution, JSON/JSON Schema outputs, and reasoning.
- Higher-capacity fallback: openai/gpt-oss-120b — Groq reports ~500 tokens/second with similar agentic/tool capabilities.
- The OpenAI gpt-oss models are Apache-2.0 open-weight models, subject to the separate gpt-oss usage policy.
- The operator supplied Free-account limits for both: 30 RPM, 8,000 TPM, 1,000 RPD, and 200,000 TPD. The account console showed both as allowed.
- Groq's public model pages show Developer-tier rates of $0.075/M input and $0.30/M output for GPT-OSS 20B, and $0.15/M input and $0.60/M output for GPT-OSS 120B. These are not the Free-plan charges; the staged profiles record zero cost only for the operator-confirmed Free plan within its quotas.
- Both profiles are configured with internal monthly/per-task safety caps, explicitly distinguished from provider-published quotas.

These models are better candidates than Qwen Preview for future production workflows. Both remain disabled; no inference is sent until the engine is deliberately wired, the operator enables the live-inference switch, and all data-handling/approval requirements are satisfied.

Sources:
- Groq supported models and production classification: https://console.groq.com/docs/models
- Groq GPT-OSS 20B model page: https://console.groq.com/docs/model/openai/gpt-oss-20b
- OpenAI gpt-oss licence and usage policy: https://github.com/openai/gpt-oss
- OpenAI model card: https://openai.com/index/gpt-oss-model-card/

## Qwen 3.8-27B model-specific review (2026-11-01)

### Licence and service terms
- The Qwen3.8-27B model card identifies the model weights as Apache-2.0. This supports commercial use of the model artifacts subject to the licence conditions; it does not replace Groq's API agreement or any model-specific terms.
- Groq's Services Agreement permits use of its APIs to integrate Cloud Services and AI Model Services into a Customer Application and make them available to end users, subject to the Agreement, AUP, and applicable AI Model Terms.
- Groq states it does not use customer Inputs/Outputs for model training or fine-tuning unless the customer explicitly grants permission or instructs it to do so.
- Inference data is not retained by default. Groq may temporarily log inputs/outputs for reliability or abuse investigations for up to 30 days. The current documentation says customers may enable Zero Data Retention in Data Controls settings. Customer data is stored in US GCP buckets.
- Do not send Raawaa's confidential formulae, customer records, credentials, or other sensitive data through the current route. If sensitive-data use is ever proposed, first review the applicable DPA, data location, ZDR configuration, and the user's own confidentiality requirements.

### Important model-stage restriction
Groq's supported-models page labels Qwen 3.8-27B as a **Preview Model** and says preview models are intended for evaluation and should not be used in production environments because they may be discontinued at short notice. Therefore this route must remain unavailable for production, customer-facing, or unattended business workflows. At most, a future explicitly isolated internal evaluation could be considered after the engine has task-kind allowlisting for evaluation-only routes. The current engine does not yet enforce such a model-stage restriction at the task level, so the profile remains disabled.

### Free-plan cost interpretation
- The operator confirmed the account is on Groq Free and supplied account-visible quotas for Qwen: 30 RPM, 8,000 TPM, 1,000 RPD, 200,000 TPD.
- Groq's Services Agreement says some services are fee-free based on usage limits. Groq's Free-plan support FAQ says Free-plan usage within the tier limits is not charged and exceeding a limit returns a rate-limit response rather than a charge.
- Groq's model pages show $0.80/M input and $4.00/M output pricing for the Developer tier; those figures must not be copied into a Free-plan profile as though they were the Free-plan charge. They are the paid-tier reference rates.
- Keep the profile disabled. Zero-price fields describe the verified Free-plan usage only; they do not authorise a Developer-tier upgrade or paid use.

Sources:
- Groq Qwen model page: https://console.groq.com/docs/model/qwen/qwen3.8-27b
- Groq supported models and Preview classification: https://console.groq.com/docs/models
- Qwen model card / Apache-2.0: https://huggingface.co/Qwen/Qwen3.8-27B
- Groq Services Agreement: https://console.groq.com/docs/legal/services-agreement
- Groq data handling: https://console.groq.com/docs/your-data
- Groq rate limits: https://console.groq.com/docs/rate-limits
- Groq Free-plan billing FAQ: https://community.groq.com/t/do-i-get-charged-for-anything-on-the-groq-free-plan/832

## Cost accounting semantics (2026-11-03)

The OpenAI-compatible and Gemini adapters receive token-usage metadata but do not receive invoice-level billing amounts. They now return `cost_usd: null` rather than presenting `0.0` as a measured charge.

- The quota gateway estimates cost from actual input/output token counts and the selected, verified provider profile's input/output rates.
- If an adapter later supplies a provider-reported cost, the gateway records the greater of the reported amount and the profile estimate.
- The durable quota audit event records `cost_basis` as `verified_profile_estimate`, `provider_reported`, or `verified_profile_floor`.
- A positive provider-reported or profile-estimated amount on a route advertised as free is recorded and quarantines that route before returning a result.
- A zero profile estimate is **not** an invoice-level guarantee. Reconcile the provider's billing/usage console after any explicitly approved controlled inference test.

All provider profiles remain disabled. One bounded public-prompt request was sent to OpenRouter's pinned Nemotron free endpoint on 2026-11-03 and failed strict response/model validation; no second request, paid fallback, or other provider inference was attempted. The live-inference kill switch remains off; paid fallback and external writes remain disabled. This change improves accounting truthfulness; it does not enable or authorize inference.

## Required evidence before enabling any route

1. Provider account is accessible and its exact plan/tier is visible.
2. Model ID is listed as available to that account and its model-specific terms permit the intended business use.
3. Exact RPM, RPD, TPM, TPD and any other applicable limits are captured from the account console or official API response headers.
4. Input/output prices are verified as exactly $0 for the selected plan/model; any unknown or non-zero price blocks activation.
5. Endpoint is HTTPS and model ID is pinned (no uncontrolled paid fallback).
6. Data retention and training/review terms are acceptable for the data class sent. Confidential formulae and customer records remain excluded from unpaid tiers that permit human review or product improvement.
7. Verification URLs and a timezone-aware verification timestamp are stored. Re-check at least every 30 days and whenever the provider changes model IDs or plan terms.
8. Engine-side task, daily, and monthly request/token quotas and atomic per-task/daily/monthly monetary caps are implemented and tested. All monetary budgets remain configured at $0; provider credit reconciliation and explicit paid-use authorization remain required before any non-zero-cost route.
9. Live inference remains disabled until the provider profile is verified and the operator explicitly enables the kill switch.

## Current state

Account-specific verification last performed (2026-11-01); engine monetary controls reconciled (2026-11-02):

- `GROQ_API_KEY` is configured as a GitHub Actions secret. A read-only authenticated `/models` request succeeded; no inference request was sent.
- The operator confirmed the Groq account is on the Free plan and supplied the account-visible model list and limits: 30 RPM, 8,000 TPM, 1,000 RPD, and 200,000 TPD for the listed chat-completion models. These are the account's observed limits, not the larger Developer-plan limits shown on public model pages.
- `openai/gpt-oss-20b` is the preferred staged production-model candidate; `openai/gpt-oss-120b` is the higher-capacity staged fallback. Groq classifies both as Production Models. Their open weights are Apache-2.0 subject to the gpt-oss usage policy.
- `qwen/qwen3.8-27b` remains staged as Preview/evaluation-only and is blocked from production routing.
- All three routes have `enabled=false`; the registry itself is `staged_disabled`; the CLI audit and profile loader enforce that state. The quota gateway independently requires an enabled profile and the explicit live-inference kill switch.
- Minute, daily, monthly, and per-task request/token controls are implemented and tested. Atomic per-task, daily, and monthly monetary reservations are also implemented and integrated into the live gateway. All monetary budgets remain zero. The monthly/per-task values in the registry are conservative engine-side limits, not provider-published account limits.
- No inference request has been sent. No paid route, automatic upgrade, publishing, messaging, or other external write is enabled.
- Remaining gates before any inference: operator's explicit live-inference opt-in, task/data-classification restrictions, confirmation that the chosen workload contains no secrets/confidential/customer-sensitive data, and review of applicable Groq data-handling terms. Paid routing additionally requires verified provider billing/credit semantics, a provider-side spend limit where available, a non-zero engine budget explicitly authorized by the operator, and explicit approval.


## Groq billing, credits, and provider-side spend limits (2026-11-02)

The current Free account verification remains the source of truth for this project. Public documentation was rechecked to clarify what provider-side controls would and would not protect if the account were ever upgraded:

- Groq's rate-limit documentation says the published table is a high-level summary and identifies the displayed base limits as Developer-plan limits. The exact limits for an organization must be read from that organization's console. The Free account's observed limits above must not be replaced with Developer-plan table values.
- Groq's Billing FAQ says moving from Free to Developer requires a valid payment method. Developer-tier usage is billed monthly or at progressive thresholds; spend limits are a Developer-tier feature.
- Groq Spend Limits are organization-wide monthly caps, require paid-tier/organization-owner access, update every 10–15 minutes, and may allow in-flight requests to complete. They are not a substitute for engine-side per-task or daily reservations and cannot guarantee a strict immediate ceiling.
- Groq's billing documentation points operators to Dashboard → Usage for near-real-time usage and charges. Do not assume an undocumented public API for balance/credit reconciliation.
- Groq Service Credit Terms describe prepaid and promotional credits, but do not prove that this specific account currently has any credit balance. Treat unverified credits as $0.
- The engine now enforces atomic per-task, daily, and monthly nano-USD reservations and actual-cost settlement. All three caps remain $0. The live gateway still rejects non-zero/unknown-priced profiles and remains disabled unless both the programmatic and operator kill switches are enabled.

**Decision:** do not upgrade the Groq account, add a payment method, purchase credits, enable provider-side spend limits, or change any engine budget as part of routine development. Before any future paid-use proposal, verify the account's exact billing tier, available credit balance and expiry, invoice/payment behaviour, spend-limit configuration, and actual model prices in the account console; then obtain explicit operator authorization. A provider monthly spend limit is an additional backstop, not the engine's primary budget control.

Sources:
- Groq rate limits: https://console.groq.com/docs/rate-limits
- Groq Billing FAQs: https://console.groq.com/docs/billing-faqs
- Groq Spend Limits: https://console.groq.com/docs/spend-limits
- Groq Service Credit Terms: https://console.groq.com/docs/legal/service-credit-terms
- Groq usage/data handling: https://console.groq.com/docs/your-data


## Enforced live-data boundary

The code gate is stricter than a documentation warning: only `research_brief` tasks explicitly classified `public` may pass the live-inference data policy. The payload schema is limited to `data_classification`, `topic`, `questions`, and public HTTPS `source_urls`; internal, code-review, marketing/content, and system-audit tasks are blocked from provider submission. Credentials in URLs, query strings, local/private hosts, common secret patterns, email addresses, oversized topics, and unknown payload fields fail closed.

This is a structural guard, not semantic proof that arbitrary text is public. The task creator must classify accurately; never put Raawaa's confidential formula, exact ingredient composition, customer records, credentials, or other proprietary material into a task marked public. All live profiles remain disabled.


The free-text sensitive-content guard is contextual rather than a blanket ban on ordinary words such as “ingredients” or “order”. It blocks Raawaa-branded formula/ingredient/composition references, confidential/proprietary/internal data phrases, and customer/order/payment or personal-data combinations, while allowing clearly public ingredient-label research. This remains a conservative heuristic, not semantic proof that all text is public.


## Cloudflare Workers AI — first-party free-tier candidate (2026-11-03)

### Free allocation and billing boundary
- Cloudflare's official pricing states that Workers AI includes 10,000 Neurons/day at no charge on Workers Free. The daily allowance resets at 00:00 UTC; usage beyond it fails on Workers Free. On Workers Paid, usage above the same allowance is billed at $0.011 per 1,000 Neurons.
- The API error reference identifies exhausted daily allocation as HTTP 429 / internal code 3036. Some models require Workers Paid and return HTTP 403 / code 5035. Both must be terminal conditions: never upgrade, purchase credits, switch to a paid model, or retry an ambiguous request.
- Public documentation does not establish this account's exact Workers plan. The earlier zero-paid-subscription inventory is not conclusive proof of Workers Free. The account plan and current daily usage must be confirmed in the Cloudflare dashboard before any inference.

### Preferred model candidate
- First candidate: `@cf/google/gemma-4-26b-a4b-it`.
- Cloudflare lists a 256,000-token context, function calling, reasoning, vision, batch support, and Zero Data Retention. Published rates: 9,091 Neurons per million input tokens and 27,273 Neurons per million output tokens.
- Google publishes Gemma 4 under Apache License 2.0. This is a permissive model-artifact license; it does not independently prove the account's entitlement or hosted-service billing plan.
- Secondary candidate: `@cf/openai/gpt-oss-20b`, with 128,000 context, function calling, reasoning, Zero Data Retention, and published rates of 18,182 Neurons/M input and 27,273 Neurons/M output.
- Do not choose a model solely from its parameter count or marketing claims. Keep model IDs pinned and re-check the catalog and terms before each onboarding cycle.

### Data handling
- Cloudflare's Workers AI data policy states that standard stateless inputs and outputs are not retained after processing and are not used to train AI models.
- Operational metadata may still be retained, including account/model identifiers, timestamps, request/response sizes, token/Neuron counts, latency, request source, and normalized status/error codes.
- Stateful features, AI Gateway logging/caching, or application logs can retain content. Do not enable those features for the initial pilot.
- The model provider's own license and terms still apply. Initial task payloads must remain explicitly classified `public`; exclude confidential Raawaa formulae, credentials, customer/order data, and private business information.

### Verification status and next gates
- The existing Cloudflare API token's Workers AI Read permission is not yet verified. Cloudflare documents the read-only model catalog endpoint as `GET /accounts/{account_id}/ai/models/search`, requiring Account > Workers AI > Read or Workers AI > Write.
- A narrowly triggered read-only workflow, `.github/workflows/cloudflare-workers-ai-inventory.yml`, runs after relevant changes land on `main` and can also be dispatched manually; it tests that read permission and confirms that the pinned Gemma 4 catalog entry is visible. It makes GET requests only; it does not call `/ai/run`, mutate account settings, create an AI binding, or change a token.
- A successful catalog read proves only token permission and public catalog visibility. It does not prove the account plan, model-specific inference entitlement, daily quota remaining, or provider-side billing hard stop.
- Before any inference, require: (1) operator/account dashboard confirmation of Workers Free; (2) read-only API permission and exact model-catalog visibility; (3) a Staging-only `AI` binding; (4) a D1-backed atomic Neuron reservation/settlement ledger with a global daily cap no higher than 10,000 Neurons; (5) strict model allowlisting and public-only data classification; (6) restart, concurrency, ambiguous-outcome, quota-exhaustion, and no-paid-fallback tests.
- The Neuron ledger must reserve a conservative estimate before provider I/O, settle actual usage, preserve ambiguous outcomes without automatic retry/release, and fail closed when usage metadata is absent. The account's own 10,000-Neuron allowance is a second boundary, not a substitute for engine-side accounting.
- No Workers AI binding, model profile, token-scope change, or inference request has been enabled or performed. Production Worker/D1 remain untouched.

Sources:
- Cloudflare Workers AI pricing: https://developers.cloudflare.com/workers-ai/platform/pricing/
- Cloudflare Workers AI errors: https://developers.cloudflare.com/workers-ai/platform/errors/
- Cloudflare Workers AI data usage: https://developers.cloudflare.com/workers-ai/platform/data-usage/
- Gemma 4 model page: https://developers.cloudflare.com/workers-ai/models/gemma-4-26b-a4b-it/
- Gemma 4 Apache 2.0 license: https://ai.google.dev/gemma/apache_2
- GPT-OSS 20B model page: https://developers.cloudflare.com/workers-ai/models/gpt-oss-20b/
- Cloudflare model catalog API permissions: https://developers.cloudflare.com/api/resources/ai/subresources/models/methods/list/
