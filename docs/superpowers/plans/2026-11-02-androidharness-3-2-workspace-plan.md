# Archived plan — AndroidHarness 3.2 Workspace Integration

> **Superseded on 2026-11-02:** This plan was completed as a repository-side configuration exercise. The operator later chose to remove AndroidHarness from the runtime path. Do not execute remaining device setup, model, SDK, worker, or recurring automation steps. The retained project configuration and doctor are optional diagnostics only.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

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

- [x] Write tests validating config references, skill auto-load, scoped profiles, and absence of unconfigured MCP endpoints.
- [x] Run the tests and verify they fail because the configuration files do not exist.
- [x] Add the project config and skill with shell/edit/network allowed within normal app checks, delete ask-gated, outside-workspace denied, and no root/MCP override.
- [x] Re-run tests and verify they pass.

### Task 2: Add a read-only AndroidHarness doctor

**Files:**
- Create: `scripts/androidharness/doctor.py`
- Modify: `tests/test_androidharness_infrastructure.py`

**Interfaces:**
- CLI: `python scripts/androidharness/doctor.py --json`
- Output: JSON containing runtime/device facts, available tools, resource observations, project branch/commit/dirty count, and boolean environment markers.
- The script must not print environment-variable values, access token files, make network requests, install packages, or change files.

- [x] Add tests for JSON output, safe secret handling, and the `mutations_performed=false` invariant.
- [x] Run tests and verify the expected failure.
- [x] Implement the standard-library-only diagnostic using bounded read-only probes.
- [x] Run the diagnostic and the complete Python test suite.

### Task 3: Update AndroidHarness 3.2 runbook and runtime registry

**Files:**
- Modify: `docs/android-harness.md`
- Modify: `config/runtime_backends.json`
- Modify: `docs/operations.md`
- Modify: `docs/architecture.md`

**Interfaces:**
- Document the v3.2 project config, skills, nested agent profiles, MCP capability boundary, built-in keyless model routing, local GGUF option, SDK installer, Shizuku/Wireless Debugging and long-run limits.
- Record upstream release/version as verified metadata; do not mark device setup or live adapter as complete.

- [x] Update the runbook with source-backed, version-specific guidance.
- [x] Update the runtime registry to record AndroidHarness 3.2 as the verified upstream app version and keep device capabilities as operator-reported.
- [x] Add links to the doctor and project skill.
- [x] Validate JSON and Markdown references.

### Task 4: Verify and prepare the PR

**Files:**
- No additional files unless tests reveal a defect.

- [x] Run `python -m unittest discover -s tests -v`.
- [x] Run `python -m engine.cli audit`.
- [x] Review the complete diff for secrets, scope creep and invalid AndroidHarness schema fields.
- [x] Open a PR and verify exact-head Engine CI.
- [x] Do not claim tablet-side setup, model download or Shizuku startup; those require the actual AndroidHarness session and explicit device-side approvals.


## Execution record

- Test-first red run: the initial configuration/doctor contract failed as expected because the new files were absent.
- Green run: project config, scoped agent profiles, project skill, memory seed and read-only doctor passed all new tests.
- A follow-up path assertion caught the research note missing from the feature branch; the note was copied onto the branch and the final exact head passed twice.
- Final head: `6aa12e5d50190d8291b8f3db0f23d7f7382fd304`.
- Exact-head CI: [41360402924](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41360402924) and [41360405546](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41360405546); policy audit, Python tests and Worker tests all passed.
- Manual review confirmed AndroidHarness v3.2 schema/tool-group names against upstream config documentation. The project config does not declare an MCP server or enable Full access/root/Shizuku.
- Device-side AndroidHarness actions remain pending because this GitHub session has no tablet UI/session control. The project skill contains the exact one-time on-device procedure.
