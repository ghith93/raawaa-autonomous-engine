---
name: raawaa-engine-androidharness
description: Optional AndroidHarness 3.1 diagnostics for Raawaa Autonomous Engine; never use as a task worker.
category: project
---

# Raawaa Engine — AndroidHarness diagnostic-only skill

## Current operating decision

AndroidHarness 3.1 is outside the engine's critical path. Use this skill only when the operator explicitly requests a device-specific diagnostic or compatibility check. Do not start or maintain a foreground poller, WorkManager schedule, recurring prompt, auto-continue session, or device-side task worker. Do not upgrade the installed app or request additional permissions.

Normal project work, code changes, tests, cloud task execution, recovery, and CI use GitHub integrations, GitHub Actions, Cloudflare Worker, and D1. Device unavailability or exhausted AndroidHarness model quota must never block the project.

## Required boundaries for an explicitly requested diagnostic

- Work only in `ghith93/raawaa-autonomous-engine`; never modify Raawaa-store.
- Read `AGENTS.md`, the permanent master directive, continuous-improvement charter, architecture, and operations docs before a substantive task.
- Use read-only probes by default. Do not enable Full access, root, Shizuku, public listeners, SSH, paid inference, provider calls, Cloudflare credentials, production writes, deployments, or customer-facing actions.
- Never print, read, upload, or commit token files or environment secret values.
- The existing issue bridge is limited to its four fixed operations. It is not a general shell, MCP server, OS sandbox, or persistent remote-control channel.
- A device operation is complete only when an authenticated result is visible in its corresponding GitHub issue. Repository code or an issue being queued is not proof of device execution.
- Do not install packages, download models, benchmark local inference, or change device permissions unless the operator explicitly requests that specific diagnostic and approves any device-side prompt.

## Read-only diagnostic

When explicitly requested, run the repository doctor:

```bash
python scripts/androidharness/doctor.py --json
```

It reports available tools and device/workspace facts. It must not make network requests, mutate files, install packages, or emit secret values. If the doctor fails, report the exact error; do not escalate privileges to make it pass.

## Reporting

Report:
- the exact repository commit inspected;
- the command and exit status;
- only the relevant diagnostic facts;
- whether any network access or mutation occurred;
- any limitation that prevents a stronger claim.

Never describe AndroidHarness as an always-on worker or claim that it executed a task without the corresponding verified device-side result.
