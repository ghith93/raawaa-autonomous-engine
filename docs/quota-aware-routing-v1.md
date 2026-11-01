# Quota-aware routing v1

## Purpose

Rotate between explicitly configured provider accounts and models when a free quota is exhausted. This is a local policy and accounting layer; it is not yet wired to live inference adapters.

## Selection rules

- A route is eligible only when free-tier eligibility and commercial-use permission have both been verified.
- Daily request and token limits must be explicitly configured. Unknown limits are treated as unavailable, not unlimited.
- The manager excludes routes in a rate-limit cooldown and routes that cannot fit the estimated token request.
- Deterministic priority selects the preferred route; remaining token capacity breaks ties.
- Usage is recorded in SQLite and can be inspected through a snapshot.
- A 429/rate-limit handler can place a route in cooldown. The caller must decide when to retry; the manager does not spin or retry automatically.

## Credits and token grants

Maintain separate records for:
1. Recurring free-tier quotas (provider-documented and account-verified).
2. Promotional/trial credits (amount, expiry, terms, eligible models, commercial-use restrictions).
3. Developer-program credits or grants (program, award, expiry, restrictions, remaining balance).
4. Local/open-source inference capacity (self-hosted compute, with actual hosting and electricity cost tracked).

Promotional credits are finite assets, not permanent free quota. Do not add them to the recurring-free pool. Never scrape, pool, rent, share, or automate multiple identities to evade provider limits. Only use accounts and credits legitimately issued to the project owner and within each provider's terms.

## Not yet implemented

- Live provider adapters and inference calls.
- Automatic quota synchronization from provider dashboards (most providers do not expose all quota dimensions through API).
- Atomic reservation before concurrent inference calls.
- Monthly credit expiry accounting and grant ledger.
- CLI commands and dashboard integration.

## Safe next integration

Add provider adapters behind the existing model gateway, then require an explicit route allowlist and per-route quota profile. Keep network calls disabled by default. A provider error must trigger bounded cooldown and route selection again; never fall back to paid usage.
