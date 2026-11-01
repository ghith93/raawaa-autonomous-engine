# Provider free-tier eligibility review

**Reviewed:** 2026-11-01  
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

Before any live call, separately verify the model-specific terms and the exact Free-plan pricing/data-handling terms applicable to this organization. Keep live inference disabled until the minute-window enforcement is merged, all profile evidence is complete, and the operator explicitly enables the existing kill switch. No real inference request has been made as part of this verification.


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

## Required evidence before enabling any route

1. Provider account is accessible and its exact plan/tier is visible.
2. Model ID is listed as available to that account and its model-specific terms permit the intended business use.
3. Exact RPM, RPD, TPM, TPD and any other applicable limits are captured from the account console or official API response headers.
4. Input/output prices are verified as exactly $0 for the selected plan/model; any unknown or non-zero price blocks activation.
5. Endpoint is HTTPS and model ID is pinned (no uncontrolled paid fallback).
6. Data retention and training/review terms are acceptable for the data class sent. Confidential formulae and customer records remain excluded from unpaid tiers that permit human review or product improvement.
7. Verification URLs and a timezone-aware verification timestamp are stored. Re-check at least every 30 days and whenever the provider changes model IDs or plan terms.
8. Engine-side task, daily, and monthly request/token quotas are configured. Monetary spend caps must be implemented and tested before any non-zero-cost route is allowed.
9. Live inference remains disabled until the provider profile is verified and the operator explicitly enables the kill switch.

## Current state

No provider profile, API credential, or live inference route was enabled as part of this review. The next practical candidate is Groq Free for non-confidential tasks, but it remains **unverified for this account** until its exact Free Plan limits and model terms are confirmed in the user's console.
