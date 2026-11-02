# AndroidHarness runtime profile

## Purpose and status

### Upstream release and installed-device version

The upstream repository's latest release was verified as `v3.2-release`, published 2026-10-31. APK SHA-256:
`e6607cd7a70bf555cfd3fb7ce8b66d77efd5461c083e9f4585e16f57c36f6261`.
This verifies the public upstream release, **not** the installed version on the operator's tablet. On 2026-11-02 the operator confirmed that the tablet is running AndroidHarness 3.1 and explicitly chose to remain on that version for now. Do not prompt for or perform an app upgrade. Treat v3.2 features as unavailable on this device unless separately verified; the GitHub issue bridge below does not require a 3.2-only project feature.

Version 3.2 adds native project configuration (`harness.json`), project skill/plugin bundles, named Code agent profiles, workspace-scoped MCP configuration, plugin review, improved local-model management, and an on-device `android_sdk_install` tool. These are optional 3.2 capabilities, not prerequisites for the Python-first engine or the bounded 3.1-compatible GitHub issue bridge. The project supplies:
- [Project configuration](../harness.json)
- [Raawaa AndroidHarness operating skill](../.harness/skills/raawaa-engine-androidharness/SKILL.md)
- [Read-only environment doctor](../scripts/androidharness/doctor.py)
- [Detailed 3.2 research and model evaluation](research/androidharness-3.2-evaluation.md)

The project config defines read-only researcher/reviewer/security-reviewer profiles and auto-loads the project skill. It allows routine shell/edit/network operations within normal app permissions, asks before deletion, denies outside-workspace access in ordinary app modes, and does not configure an MCP endpoint. AndroidHarness Full access is a separate user-controlled app setting that can override project permission rules; it is not enabled or requested here. These profiles are nested-agent profiles, not persistent autonomous agents.

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

Java/D8 absence does not block this Python-first engine. The consolidated `android_sdk_install` flow is a 3.2 feature and is not available by assumption on the current 3.1 device; do not attempt to use it there. Native Android build tooling is optional and not required for the current Python development/tests.

The active harness environment owns `HARNESS_SCRATCH`; inspect it through `env_status` and the diagnostic rather than provisioning tarballs or guessing paths. OpenSSH is not needed for the current HTTPS GitHub workflow.

## AndroidHarness operating model — 3.1 current-device baseline

Use **Code** for project work because it has the workspace, shell, Git, task/subagent and project tools. **Chat** is a separate research/conversation area with no project shell or file access. A Chat research result can be handed to Code, but that handoff does not run commands until a Code message is sent.

On 3.2, the project `harness.json` can auto-load the root `AGENTS.md`, project skill and selected references. Do not assume this auto-loading exists on the current 3.1 device; use the Code workspace and read the repository instructions explicitly. Use bundled skills on demand: `androidharness-environment`, `linux-env`, `git`, `doctor`, `test-driven-development`, `systematic-debugging`, `review`, `web-docs`, and `android-build` only when a native Android build is actually needed.

Plugin bundle management and project-level auto-loading described by the 3.2 release are not assumed on the current 3.1 device. If any plugin capability is present in 3.1, review its source, license, instructions, MCP definitions, hooks and dependencies before use. Installation does not automatically authorize hooks or MCP servers. CodeGraph is optional and must be installed/enabled and indexed before its tools can be relied on.

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

For the Lenovo TB370FU (operator-reported 8 GB RAM, Dimensity 7050), first local candidate is Qwen3 1.7B GGUF Q4_K_M (~1.28 GB), pinned by AndroidHarness to a fixed revision and SHA-256. SmolLM2 1.7B Q4_K_M (~1.06 GB, Apache-2.0 in the app catalog) is the lower-size alternative. Start with context 2048, input 1536, output 512, four CPU threads and at least 2.5 GB currently available RAM. Measure latency, available RAM, temperature and tool reliability. Do not begin with a 4B model. A local model is initially for offline helper tasks, not the primary coding/orchestration agent.

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

The direct authenticated tool/session adapter described above does not exist. A narrower GitHub issue bridge is implemented below for four fixed operations; it is not a direct MCP tool interface or general device control.

## GitHub-mediated AndroidHarness task bridge

A narrow issue-polling bridge is implemented in `scripts/androidharness_issue_bridge.py`. It is not an MCP server and does not expose general remote shell or direct UI control. ChatGPT can enqueue a bounded task by opening a GitHub issue with title prefix `[AndroidHarness Task]` and the required task marker/JSON body. The AndroidHarness process polls the repository through the existing authenticated GitHub CLI session, claims only issues authored by `ghith93`, and posts structured results back to the issue.

Allowed operations are fixed in code:
- `inspect_environment`: report tool versions, architecture, free disk, and available memory.
- `git_status`: report branch, HEAD, and clean/dirty status without listing changed paths.
- `run_python_tests`: fetch `origin/main`, require the requested full SHA to equal that exact fetched main commit, create a clean temporary archive, verify the five policy documents inside that exact archive, and run the standard Python unit tests. Only aggregate test counts/status are posted; raw test output and tracebacks are not exposed.
- `run_androidharness_doctor`: execute the project's read-only doctor and verify its explicit no-mutation, no-network, and no-secret-output assertions.

The bridge never accepts shell commands, arbitrary paths, package installation, root, SSH, provider calls, Cloudflare access, production writes, or deployments. Test execution uses a minimal environment that does not inherit provider, Cloudflare, or GitHub credential environment variables and uses a temporary HOME. It verifies all five mandatory policy documents before claiming each task. Task output and runtime are bounded. A private local audit log is written under `~/.cache/raawaa-autonomous-engine/`; a file lock prevents multiple pollers. **This is not an OS sandbox:** Python tests run as the AndroidHarness Linux/app UID and can still access files and network resources available to that UID. Use the bridge only with trusted, owner-controlled `origin/main` code; do not mistake environment filtering or a temporary HOME for filesystem/network isolation. Successful tasks are closed after their result is posted; failed tasks remain open with a failed title. A task interrupted after claim remains marked Running and must be inspected/requeued manually to avoid accidental duplicate execution.

### One-time activation on the tablet (AndroidHarness 3.1)

The supplied AndroidHarness clone is at `/workspace/raawaa-autonomous-engine`. After the bridge is merged, the operator only needs to start one long-running foreground terminal session in AndroidHarness:

```bash
cd /workspace/raawaa-autonomous-engine
git status --short
if [ -z "$(git status --porcelain)" ]; then
  git pull --ff-only origin main
else
  echo "Working tree is not clean; stop without changing files."
  exit 1
fi
python3 scripts/androidharness_issue_bridge.py --poll --poll-interval 30
```

Run the pull only when the working tree is clean. The script requires the already-authenticated GitHub CLI session with repository issue read/write access; it never reads or prints the stored token. These bounded issue-title/comment/close operations are internal task-lifecycle writes only; customer-facing or other external writes remain disabled. No new token scope, Cloudflare secret, SSH, Java, Shizuku, or root permission is required. Keep the AndroidHarness foreground session alive; Android may still stop background work, so this is not an always-on guarantee.

The bridge becomes operational only after the first successful device-side task result is visible in its GitHub issue. Until then, the runtime backend remains implemented-but-not-activated. ChatGPT must not claim a tablet task ran without that result.

### Task body format

The issue must be authored by `ghith93`, use the exact title prefix above, and include:

<!-- RAawaa-AndroidHarness-Task:v1 -->
```json
{"schema_version":1,"operation":"run_androidharness_doctor","timeout_seconds":120,"max_output_bytes":12000}
```

For `run_python_tests`, include `"commit_sha":"<full 40-character current main SHA>"`. Never put secrets or arbitrary commands in task bodies.

## Initial enablement sequence

1. Keep the installed AndroidHarness 3.1 version. Select the writable `raawaa-autonomous-engine` Code workspace; do not rely on 3.2-only project auto-loading, plugin bundles, WorkManager features, or SDK installer.
2. Run `/doctor`, `env_status`, and `python scripts/androidharness/doctor.py --json`; compare the local SHA with GitHub `main`.
3. Run `python -m engine.cli audit` and `python -m unittest discover -s tests -v` without secrets or provider calls.
4. If native Android build capability is requested and any component is missing, call `android_sdk_install(platform=35, gradle=true)` once. Approve the single install card and verify Java/D8/aapt2/apksigner/Gradle/android.jar. This is not required for the Python engine.
5. Keep SSH and Shizuku inactive unless a concrete task needs them; keep direct root off.
6. Select a currently listed Kilo free model with tool support for Code. Optionally benchmark Qwen3 1.7B Q4_K_M locally after checking available RAM and network suitability.
7. Establish per-project scratch/workspace quotas and artifact cleanup. Use the harness-provided paths.
8. Add one candidate workload at a time and record exact tool versions, model ID, latency, RAM, thermal behavior, exit status and artifact checksums.
9. Add direct MCP/tool integration only after the host's actual tool interface and authentication model are documented and testable.

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


## Future recurring worker: WorkManager automation (3.2 capability)

The WorkManager runbook documents the newer AndroidHarness recurring-message path. The device is intentionally remaining on 3.1, so do not assume this 3.2-era workflow is available or configure it now. For the current 3.1 setup, use the bounded GitHub issue bridge only when its foreground poller is explicitly started. Keep the cloud queue and task ledger durable in Cloudflare D1, and GitHub Actions as the reproducible CI gate. Do not run two device workers concurrently. If the device later moves to a version with verified WorkManager support, assess it as a separate migration.

Any Android background schedule remains opportunistic, not a 24/7 guarantee. Backend activation requires device-side evidence; repository documentation alone is not proof that a schedule or poller is running.
