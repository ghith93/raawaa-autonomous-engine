# AndroidHarness runtime profile

## Purpose and status

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
| Java/Gradle/Android builds | Not ready in supplied snapshot | Install a compatible JDK/Android SDK only when a concrete build requires it |
| SSH-based remote execution | Not ready in supplied snapshot | Install and harden OpenSSH only for a documented remote workflow |
| Root-only tools | Not enabled by default | Demonstrated need, explicit per-operation authorization, reversible scope and audit |

No workload should be marked supported based on package installation alone. Record an executable smoke test and resource measurements.

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

The direct authenticated tool/session adapter described above does not exist. The GitHub issue bridge below is a narrower, implemented alternative for three fixed operations; it is not a general MCP tool interface or direct device control.

## GitHub-mediated AndroidHarness task bridge

A narrow issue-polling bridge is implemented in `scripts/androidharness_issue_bridge.py`. It is not an MCP server and does not expose general remote shell or direct UI control. ChatGPT can enqueue a bounded task by opening a GitHub issue with title prefix `[AndroidHarness Task]` and the required task marker/JSON body. The AndroidHarness process polls the repository through the existing authenticated GitHub CLI session, claims only issues authored by `ghith93`, and posts structured results back to the issue.

Allowed operations are fixed in code:
- `inspect_environment`: report tool versions, architecture, free disk, and available memory.
- `git_status`: report branch, HEAD, and clean/dirty status without listing changed paths.
- `run_python_tests`: fetch `origin/main`, require the requested full SHA to equal that exact fetched main commit, create a clean temporary archive, and run the standard Python unit tests.

The bridge never accepts shell commands, arbitrary paths, package installation, root, SSH, provider calls, Cloudflare access, production writes, or deployments. Test execution uses a minimal environment that does not inherit provider, Cloudflare, or GitHub credential environment variables and uses a temporary HOME. It also verifies all five mandatory policy documents in the exact source archive being tested, not only the bridge's current checkout. It publishes only aggregate unittest counts/status, never raw test output or tracebacks.

**Security boundary:** the minimal environment is not an operating-system sandbox. Python tests run as the AndroidHarness Linux/app user and can still access files and network resources available to that UID. Only run this bridge against owner-controlled `origin/main` code that is trusted to execute on the device. Do not treat temporary HOME or environment filtering as filesystem/network isolation, and do not store unrelated secrets in files accessible to the app UID. A private local audit log is written under `~/.cache/raawaa-autonomous-engine/`; a file lock prevents multiple pollers. Successful tasks are closed after their result is posted; failed tasks remain open with a failed title. A task interrupted after claim remains marked Running and must be inspected/requeued manually to avoid accidental duplicate execution. A task interrupted after claim remains marked Running and must be inspected/requeued manually to avoid accidental duplicate execution.

### One-time activation on the tablet

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
{"schema_version":1,"operation":"inspect_environment","timeout_seconds":60,"max_output_bytes":8000}
```

For `run_python_tests`, include `"commit_sha":"<full 40-character current main SHA>"`. Never put secrets or arbitrary commands in task bodies.

## Initial enablement sequence

1. Re-probe the environment and confirm local repository HEAD is synchronized with GitHub `main`.
2. Run read-only environment inventory and repository audit.
3. Run existing Python tests without secrets or provider calls.
4. Establish a per-project scratch/workspace quota and artifact cleanup policy.
5. Add one candidate workload at a time, beginning with a Python-only smoke test.
6. Evaluate LiteLLM in no-provider/no-key mode; test only local health and configuration parsing.
7. Evaluate local inference separately with a small quantized model and strict memory/thermal limits.
8. Add direct MCP/tool integration only after the host's actual tool interface and authentication model are documented and testable.

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
