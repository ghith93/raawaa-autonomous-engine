---
name: raawaa-engine-androidharness
description: Safe AndroidHarness 3.2 setup and operation for Raawaa Autonomous Engine.
category: project
---

# Raawaa Engine — AndroidHarness 3.2 operating skill

This project uses AndroidHarness as an optional local Android/Linux worker. GitHub Actions remains the reproducible CI system; the cloud control plane remains separate. The tablet is not an always-on production host.

## Mandatory project policy

- Read root `AGENTS.md`, `docs/master-autonomous-execution-directive.ar.md`, `docs/continuous-improvement-charter.md`, `docs/architecture.md`, `docs/operations.md`, and this runtime guide before a substantive work cycle.
- Work only in `ghith93/raawaa-autonomous-engine`. Never modify Raawaa-store or another storefront repository.
- Use the already authenticated GitHub integration/CLI. The operator-reported token scopes `repo` and `workflow` are sufficient for the current repository workflow; do not request `delete_repo`, `gist`, or `read:org` without a concrete requirement.
- Keep all existing engine policy, data-classification, quota, kill-switch, external-write, production and deployment gates intact.
- Never print or read token files, dump the environment, commit credentials, or place API keys in prompts, scripts, issues, logs, or project memory.
- Routine development can use the project's normal app-level permissions. Do not enable Full access, Root, Shizuku, or outside-workspace access as a convenience.

## Start every AndroidHarness Code run

1. Confirm the selected workspace is this repository and is writable. Chat mode is for read-only research; Code mode is required for project files, shell, Git and tests.
2. Confirm AndroidHarness version in About/Settings. The upstream `v3.2-release` is the target. A GitHub clone or version note is not proof the device app has updated.
3. Run the built-in `/doctor` when tool health is uncertain. It checks the harness tool families; it is not a substitute for environment probes.
4. Run `env_status` and `python scripts/androidharness/doctor.py --json`. Do not infer capability from an installed-package marker; verify the executable and its version.
5. Check `git status --short`, current branch and HEAD. Never overwrite unrelated work. Fetch and fast-forward `main` only when the working tree is clean and the task requires it.
6. Run `python -m engine.cli audit` and the relevant tests. Use `python -m unittest discover -s tests -v` for the complete Python suite.

## One-time native build toolchain setup

The operator explicitly requested a complete AndroidHarness development environment. If the Linux environment is ready and any required component is missing, use the built-in `android_sdk_install` tool once with:

- `platform: 35`
- `gradle: true`
- `reason: One-time Android build toolchain setup for future native tooling and APK validation in the Raawaa Engine workspace.`

This single approved action installs OpenJDK 21, D8, aapt2, apksigner, Gradle, and `android.jar` for API 35, skipping components already present. It downloads from the Linux package repository and Google's Android SDK repository and requires the tool's explicit approval card. If the operator declines, continue with the Python/Node workflow; do not retry or install pieces individually.

After approval, verify with actual commands: `java -version`, `d8 --version`, `aapt2 version`, `apksigner version`, `gradle --version`, and existence of `$HOME/android-sdk/platforms/android-35/android.jar`. The tool provisions the SDK layout; do not download desktop SDK build-tools ZIPs or manually unpack JDK/Gradle tarballs. Use the active environment's returned `HARNESS_SCRATCH`; do not guess or hard-code Termux paths.

This engine is Python-first. Missing Java/D8/Gradle must never block its Python tests or be described as a blocker for the current engine workflow.

## SSH, Shizuku and root

- Do not install OpenSSH for the current HTTPS GitHub workflow. Install it only if a concrete SSH workspace/host is selected, using `pkg_search` and `pkg_install` with approval.
- Shizuku is reported installed but stopped. Keep it stopped unless a specific Android system-level operation requires shell UID access. On Android 11+, Shizuku's wireless-debugging start is performed on-device and must be repeated after reboot; do not claim it is active until AndroidHarness reports a bound, verified connection.
- Ordinary app-owned project work uses the app UID even when privileged routes exist. Shizuku is not root.
- Direct root is unverified and unnecessary for repository work. Never invoke `su`/ `sudo`, request root, or enable temporary root for routine development.

## Model selection and cost control

### Cloud/keyless

- Start with AndroidHarness's built-in Harness keyless provider and refresh its live model catalog. The Kilo route includes only entries explicitly marked free and advertising tool support; choose a currently listed tool-capable model, not a guessed ID.
- Upstream source currently notes approximately 200 Kilo requests/hour/IP and approximately 4 Pollinations `openai-fast` requests/minute/IP. These are implementation notes, not guaranteed quotas; verify current provider terms and actual response limits.
- Use Pollinations only as a low-rate fallback. Do not build a persistent workflow around anonymous rate-limited endpoints.
- Gemini, Grok, OpenRouter, Groq or ChatGPT-connected models may be used only if already configured in the app and current free eligibility/limits are verified. Enter credentials only in AndroidHarness's encrypted provider settings. Never put keys in repository files.
- Do not enable paid fallback, automatic billing, or engine live inference. Engine CI and smoke tests must remain provider-free.

### Local/offline

- Candidate first benchmark for the Lenovo TB370FU (8 GB RAM, Dimensity 7050): Qwen3 1.7B GGUF, Q4_K_M, approximately 1.28 GB. Start at context 2048, input 1536, output 512 and 4 CPU threads, then measure latency, peak available RAM, heat and tool-call reliability.
- Download only after checking current free RAM, available storage and network/data suitability. Require at least 2.5 GB available RAM for the initial benchmark; if below that, skip rather than pressure Android memory.
- Use the local model for offline summaries, classification and small helper tasks only after testing. Do not make it the main coding/orchestration model until it passes the same tool-use and task-completion benchmark as the cloud route.
- Do not start with a 4B model on this 8 GB tablet. Consider it only if the 1.7B benchmark and current memory measurements leave adequate headroom.
- Use AndroidHarness Local Models to select/download models and verify the model's license. Do not manually download unverified community builds.

## Agents, skills, CodeGraph and plugins

- Use `task(agent="researcher", ...)` for read-only source/document/model research, `task(agent="reviewer", ...)` for independent code review, and `task(agent="security_reviewer", ...)` for security/privacy review when the task tool is available.
- These are scoped nested-agent profiles, not persistent agents or proof of parallel execution. Confirm tool availability and report when a role cannot run.
- Load bundled `androidharness-environment`, `linux-env`, `git`, `test-driven-development`, `systematic-debugging`, `review`, and `web-docs` skills when relevant; do not load every skill into every prompt.
- Use CodeGraph only if the feature is installed/enabled and its tools are present. Index this workspace before relying on symbol or impact results.
- Prefer bundled/reviewed plugins. Community plugin bundles are untrusted until source, license, scripts, MCP definitions and dependencies have been inspected. Installing a plugin does not authorize its hooks or MCP servers; review the exact approval card.
- Do not add an MCP definition to `harness.json` until this engine has a real authenticated MCP service and the endpoint, auth, scopes and threat model are verified. AndroidHarness being an MCP client does not make it a server or connect its tools to ChatGPT.

## Long runs and persistence

- Use the foreground service, persistent message queue, resume cards and auto-continue for bounded device sessions. Verify the actual session state after screen-off or connection loss.
- AndroidHarness long-run recovery does not mean the tablet is a 24/7 worker. Keep scheduled work and durable queues on GitHub/cloud infrastructure; the tablet is optional.
- Use tracked background jobs, task IDs, bounded timeouts, output limits and explicit stop controls. Never start duplicate daemons without checking existing jobs.
- Record commit SHA, tool versions, command, exit status, duration, resource observations and artifact checksums. Do not record secrets or personal data.

## Completion standard

Report separately: configuration prepared, device-side approval completed, packages installed, tests run, CI result, PR merged, and runtime smoke test. Never collapse these into a single "setup complete" claim. If the AndroidHarness tool/session is not available in this run, prepare repository-side configuration and give the operator one concise Code-run instruction; do not pretend to control the tablet.


## GitHub issue bridge activation and operation

The repository includes a bounded GitHub issue poller at scripts/androidharness_issue_bridge.py. It is a GitHub-mediated task bridge, not a direct MCP server, general shell, or UI-control channel.

When the operator asks to activate the bridge:
1. Confirm the selected workspace is the exact Raawaa Autonomous Engine repository and the GitHub CLI is already authenticated. Never print or read the token.
2. Check git status. If the working tree is clean, fast-forward main; if it is dirty, stop without overwriting anything.
3. Start exactly one foreground process with: python3 scripts/androidharness_issue_bridge.py --poll --poll-interval 30
4. Keep the process in the AndroidHarness foreground service/session. Do not claim 24/7 availability.
5. The bridge accepts only issues authored by ghith93 with the exact [AndroidHarness Task] title prefix and versioned JSON marker.

Allowed bridge operations are fixed: inspect_environment, git_status, run_python_tests (only against the exact fetched origin/main SHA), and run_androidharness_doctor (read-only; must assert no mutations, no network requests, and no secret output). The Python test runner uses a minimal environment and temporary HOME, but it is not an OS sandbox: test code runs as the AndroidHarness Linux/app UID and can access files/network resources available to that UID. Run it only against trusted owner-controlled main code; do not store unrelated secrets in files accessible to the app UID. Do not modify the bridge to accept arbitrary shell or user-provided command strings. Do not enqueue root, package installation, SSH, provider, Cloudflare, production, or customer-facing operations. Task output is bounded and results are posted to the issue. If a task is left marked Running after interruption, inspect the private audit log before manually requeueing it.


## Preferred recurring operation: WorkManager

For ongoing project cycles, prefer AndroidHarness' native scheduled-message automation through Android WorkManager over a manually launched infinite GitHub issue poller.

- Follow the complete one-time setup and recurring prompt in docs/androidharness-workmanager-automation.md.
- Use one dedicated Code conversation and one hourly scheduled automation initially. Each occurrence may perform at most one bounded project task.
- Enable bounded Auto-continue (initial retry count 3), conservative per-task token/time/cost limits, and notifications. Do not treat auto-continue as permission to bypass approvals.
- Configure Android battery/background settings to avoid aggressive app suspension where the OS offers that control. Never request root, Full access, Shizuku, or disable system protections for persistence.
- Verify a real scheduled occurrence and its next scheduled occurrence in Automation history before marking the backend active.
- WorkManager scheduling is opportunistic and can be delayed or stopped by device power, force-stop, connectivity, battery policy, or OEM behavior. It is not guaranteed 24/7 execution.
- Keep Cloudflare D1 as the durable task ledger and GitHub Actions as CI. AndroidHarness is a recurring optional worker, not the sole source of task state.
- Do not run the legacy issue poller alongside the recurring Code automation unless a separately reviewed use case requires it; duplicate workers can race for workspace state.
