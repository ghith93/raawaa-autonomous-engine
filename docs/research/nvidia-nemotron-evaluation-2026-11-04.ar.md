# NVIDIA Nemotron 3.5 Lightning — candidate evaluation

**Date:** 2026-11-04  
**Decision:** `CANDIDATE / REGISTERED DISABLED`  
**Proposed role:** independent third-model advisor and bounded implementation specialist  
**Runtime status:** not connected; no inference sent; not enabled for routing

## Why this model

NVIDIA Nemotron 3.5 Lightning is a 30B-parameter hybrid Mamba-2 / Mixture-of-Experts / Attention model with approximately 3B active parameters. NVIDIA positions it specifically for high-volume execution in long-running agents and sub-agent workloads. The NVIDIA model card advertises up to 1M context, text/code workloads, tool-oriented agentic use, and an OpenAI-compatible prototype endpoint.

Official sources:
- Model card and free prototype endpoint: https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b
- NVIDIA release/technical overview (2026-08-11): https://developer.nvidia.com/blog/nvidia-nemotron-3-5-lightning-30b-a3b-delivers-fast-accurate-specialized-task-execution-for-long-running-agents/
- NVIDIA model weights: https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4
- NVIDIA Nemotron developer repository: https://github.com/NVIDIA-NeMo/Nemotron
- Hosted API reference/endpoint: https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b?section=deploy

## Openness, licence, and cost distinction

- The model weights are downloadable and the model is presented for commercial and non-commercial use under OpenMDW License Agreement v1.1. Review the actual licence text before production use: https://openmdw.ai/
- Downloading weights is free; inference is not automatically free. Self-hosting requires substantial GPU resources. NVIDIA's reference material lists H100/A100-class hardware for BF16 and optimized NVFP4 deployments; this is not suitable for the project's current phone-only/local-device setup.
- NVIDIA currently marks a free prototype endpoint as available at `https://integrate.api.nvidia.com/v1`, using an NVIDIA API key and OpenAI-compatible chat completions.
- NVIDIA's own FAQ describes the API Catalog as a no-cost trial for evaluation/prototyping, says limits vary by model and concurrent load, and directs users to the account console for the actual limits. No stable numeric request/token quota or provider-enforced spend hard-stop was established from public documentation.
- The hosted API's exact account-specific eligibility, quota, retention/data handling and commercial-use conditions must be verified independently. Do not assume the downloadable-weight licence automatically governs the hosted API.

## Why Lightning rather than Super or Ultra

Lightning is the better first candidate for the engine's third execution/advisor slot because NVIDIA specifically positions it as a high-volume sub-agent workhorse. Super is the stronger candidate for complex planning/reasoning but has 120B total / 12B active parameters and much higher serving requirements. Ultra is larger still and unsuitable for this project's cost/compute envelope. These should remain alternatives, not simultaneous integrations.

## Fit with the existing engine

The engine already has:
- a provider-neutral `OpenAICompatibleAdapter`;
- a strict provider-profile registry with current verification, finite per-minute/day/month/per-task quotas, zero-cost checks, commercial/production eligibility, and exact endpoint allowlisting;
- a quota-aware live gateway, durable D1 quota ledger in cloud mode, monetary ledger, and global live-inference kill switch;
- a declarative `config/agents.json` roster.

Therefore, do not create a second provider adapter, model router, scheduler, quota ledger, or agent runtime. The current OpenAI-compatible adapter could be reused after the provider is explicitly allowlisted in `engine/live_runtime.py`, but that is deliberately not done in this candidate-registration change.

## Intended bounded role

When all gates are satisfied, use Nemotron as an independent third model for:
1. architecture and implementation-plan critique;
2. independent review of ChatGPT/Arena recommendations;
3. bounded code generation and patch proposals in isolated branches;
4. test-case generation and failure analysis.

It must not self-approve, merge, change policy/budgets/secrets, run production operations, or execute arbitrary tools. Any code changes remain subject to CI, independent review, and the repository's existing approval rules.

## Activation gates — all required

- [ ] Confirm the user's NVIDIA Developer account can create and use an API key for this exact endpoint.
- [ ] Read and record the current hosted API terms, privacy/data handling, and commercial-use permission separately from the model-weight licence.
- [ ] Record account-console enforced RPM/TPM/RPD/TPD and any monthly caps. Unknown or non-finite quotas keep the route blocked.
- [ ] Verify the free endpoint is genuinely zero-cost for the intended usage and has no automatic paid conversion/overage.
- [ ] Confirm API-key secret is present in the existing approved secret store; record the name only (`NVIDIA_API_KEY`), never its value.
- [ ] Add exact provider endpoint allowlisting and tests only after the preceding evidence exists.
- [ ] Run one public, non-sensitive, single-request staging smoke under the existing kill switch, D1 ledgers, and per-task caps; inspect usage and response format.
- [ ] Benchmark against current enabled candidates on the same fixed tasks: coding fixes, code review, architectural critique, structured output/tool calls, latency, failure rate, and tokens.
- [ ] Enable the declarative specialist and task mapping only after the measured evaluation passes and the operator has reviewed the results.

## Current decision

Register the model and specialist as disabled candidates only. This is a real code/configuration registration, not a live integration. The missing numeric free-tier quota is a hard blocker under the project's policy; no inference, paid service, deployment, or secret change is authorized or performed.
