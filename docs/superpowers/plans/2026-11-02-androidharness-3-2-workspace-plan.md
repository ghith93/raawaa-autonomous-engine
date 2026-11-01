# AndroidHarness 3.2 Workspace Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare Raawaa Engine's project workspace for AndroidHarness 3.2 with scoped agent profiles, project skills, safe diagnostics and accurate setup guidance.

**Architecture:** Use AndroidHarness's native `harness.json` project configuration and conventional `.harness/skills` directory. Add a standard-library-only read-only Python diagnostic script and tests. Keep AndroidHarness optional and do not introduce an unauthenticated remote bridge.

**Tech Stack:** JSON, Markdown, Python 3.11 standard library, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-02-androidharness-3-2-workspace-design.md`

## Global Constraints

- Do not modify `Raawaa-store` or the upstream AndroidHarness application.
- Do not expose secrets, enable root/Shizuku, run paid inference, or perform production writes/deployments.
- No new Python runtime dependencies.
- Diagnostic execution must be read-only and bounded by per-command timeouts.
- AndroidHarness config must conform to the upstream 3.2 schema.
- The device snapshot is operator-reported and must not be presented as a live probe.

## Review Focus

- AndroidHarness is upgraded but Linux environment is not ready: config/diagnostics must not assume shell tools exist.
- Optional JDK/SDK/Gradle/SSH are absent: their absence must not fail Python engine readiness.
- Secret variables are present: diagnostic output must never reveal their values.
- Config references or auto-loaded skill paths are missing: CI must catch them.
- The selected workspace is SAF/read-only/SSH rather than a writable local workspace: documentation must explain capability boundaries.

---

### Task 1: Add AndroidHarness configuration and project skill

**Files:**
- Create: `harness.json`
- Create: `.harness/skills/raawaa-engine-androidharness/SKILL.md`
- Create: `.harness/memory.md`
- Test: `tests/test_androidharness_infrastructure.py`

**Interfaces:**
- AndroidHarness reads `harness.json` at the start of Code runs.
- The skill is auto-loaded as `raawaa-engine-androidharness`.
- Read-only profiles are selected through `task(agent="researcher" | "reviewer" | "security_reviewer", ...)`.

- [ ] Write tests validating config references, skill auto-load, scoped profiles, and absence of unconfigured MCP endpoints.
- [ ] Run the tests and verify they fail because the configuration files do not exist.
- [ ] Add the project config and skill with shell/edit/network allowed within normal app checks, delete ask-gated, outside-workspace denied, and no root/MCP override.
- [ ] Re-run tests and verify they pass.

### Task 2: Add a read-only AndroidHarness doctor

**Files:**
- Create: `scripts/androidharness/doctor.py`
- Modify: `tests/test_androidharness_infrastructure.py`

**Interfaces:**
- CLI: `python scripts/androidharness/doctor.py --json`
- Output: JSON containing runtime/device facts, available tools, resource observations, project branch/commit/dirty count, and boolean environment markers.
- The script must not print environment-variable values, access token files, make network requests, install packages, or change files.

- [ ] Add tests for JSON output, safe secret handling, and the `mutations_performed=false` invariant.
- [ ] Run tests and verify the expected failure.
- [ ] Implement the standard-library-only diagnostic using bounded read-only probes.
- [ ] Run the diagnostic and the complete Python test suite.

### Task 3: Update AndroidHarness 3.2 runbook and runtime registry

**Files:**
- Modify: `docs/android-harness.md`
- Modify: `config/runtime_backends.json`
- Modify: `docs/operations.md`
- Modify: `docs/architecture.md`

**Interfaces:**
- Document the v3.2 project config, skills, nested agent profiles, MCP capability boundary, built-in keyless model routing, local GGUF option, SDK installer, Shizuku/Wireless Debugging and long-run limits.
- Record upstream release/version as verified metadata; do not mark device setup or live adapter as complete.

- [ ] Update the runbook with source-backed, version-specific guidance.
- [ ] Update the runtime registry to record AndroidHarness 3.2 as the verified upstream app version and keep device capabilities as operator-reported.
- [ ] Add links to the doctor and project skill.
- [ ] Validate JSON and Markdown references.

### Task 4: Verify and prepare the PR

**Files:**
- No additional files unless tests reveal a defect.

- [ ] Run `python -m unittest discover -s tests -v`.
- [ ] Run `python -m engine.cli audit`.
- [ ] Review the complete diff for secrets, scope creep and invalid AndroidHarness schema fields.
- [ ] Open a PR and verify exact-head Engine CI.
- [ ] Do not claim tablet-side setup, model download or Shizuku startup; those require the actual AndroidHarness session and explicit device-side approvals.
