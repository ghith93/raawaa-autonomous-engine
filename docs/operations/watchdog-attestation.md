# Watchdog zero-cost attestation: local verifier prototype

`worker/zero-cost-attestation.js` is a **standalone, unwired verifier**. It
cannot make a watchdog safe by itself and does not alter the currently deployed
Worker. No trusted issuer, evidence collector, key provisioning, refresh path,
revocation, account-to-D1-binding verification, or runtime enforcement exists.
A successful signature proves only that the private-key holder signed a claim;
it does not prove the claim or its evidence hash is true, nor establish that
Free subscriptions or usage models prevent charges. Never use this verifier as
permission to deploy or write to Cloudflare until the full boundary is proven.

The prototype checks a compact base64url JSON payload and ES256 signature, a
maximum two-minute lifetime, exact scope, signed eligibility, expected account,
Worker and database identifiers, and an evidence digest. Verifier inputs must
come from independently controlled deployment configuration, not from the
attestation itself. Equal current identifiers cannot alone prove which actual D1
binding is in use. Signing keys must never be supplied by the token or logged.

Before runtime integration, establish and test an independent trustworthy issuer
that verifies the actual account, Worker, database and enforceable USD 0 policy;
secure public-key provisioning and rotation; current-time checks immediately
before each D1 write and dispatch; error paths that never write after a failed
check; expiry during a tick; anti-double-dispatch; and separate safe handling of
public research schedules. Consider in-flight writes, clock drift, replay and
revocation. Unknown or stale evidence must remain blocked. Continue preserving
existing workflow schedules and machine-only `workflow_dispatch` fallback; a
GitHub trigger is not proof that tasks ran or Arena was awakened.
