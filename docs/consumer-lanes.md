# Task consumer lanes

The task queue supports explicit, durable ownership lanes. This is a routing and
lease-ownership boundary, not a claim that each lane is already running as a
separate continuously scheduled process.

## Lanes

| Lane | Admitted task type |
| --- | --- |
| `general` | Existing work and all task types not explicitly assigned to a specialist |
| `specialist:researcher` | `research_brief` |
| `specialist:reviewer` | `code_review` |
| `specialist:marketer` | `content_draft` |
| `specialist:operator` | `system_audit` |

Existing SQLite and D1 rows migrate to `general`; no existing task is silently
reclassified. Enqueue idempotency includes the lane. A task type cannot be
enqueued into a mismatched specialist lane.

## Worker authorization

All task API calls continue to require the existing `ENGINE_API_TOKEN`.
Specialist claim and lifecycle calls additionally require a distinct lane
capability token in `X-Engine-Lane-Token`. Configure a distinct random token of at least
32 characters as a GitHub repository Actions secret for each enabled lane:

- `ENGINE_RESEARCHER_LANE_TOKEN`
- `ENGINE_REVIEWER_LANE_TOKEN`
- `ENGINE_MARKETER_LANE_TOKEN`
- `ENGINE_OPERATOR_LANE_TOKEN`

Each token must be different from `ENGINE_API_TOKEN` and from the other lane
tokens. Never put these values in the repository or logs. A lane without its
own configured token is denied; it does not fall back to the general token.

The Python runner selects a lane with `RAAWAA_ENGINE_WORKER_LANE` (default
`general`) and reads the corresponding capability from
`RAAWAA_ENGINE_LANE_TOKEN`. The prepared specialist runner maps each repository
secret to that runtime variable. It refuses to run when live inference is disabled
rather than falsely completing business work as a dry run. If explicitly enabled,
the existing provider policy, verified-zero-cost routing, and quota ledgers remain
mandatory. The general runner needs no additional token.
Remote D1 task readback, claims, stale recovery, heartbeats, retries, and finishes
carry the same lane identity and require the lane capability. SQLite exposes the
same lane-filtered list and lease-ownership contract to local workers; direct local
database access remains a trusted host boundary, not a separate authentication
boundary.

## Execution and rollout

- The existing scheduled Task Runner remains on `general`.
- The bounded specialist runner script is prepared and tested, but no
  production specialist schedule is enabled yet. Keep these lanes dormant until
  the per-lane capability secrets and separate activation gates are reviewed.
  No shared-token fallback is permitted.
- The CI Staging proof creates temporary researcher credentials, tests generic
  exclusion and wrong-lane heartbeat/retry/finish rejection against remote
  Staging D1, then revokes the temporary credential.
- Lane isolation tests use dry-run tasks only. This feature does not enable
  live inference, provider fallback, external writes, or paid services.
- API-triggered stale recovery is scoped to the caller's lane and requires the
  same lane capability as claim/heartbeat/finish. The scheduled Worker Cron is
  the separate central maintenance path that can recover expired leases across
  lanes; it does not execute or complete tasks.

Do not add a new queue, database, or scheduler for each lane. Use the existing
D1 queue and bounded runner infrastructure.
