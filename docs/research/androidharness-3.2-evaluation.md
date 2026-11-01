# AndroidHarness 3.2 — Technical Research and Operating Recommendation

**Research date:** 2026-11-02  
**Upstream project:** [Sanuu7/AndroidHarness](https://github.com/Sanuu7/AndroidHarness)  
**Verified release:** [v3.2-release](https://github.com/Sanuu7/AndroidHarness/releases/tag/v3.2-release), published 2026-10-31  
**APK SHA-256:** `e6607cd7a70bf555cfd3fb7ce8b66d77efd5461c083e9f4585e16f57c36f6261`

## Executive conclusion

AndroidHarness 3.2 is a capable on-device coding and agent environment, not merely a terminal emulator. It combines project file tools, Linux shell, Git/GitHub, browser automation, skills, nested subagents, project-level agent profiles, MCP client, plugin bundles, local CodeGraph, local GGUF inference, provider/model selection, task resumption and a foreground service.

The highest-value use for Raawaa Engine is as an **optional device-side development and compatibility worker**. GitHub Actions should remain the reproducible CI system; the cloud worker/control plane remains responsible for durable scheduled work. The tablet must not become a single point of failure or be described as an always-on worker.

The existing AndroidHarness integration profile is therefore being upgraded from a dated capability note into an actual project configuration, a reusable operating skill and a read-only diagnostic script. The upstream app itself is not forked or modified.

## 1. AndroidHarness 3.2 capabilities verified in upstream source

| Area | Verified capability | Raawaa Engine use |
|---|---|---|
| Project workspace | `harness.json` / `harness.jsonc`, root `AGENTS.md`, references, skills, memory | Load project rules and workflows automatically |
| Agent profiles | Named Code profiles with tool allow/deny groups; nested `task(agent=...)` | Read-only researcher, reviewer and security reviewer |
| File operations | Read/write/edit/search/grep/apply-patch, file history and restore | Controlled code changes with recovery |
| Shell | App UID, Shizuku and optional root routes; foreground/background jobs | Python/Node tests and bounded compatibility work |
| GitHub | Repository access, commits, branches, PR creation and review | Device-side checkout and verification |
| Browser | Browser automation, preview, screenshots, console logs | Test web previews and local tooling |
| CodeGraph | Local code/symbol graph, impact and affected-test analysis | Optional codebase navigation after indexing |
| Skills | Bundled, user and project skills; project skills under `.harness/skills` | Load the Raawaa-specific operating skill |
| Plugins | npm/GitHub/local bundles; review instructions, skills, MCP and hooks before install | Add only audited capabilities |
| MCP | Local stdio and remote HTTP MCP client; workspace-bound approval | Future authenticated engine tools, once implemented |
| Local models | GGUF and selected safetensors conversion; RAM/storage warnings | Offline lightweight tasks and local experiments |
| Long runs | Foreground service, persistent queue, resume cards and auto-continue | Bounded sessions that survive some interruptions |

Primary sources:
- [AndroidHarness README](https://github.com/Sanuu7/AndroidHarness/blob/main/README.md)
- [Workspace configuration guide](https://github.com/Sanuu7/AndroidHarness/blob/main/docs/configuration/README.md)
- [Plugin manager guide](https://github.com/Sanuu7/AndroidHarness/blob/main/docs/configuration/plugin-manager.md)
- [AndroidHarness environment skill](https://github.com/Sanuu7/AndroidHarness/blob/main/app/src/main/assets/skills/androidharness-environment/SKILL.md)
- [Android SDK installer implementation](https://github.com/Sanuu7/AndroidHarness/blob/main/app/src/main/java/com/androidharness/app/tools/AndroidSdkTools.kt)

## 2. Project configuration and permissions

AndroidHarness 3.2 reads project `harness.json` at the beginning of each Code run. It automatically loads the root `AGENTS.md` and `HARNESS.md` when configuration is present. The configuration can declare references, project skills, named agent profiles, memory paths, MCP servers and permission preferences.

This repository now contains:
- [`harness.json`](../../harness.json) — scoped project configuration.
- [Raawaa AndroidHarness skill](../../.harness/skills/raawaa-engine-androidharness/SKILL.md) — one-time setup and runbook.
- [Workspace memory](../../.harness/memory.md) — non-secret project constraints.
- [Read-only environment doctor](../../scripts/androidharness/doctor.py).

The three named profiles are nested-agent profiles, not persistent autonomous agents:
- `researcher`: read-only, with network research.
- `reviewer`: read-only, no network.
- `security_reviewer`: read-only, with network for source/CVE checks.

Routine shell/edit/network operations use the project's normal app permission checks. Deletion remains ask-gated; outside-workspace access is denied. This does not enable Android Full access, Shizuku or root, and it does not override the engine's own approval policy.

No MCP server is configured. AndroidHarness is an MCP **client**. This engine does not yet expose a reviewed authenticated MCP endpoint, so adding a fictitious endpoint would create a broken integration and a needless attack surface.

## 3. Native build toolchain: no manual JDK/Gradle tarballs

The previous readiness snapshot reported Java/D8 missing and no scratch workspace. Upstream v3.2 provides a better route:

The `android_sdk_install` tool presents one approval card and installs:
- OpenJDK 21
- D8
- aapt2
- apksigner
- Google's `android.jar` for a selected supported API level
- Gradle when `gradle=true`

It skips already installed packages and uses the same package repository as the harness package manager. The Android SDK platform download is subject to Google's Android SDK License Agreement. The tool installs the SDK platform under `$HOME/android-sdk/platforms/android-<api>/android.jar`.

**Recommendation:** because the operator requested a complete development environment, use the one-time tool with API 35 and Gradle enabled after confirming the Linux environment is ready. Approve the single installation card on the device. Verify the executables afterward. This is optional for the current Python-only engine tests; it prepares future native APK builds.

Do not manually download desktop Android build-tools archives, unpack JDK/Gradle tarballs, or invent Termux paths. Use the active environment's actual `HARNESS_SCRATCH`. Do not install OpenSSH for the current HTTPS GitHub workflow; it is needed only if a concrete SSH workspace is introduced.

## 4. Device privilege and Shizuku

The operator-reported snapshot (2026-11-01 22:40 UTC) says:
- Shizuku is installed but not running.
- Direct root status has not been checked.
- AndroidHarness access mode is Auto with fallback enabled.
- Temporary root mode was reported enabled per project/per use.

These are not live-verified device states. Upstream AndroidHarness documentation makes clear that ordinary app-owned workspace work remains on the app UID even when privileged transports exist.

**Recommendation:**
- Keep project development on the normal app UID.
- Do not enable direct root or use `su`/ `sudo`.
- Leave Shizuku stopped until a specific Android system-level operation requires it.
- If later required, Shizuku supports on-device Wireless Debugging startup on Android 11+ without a PC. Pair once, start it from the Shizuku app, and expect to repeat the start after reboot. Shizuku normally provides shell UID (2000), not root.
- Do not claim Shizuku readiness until AndroidHarness reports a bound and verified connection.

Official guide: [Shizuku startup via Wireless Debugging](https://github.com/RikkaApps/websites/blob/master/shizuku/guide/setup.md).

## 5. Model strategy for the Lenovo TB370FU

Device facts supplied by the operator: MediaTek Dimensity 7050, 8 GB RAM, Android 15, 256 GB storage with approximately 209 GB free. Available RAM changes dynamically and must be measured before local inference.

### Primary Code model

Start with AndroidHarness's built-in **Harness keyless provider**:
- Its live Kilo catalog is filtered to models explicitly marked free and advertising tool support.
- The default router is `kilo-auto/free`.
- Upstream implementation notes approximately 200 requests/hour/IP for Kilo and approximately 4 requests/minute/IP for Pollinations `openai-fast`.
- These are implementation notes, not guaranteed service quotas. The catalog and service terms can change.

Use a currently listed Kilo model with tool support for Code work. Use Pollinations only as a low-rate fallback. Do not use anonymous endpoints as a production worker dependency.

### Local model candidate

The most balanced first benchmark is:

- **Qwen3 1.7B GGUF, Q4_K_M** — approximately 1.28 GB.
- AndroidHarness's own model recommendation lists a 4 GiB minimum RAM tier and supports a maximum context of 40,960 tokens; that is not a guarantee of stable performance on every workload.
- Start with context 2,048, input 1,536, output 512, and four CPU threads.
- Measure generation latency, available RAM before/after, Android process stability, heat/thermal throttling and tool-call reliability.
- Require at least 2.5 GB available RAM for the initial test; otherwise skip it.

Other candidates:
- [Qwen2.5-Coder 1.5B Q4_K_M](https://huggingface.co/bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF): approximately 0.99 GB; a small coding-specialist comparison candidate.
- Qwen3 4B Q4_K_M: approximately 2.50 GB, with higher memory and heat risk. Do not start here on an 8 GB tablet.

Use local inference for offline summaries, classification and small helper tasks only after benchmarking. Do not make a small local model the main autonomous coding agent until it passes the same tool-use and completion tests as the cloud route. Verify the exact repository/model license before downloading or redistributing any quantization.

Sources:
- [AndroidHarness pinned local-model recommendations](https://github.com/Sanuu7/AndroidHarness/blob/main/app/src/main/java/com/androidharness/app/local/LocalModelRecommendations.kt)
- [Qwen3 1.7B GGUF files and quantization sizes](https://huggingface.co/bartowski/Qwen_Qwen3-1.7B-GGUF)
- [Qwen2.5-Coder 1.5B GGUF files and license](https://huggingface.co/bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF)

### Provider rules

Gemini, Grok, OpenRouter, Groq and ChatGPT-connected accounts are optional routes only if already configured in AndroidHarness and their current account-specific free quotas and terms are verified. Store keys only in AndroidHarness's encrypted provider settings. No paid fallback or automatic billing. Engine live inference remains disabled until its own explicit opt-in, classification and quota gates are met.

## 6. Plugins, skills and external projects

Use built-in capabilities before adding dependencies:
1. `androidharness-environment` for workspace/device/shell boundaries.
2. `linux-env` for package/runtime diagnostics.
3. `git` for repository operations.
4. `test-driven-development`, `systematic-debugging` and `review` for implementation quality.
5. `web-docs` for current API/library documentation.
6. `android-build` only when an Android app build is actually required.
7. CodeGraph only after the workspace index exists and its tools are available.

The plugin manager can install npm, GitHub or local bundles, but community bundles are published by their authors and are not automatically trusted. Inspect source, license, bundled instructions, MCP servers, hooks and dependencies. The installer does not run npm lifecycle scripts or automatically install server dependencies. MCP and executable hooks have separate exact-definition approvals.

Do not add a new agent framework, terminal package, local server or plugin just because it is popular. Prefer AndroidHarness's native capabilities; benchmark any additional project in an isolated workspace before adoption.

## 7. Long-running execution and remote operation

AndroidHarness 3.2 includes a foreground service, persistent message queue, resume cards and optional auto-continue. These help a task survive screen-off periods, process interruptions and connection drops. They do not provide a guaranteed always-on 24/7 service.

The upstream README also describes optional Telegram remote control. It could let the operator steer an AndroidHarness session from an iPhone after a private bot is paired and permissions are configured. It is not configured in this repository and does not connect this ChatGPT session to the tablet.

## 8. Recommended operating architecture

```text
ChatGPT / GitHub integration
    |  research, code review, branch and PR operations
    v
GitHub repository (source of truth) <----> GitHub Actions (reproducible CI)
    |
    |  optional checkout / bounded tests
    v
AndroidHarness on Lenovo
    |  Python/Node tests, Android SDK builds, local model experiments
    |  artifacts + commit SHA + logs
    v
PR / CI evidence

Cloudflare control plane and durable task state remain separate.
```

Do not claim a direct ChatGPT-to-AndroidHarness control channel. A future bridge should expose a small authenticated MCP or HTTP interface with task IDs, signed requests, bounded commands, allowlisted workspace paths, timeouts, output caps, audit records, kill controls and no implicit root. It should be implemented only after the engine-side server and device authentication are reviewed.

## 9. Remaining device-side actions

The repository-side configuration and read-only doctor are committed in this branch. The actual tablet state cannot be changed from the GitHub connector in this session. The AndroidHarness Code run should:

1. Confirm app version 3.2 and selected writable engine workspace.
2. Run `/doctor`, `env_status` and `python scripts/androidharness/doctor.py --json`.
3. If Linux environment is ready and components are missing, call `android_sdk_install(platform=35, gradle=true)` and obtain approval for the single install card.
4. Verify Java, D8, aapt2, apksigner, Gradle and API 35 `android.jar`.
5. Run engine audit and tests.
6. Report actual RAM/storage, Shizuku status, installed packages and exact local HEAD. Do not install SSH or enable Shizuku/root without a concrete requirement.

The AndroidHarness project skill is designed to guide this run without requiring a long manually pasted prompt.
