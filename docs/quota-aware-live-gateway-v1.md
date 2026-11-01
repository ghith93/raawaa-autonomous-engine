# Quota-aware live inference gateway v1

This phase connects the provider adapters to the quota ledger through an explicit, opt-in gateway.

## Enforcement
- Network execution is disabled unless the caller explicitly sets `network_calls_enabled=True`.
- Only quota profiles with verified free eligibility, explicit commercial-use permission, and known daily request/token limits are eligible.
- The gateway atomically reserves one request and estimated tokens before network I/O. It settles the reservation to actual provider-reported token usage afterward.
- HTTP 429 responses place that exact provider/account/model route into cooldown; the gateway can continue to another independently configured eligible provider.
- Unknown quotas, unverified free claims, and commercial-use restrictions fail closed.
- There is no paid fallback, automatic billing, account pooling, or retry loop.
- API keys remain environment-only and provider errors are sanitized.

## Important cost limitation
The current adapter contract reports `cost_usd=0.0` as a placeholder. This is not a cost measurement and must never be interpreted as proof that a call was free. The gateway is only safe to activate after the operator has independently verified the exact provider plan, model, commercial-use terms, account eligibility, and current quota limits, then entered those facts in quota profiles. Keep network execution disabled until then.

## Validation
Run:

```bash
python -m unittest discover -s tests -v
python -m engine.cli audit
```

Live adapters are not wired into the CLI, worker, or scheduled workflows. This library gateway is opt-in only.


## Provider response validation

The gateway verifies that the returned provider and model match the reserved route, response text is a string, token counts are non-negative integers, and reported cost is finite and non-negative. Invalid adapter responses stop route rotation and are not recorded as successful usage. The quota reservation is settled to zero tokens while the request itself remains counted.
