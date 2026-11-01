# AndroidHarness runtime profile

## Purpose and status

### Upstream AndroidHarness 3.2 verification

The upstream repository's latest release was verified as `v3.2-release`, published 2026-10-31. APK SHA-256:
`e6607cd7a70bf555cfd3fb7ce8b66d77efd5461c083e9f4585e16f57c36f6261`.
This verifies the public upstream release, **not** the installed version on the operator's tablet. Confirm the app's About/Settings screen before relying on v3.2-specific features.

Version 3.2 adds native project configuration (`harness.json`), project skill/plugin bundles, named Code agent profiles, workspace-scoped MCP configuration, plugin review, improved local-model management, and an on-device `android_sdk_install` tool. The project now supplies:
- [Project configuration](../harness.json)
- [Raawaa AndroidHarness operating skill](../.harness/skills/raawaa-engine-androidharness/SKILL.md)
- [Read-only environment doctor](../scripts/androidharness/doctor.py)
- [Detailed 3.2 research and model evaluation](research/androidharness-3.2-evaluation.md)

The project config defines read-only researcher/reviewer/security-reviewer profiles and auto-loads the project skill. It allows routine shell/edit/network operations only within normal app permissions, asks before deletion, denies outside-workspace access, and does not configure an MCP endpoint. These profiles are nested-agent profiles, not persistent autonomous agents.

AndroidHarness is registered as an optional external Android/Linux execution environment for this project. It complements GitHub Actions and Cloudflare; it does not replace the repository's control plane, task ledger, policy enforcement, or CI.

**Integration status:** documented and registered as a candidate execution backend. The repository does not yet contain a live AndroidHarness MCP/client adapter, and GitHub Actions cannot reach a private tablet session merely because this profile exists. An agent may execute through AndroidHarness only when a real, authenticated tool/session is exposed to that agent. Never claim a device command ran unless the harness returns its result.

The operator supplied a readiness snapshot dated 2026-11-01 22:40 UTC. It reported:
- AndroidHarness device access: Auto with fallback enabled; temporary root mode enabled per project/use; direct root not checked.
- Shizuku installed but not running.
- Bash, Git, GitHub CLI, Python 3, Node 20, npm, and pip available.
- Java/D8 absent; SSH binary absent (package available but not installed).
- GitHub CLI authenticated as `ghith93` with `repo` and `workflow` scopes; token stored locally in `.gh-token` with mode 0600.
- Clone at `/workspace/raawaa-autonomous-engine`, branch `main`, reported local HEAD `28433ab`, clean working tree.
- Python tests passed in that snapshot.
- Cloudflare deployment secrets absent.

These are a dated operator-reported snapshot, not a live health check. Re-probe before relying on any capability. Never copy, print, upload, or commit `.gh-token` or other secrets.

The snapshot predates this v3.2 capability review. In particular, Java/D8 absence no longer requires hand-provisioning JDK/Gradle tarballs: v3.2 exposes `android_sdk_install`, which can install OpenJDK 21, D8, aapt2, apksigner, a selected Android SDK platform's `android.jar`, and optional Gradle behind one explicit approval card. It skips installed components. The SDK platform download is from Google's SDK repository and is subject to Google's Android SDK License Agreement. For this Python-first engine, the native Android build toolchain is optional and does not block Python development/tests.

The active harness environment owns `HARNESS_SCRATCH`; inspect it through `env_status` and the diagnostic rather than provisioning tarballs or guessing paths. OpenSSH is not needed for the current HTTPS GitHub workflow.

## AndroidHarness 3.2 operating model

Use **Code** for project work because it has the workspace, shell, Git, task/subagent and project tools. **Chat** is a separate research/conversation area with no project shell or file access. A Chat research result can be handed to Code, but that handoff does not run commands until a Code message is sent.

The project `harness.json` auto-loads the root `AGENTS.md`, project skill and selected references. The skill guides one-time SDK setup, model selection, tests, Shizuku boundaries and long-run recovery. Use bundled skills on demand: `androidharness-environment`, `linux-env`, `git`, `doctor`, `test-driven-development`, `systematic-debugging`, `review`, `web-docs`, and `android-build` only when a native Android build is actually needed.

The plugin manager supports npm, GitHub and local plugin bundles. Community plugins are author-published, not verified endorsements. Review source, license, instructions, MCP definitions, hooks and dependencies before installation. Installation does not automatically approve or execute MCP servers/hooks. CodeGraph is optional and must be installed/enabled and indexed before its tools can be relied on.

AndroidHarness is an MCP client, not an MCP server. No engine MCP endpoint is currently configured. Do not add a fake or unauthenticated endpoint. A future bridge must have authentication, bounded operations, task IDs, audit logging, workspace allowlists, output/time limits and explicit privilege gates.

The upstream app has a foreground service, persistent message queue, resume cards and optional auto-continue. These improve session resilience but do not make the tablet a guaranteed 24/7 worker. Optional Telegram remote control can be paired to steer a tablet session from another device, but it is not configured and does not give ChatGPT this conversation direct device access.

## Intended role in the execution architecture

Use AndroidHarness for bounded, inspectable device-side or Linux-side work that benefits from the Android tablet environment:

1. **Compatibility lab:** install and test open-source CLI tools and Python/Node projects in isolated workspaces.
2. **Local model experiments:** benchmark CPU-compatible quantized models and runtimes, recording model format, memory use, latency, thermal behavior, and failures. GPU/NPU acceleration is not assumed.
3. **Agent/framework evaluation:** run dependency-isolated experiments for frameworks, tool servers, local inference gateways, and coding agents.
4. **Build/test worker:** run Python, Node, Git, lint, unit tests, packaging, and Android build tools after their prerequisites are verified.
5. **Repository maintenance:** clone/fetch branches, run tests, inspect artifacts, and prepare evidence. GitHub writes still follow branch/PR/CI policy.
6. **Recovery and diagnostics:** collect bounded logs and environment facts; do not silently escalate privileges or weaken security controls.

Prefer AndroidHarness for interactive or device-specific validation; prefer GitHub Actions for reproducible CI; use Cloudflare only for the already-approved deployed control-plane functions. Do not make the tablet a required single point of failure.

## Candidate workload compatibility

| Workload | Initial assessment | Required validation |
|---|---|---|
| Python CLI/library tools | Likely, subject to package compatibility | Python version, wheels/native extensions, storage and network |
| Node.js tools and MCP servers | Likely for compatible packages | Node version, native modules, process lifetime, inbound/outbound networking |
| LiteLLM proxy | Possible, not yet verified | Python/runtime support, dependencies, bind address, process supervision, provider credentials and network; no provider calls during smoke test |
| Small local LLM inference | Possible for selected quantized models | ARM64 ABI, runtime support, model format, RAM headroom, CPU performance and thermal throttling |
| llama.cpp / GGUF | Candidate, not assumed working | Android/Termux build path, compiler/toolchain, supported backend and model memory |
| Ollama | Unverified; Android support is not assumed | Supported Linux environment/architecture, daemon lifecycle, storage, memory, network binding |
| Java/Gradle/Android builds | Not ready in supplied snapshot; v3.2 includes a consolidated installer | If native builds are required, call `android_sdk_install(platform=35, gradle=true)` once after checking `env_status`; approve the single card, then verify each executable and android.jar |
| SSH-based remote execution | Not ready in supplied snapshot; unnecessary for HTTPS GitHub | Install OpenSSH only if a concrete SSH workspace/host is selected |
| Root-only tools | Direct root unverified; not required | Keep disabled; require a demonstrated need, explicit per-operation authorization, reversible scope and audit |

No workload should be marked supported based on package installation alone. Record an executable smoke test and resource measurements.

## Models and inference strategy

The upstream Harness keyless provider fetches a live Kilo catalog and filters to models explicitly marked free and advertising tool support. Its default router is `kilo-auto/free`. The implementation currently notes approximately 200 Kilo requests/hour/IP and approximately 4 Pollinations `openai-fast` requests/minute/IP; these are code notes, not guaranteed quotas or terms. Refresh the catalog and check the actual model metadata before use. Anonymous endpoints are fallback options, not durable worker infrastructure.

For the Lenovo TB370FU (operator-reported 8 GB RAM, Dimensity 7050), first local candidate is Qwen3 1.7B GGUF Q4_K_M (~1.28 GB). Start with context 2048, input 1536, output 512, four CPU threads and at least 2.5 GB currently available RAM. Measure latency, available RAM, temperature and tool reliability. Do not begin with a 4B model. A local model is initially for offline helper tasks, not the primary coding/orchestration agent.

Gemini, Grok, OpenRouter, Groq and ChatGPT-connected accounts are optional only when configured in the app and their current account-specific free quotas/terms are verified. Keys belong in AndroidHarness encrypted settings, never in project files. Engine live inference remains independently disabled until its own explicit opt-in and quota/data-classification gates pass.

See [the detailed model and runtime evaluation](research/androidharness-3.2-evaluation.md).

## Safe operating contract

- Use a dedicated project workspace; never run untrusted repositories as root.
- Default to unprivileged execution. Temporary root is exceptional, per-task, and must be justified and recorded.
- Shizuku being installed does not mean it is running or authorized. Do not start it or grant permissions automatically.
- Never expose the local GitHub token, Cloudflare credentials, provider keys, or user data to model prompts, logs, test fixtures, or third-party services.
- Do not enable paid inference, paid downloads/hosting, automatic billing, publishing, messaging, customer actions, production deployment, or production database changes.
- Do not use the Android device as an unauthenticated public server. Bind local services to loopback unless a reviewed network requirement says otherwise.
- Set CPU, memory, disk, runtime, and network limits for every experiment. Stop on thermal stress, low storage, repeated crashes, or unexpected network activity.
- Pin dependencies and record versions/hashes. Prefer official release sources and verify checksums/signatures where available.
- Keep secrets outside the repository and use least-privilege credentials. The existing `.gh-token` must remain local and mode 0600.
- Every run records: task ID, commit SHA, command/tool version, start/end, exit status, resource observations, artifacts, and whether network/provider access occurred.
- Do not claim continuous execution: AndroidHarness sessions may be foreground-bound or disconnected. A durable always-on worker requires a separate supported supervisor/host and persistence design.

## Agent tool integration contract

When a real AndroidHarness tool/session becomes available to the engine, expose it through a narrow adapter with these operations only:
- `inspect_environment` — versions, ABI, free disk/RAM, installed tool availability.
- `run_bounded_command` — allowlisted working directory, timeout, output cap, no implicit root.
- `sync_repository` — fetch/pull a named branch with fast-forward-only semantics.
- `run_test_suite` — invoke a declared test command and return structured exit status.
- `collect_artifact` — return an artifact reference and checksum, not secret files.
- `stop_process` — stop only a process started and tracked by the same task.

Every call must carry a task ID, workspace boundary, timeout, output limit, and policy decision. Deny arbitrary shell strings from untrusted task payloads; use argument arrays or reviewed command templates. Root, network exposure, package installation, credential access, and external writes require separate policy gates. The adapter must integrate the repository operating-policy loader and audit log before any execution.

Until this adapter exists, this document is an integration specification and capability register—not proof that the autonomous engine can remotely invoke AndroidHarness.

## Initial enablement sequence

1. Confirm AndroidHarness 3.2 in the device app and select the writable `raawaa-autonomous-engine` Code workspace.
2. Run `/doctor`, `env_status`, and `python scripts/androidharness/doctor.py --json`; compare the local SHA with GitHub `main`.
3. Run `python -m engine.cli audit` and `python -m unittest discover -s tests -v` without secrets or provider calls.
4. If native Android build capability is requested and any component is missing, call `android_sdk_install(platform=35, gradle=true)` once. Approve the single install card and verify Java/D8/aapt2/apksigner/Gradle/android.jar. This is not required for the Python engine.
5. Keep SSH and Shizuku inactive unless a concrete task needs them; keep direct root off.
6. Select a currently listed Kilo free model with tool support for Code. Optionally benchmark Qwen3 1.7B Q4_K_M locally after checking available RAM and network suitability.
7. Establish per-project scratch/workspace quotas and artifact cleanup. Use the harness-provided paths.
8. Add one candidate workload at a time and record exact tool versions, model ID, latency, RAM, thermal behavior, exit status and artifact checksums.
9. Add a reviewed AndroidHarness adapter only after the host's actual tool interface and authentication model are documented and testable.

## Snapshot drift and synchronization

The supplied snapshot's local clone (`28433ab`) was behind GitHub after subsequent merges. Before using it for current project tests, synchronize with:

```bash
cd /workspace/raawaa-autonomous-engine
git fetch origin
git pull --ff-only origin main
git rev-parse --short HEAD
git status --short
```

The expected commit must be the current GitHub main SHA or a descendant, not a hard-coded historical SHA. This command is a user-side instruction for the AndroidHarness terminal; repository documentation changes do not execute it on the tablet.

## Review checklist for each new project

- [ ] Official source and license checked
- [ ] Architecture/ABI and Android compatibility checked
- [ ] Runtime/dependency footprint estimated
- [ ] No secret or provider credential required for initial smoke test
- [ ] No paid service or unexpected billing path
- [ ] Network listeners disabled or loopback-only
- [ ] CPU/RAM/disk/time limits defined
- [ ] Smoke test and failure cleanup documented
- [ ] Results, versions, commit SHA, and resource measurements recorded
- [ ] Agent invocation still passes policy and audit gates
