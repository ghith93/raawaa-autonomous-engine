# AndroidHarness 3.2 Workspace Integration — Design

**Date:** 2026-11-02  
**Status:** Implementation authorized by the operator's request to prepare AndroidHarness infrastructure and continue the engine.

## Intent

Prepare the existing Raawaa Autonomous Engine workspace to use AndroidHarness 3.2 as a well-configured, optional Android/Linux development and test worker, while keeping GitHub Actions and the cloud control plane independent of the tablet.

## Goals

- Use AndroidHarness 3.2's project configuration, skills, agent profiles, local tools and permission controls.
- Reduce repeated routine approvals without enabling root, broad outside-workspace access, production writes, paid calls or deployment.
- Provide a safe, read-only environment diagnostic that records tool availability and device resource observations without printing credentials or mutating the environment.
- Make the Android-specific setup discoverable from the project itself.
- Give the operator a single Code-run instruction to complete the one-time on-device setup and capture a fresh readiness report.

## Non-goals

- Do not modify or fork the upstream AndroidHarness app.
- Do not claim direct ChatGPT access to the tablet or an always-on Android service.
- Do not configure a fake MCP server; the engine currently has no authenticated MCP endpoint.
- Do not enable Shizuku, direct root, public listeners, paid inference, provider keys, Cloudflare secrets, SSH, production deployment, D1 writes or customer-facing actions.
- Do not download a model or install optional packages without the device-side app's own explicit approval.

## Approaches

1. **Project-local AndroidHarness configuration (selected):** add a validated `harness.json`, a project skill, a read-only doctor script, and current setup documentation. This uses AndroidHarness 3.2's native configuration discovery and keeps the app unmodified.
2. **Fork AndroidHarness:** gives deeper control but creates an ongoing Kotlin/Android maintenance burden and duplicates capabilities already present in 3.2.
3. **Remote bridge/MCP first:** would add a networked control surface before a reviewed engine MCP service and device authentication exist. This is premature and expands the attack surface.

## Operating decisions

- The project remains the sole authority for engine policy; AndroidHarness configuration cannot override `AGENTS.md`, the operating-policy loader or production gates.
- Routine shell, edit and network operations may use the app's existing permission mode; destructive deletion remains ask-gated and outside-workspace access remains denied.
- Named researcher/reviewer/security profiles are read-only nested-agent profiles, not claims of a persistent autonomous team.
- No MCP server is configured until a real authenticated engine MCP adapter exists.
- The diagnostic script is read-only, uses subprocess argument arrays with timeouts, reports presence rather than values for environment variables, and never prints secret material.
- Android SDK/JDK setup is optional for this Python-first repository. If the operator wants complete native build capability, AndroidHarness 3.2's `android_sdk_install` performs a single approved install of OpenJDK 21, D8, aapt2, apksigner, the selected `android.jar`, and optional Gradle. It does not need hand-provisioned tarballs.
- SSH is unnecessary for the current HTTPS GitHub workflow. Shizuku is optional for Android system commands; direct root remains off.

## Acceptance

- AndroidHarness config parses and references only existing project files/skills.
- Read-only doctor runs on Android and standard Linux CI without failing when optional tools are absent.
- Tests prove secret values are never emitted and the diagnostic advertises no mutations.
- Documentation reflects the upstream 3.2 source and the dated, operator-reported device snapshot without treating it as a live probe.
- Exact-head Engine CI passes; merge only after review and all required checks.
