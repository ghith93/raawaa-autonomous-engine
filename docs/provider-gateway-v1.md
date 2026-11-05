# Provider gateway v1

## Included
- Provider-neutral request/response contracts.
- Deterministic offline mock adapter for tests only.
- SQLite usage ledger with daily/monthly totals.
- Gateway that fails closed when model execution is disabled, no model is eligible, no adapter is registered, or budgets are exhausted.
- Explicit paid-call guard and status reporting.

## Not included
- No Gemini, OpenRouter, Groq, or other live network adapter.
- No credentials, secrets, outbound requests, or provider account access.
- The current model router remains deterministic and configuration-driven; quality/latency benchmarks are not yet available.

## Safety defaults
The repository enforces a USD 0 zero-spend ceiling: every monetary cap is exactly USD 0. (The former USD 15 all-in ceiling with a USD 12 model-inference cap was superseded by the owner's 2026-11-05 zero-spend directive.) Verified-free routes remain the only eligible routes; paid fallback and external writes remain disabled. The gateway is not wired to production task execution. Its only offline test path uses the mock adapter and an `offline_test` task.

## Next
Before enabling a real adapter, verify the provider's current API contract, pricing, free-tier limits, data handling, and key permissions. Before enabling any paid model, configure and test a provider-enforced spend block or prepaid balance no greater than an explicitly owner-approved allocation (currently none; zero-spend). A billing alert is not a hard cap. Store credentials only in GitHub Actions Secrets or a dedicated secret manager; never commit them or paste them into chat. Add provider-specific tests with mocked HTTP responses before permitting any live request.
