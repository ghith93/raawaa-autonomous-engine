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
The repository enforces a USD 15 all-in monthly ceiling, with a USD 12 model-inference cap (USD 0.40/day and USD 0.20/task), USD 0 paid infrastructure overages, and USD 3 unallocated reserve. Verified-free routes remain the only eligible routes; paid fallback and external writes remain disabled. The gateway is not wired to production task execution. Its only offline test path uses the mock adapter and an `offline_test` task.

## Next
Before enabling a real adapter, verify the provider's current API contract, pricing, free-tier limits, data handling, and key permissions. Before enabling any paid model, configure and test a provider-enforced spend block or prepaid balance no greater than the remaining USD 12 monthly model allocation. A billing alert is not a hard cap. Store credentials only in GitHub Actions Secrets or a dedicated secret manager; never commit them or paste them into chat. Add provider-specific tests with mocked HTTP responses before permitting any live request.
