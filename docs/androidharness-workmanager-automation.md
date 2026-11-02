# AndroidHarness WorkManager automation — retired proposal

## Decision (2026-11-02)

Do not use AndroidHarness WorkManager, recurring prompts, auto-continue, or a foreground poller as part of the normal Raawaa Autonomous Engine operating model.

The operator has identified the free model quota as a reliability bottleneck and asked to remove this dependency. The engine is therefore device-independent by design:

- Cloudflare Worker + D1 remain the remote control plane and durable task ledger.
- GitHub Actions remains CI and the bounded scheduled consumer.
- The AndroidHarness 3.1 environment is optional for occasional diagnostics or device-specific compatibility checks only.
- A missing device session, closed app, offline tablet, or exhausted AndroidHarness quota must not block task intake, cloud execution, tests, or recovery.

## Why the previous proposal is not adopted

The earlier proposal assumed AndroidHarness 3.2 WorkManager scheduling, while the installed device was confirmed as 3.1 and the operator chose not to upgrade. Even if available, WorkManager is opportunistic device scheduling and the harness's free model quota remains a separate execution limit. It cannot provide a cloud-side continuity guarantee.

GitHub Actions is also not an always-on service: scheduled workflows can be delayed or dropped during platform load. The current bounded runner is useful as a low-cost fallback, not as a claim of continuous 24/7 execution.

## Cloud-native next step

Evaluate Cloudflare Durable Objects SQLite + Alarms as a wake-up/scheduling layer only. Keep D1 as the canonical task ledger and reuse the existing authenticated claim/heartbeat/finish/retry lifecycle. Do not introduce a second task queue or production Durable Object until isolated tests cover alarm retries, duplicate delivery, fencing, D1 unavailability, restart recovery, and free-tier ceilings.

See [the cloud runtime architecture](architecture.md), [D1 control Worker](d1-control-worker.md), and [the Durable Objects alarm evaluation](research/cloudflare-durable-object-alarm-evaluation-2026-11.md).