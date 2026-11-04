# GLM / Z.ai provider evaluation

**Reviewed:** 2026-11-04  
**Status:** discovered; public pricing checked; account entitlement and runtime not verified  
**Scope:** zero-spend-first model discovery for Raawaa Autonomous Engine. This document does not enable a provider, create credentials, or send inference requests.

## Decision

Add Z.ai's GLM family to the provider/model evaluation set. Keep every GLM route disabled until the exact account entitlement, rate limits, data terms, commercial-use terms, and a hard-zero-spend route are verified.

The strongest zero-price API candidate found in Z.ai's current published price table is **GLM-4.7-Flash** (listed Free for input, cached input, and output). **GLM-4.5-Flash** and **GLM-4.6V-Flash** are also listed as Free. These are public price listings, not proof that this account has access, a stable quota, or a production SLA. Do not assume a free tier has a guaranteed request/token allowance.

The current flagship **GLM-5.3** and **GLM-5.3-Flash** are paid API models in the published table. They are not eligible for live use under the current zero-spend default unless a separately verified no-charge entitlement exists and the operator explicitly authorizes any paid spend.

## Candidate matrix

| Model / route | Public price status | Best evaluation target | Current decision |
|---|---|---|---|
| `glm-4.7-flash` | Z.ai pricing lists Free input/output | Low-cost/free text tasks, tool-use compatibility, coding and research quality | First GLM account-verification candidate; disabled |
| `glm-4.5-flash` | Z.ai pricing lists Free input/output | Fallback baseline and compatibility comparison | Secondary candidate; disabled |
| `glm-4.6v-flash` | Z.ai pricing lists Free input/output | Vision/document/image understanding | Candidate only after account and data checks; disabled |
| `glm-5.3-flash` | $0.15 input / $0.50 output per 1M tokens | Stronger agentic/coding evaluation | Paid; do not call without explicit spend authorization |
| `glm-5.3` | $1.40 input / $4.40 output per 1M tokens | Highest-capability GLM coding/long-horizon benchmark | Paid; do not call without explicit spend authorization |

Prices are USD per million tokens and are the published Z.ai API prices checked on the review date. Cached input prices differ; see the official price table.

## Capability and deployment notes

- Z.ai's GLM-5.3 documentation and model card describe a 1M-token context window and agentic coding / long-horizon engineering focus. These are vendor-published capability claims, not independent benchmark results.
- GLM-5.3 is open-weight under a Z.AI-specific license, not simply an Apache-2.0 license. The license grants broad rights but includes a special security-review clause for a Model-as-a-Service operator whose aggregate revenue exceeds USD 10B over a consecutive 12-month period. Review the exact license before any redistribution or hosted service.
- GLM-5.3-Flash is listed with an MIT license on its Hugging Face model page. Verify the exact repository/license for any chosen checkpoint and do not infer the license of one GLM model from another.
- Large open weights do not make local inference free. GLM-5.3 is a 753B-parameter model; self-hosting it is not a practical zero-cost route on the project's currently documented small-resource environment. Hosted inference may be more practical, but only within verified free entitlement.
- Z.ai's Coding Plan is a separate subscription/quota product with credit-based and weekly usage limits. Do not mistake a Coding Plan trial or a promotional allowance for a permanent free API tier. The API pricing page and the coding-plan quota are separate products.
- Z.ai documents OpenAI-compatible API endpoints. A future adapter may be technically straightforward, but compatibility must be tested for tool calls, streaming, structured output, retries, timeouts, and usage accounting before production routing.

## Required account and safety verification

Before creating an enabled provider profile:

1. Confirm whether the existing operator account can access Z.ai from its registered region and account type.
2. Verify that `glm-4.7-flash` is selectable and actually returns a zero-price entitlement in the account console; record exact RPM, TPM, daily/monthly caps, expiration, and whether failed requests count.
3. Confirm the official API endpoint, model ID, current price, free-quota expiry/renewal, and whether a payment method or prepaid balance is required.
4. Review current Z.ai terms, privacy/data-retention policy, subprocessors, and model-specific license. Until reviewed, send only public, non-confidential test prompts; never send credentials, customer/order data, or Raawaa formulae.
5. Add a hard-zero-cost provider profile: no paid fallback, no automatic model alias that can silently route to a paid model, no credit purchase, no billing enablement, and no call when the verified free quota is absent or exhausted.
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
- Z.ai API model platform: https://z.ai/model-api
- ZCode model connection and Coding Plan notes: https://zcode.z.ai/en/docs/configuration
- GLM-5.3 model card: https://huggingface.co/zai-org/GLM-5.3
- GLM-5.3 license: https://huggingface.co/zai-org/GLM-5.3/blob/main/LICENSE
- GLM-5.3-Flash model card and license: https://huggingface.co/zai-org/GLM-5.3-Flash

## Review outcome

**Recommendation:** retain GLM as a first-class candidate in model discovery, with GLM-4.7-Flash as the first no-spend account-verification target. Do not add a production provider profile or enable inference yet. The exact account quota, availability, data terms, and operational benchmark remain unverified.
