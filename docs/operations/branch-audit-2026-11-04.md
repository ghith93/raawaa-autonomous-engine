# Branch audit — 2026-11-04

The repository carried **468 remote branches** on a history a few days old. A list nobody can read
is a list nobody checks, which makes abandoned work and live work look identical. This is the record
of what was removed and what was kept, so that the deletion is reversible from the SHAs below.

## Method

A branch was deleted only when **all three** held:

1. its head commit was the exact head of a pull request,
2. that pull request was merged into `main`, and
3. the branch's current tip still equalled the PR head — so a branch reused after its merge was kept.

Nothing unmerged was touched, `main` and the active session branch were protected, and every SHA was
recorded here before the first deletion. `scripts/audit_stale_branches.py` reproduces the classification.

## Result

| Bucket | Count | Meaning |
|---|---|---|
| deleted | 310 | tip is exactly a merged PR head |
| kept — active | 3 | `main`, the session branch, or an open PR |
| kept — diverged | 19 | merged PR exists but the branch moved afterwards |
| kept — unmerged | 136 | no merged PR on `main` |

Remote branches: **468 → 158**.

## Deleted (recoverable from these SHAs)

| Branch | PR | Tip SHA |
|---|---|---|
| `arena/09abed3b-raawaa-autonomous-engine` | #475 | `c282a46b044c` |
| `arena/308c7259-raawaa-autonomous-engine` | #480 | `e68477570446` |
| `arena/3e10e684-raawaa-autonomous-engine` | #478 | `35a75c9f6afc` |
| `arena/79c77aec-raawaa-autonomous-engine` | #470 | `facfe33f7639` |
| `arena/7f7405df-raawaa-autonomous-engine` | #481 | `2a8d10374ff9` |
| `arena/9331b504-raawaa-autonomous-engine` | #468 | `3fc449f0b80c` |
| `arena/ad31106f-raawaa-autonomous-engine` | #466 | `555193dc5555` |
| `arena/c207d329-raawaa-autonomous-engine` | #469 | `47348926f9ee` |
| `arena/c23fae66-raawaa-autonomous-engine` | #467 | `4e71bc288772` |
| `arena/df22d9d9-raawaa-autonomous-engine` | #479 | `f553d3a457fb` |
| `arena/f0c2a823-raawaa-autonomous-engine` | #471 | `c4cd2454ba16` |
| `bootstrap/phase-1-foundation` | #1 | `eb7926eda595` |
| `chore/dependabot-github-actions` | #368 | `0261018b3d7c` |
| `chore/remove-androidharness` | #232 | `6774df4d0a5b` |
| `ci/fallback-public-rpc-for-venue-smoke-2026-11-02` | #224 | `ba90073fe69a` |
| `diagnostics/authenticated-post-preflight` | #41 | `88fe51ceab34` |
| `diagnostics/cloudflare-access-inventory` | #67 | `9311a69186b1` |
| `diagnostics/cloudflare-worker-inventory` | #66 | `e71307ba0a32` |
| `diagnostics/compare-worker-health-and-auth` | #62 | `248ebf61d18a` |
| `docs/androidharness-3.1-current-device` | #131 | `db98bd66da30` |
| `docs/androidharness-task140-status` | #141 | `479d2829eadf` |
| `docs/arabic-resume-prompt-and-research-ops` | #97 | `6b7bd171eca9` |
| `docs/auth-post-preflight-operations` | #42 | `03893158261d` |
| `docs/clarify-cloudflare-service-auth-policy` | #65 | `8f2b55016e1c` |
| `docs/clarify-worker-cron-production-rollout` | #272 | `ed855d2eacd1` |
| `docs/cloudflare-billing-inventory-account-findings` | #331 | `a1904a92d91b` |
| `docs/coinbase-public-market-data-2026-11-02` | #211 | `f333b57f8cf6` |
| `docs/continuation-audit-2026-11-03-0412` | #344 | `afcc93100cfc` |
| `docs/continuous-improvement-charter` | #84 | `b0a2ba954284` |
| `docs/correct-aeon-skill-inventory` | #287 | `26be445bcd7d` |
| `docs/correct-manual-deploy-gates` | #278 | `416604b96b95` |
| `docs/correct-scheduler-evidence-readme` | #274 | `c01f2d6f7d1e` |
| `docs/current-implementation-status` | #16 | `80125ada9740` |
| `docs/current-operations-checkpoint-2026-11-03` | #425 | `384a2f439375` |
| `docs/current-provider-state-and-parallel-handoff` | #91 | `50a925fde2ff` |
| `docs/current-state-refresh-2026-11-03` | #399 | `ab769ec38c7c` |
| `docs/f2-completion-graphify` | #446 | `6aa0e0de55ce` |
| `docs/f5-staging-verification-record` | #458 | `b272c1066da7` |
| `docs/fix-task-runner-cadence-operations-2026-11-02` | #146 | `fb0002532ad9` |
| `docs/groq-free-account-verification` | #86 | `7e1f6a063fa3` |
| `docs/groq-qwen-preview-terms` | #89 | `026401bc64db` |
| `docs/hosting-assessment` | #20 | `6fab5d7e6851` |
| `docs/latest-ops-state-2244` | #441 | `0422028898fa` |
| `docs/link-provider-eligibility-review` | #81 | `4aa3f3d932ba` |
| `docs/mobile-only-arbitrage-ecosystem-2026-11-02` | #227 | `47319d2aec76` |
| `docs/operations-state-2026-11-03` | #400 | `eaff95cd6ece` |
| `docs/ops-revalidation-2200` | #433 | `7e6d880cb267` |
| `docs/ops-revalidation-2229` | #434 | `db0fb5402835` |
| `docs/permanent-operating-charter-v2` | #108 | `9518e5ec4d05` |
| `docs/provider-evidence-validation` | #49 | `8e9966681191` |
| `docs/quota-accounting-guardrails` | #46 | `1b138c313bc2` |
| `docs/reconcile-provider-readiness-2026-11-02` | #126 | `f8d7844fc634` |
| `docs/reconcile-worker-cron-and-telegram-deployment` | #286 | `8dac42f2170e` |
| `docs/record-androidharness-bridge-evidence` | #137 | `61e05fcdcf36` |
| `docs/record-post-deploy-task-runner-success-2026-11-03` | #269 | `a8ae5bedccc3` |
| `docs/record-production-d1-verification-2026-11-03` | #267 | `5f6df1cc400c` |
| `docs/record-runtime-policy-enforcement` | #110 | `1b8e6452349f` |
| `docs/record-staging-schema-readiness-2026-11-03` | #340 | `10cc02733ce8` |
| `docs/record-task-runner-post-deploy-verification-2026-11-03` | #268 | `f23fc34d3d60` |
| `docs/record-verified-production-rollout-state` | #279 | `d6b685b79488` |
| `docs/record-watchdog-activation-2026-11-03` | #333 | `207903d16803` |
| `docs/record-watchdog-opt-in-deployment-2026-11-03` | #309 | `23e2948b6daf` |
| `docs/record-watchdog-production-deployment` | #301 | `0fb0da072318` |
| `docs/record-watchdog-readiness-and-scheduler-blocker` | #315 | `4b437c004b06` |
| `docs/reflect-production-d1-migration-0005` | #276 | `3ce45fda1bd3` |
| `docs/refresh-androidharness-live-snapshot` | #136 | `56c3c125d8ba` |
| `docs/refresh-current-operating-status` | #63 | `e791c43361f4` |
| `docs/refresh-verified-runtime-status` | #80 | `350bdd2c7069` |
| `docs/research-development-operating-system` | #408 | `cb92c2fd0faf` |
| `docs/retire-device-runtime-guidance-2026-11-02` | #145 | `44c15870352f` |
| `docs/retire-harness-worker-skill-2026-11-02` | #147 | `6488a1fbdf69` |
| `docs/revalidate-engine-capability-and-staging-status-2026-11-03` | #337 | `bc482c3a287b` |
| `docs/runner-healthcheck-2234` | #436 | `ff7fff099db2` |
| `docs/v3-live-smoke-evidence-mainline` | #255 | `17c285050d82` |
| `docs/verify-worker-cron-heartbeat` | #285 | `f0297dcdab8e` |
| `docs/workflows-free-tier-billing-review-2026-11-02` | #219 | `51c2d3460b20` |
| `feat/agent-task-execution-contract` | #37 | `e1b08dbefa2f` |
| `feat/arena-agent-integration-contract` | #463 | `2edd3ca2b9b0` |
| `feat/atomic-monetary-budget-ledger` | #127 | `dbf4c172186f` |
| `feat/audit-staged-provider-registry` | #88 | `91a828232386` |
| `feat/audit-workers-dev-route` | #72 | `e9da1e86a45f` |
| `feat/bounded-parallel-task-batches-v2-2026-11-02` | #218 | `93437fa5b5bc` |
| `feat/cloudflare-audit-result-reporting` | #71 | `0b08368453f2` |
| `feat/codespace-execution-bridge-design` | #426 | `2653172a2e49` |
| `feat/codespace-task-isolation` | #427 | `504b68bfcc04` |
| `feat/codespaces-agent-workspace` | #387 | `e7c896cee705` |
| `feat/coding-agent-auth-and-runtime-readiness` | #403 | `dfa7408b0c83` |
| `feat/coding-agent-cli-readiness` | #385 | `b0a86e0869ad` |
| `feat/crossref-academic-discovery` | #99 | `1f18e9fa12ab` |
| `feat/d1-quota-ledger` | #134 | `00bf6704dff6` |
| `feat/diagnose-worker-edge-403` | #74 | `81decb703959` |
| `feat/durable-d1-monetary-ledger-issue-332` | #341 | `ba6e8542c323` |
| `feat/durable-task-step-invocations-v2` | #373 | `ea476461fcf7` |
| `feat/executive-intelligence-v1` | #428 | `806c2ca256e4` |
| `feat/explicit-live-runtime-opt-in` | #92 | `57615b1679ee` |
| `feat/extract-worker-edge-json-error` | #75 | `e700c1d64f0c` |
| `feat/f1-status-accuracy-v3` | #454 | `08e91e41a54e` |
| `feat/f5-dependency-priority-queue-v1` | #457 | `30b4e5973dcd` |
| `feat/groq-api-connection-check` | #83 | `f31081e86b31` |
| `feat/groq-free-inference-staging-smoke` | #351 | `0c311535873f` |
| `feat/integrate-monetary-ledger-live-gateway` | #128 | `ce05516e6e31` |
| `feat/jupiter-solana-cli-v2-2026-11-02` | #226 | `8913e05c9848` |
| `feat/kill-switch-audit-on-latest` | #59 | `e2f0a4b41db1` |
| `feat/live-inference-data-classification` | #96 | `7167b2a43f0e` |
| `feat/live-inference-kill-switch-audit` | #56 | `97ccd2fa1f31` |
| `feat/long-task-lease-heartbeats` | #50 | `a46454e99a14` |
| `feat/minute-window-quota-enforcement` | #85 | `550b075d24c1` |
| `feat/mobile-ci-and-snapshot-health` | #231 | `9f11ad110f7a` |
| `feat/mobile-dashboard-model-costs` | #235 | `5a0512f7afe5` |
| `feat/mobile-model-cost-telemetry` | #233 | `9470a455f658` |
| `feat/mobile-status-dashboard` | #230 | `a195997b5da9` |
| `feat/mobile-telegram-webhook` | #229 | `b6d75f5535f2` |
| `feat/monetary-status-cli` | #130 | `7a6a1a83d0e3` |
| `feat/monthly-provider-quota-caps` | #51 | `f21ce8fdf3c0` |
| `feat/no-lookahead-paper-observer-v4-2026-11-02` | #225 | `ef41cf0cf14c` |
| `feat/one-shot-free-research-run` | #429 | `7562b036d6ac` |
| `feat/per-task-monetary-caps` | #129 | `2337794d431d` |
| `feat/per-task-provider-quota-caps` | #57 | `a5e1ed4dd1d2` |
| `feat/provider-adapter-routing` | #349 | `6bb4cada743b` |
| `feat/provider-profile-registry` | #87 | `a01113df8f51` |
| `feat/quota-monitoring-status` | #94 | `e2bca59ea436` |
| `feat/read-only-brain-cycle-v1` | #474 | `8efa9a8b163e` |
| `feat/register-google-artemis-integration` | #424 | `57e3b1999f59` |
| `feat/report-task-runner-health-and-batch` | #77 | `d92bacdc1337` |
| `feat/report-worker-cron-heartbeat-in-runner` | #283 | `f363040c07a2` |
| `feat/report-workers-dev-route-status` | #73 | `6786ed318476` |
| `feat/research-candidate-triage` | #107 | `48eeba5d4b38` |
| `feat/research-d1-readback-verification` | #105 | `e234f0cc0c68` |
| `feat/research-discovery-e2e-trigger` | #102 | `2d1d91181621` |
| `feat/research-scan-telemetry-mainline` | #396 | `c3b766365732` |
| `feat/research-to-proposal-converter` | #423 | `efef94b431fa` |
| `feat/scheduled-github-research-discovery-v2` | #95 | `bc38db678dec` |
| `feat/skill-gate-synthetic-benchmark` | #422 | `ec50b5b4b7d3` |
| `feat/skill-static-preflight-gate` | #420 | `54f5e76f9a25` |
| `feat/staging-invocation-lifecycle-verification` | #375 | `aad9b8ac6708` |
| `feat/staging-ops-telemetry` | #358 | `c9d4692ba612` |
| `feat/telegram-schema-status-probe` | #270 | `bb69fd0e7a9d` |
| `feat/trusted-skills-registry-v1` | #372 | `955a6d70f3ca` |
| `feat/v3-exact-tick-math` | #234 | `18f0314d264f` |
| `feat/v3-multi-tick-simulator` | #236 | `0ba2f2f9b734` |
| `feat/v3-pinned-rpc-snapshot` | #237 | `acceed07648e` |
| `feat/verified-provider-profile-evidence` | #47 | `0c098beea8c7` |
| `feat/wire-durable-invocations-into-runner` | #377 | `a3f7c0515e07` |
| `feat/worker-cron-heartbeat-observability` | #271 | `e4d2adddca9c` |
| `feature/android-harness-runtime-profile` | #115 | `8d729526b8f2` |
| `feature/androidharness-3-2-workspace-infrastructure` | #118 | `4d0f564452c2` |
| `feature/androidharness-github-task-bridge-v2` | #121 | `7b5ac26f4c86` |
| `feature/androidharness-workmanager-automation` | #124 | `d584c02d138e` |
| `feature/append-only-paper-trading-ledger-2026-11-02` | #162 | `7c933891489e` |
| `feature/atomic-v2-flash-swap-harness-2026-11-02` | #155 | `e7e48fd70ba0` |
| `feature/authenticated-checkpoint-envelope` | #116 | `7b531262dab0` |
| `feature/automated-worker-deploy` | #27 | `d1cf71080283` |
| `feature/automatic-d1-migrations` | #26 | `64ff8cb46cfe` |
| `feature/binance-public-market-adapter-current-main-2026-11-02` | #193 | `236eb490cdcd` |
| `feature/bounded-batch-runner` | #19 | `39c53e7e8458` |
| `feature/bsc-v2-multichain-scanner-2026-11-02` | #204 | `71605c0922a2` |
| `feature/bybit-readonly-market-adapter-final-rebased-2026-11-02` | #189 | `073866880cbe` |
| `feature/cex-cross-venue-depth-scanner-exact-pnl` | #202 | `97cc2a1617e0` |
| `feature/cex-instrument-metadata-current-main-2026-11-02` | #203 | `5f60d267d2c1` |
| `feature/cex-order-minimum-enforcement-main-2026-11-02` | #208 | `b4cf5aedbe2a` |
| `feature/cex-readonly-cli-mainline` | #256 | `6542c51a226d` |
| `feature/cex-universe-scan-mainline-2` | #262 | `4c89233c75ad` |
| `feature/chain-aware-v2-venue-defaults-2026-11-02` | #209 | `bab77a248abe` |
| `feature/checkpoint-envelope-contract` | #112 | `c3b48461ef60` |
| `feature/cloudflare-d1-foundation` | #21 | `3073735b477f` |
| `feature/cloudflare-workflows-durable-spike-ci-fix` | #125 | `a305e2457cde` |
| `feature/conservative-opportunity-cost-model-current-main-2026-11-02` | #195 | `02b863fc3769` |
| `feature/cross-venue-flash-swap-math-2026-11-02` | #152 | `f29d0e62be9d` |
| `feature/d1-control-worker` | #24 | `1bc2b8a5ed37` |
| `feature/d1-lease-recovery-cron` | #30 | `1eb441e5e6ea` |
| `feature/d1-ledger-flash-swap-wiring-2026-11-02` | #171 | `07811abd17a8` |
| `feature/d1-paper-ledger-2026-11-02` | #167 | `54c240ba41a0` |
| `feature/d1-task-lifecycle-api` | #28 | `c20ab71dc723` |
| `feature/file-backed-checkpoint-restart-eval` | #117 | `618647c43560` |
| `feature/flash-swap-cli-paper-capture-2026-11-02` | #166 | `dbd84ca4bc0a` |
| `feature/framework-checkpoint-storage-eval` | #113 | `dd8d7ae6f62a` |
| `feature/framework-checkpoint-workflow-resume-eval` | #114 | `c7876de0bf4b` |
| `feature/jupiter-price-v3-read-only-2026-11-02` | #205 | `2e96a0596232` |
| `feature/jupiter-solana-quote-only-adapter-2026-11-02` | #184 | `ee5d57793746` |
| `feature/kraken-public-spot-market-data-2026-11-02` | #206 | `254a436cdda7` |
| `feature/live-multichain-venue-verification-2026-11-02` | #212 | `ba50d010e2e5` |
| `feature/live-readonly-venue-verification-clean-2026-11-02` | #151 | `8e65c9febd58` |
| `feature/manual-d1-migration-workflow` | #25 | `ff29bb29ffc7` |
| `feature/mobile-telegram-schema-observability-2026-11-03` | #265 | `41e01a3ed38b` |
| `feature/multi-venue-cex-comparison-2026-11-02` | #214 | `edd6e30aaaf0` |
| `feature/okx-public-spot-market-data-2026-11-02` | #215 | `08ac440d9d1e` |
| `feature/onchain-v2-candidate-scan-rebased-2026-11-02` | #185 | `0e3371fc48cb` |
| `feature/persist-policy-provenance-per-task` | #111 | `8ef0b56594ba` |
| `feature/pinned-ethereum-v2-fork-smoke-2026-11-02` | #157 | `f4a742a270d1` |
| `feature/python-d1-task-store` | #29 | `e40c4731be22` |
| `feature/quota-gated-worker-execution` | #61 | `9ea3b54ba250` |
| `feature/read-only-perpetual-funding-history-2026-11-02` | #213 | `c38645b8b1e1` |
| `feature/runtime-operating-policy-context` | #109 | `398a6e13b885` |
| `feature/scheduled-d1-task-runner` | #31 | `57a4356c2b74` |
| `feature/side-effect-operation-ledger` | #18 | `848c5fa28d66` |
| `feature/telegram-arbitrage-engine-clean-base` | #149 | `b878685e3602` |
| `feature/v2-flash-swap-gas-economics-2026-11-02` | #159 | `2d31dee2d990` |
| `feature/verified-v2-venue-registry-2026-11-02` | #150 | `49ac4f6b45d0` |
| `fix/accept-created-research-task` | #430 | `a5aff71e969f` |
| `fix/accurate-provider-cost-accounting` | #290 | `69f27de2b9e4` |
| `fix/androidharness-3.1-backend-status` | #132 | `154ac32d69dd` |
| `fix/androidharness-doctor-ci-timeout` | #138 | `1e35ae53210d` |
| `fix/androidharness-git-fetch-retries` | #139 | `50547aa12a8a` |
| `fix/avoid-duplicate-pr-ci-2026-11-02` | #143 | `93fd2869ed95` |
| `fix/bound-all-research-responses` | #100 | `22d9563d49c3` |
| `fix/capability-gap-runner-current-main` | #416 | `573fbd229a15` |
| `fix/cloudflare-access-audit-diagnostics` | #70 | `260f35c86d09` |
| `fix/cloudflare-access-audit-optional-not-enabled` | #327 | `d29ef59e5312` |
| `fix/cloudflare-access-service-token-support` | #64 | `18d8f589a08b` |
| `fix/cloudflare-billing-inventory-diagnose-endpoints` | #330 | `f1d6a45a4094` |
| `fix/cross-venue-quote-synchronization-guard-2026-11-02` | #220 | `a2f352bac94f` |
| `fix/d1-http-error-redaction` | #22 | `e17b8bbceec0` |
| `fix/d1-ledger-redirect-security-rebased-2026-11-02` | #188 | `27cc536119f5` |
| `fix/d1-quota-client-admin-scope` | #392 | `db1a4997f307` |
| `fix/f2-capacity-preflight` | #445 | `ef56f398258d` |
| `fix/groq-403-classification-clean` | #367 | `2746a6b3f674` |
| `fix/groq-403-permission-and-user-agent-v2` | #356 | `c4b8cfa059e0` |
| `fix/groq-free-20b-only-smoke` | #370 | `f87cbc6bea5f` |
| `fix/groq-free-staging-smoke-auth` | #352 | `4fdc17d02aed` |
| `fix/groq-model-catalog-diagnostic` | #353 | `c8f14d3b7d37` |
| `fix/groq-reconciliation-token-readiness` | #371 | `0889776a05b9` |
| `fix/hard-zero-cost-provider-eligibility` | #82 | `3e23f0b82556` |
| `fix/mobile-status-404-nonblocking` | #260 | `afe37a220c69` |
| `fix/per-task-tests-kill-switch` | #58 | `3fd1ae9f1e74` |
| `fix/preserve-cloudflare-dashboard-vars` | #432 | `2d3bdf2273c4` |
| `fix/provider-error-code-audit` | #354 | `e41e492e1231` |
| `fix/provider-not-called-backoff` | #444 | `1413c37bc89c` |
| `fix/quota-body-stream-limit` | #384 | `d81d3cc88a7b` |
| `fix/quota-cooldown-mainline-v2` | #395 | `f285227ec58d` |
| `fix/quota-gateway-conservative-reservations` | #35 | `3ba1137efff9` |
| `fix/quota-gateway-enforce-response-token-ceilings` | #36 | `6b7b02a0ac39` |
| `fix/quota-settlement-idempotency` | #44 | `851d21561ea6` |
| `fix/quota-task-usage-route-scope` | #360 | `86852ad2cac0` |
| `fix/reactivate-task-runner-schedule` | #55 | `794e10766d96` |
| `fix/reject-mocked-live-completions` | #45 | `da92e0d35317` |
| `fix/reject-placeholder-hosts-v2` | #53 | `b0ddb0022ba6` |
| `fix/remote-403-diagnostics` | #38 | `b12c1b3aada0` |
| `fix/remote-task-transport-resilience` | #33 | `accc8c6b8049` |
| `fix/research-telemetry-staging-credentials` | #397 | `341209fd3a5f` |
| `fix/research-triage-confidence-gates` | #275 | `754d94c61221` |
| `fix/staging-mobile-token-propagation` | #369 | `4a503f2046da` |
| `fix/staging-monetary-auth-propagation` | #388 | `58ce630795d1` |
| `fix/staging-ops-telemetry-import-path` | #362 | `a60c95d03b81` |
| `fix/staging-telemetry-manual-event-v2` | #456 | `ccecd7931a71` |
| `fix/staging-token-cleanup` | #348 | `a6a8baf77018` |
| `fix/stop-provider-route-rotation-v2` | #60 | `3e529457b50f` |
| `fix/strict-provider-profile-validation` | #48 | `19546d77b38e` |
| `fix/task-lease-ownership` | #17 | `a82aa5e97fb5` |
| `fix/task-runner-cron-reactivation-20261103` | #439 | `c8201506d787` |
| `fix/task-runner-heredoc-and-runtime-status` | #282 | `805475d87a57` |
| `fix/task-runner-output-newlines` | #431 | `f8fd44cbc38a` |
| `fix/trigger-task-runner-after-workflow-updates` | #78 | `a325102e00fa` |
| `fix/v3-rpc-provider-fallback` | #241 | `52d4b43f46a4` |
| `fix/v3-smoke-rpc-final3` | #249 | `921b4066cc01` |
| `fix/workers-dev-descriptive-user-agent` | #76 | `af46de1be052` |
| `fix/zero-per-task-budget-gate-2026-11-02` | #216 | `cb36dd421a32` |
| `improvement/cloud-runtime-no-device-dependency-2026-11-02` | #142 | `7b4bf98e4cd3` |
| `improvement/five-minute-cloud-task-runner-2026-11-02` | #144 | `8700d26c6809` |
| `infra/auto-apply-reviewed-d1-migrations-to-staging-2026-11-03` | #338 | `77fa78711fb5` |
| `infra/ci-gated-staging-migrations-2026-11-03` | #339 | `523267746d31` |
| `infra/d1-staging-readonly-bootstrap-2026-11-03` | #335 | `4f4b0329ab0b` |
| `integration/engine-v1-consolidated` | #15 | `bf8f735b06f4` |
| `ops/auto-verify-d1-staging-on-main-2026-11-03` | #336 | `64783bb4c8a4` |
| `ops/cex-public-market-smoke` | #263 | `3564fab2b4a3` |
| `ops/cloudflare-access-audit-token` | #68 | `c0dc038a231b` |
| `ops/d1-readonly-migration-inventory` | #266 | `c4110d717773` |
| `ops/expose-watchdog-readiness-in-mobile-status` | #310 | `4268453034ef` |
| `ops/manual-staging-replay-v3` | #452 | `2c296cc4c772` |
| `ops/mobile-status-probe` | #258 | `85f4bd754420` |
| `ops/publish-watchdog-readiness-in-task-runner-2026-11-03` | #312 | `6ae9931f3729` |
| `ops/remove-tinyfish-add-zero-cost-dispatch` | #281 | `19791064a9d5` |
| `ops/revalidate-assets-20261104-0051` | #460 | `2a569abebb36` |
| `ops/stagger-task-runner-schedule` | #39 | `94be351f401d` |
| `ops/trigger-access-audit` | #69 | `1d9927216e7b` |
| `ops/workers-ai-inventory-trigger` | #402 | `ce356c4be5aa` |
| `perf/v3-read-only-path-tick-filter` | #250 | `1ef83382e025` |
| `policy/monthly-budget-15-hybrid-hard-caps-public-safe` | #329 | `5b9458528a23` |
| `research/agent-runtime-and-skill-governance-2026-11-03` | #320 | `d8bebce42924` |
| `research/agent-runtime-evaluation-2026-11-03` | #334 | `437e0f8a3a4a` |
| `research/broaden-skill-and-eval-search-queries` | #323 | `c13ffb63837d` |
| `research/cloudflare-agents-sdk-runtime-evaluation` | #277 | `51fbb0b6e500` |
| `research/consolidated-agent-runtime-platform-review-2026-11-03` | #289 | `adf7c7dc6fb2` |
| `research/daily-discovery-review-2026-11-03` | #304 | `80f1def51bbc` |
| `research/evaluate-microsoft-agent-framework` | #106 | `a2fd9d24660f` |
| `research/expand-skills-security-and-agent-evaluation-discovery` | #322 | `2359505a3ab0` |
| `research/mev-arbitrage-empirical-evidence-2026-11-02` | #163 | `547e2433ec41` |
| `research/multi-market-coverage-rebased-2026-11-02` | #187 | `59fdcaeaecd7` |
| `research/refresh-agent-runtime-releases-2026-11-03` | #321 | `35923a066eec` |
| `research/target-skill-scanners-and-benchmarks` | #325 | `930e06db08d5` |
| `research/telegram-arbitrage-ecosystem-2026-11-02` | #160 | `809943b2e283` |
| `research/triage-fresh-agent-runtime-candidates` | #280 | `8ddb4fddb759` |
| `safety/explicit-watchdog-opt-in-2026-11-03` | #305 | `56037b199484` |
| `safety/retain-quota-on-uncertain-failure` | #40 | `fd7135151cef` |
| `security/codespace-release-gate` | #442 | `d6c16083a89e` |
| `security/guard-secret-workflow-dispatch-main` | #406 | `18eb6a6a3c9a` |
| `security/lock-live-inference-to-research-brief` | #295 | `c84af8bce789` |
| `security/pin-github-actions-by-verified-sha` | #361 | `c3388d71ce46` |
| `security/server-owned-quota-policy-scopes` | #389 | `f5c63f62f35f` |
| `security/telegram-private-sender-binding-2026-11-03` | #264 | `b346a05b19b5` |
| `security/tool-broker-after-rd-merge` | #414 | `a1a5918cc734` |
| `security/trusted-main-only-codespace-bridge` | #459 | `3a0b8a033bea` |
| `test/d1-local-worker-integration` | #252 | `1c1dc3fb60e0` |
| `test/d1-migration-contract` | #23 | `21067efff888` |
| `test/d1-migration-sqlite-contract` | #32 | `ebded7aa9f5b` |
| `test/d1-one-time-end-to-end-smoke` | #79 | `1bb5880044bc` |
| `test/d1-quota-sql-concurrency-2026-11-02` | #148 | `2d854ecb7042` |
| `test/quota-concurrent-reservations` | #43 | `34720d50640e` |
| `test/staging-mobile-status-readback` | #364 | `96eaaf9de0d9` |
| `test/telegram-mobile-contracts-mainline` | #254 | `1d3c046acad5` |
| `test/v3-core-parity-fixtures` | #239 | `99682b1b895a` |

## Kept because the branch moved after its merge (not deleted)

- `arena/229d730a-raawaa-autonomous-engine` — advanced past merged PR #472 head a5fe85385fd8
- `arena/7e6a38fa-raawaa-autonomous-engine` — advanced past merged PR #484 head 328f7eac471d
- `arena/84692145-raawaa-autonomous-engine` — advanced past merged PR #482 head 133238602616
- `arena/b8e34eb1-raawaa-autonomous-engine` — advanced past merged PR #476 head 94914c562847
- `docs/capability-revalidation-2233` — advanced past merged PR #435 head 13ee609bc4d0
- `feat/arxiv-discovery-current-main` — advanced past merged PR #417 head 047cbcab8a66
- `feat/cloudflare-github-app-task-runner-watchdog` — advanced past merged PR #291 head 7348e1b777af
- `feat/deterministic-capability-gap-analysis` — advanced past merged PR #409 head 297433b71050
- `feat/groq-production-model-profiles` — advanced past merged PR #90 head 4ecbd087cc47
- `feat/optional-searxng-discovery-adapter` — advanced past merged PR #418 head acc516d02a80
- `feat/persistent-research-memory` — advanced past merged PR #101 head edffbfa79304
- `feat/task-execution-mode-v2` — advanced past merged PR #448 head 21dc02803845
- `feat/v3-live-readonly-smoke` — advanced past merged PR #238 head 551999afa6c8
- `ops/deploy-watchdog-readiness-mobile-panel` — advanced past merged PR #311 head 0f9681987ce5
- `ops/workers-ai-readonly-inventory` — advanced past merged PR #401 head 7ae803ad75e7
- `research/review-additional-agent-platforms-2026-11-03` — advanced past merged PR #284 head 2696349911c1
- `security/contextual-sensitive-text-guard-v3-2026-11-03` — advanced past merged PR #308 head 916f312790cf
- `security/public-inference-free-text-guard` — advanced past merged PR #300 head 2e410e467f21
- `security/strict-public-live-inference-policy` — advanced past merged PR #292 head a151bd7dfb01

Restoring a deleted branch:

```
git push origin <sha>:refs/heads/<branch>
```
