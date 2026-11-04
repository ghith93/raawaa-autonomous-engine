# GLM / Z.ai provider evaluation

**Reviewed:** 2026-11-04  
**Status:** discovered; official public pricing and API terms checked; account entitlement and runtime not verified  
**Scope:** zero-spend-first model discovery for Raawaa Autonomous Engine. This document does not enable a provider, create credentials, or send inference requests.

## Decision

Add Z.ai's GLM family to the provider/model evaluation set, but do not yet classify it as eligible for autonomous-agent production use. Keep every GLM route disabled until the exact account entitlement, rate limits, API terms (including the broad restriction on decision-making activities), model-specific license, and a hard-zero-spend route are verified. Obtain written clarification from Z.ai on whether internal autonomous-agent planning, model routing, code review, and execution orchestration are permitted under the API Terms.

The official Z.ai API pricing table lists **GLM-4.7-Flash**, **GLM-4.5-Flash**, and **GLM-4.6V-Flash** as Free for input, cached input, cached-input storage, and output. This is a strong public zero-price candidate, but it is not proof that this account has access, a fixed quota, a stable production SLA, or any particular card/billing requirement. The pricing page publishes no numeric RPM/TPM/RPD/TPD allowance for these rows. The account's rate-limit console and current eligibility remain unverified.

The current flagship **GLM-5.3** and **GLM-5.3-Flash** are paid API models in the published table. They are not eligible for live use under the current zero-spend default unless a separately verified no-charge entitlement exists and the operator explicitly authorizes any paid spend.

## Candidate matrix

| Model / route | Official public price status | Best evaluation target | Current decision |
|---|---|---|---|
| `glm-4.7-flash` | Free input, cached input/storage, output | First text-model candidate: coding, tool use, Arabic/English research, JSON | First account-verification candidate; disabled |
| `glm-4.5-flash` | Free input, cached input/storage, output | Secondary text baseline and fallback comparison | Secondary candidate; disabled |
| `glm-4.6v-flash` | Free input, cached input/storage, output | Vision/document/image understanding | Vision candidate; disabled |
| `glm-5.3-flash` | $0.15 input / $0.50 output per 1M tokens; cached input $0.03 | Stronger agentic/coding evaluation | Paid; do not call without explicit spend authorization |
| `glm-5.3` | $1.40 input / $4.40 output per 1M tokens; cached input $0.26 | Higher-capability coding/long-horizon benchmark | Paid; do not call without explicit spend authorization |
| `glm-5.3-flashx` | $0.37 input / $1.25 output per 1M tokens | Premium Flash comparison only if separately authorized | Paid; do not call |

Prices are USD per million tokens from the official Z.ai API pricing table checked on 2026-11-04. Do not confuse "Limited-time Free" cached-input-storage offers on paid models with a free inference tier.

## Capability and deployment notes

- Z.ai documents an OpenAI-compatible API endpoint: `https://api.z.ai/api/paas/v4/`, and its Quick Start demonstrates use of the OpenAI Python and Node SDKs. Compatibility is a useful integration shortcut, not proof that every feature (tool calls, streaming, structured outputs, retries, usage accounting) behaves identically.
- Z.ai's Quick Start directs users to the Billing Page to top up "if needed". The public pricing page does not publish numeric free-model rate limits. Do not infer that a payment card is or is not required; verify the actual account flow without making a purchase.
- GLM-5.3 model documentation describes a 1M-token context window and agentic coding / long-horizon engineering focus. These are vendor-published capability claims, not independent benchmark results.
- GLM-5.3 is open-weight under a Z.AI-specific license, not simply Apache-2.0 or MIT. It grants broad rights but includes a special security-review clause for a Model-as-a-Service operator whose aggregate revenue exceeds USD 10B over a consecutive 12-month period. Review the exact license before redistribution or hosted service.
- GLM-5.3-Flash is listed with an MIT license on its Hugging Face model card. Verify the exact repository/license for any chosen checkpoint and do not infer the license of one GLM model from another.
- Large open weights do not make local inference free. GLM-5.3 is a 753B-parameter model; self-hosting it is not a practical zero-cost route on the project's currently documented small-resource environment. Hosted inference is more practical only within a verified free entitlement.
- Z.ai's Coding Plan is a separate subscription/quota product; it must not be mistaken for the free API pricing rows.

## Data protection and terms — important distinction

- The current Terms of Use (updated 2026-04-14) state that API customers/developers are also governed by the Additional Terms for API Services, and that the Additional Terms prevail in case of inconsistency.
- The API Additional Terms state that Z.ai will use API End User Content only as necessary to provide the API, comply with law, enforce policies, and prevent abuse; it says API content will not be used to develop or improve services unless the customer explicitly agrees.
- **Critical terms ambiguity:** the same API Additional Terms prohibit use of Z.ai for "decision-making activities" (section 1.f.iv). The scope is not defined on the page. Autonomous agents that plan tasks, choose providers/tools, review code, or initiate actions may be considered decision-making. Do not use GLM for those Engine roles until Z.ai provides written clarification that the intended internal/business workflow is permitted. The fact that API integration for downstream systems is generally allowed does not resolve this specific restriction.
- The API Data Processing Addendum describes Z.ai as processor for customer personal data and says it processes such data on the customer's behalf for API support. The DPA also describes Singapore as the general processing location and refers to safeguards for international transfers. Review the full current DPA and any applicable order/contract before sending personal data.
- Do not confuse the consumer Z.ai chat privacy policy/consumer terms with the API Additional Terms. The consumer terms permit broader service-improvement use of individual-user content; API-specific clauses are different.
- Even with the API no-training clause, never send secrets, credentials, private source code, customer/order data, personal data, or Raawaa formulae during initial evaluation. Use synthetic public prompts only. Do not use Z.ai outputs to train or improve competing models; API terms expressly restrict this outside authorized integration for specific business scenarios.

## Required account and safety verification

Before creating an enabled provider profile:

1. Confirm whether the existing operator account can access Z.ai from its registered region and account type.
2. Verify that `glm-4.7-flash` is selectable and actually returns a zero-price entitlement in the account console; record exact RPM, TPM, daily/monthly caps, concurrency, expiry/renewal, and whether failed requests count.
3. Confirm the official endpoint, model ID, current price, free-quota expiry/renewal, and whether a payment method, prepaid balance, or billing activation is required. Do not top up or enable billing.
4. Review current Terms, API Additional Terms, DPA, subprocessors, data-location provisions, and model-specific license. Until reviewed, send only public, non-confidential synthetic prompts.
5. Add a hard-zero-cost provider profile: no paid fallback, no automatic model alias that can silently route to a paid model, no credit purchase, no billing enablement, and no call when verified free entitlement is absent or exhausted.
6. Test only with a small public synthetic benchmark and record exact request/response metadata, latency, tool-call correctness, structured-output validity, error rates, and token usage.
7. Keep route disabled unless the above checks pass and the operator explicitly enables it.

## Suggested benchmark against current candidates

Compare GLM-4.7-Flash against the currently documented Groq Free candidates (GPT-OSS 20B / 120B) and other eligible free routes using the same synthetic tasks:

- Python/TypeScript bug diagnosis and patch proposal (no repository write access).
- JSON-schema constrained extraction.
- Multi-step planning with explicit uncertainty and source attribution.
- Tool/function-call selection and argument correctness.
- Arabic and English research summarization.
- Context retention across a bounded long prompt.
- Prompt-injection resistance and refusal to expose secrets.

Score quality, correctness, latency, reliability, context efficiency, and actual cost. Do not rank by vendor benchmarks alone.

## Official sources

- Z.ai API pricing: https://docs.z.ai/guides/overview/pricing
- Z.ai API quick start and OpenAI SDK compatibility: https://docs.z.ai/guides/overview/quick-start
- Z.ai Terms of Use and API Additional Terms: https://chat.z.ai/legal-agreement/terms-of-service
- Z.ai Privacy Policy and API Data Processing Addendum: https://chat.z.ai/legal-agreement/privacy-policy
- GLM-5.3 model card: https://huggingface.co/zai-org/GLM-5.3
- GLM-5.3 license: https://huggingface.co/zai-org/GLM-5.3/blob/main/LICENSE
- GLM-5.3-Flash model card and license: https://huggingface.co/zai-org/GLM-5.3-Flash

## Review outcome

**Recommendation:** retain GLM as a research candidate, with GLM-4.7-Flash as the first no-spend account-verification target. Do not add an enabled provider profile or use it for autonomous decision-making yet. The API Terms' undefined decision-making prohibition must be clarified in writing, in addition to verifying account quota, availability, billing requirements, and operational quality.
