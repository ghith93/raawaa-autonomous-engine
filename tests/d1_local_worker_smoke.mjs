import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const PORT = 8792;
const BASE = "http://127.0.0.1:" + PORT;
const TOKEN = "test-engine-token-local-only";
const CODESPACE_TOKEN = "test-codespace-token-0123456789abcdef0123456789abcdef";
const WRANGLER = ["--yes", "wrangler@4.156.1"];
const root = mkdtempSync(join(tmpdir(), "raawaa-d1-local-"));
const stateDir = join(root, "state");
const homeDir = join(root, "home");
mkdirSync(stateDir, { recursive: true });
mkdirSync(homeDir, { recursive: true });

function safeEnvironment() {
  // Deliberately omit Cloudflare account credentials and isolate Wrangler's HOME.
  // npm's package cache may be reused, but no account credentials are inherited.
  return {
    PATH: process.env.PATH || "",
    HOME: homeDir,
    npm_config_cache: join(process.env.HOME || tmpdir(), ".npm"),
    CI: "1",
    NO_UPDATE_NOTIFIER: "1",
    NO_D1_WARNING: "1",
    WRANGLER_SEND_METRICS: "false",
  };
}

function runMigrations() {
  const result = spawnSync("npx", [
    ...WRANGLER, "d1", "migrations", "apply", "raawaa-autonomous-e",
    "--local", "--config", "wrangler.toml", "--persist-to", stateDir,
  ], {
    cwd: process.cwd(),
    env: safeEnvironment(),
    encoding: "utf8",
    timeout: 120_000,
    maxBuffer: 2 * 1024 * 1024,
  });
  if (result.error || result.status !== 0) {
    throw new Error(
      "Local D1 migrations failed. The command is explicitly --local and has no Cloudflare credentials.\n" +
      (result.stdout || "") + "\n" + (result.stderr || "") +
      (result.error ? "\n" + result.error.message : ""),
    );
  }
  console.log("Local D1 migrations applied successfully.");
}

function executeLocalSql(sql) {
  const result = spawnSync("npx", [
    ...WRANGLER, "d1", "execute", "raawaa-autonomous-e",
    "--local", "--command", sql, "--config", "wrangler.toml",
    "--persist-to", stateDir,
  ], {
    cwd: process.cwd(),
    env: safeEnvironment(),
    encoding: "utf8",
    timeout: 30_000,
    maxBuffer: 1024 * 1024,
  });
  if (result.error || result.status !== 0) {
    throw new Error(
      "Local-only D1 policy setup failed. No Cloudflare credentials are available.\n" +
      (result.stdout || "") + "\n" + (result.stderr || "") +
      (result.error ? "\n" + result.error.message : ""),
    );
  }
}

async function api(path, { method = "GET", body, token = TOKEN } = {}) {
  const headers = { accept: "application/json" };
  if (token) headers.authorization = "Bearer " + token;
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await fetch(BASE + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(10_000),
  });
  const raw = await response.text();
  let payload;
  try { payload = raw ? JSON.parse(raw) : null; }
  catch { throw new Error("Worker returned non-JSON HTTP " + response.status + ": " + raw.slice(0, 500)); }
  return { status: response.status, payload };
}

async function waitForWorker(child, getLogs, timeoutMs = 25_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error("Wrangler exited before readiness:\n" + getLogs());
    try {
      const result = await api("/health", { token: null });
      if (result.status === 200 && result.payload?.ok === true) return;
    } catch {
      // Workerd is still compiling or binding the local D1 database.
    }
    await delay(250);
  }
  throw new Error("Local Worker did not become ready:\n" + getLogs());
}

function assertStatus(result, expected, label) {
  assert.equal(result.status, expected, label + ": " + JSON.stringify(result.payload));
}

function launchWorker() {
  const child = spawn("npx", [
    ...WRANGLER, "dev",
    "--config", "wrangler.toml",
    "--local",
    "--ip", "127.0.0.1",
    "--port", String(PORT),
    "--log-level", "error",
    "--persist-to", stateDir,
    "--var", "ENGINE_API_TOKEN:" + TOKEN,
    "--var", "ENGINE_CODESPACE_TOKEN:" + CODESPACE_TOKEN,
    "--var", "QUOTA_ROUTE_LIMITS_JSON:" + JSON.stringify({
      "d1-local:concurrency": {
        enabled: true,
        minute_request_limit: 2, minute_token_limit: 20,
        daily_request_limit: 10, daily_token_limit: 100,
        monthly_request_limit: 20, monthly_token_limit: 500,
        per_task_request_limit: 3, per_task_token_limit: 50,
      },
    }),
  ], {
    cwd: process.cwd(),
    env: safeEnvironment(),
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32",
  });
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", chunk => { logs = (logs + chunk).slice(-12_000); });
  child.stderr.on("data", chunk => { logs = (logs + chunk).slice(-12_000); });
  return child;
}

async function stopWorker(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform !== "win32" && child.pid) {
    try { process.kill(-child.pid, "SIGTERM"); } catch { /* already exited */ }
  } else {
    child.kill("SIGTERM");
  }
  await Promise.race([
    new Promise(resolve => child.once("exit", resolve)),
    delay(5000),
  ]);
  if (child.exitCode === null && child.signalCode === null) {
    if (process.platform !== "win32" && child.pid) {
      try { process.kill(-child.pid, "SIGKILL"); } catch { /* already exited */ }
    } else {
      child.kill("SIGKILL");
    }
  }
}

let server = null;
let logs = "";

try {
  runMigrations();

  server = launchWorker();

  await waitForWorker(server, () => logs);

  const unauthenticated = await api("/v1/mobile/status", { token: null });
  assertStatus(unauthenticated, 401, "mobile status must require bearer auth");
  const unauthenticatedMonetary = await api("/v1/monetary-ledger", { token: null });
  assertStatus(unauthenticatedMonetary, 401, "monetary ledger status requires authentication");

  let status = await api("/v1/mobile/status");
  assertStatus(status, 200, "mobile status must query migrated D1");
  assert.equal(status.payload.unavailable_metrics.paper_ledger, "Available");
  assert.equal(status.payload.unavailable_metrics.provider_usage, "Available");
  assert.equal(status.payload.execution_modes.available, true);
  assert.equal(status.payload.execution_modes.counts.unknown, 0);

  // Verify the widened source CHECK constraint and real Worker normalization after local migrations.
  const researchIngest = await api("/v1/research/candidates", {
    method: "POST",
    body: { candidates: [
      { source: "arxiv", arxiv_id: "2601.01234v2", title: "Local arXiv schema check",
        url: "https://arxiv.org/abs/2601.01234v2", abstract_retrieved: false, full_text_retrieved: false },
      { source: "searxng", title: "Local SearXNG schema check",
        url: "https://docs.example.org/agent-security?source=searxng", snippet: "metadata only" },
    ] },
  });
  assertStatus(researchIngest, 201, "arXiv and SearXNG research ingestion");
  assert.equal(researchIngest.payload.accepted_count, 2);
  const researchReadback = await api("/v1/research/candidates?status=all&limit=100&keys_only=true");
  assertStatus(researchReadback, 200, "multi-source research readback");
  const persistedSources = new Set(researchReadback.payload.candidates.map(item => item.source_key));
  assert.ok(persistedSources.has("arxiv:2601.01234v2"));
  assert.ok(persistedSources.has("searxng:https://docs.example.org/agent-security?source=searxng"));

  // --- Atomic workflow dispatch against the real D1 -------------------------
  const dispatchBody = {
    run_id: "smoke-run-1",
    workflow_id: "smoke-workflow",
    plan_digest: "a".repeat(64),
    plan_version: 1,
    steps: [
      { step_id: "prepare", task_type: "system_audit", title: "workflow prepare", payload: { step: 1 } },
      { step_id: "verify", task_type: "system_audit", title: "workflow verify", payload: { step: 2 },
        depends_on: ["prepare"] },
    ],
  };
  const dispatched = await api("/v1/workflows/dispatch", { method: "POST", body: dispatchBody });
  assertStatus(dispatched, 201, "workflow dispatch");
  assert.equal(dispatched.payload.run.status, "dispatched");
  assert.equal(dispatched.payload.run.workflow_id, "smoke-workflow");
  assert.equal(dispatched.payload.run.step_count, 2);
  assert.deepEqual(dispatched.payload.run.entry_step_ids, ["prepare"]);

  const afterDispatch = await api("/v1/tasks?limit=50");
  assertStatus(afterDispatch, 200, "task listing after dispatch");
  const workflowTasks = afterDispatch.payload.tasks.filter(task => task.id.startsWith("wf-smoke-run-1-"));
  assert.equal(workflowTasks.length, 2, "both steps must be enqueued");
  const dependent = workflowTasks.find(task => task.id === "wf-smoke-run-1-verify");
  assert.deepEqual(JSON.parse(dependent.dependencies_json), ["wf-smoke-run-1-prepare"]);

  // A replay with the same run id and digest is a no-op, not a duplicate.
  const replayed = await api("/v1/workflows/dispatch", { method: "POST", body: dispatchBody });
  assertStatus(replayed, 200, "workflow dispatch replay");
  assert.equal(replayed.payload.created, false);
  const afterReplay = await api("/v1/tasks?limit=50");
  assert.equal(
    afterReplay.payload.tasks.filter(task => task.id.startsWith("wf-smoke-run-1-")).length,
    2,
    "a replay must not duplicate steps",
  );

  // Reusing the run id for a different plan is refused rather than silently applied.
  const conflicting = await api("/v1/workflows/dispatch", {
    method: "POST",
    body: { ...dispatchBody, plan_digest: "b".repeat(64) },
  });
  assertStatus(conflicting, 409, "workflow dispatch digest conflict");
  assert.equal(conflicting.payload.error, "run_digest_conflict");

  // Reconciliation: the run can be read back and names its tasks, without writing.
  const reconciled = await api("/v1/workflows/smoke-run-1");
  assertStatus(reconciled, 200, "workflow run reconciliation");
  assert.equal(reconciled.payload.complete, true);
  assert.deepEqual(reconciled.payload.steps.map(step => step.step_id).sort(), ["prepare", "verify"]);
  assert.equal(reconciled.payload.run.step_count, 2);
  assert.match(reconciled.payload.run.steps_digest, /^[0-9a-f]{64}$/);

  const unknownRun = await api("/v1/workflows/never-dispatched");
  assertStatus(unknownRun, 404, "unknown workflow run");

  // A step type outside the manifest's allowlist is refused by the Worker itself.
  const badType = await api("/v1/workflows/dispatch", {
    method: "POST",
    body: {
      run_id: "smoke-run-2",
      workflow_id: "smoke-workflow",
      plan_digest: "d".repeat(64),
      plan_version: 1,
      steps: [{ step_id: "x", task_type: "not_a_real_type", title: "x" }],
    },
  });
  assertStatus(badType, 400, "unsupported step task type");
  assert.equal(badType.payload.error, "unsupported_step_task_type");

  // Real concurrent dispatch, not a simulated one: two requests for the same run
  // fired together against the same D1. Exactly one may create the run, and the
  // queue must end up with two tasks, not four.
  const concurrentBody = {
    run_id: "smoke-run-3",
    workflow_id: "smoke-workflow",
    plan_digest: "e".repeat(64),
    plan_version: 1,
    steps: [
      { step_id: "alpha", task_type: "system_audit", title: "concurrent alpha" },
      { step_id: "beta", task_type: "system_audit", title: "concurrent beta", depends_on: ["alpha"] },
    ],
  };
  const [firstDispatch, secondDispatch] = await Promise.all([
    api("/v1/workflows/dispatch", { method: "POST", body: concurrentBody }),
    api("/v1/workflows/dispatch", { method: "POST", body: concurrentBody }),
  ]);
  assert.deepEqual(
    [firstDispatch.status, secondDispatch.status].sort(), [200, 201],
    "one dispatch creates the run, the other converges on it",
  );
  assert.equal([firstDispatch.payload.created, secondDispatch.payload.created].filter(Boolean).length, 1);
  const afterConcurrent = await api("/v1/tasks?limit=100");
  assert.equal(
    afterConcurrent.payload.tasks.filter(task => task.id.startsWith("wf-smoke-run-3-")).length, 2,
    "a concurrent dispatch must not duplicate steps",
  );

  // Dependency gating, demonstrated rather than assumed: the entry step is
  // claimable, and the dependent step becomes claimable only after it succeeds.
  const prepareClaim = await api("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } });
  assertStatus(prepareClaim, 200, "claim entry step");
  assert.equal(prepareClaim.payload.task.id, "wf-smoke-run-1-prepare");
  await api(`/v1/tasks/${prepareClaim.payload.task.id}/finish`, {
    method: "POST",
    body: { lease_token: prepareClaim.payload.lease_token, status: "succeeded",
      result: { mode: "dry_run", step: "prepare" } },
  });
  const verifyClaim = await api("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } });
  assertStatus(verifyClaim, 200, "claim dependent step after its parent succeeded");
  assert.equal(verifyClaim.payload.task.id, "wf-smoke-run-1-verify");
  await api(`/v1/tasks/${verifyClaim.payload.task.id}/finish`, {
    method: "POST",
    body: { lease_token: verifyClaim.payload.lease_token, status: "succeeded",
      result: { mode: "dry_run", step: "verify" } },
  });

  // Drain the concurrently dispatched run in dependency order, so the rest of
  // this smoke starts from an empty queue.
  for (const expectedStep of ["alpha", "beta"]) {
    const claimedStep = await api("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } });
    assertStatus(claimedStep, 200, `claim ${expectedStep}`);
    assert.equal(claimedStep.payload.task.id, `wf-smoke-run-3-${expectedStep}`);
    await api(`/v1/tasks/${claimedStep.payload.task.id}/finish`, {
      method: "POST",
      body: { lease_token: claimedStep.payload.lease_token, status: "succeeded",
        result: { mode: "dry_run", step: expectedStep } },
    });
  }

  const taskPayload = {
    task_type: "system_audit",
    title: "Local D1 integration smoke",
    payload: { purpose: "verify real local D1 persistence", external_writes: false },
    idempotency_key: "d1-local-integration-smoke",
    max_attempts: 1,
  };
  const enqueued = await api("/v1/tasks/enqueue", { method: "POST", body: taskPayload });
  assertStatus(enqueued, 201, "task enqueue");
  const duplicate = await api("/v1/tasks/enqueue", { method: "POST", body: taskPayload });
  assertStatus(duplicate, 200, "idempotent task replay");
  assert.equal(duplicate.payload.task.id, enqueued.payload.task.id);
  assert.equal(duplicate.payload.created, false);
  assert.equal(enqueued.payload.task.execution_mode, "not_started");

  // Captured before the claim: claiming is what sets execution_mode, so the
  // aggregate must be read before this task joins the dry_run count. Asserted as
  // a delta rather than an absolute, because the workflow block above already
  // executed four dry-run steps.
  const beforeClaim = await api("/v1/mobile/status");
  assertStatus(beforeClaim, 200, "mobile status before claim");
  const dryRunBefore = beforeClaim.payload.execution_modes.counts.dry_run;

  const claimed = await api("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run" },
  });
  assertStatus(claimed, 200, "task claim");
  assert.equal(claimed.payload.task.id, enqueued.payload.task.id);
  assert.equal(typeof claimed.payload.lease_token, "string");
  assert.equal(claimed.payload.task.execution_mode, "dry_run");

  const finished = await api("/v1/tasks/" + enqueued.payload.task.id + "/finish", {
    method: "POST",
    body: {
      lease_token: claimed.payload.lease_token,
      status: "succeeded",
      result: { mode: "dry_run", local_d1_verified: true },
    },
  });
  assertStatus(finished, 200, "task finish");
  assert.equal(finished.payload.status, "succeeded");
  const taskReadback = await api("/v1/tasks?limit=10");
  assertStatus(taskReadback, 200, "execution mode task readback");
  const persistedTask = taskReadback.payload.tasks.find(task => task.id === enqueued.payload.task.id);
  assert.equal(persistedTask.execution_mode, "dry_run");
  status = await api("/v1/mobile/status");
  assertStatus(status, 200, "mobile execution-mode aggregate");
  assert.equal(status.payload.execution_modes.counts.dry_run, dryRunBefore + 1);

  const staleLease = await api("/v1/tasks/" + enqueued.payload.task.id + "/heartbeat", {
    method: "POST",
    body: { lease_token: claimed.payload.lease_token },
  });
  assertStatus(staleLease, 409, "finished task must reject stale lease");

  // Priority and dependency eligibility are enforced by the real local D1 queue.
  const parentTask = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "research_brief", title: "DAG parent", payload: { step: "parent" },
      idempotency_key: "d1-local-dag-parent-" + randomUUID(), priority: 10, max_attempts: 1,
    },
  });
  assertStatus(parentTask, 201, "DAG parent enqueue");
  const childTask = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "content_draft", title: "DAG child", payload: { step: "child" },
      idempotency_key: "d1-local-dag-child-" + randomUUID(), priority: 100,
      depends_on: [parentTask.payload.task.id], max_attempts: 1,
    },
  });
  assertStatus(childTask, 201, "DAG child enqueue");
  const independentTask = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "system_audit", title: "Independent task", payload: { step: "independent" },
      idempotency_key: "d1-local-dag-independent-" + randomUUID(), priority: 50, max_attempts: 1,
    },
  });
  assertStatus(independentTask, 201, "independent task enqueue");
  const independentClaim = await api("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run" },
  });
  assert.equal(independentClaim.payload.task.id, independentTask.payload.task.id,
    "higher-priority blocked child cannot bypass its dependency");
  await api("/v1/tasks/" + independentTask.payload.task.id + "/finish", {
    method: "POST", body: { lease_token: independentClaim.payload.lease_token,
      status: "succeeded", result: { mode: "dry_run" } },
  });
  const parentClaim = await api("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run" },
  });
  assert.equal(parentClaim.payload.task.id, parentTask.payload.task.id);
  await api("/v1/tasks/" + parentTask.payload.task.id + "/finish", {
    method: "POST", body: { lease_token: parentClaim.payload.lease_token,
      status: "succeeded", result: { mode: "dry_run" } },
  });
  const wrongModeClaim = await api("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "live_inference" },
  });
  assert.equal(wrongModeClaim.payload.task, null,
    "a dry-run dependency must not release a live-inference task");
  const childClaim = await api("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run" },
  });
  assert.equal(childClaim.payload.task.id, childTask.payload.task.id);
  await api("/v1/tasks/" + childTask.payload.task.id + "/finish", {
    method: "POST", body: { lease_token: childClaim.payload.lease_token,
      status: "succeeded", result: { mode: "dry_run" } },
  });
  const failureParent = await api("/v1/tasks/enqueue", {
    method: "POST", body: {
      task_type: "research_brief", title: "Failing DAG parent", payload: {},
      idempotency_key: "d1-local-dag-fail-parent-" + randomUUID(), max_attempts: 1,
    },
  });
  const failureChild = await api("/v1/tasks/enqueue", {
    method: "POST", body: {
      task_type: "content_draft", title: "Blocked DAG child", payload: {},
      idempotency_key: "d1-local-dag-fail-child-" + randomUUID(),
      depends_on: [failureParent.payload.task.id], max_attempts: 1,
    },
  });
  const failureGrandchild = await api("/v1/tasks/enqueue", {
    method: "POST", body: {
      task_type: "code_review", title: "Blocked DAG grandchild", payload: {},
      idempotency_key: "d1-local-dag-fail-grandchild-" + randomUUID(),
      depends_on: [failureChild.payload.task.id], max_attempts: 1,
    },
  });
  const failureClaim = await api("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run" },
  });
  assert.equal(failureClaim.payload.task.id, failureParent.payload.task.id);
  await api("/v1/tasks/" + failureParent.payload.task.id + "/finish", {
    method: "POST", body: { lease_token: failureClaim.payload.lease_token,
      status: "failed", error: "intentional local smoke failure" },
  });
  const afterFailureClaim = await api("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run" },
  });
  assert.equal(afterFailureClaim.payload.task, null);
  const dagReadback = await api("/v1/tasks?limit=100");
  const byId = new Map(dagReadback.payload.tasks.map(task => [task.id, task]));
  assert.equal(byId.get(failureChild.payload.task.id).status, "blocked");
  assert.equal(byId.get(failureGrandchild.payload.task.id).status, "blocked");
  assert.deepEqual(byId.get(childTask.payload.task.id).depends_on, [parentTask.payload.task.id]);
  assert.equal(byId.get(childTask.payload.task.id).priority, 100);
  const priorityConflict = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "research_brief", title: "DAG parent", payload: { step: "parent" },
      idempotency_key: parentTask.payload.task.idempotency_key, priority: 99, max_attempts: 1,
    },
  });
  assertStatus(priorityConflict, 409, "idempotency key cannot change priority");


  // Codespaces remains hard-disabled until issue #440's adversarial sandbox
  // acceptance criteria pass. Verify the real local D1 Worker rejects every
  // activation path and does not enqueue a Codespace task.
  const codespaceStatus = await api("/v1/codespace/status", { token: CODESPACE_TOKEN });
  assertStatus(codespaceStatus, 503, "Codespace release gate");
  assert.deepEqual(codespaceStatus.payload, { error: "codespace_executor_disabled" });
  const sharedTokenDenied = await api("/v1/codespace/status", { token: TOKEN });
  assertStatus(sharedTokenDenied, 503, "release gate precedes token validation");
  const codespaceTask = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "codespace_test",
      title: "Codespace D1 isolation smoke",
      payload: { operation: "python_tests" },
      idempotency_key: "d1-local-codespace-isolation-" + randomUUID(),
      max_attempts: 1,
    },
  });
  assertStatus(codespaceTask, 503, "Codespace enqueue blocked before D1 write");
  assert.deepEqual(codespaceTask.payload, { error: "codespace_executor_disabled" });
  const legacyCannotClaim = await api("/v1/tasks/claim", { method: "POST", body: {} });
  assertStatus(legacyCannotClaim, 200, "legacy runner remains operational");
  assert.equal(legacyCannotClaim.payload.task, null, "no Codespace task was enqueued");
  const scopedCannotList = await api("/v1/tasks", { token: CODESPACE_TOKEN });
  assertStatus(scopedCannotList, 401, "Codespace principal cannot list general tasks");
  const codespaceClaim = await api("/v1/codespace/tasks/claim", {
    method: "POST", body: {}, token: CODESPACE_TOKEN,
  });
  assertStatus(codespaceClaim, 503, "Codespace claim blocked");
  const codespaceFinished = await api("/v1/codespace/tasks/local-disabled/finish", {
    method: "POST",
    token: CODESPACE_TOKEN,
    body: { lease_token: "local-disabled", status: "succeeded" },
  });
  assertStatus(codespaceFinished, 503, "Codespace finish blocked");
  const retryDenied = await api("/v1/codespace/tasks/local-disabled/retry", {
    method: "POST",
    token: CODESPACE_TOKEN,
    body: { lease_token: "local-disabled" },
  });
  assertStatus(retryDenied, 404, "Codespace principal has no retry endpoint");

  // Durable model invocation identities survive retries and distinguish steps.
  const invocationTask = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "system_audit",
      title: "Invocation lifecycle integration",
      payload: { provider_calls: false },
      idempotency_key: "d1-local-invocation-" + randomUUID(),
      max_attempts: 2,
    },
  });
  assertStatus(invocationTask, 201, "invocation task enqueue");
  const invocationClaim = await api("/v1/tasks/claim", { method: "POST", body: {} });
  assertStatus(invocationClaim, 200, "invocation task claim");
  const invocationTaskId = invocationTask.payload.task.id;
  const invocationLease = invocationClaim.payload.lease_token;
  const invocationPath = "/v1/tasks/" + invocationTaskId + "/invocations";
  const prepareBody = {
    action: "prepare",
    lease_token: invocationLease,
    step_key: "research:primary",
    task_kind: "research",
  };
  const prepared = await api(invocationPath, { method: "POST", body: prepareBody });
  assertStatus(prepared, 201, "prepare durable invocation");
  const replayPrepared = await api(invocationPath, { method: "POST", body: prepareBody });
  assertStatus(replayPrepared, 201, "prepared invocation replay");
  assert.equal(replayPrepared.payload.invocation.request_id, prepared.payload.invocation.request_id);
  assert.equal(replayPrepared.payload.invocation.invocation_id, prepared.payload.invocation.invocation_id);

  const secondStep = await api(invocationPath, {
    method: "POST",
    body: { ...prepareBody, step_key: "research:review" },
  });
  assertStatus(secondStep, 201, "second logical step");
  assert.notEqual(secondStep.payload.invocation.request_id, prepared.payload.invocation.request_id);
  assert.notEqual(secondStep.payload.invocation.invocation_id, prepared.payload.invocation.invocation_id);

  const invocationId = prepared.payload.invocation.invocation_id;
  const invocationStarted = await api(invocationPath, {
    method: "POST",
    body: { action: "start", lease_token: invocationLease, invocation_id: invocationId, route_id: "groq:model-a" },
  });
  assertStatus(invocationStarted, 200, "start first route attempt");
  const duplicateRoute = await api(invocationPath, {
    method: "POST",
    body: { action: "start", lease_token: invocationLease, invocation_id: invocationId, route_id: "groq:model-a" },
  });
  assertStatus(duplicateRoute, 409, "same route replay is blocked");
  const rejected429 = await api(invocationPath, {
    method: "POST",
    body: { action: "reject", lease_token: invocationLease, invocation_id: invocationId,
      route_id: "groq:model-a", rejection_code: "http_429" },
  });
  assertStatus(rejected429, 200, "explicit 429 permits a new route attempt");
  const replayRejected429 = await api(invocationPath, {
    method: "POST",
    body: { action: "reject", lease_token: invocationLease, invocation_id: invocationId,
      route_id: "groq:model-a", rejection_code: "http_429" },
  });
  assertStatus(replayRejected429, 200, "lost reject response is idempotently replayable");
  assert.equal(replayRejected429.payload.replay, true);
  const alternateRoute = await api(invocationPath, {
    method: "POST",
    body: { action: "start", lease_token: invocationLease, invocation_id: invocationId, route_id: "cerebras:model-b" },
  });
  assertStatus(alternateRoute, 200, "start alternate route");
  const settledInvocation = await api(invocationPath, {
    method: "POST",
    body: { action: "settle", lease_token: invocationLease, invocation_id: invocationId },
  });
  assertStatus(settledInvocation, 200, "settle logical invocation");
  const replaySettled = await api(invocationPath, {
    method: "POST",
    body: { action: "settle", lease_token: invocationLease, invocation_id: invocationId },
  });
  assertStatus(replaySettled, 200, "lost settle response is idempotently replayable");
  assert.equal(replaySettled.payload.replay, true);
  const settledReplay = await api(invocationPath, { method: "POST", body: prepareBody });
  assertStatus(settledReplay, 409, "settled invocation cannot be replayed");

  const secondInvocationId = secondStep.payload.invocation.invocation_id;
  const ambiguousStart = await api(invocationPath, {
    method: "POST",
    body: { action: "start", lease_token: invocationLease, invocation_id: secondInvocationId, route_id: "groq:model-a" },
  });
  assertStatus(ambiguousStart, 200, "start second logical step");
  const invocationAmbiguous = await api(invocationPath, {
    method: "POST",
    body: { action: "ambiguous", lease_token: invocationLease, invocation_id: secondInvocationId },
  });
  assertStatus(invocationAmbiguous, 200, "mark ambiguous provider outcome");
  const replayAmbiguous = await api(invocationPath, {
    method: "POST",
    body: { action: "ambiguous", lease_token: invocationLease, invocation_id: secondInvocationId },
  });
  assertStatus(replayAmbiguous, 200, "lost ambiguous response is idempotently replayable");
  assert.equal(replayAmbiguous.payload.replay, true);
  const ambiguousReplay = await api(invocationPath, {
    method: "POST",
    body: { ...prepareBody, step_key: "research:review" },
  });
  assertStatus(ambiguousReplay, 409, "ambiguous invocation cannot be replayed");

  const quotaBase = {
    action: "reserve",
    route_id: "d1-local:concurrency",
    task_id: "d1-local-quota-task",
    estimated_tokens: 5,
  };
  const reservations = await Promise.all([
    api("/v1/quota", { method: "POST", body: quotaBase }),
    api("/v1/quota", { method: "POST", body: quotaBase }),
    api("/v1/quota", { method: "POST", body: quotaBase }),
  ]);
  assert.deepEqual(
    reservations.map(item => item.status).sort((a, b) => a - b),
    [201, 201, 409],
    "atomic D1 reservation must enforce the rolling-minute request cap under concurrency",
  );
  const accepted = reservations.filter(item => item.status === 201);
  for (const reservation of accepted) {
    const settled = await api("/v1/quota", {
      method: "POST",
      body: { action: "settle", reservation_id: reservation.payload.reservation_id, route_id: "d1-local:concurrency", task_id: "d1-local-quota-task", actual_tokens: 3 },
    });
    assertStatus(settled, 200, "quota settlement");
  }
  const quota = await api("/v1/quota?route_id=d1-local%3Aconcurrency&task_id=d1-local-quota-task");
  assertStatus(quota, 200, "quota usage read");
  assert.equal(quota.payload.usage.minute.requests, 2);
  assert.equal(quota.payload.usage.minute.tokens, 10, "settlement cannot reduce the reserved token estimate");

  const monetaryBase = {
    action: "reserve",
    provider: "d1-local-provider",
    model: "d1-local-model",
    task_kind: "integration_test",
    task_id: "d1-local-monetary-cap-task",
    estimated_cost_nanos: 100_000_000,
    daily_budget_nanos: 400_000_000,
    monthly_budget_nanos: 12_000_000_000,
    per_task_budget_nanos: 200_000_000,
  };
  const policyMismatch = await api("/v1/monetary-ledger", {
    method: "POST",
    body: {
      ...monetaryBase,
      reservation_id: randomUUID(),
      idempotency_key: randomUUID(),
      daily_budget_nanos: 400_000_001,
    },
  });
  assertStatus(policyMismatch, 409, "caller cannot raise the operator-owned monetary hard cap");
  const raisedCapAttempt = await api("/v1/monetary-ledger", {
    method: "POST",
    body: {
      ...monetaryBase,
      daily_budget_nanos: 400_000_001,
      reservation_id: randomUUID(),
      idempotency_key: randomUUID(),
    },
  });
  assertStatus(raisedCapAttempt, 409, "runner cannot raise the operator-owned D1 hard cap");
  assert.equal(raisedCapAttempt.payload.error, "budget_policy_mismatch");

  const monetaryAttempts = await Promise.all([1, 2, 3].map(() => {
    const reservation_id = randomUUID();
    return api("/v1/monetary-ledger", {
      method: "POST",
      body: { ...monetaryBase, reservation_id, idempotency_key: randomUUID() },
    });
  }));
  assert.deepEqual(
    monetaryAttempts.map(item => item.status).sort((a,b) => a-b),
    [201, 201, 409],
    "D1 monetary reservations must enforce the per-task hard cap across concurrent callers",
  );
  const acceptedMoney = monetaryAttempts.filter(item => item.status === 201);
  const preCall = acceptedMoney[0].payload.reservation_id;
  const inFlight = acceptedMoney[1].payload.reservation_id;

  const idempotentBase = {
    ...monetaryBase,
    task_id: "d1-local-idempotency-task",
    estimated_cost_nanos: 2_000_000,
    per_task_budget_nanos: 200_000_000,
  };
  const idemId = randomUUID();
  const idemKey = randomUUID();
  const idemFirst = await api("/v1/monetary-ledger", {
    method: "POST", body: { ...idempotentBase, reservation_id: idemId, idempotency_key: idemKey },
  });
  assertStatus(idemFirst, 201, "initial monetary idempotency reservation");
  const idemReplay = await api("/v1/monetary-ledger", {
    method: "POST", body: { ...idempotentBase, reservation_id: randomUUID(), idempotency_key: idemKey },
  });
  assertStatus(idemReplay, 200, "monetary idempotency replay");
  assert.equal(idemReplay.payload.reservation_id, idemId);
  assert.equal(idemReplay.payload.replay, true);
  const idemConflict = await api("/v1/monetary-ledger", {
    method: "POST",
    body: { ...idempotentBase, estimated_cost_nanos: 3_000_000, reservation_id: randomUUID(), idempotency_key: idemKey },
  });
  assertStatus(idemConflict, 409, "idempotency key cannot be reused for a different reservation");
  const idemRelease = await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "release", reservation_id: idemId },
  });
  assertStatus(idemRelease, 200, "release idempotent reservation before I/O");

  const released = await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "release", reservation_id: preCall },
  });
  assertStatus(released, 200, "pre-provider-I/O release");
  const preGlobalStatus = await api("/v1/monetary-ledger");
  assertStatus(preGlobalStatus, 200, "pre-global monetary snapshot");
  assert.equal(
    preGlobalStatus.payload.snapshot.pending_reserved_nanos,
    100_000_000,
    "only the in-flight reservation should remain pending before global-cap concurrency test",
  );
  const globalBase = {
    ...monetaryBase,
    estimated_cost_nanos: 200_000_000,
    daily_budget_nanos: 400_000_000,
    monthly_budget_nanos: 12_000_000_000,
    per_task_budget_nanos: 200_000_000,
  };
  const globalAttempts = await Promise.all([1, 2, 3].map(index => {
    const reservation_id = randomUUID();
    return api("/v1/monetary-ledger", {
      method: "POST",
      body: {
        ...globalBase,
        provider: "d1-local-global-provider-" + index,
        model: "d1-local-global-model-" + index,
        task_id: "d1-local-global-task-" + index,
        reservation_id,
        idempotency_key: randomUUID(),
      },
    });
  }));
  assert.deepEqual(
    globalAttempts.map(item => item.status).sort((a,b) => a-b),
    [201, 409, 409],
    "daily monetary cap must be global across providers and concurrent tasks; responses=" + JSON.stringify(globalAttempts.map(item => ({status:item.status,payload:item.payload}))),
  );
  for (const item of globalAttempts.filter(item => item.status === 201)) {
    const releasedGlobal = await api("/v1/monetary-ledger", {
      method: "POST", body: { action: "release", reservation_id: item.payload.reservation_id },
    });
    assertStatus(releasedGlobal, 200, "release global-cap test reservation before I/O");
  }

  // Mutate only the isolated local D1 policy to exercise the monthly cap
  // independently. This test never receives or uses Cloudflare credentials.
  executeLocalSql("UPDATE monetary_budget_policy SET monthly_budget_nanos=300000000 WHERE policy_key='default'");
  const monthlyBase = {
    ...globalBase,
    estimated_cost_nanos: 100_000_000,
    daily_budget_nanos: 400_000_000,
    monthly_budget_nanos: 300_000_000,
    per_task_budget_nanos: 200_000_000,
  };
  const monthlyAttempts = await Promise.all([1, 2, 3].map(index => {
    const reservation_id = randomUUID();
    return api("/v1/monetary-ledger", {
      method: "POST",
      body: {
        ...monthlyBase,
        provider: "d1-local-monthly-provider-" + index,
        model: "d1-local-monthly-model-" + index,
        task_id: "d1-local-monthly-task-" + index,
        reservation_id,
        idempotency_key: randomUUID(),
      },
    });
  }));
  assert.deepEqual(
    monthlyAttempts.map(item => item.status).sort((a,b) => a-b),
    [201, 201, 409],
    "monthly monetary cap must be global across providers and concurrent tasks",
  );
  for (const item of monthlyAttempts.filter(item => item.status === 201)) {
    const releasedMonthly = await api("/v1/monetary-ledger", {
      method: "POST", body: { action: "release", reservation_id: item.payload.reservation_id },
    });
    assertStatus(releasedMonthly, 200, "release monthly-cap test reservation before I/O");
  }
  executeLocalSql("UPDATE monetary_budget_policy SET monthly_budget_nanos=12000000000 WHERE policy_key='default'");

  const started = await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "start", reservation_id: inFlight },
  });
  assertStatus(started, 200, "mark provider I/O started");
  const forbiddenRelease = await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "release", reservation_id: inFlight },
  });
  assertStatus(forbiddenRelease, 409, "release after provider I/O must be forbidden");
  const ambiguous = await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "ambiguous", reservation_id: inFlight },
  });
  assertStatus(ambiguous, 200, "persist ambiguous provider outcome");
  const settledMoney = await api("/v1/monetary-ledger", {
    method: "POST",
    body: {
      action: "settle", reservation_id: inFlight,
      actual_cost_nanos: 250_000_000, input_tokens: 11, output_tokens: 7,
    },
  });
  assertStatus(settledMoney, 200, "settle actual monetary cost");
  assert.equal(settledMoney.payload.over_budget, true, "actual spend above estimate/task cap must trigger quarantine");
  const duplicateSettlement = await api("/v1/monetary-ledger", {
    method: "POST",
    body: {
      action: "settle", reservation_id: inFlight,
      actual_cost_nanos: 250_000_000, input_tokens: 11, output_tokens: 7,
    },
  });
  assertStatus(duplicateSettlement, 200, "duplicate settlement is idempotent");
  assert.equal(duplicateSettlement.payload.replay, true);
  const conflictingSettlement = await api("/v1/monetary-ledger", {
    method: "POST",
    body: {
      action: "settle", reservation_id: inFlight,
      actual_cost_nanos: 26_000_000, input_tokens: 11, output_tokens: 7,
    },
  });
  assertStatus(conflictingSettlement, 409, "conflicting settlement replay must be rejected");
  const quarantinedAttempt = await api("/v1/monetary-ledger", {
    method: "POST",
    body: { ...monetaryBase, reservation_id: randomUUID(), idempotency_key: randomUUID() },
  });
  assertStatus(quarantinedAttempt, 409, "quarantined route cannot reserve again");

  const crashBase = {
    ...monetaryBase,
    task_id: "d1-local-crash-recovery-task",
    provider: "d1-local-crash-provider",
    model: "d1-local-crash-model",
    estimated_cost_nanos: 30_000_000,
    per_task_budget_nanos: 200_000_000,
  };
  const crashReservation = await api("/v1/monetary-ledger", {
    method: "POST",
    body: { ...crashBase, reservation_id: randomUUID(), idempotency_key: randomUUID() },
  });
  assertStatus(crashReservation, 201, "create restart recovery reservation");
  const crashStarted = await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "start", reservation_id: crashReservation.payload.reservation_id },
  });
  assertStatus(crashStarted, 200, "start restart recovery reservation");
  const crashAmbiguous = await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "ambiguous", reservation_id: crashReservation.payload.reservation_id },
  });
  assertStatus(crashAmbiguous, 200, "mark restart recovery reservation ambiguous");
  await stopWorker(server);
  server = launchWorker();
  await waitForWorker(server, () => logs);
  const monetaryStatus = await api("/v1/monetary-ledger");
  assertStatus(monetaryStatus, 200, "read durable monetary status");
  assert.deepEqual(monetaryStatus.payload.budget_policy, {
    policy_key: "default",
    daily_budget_nanos: 400_000_000,
    monthly_budget_nanos: 12_000_000_000,
    per_task_budget_nanos: 200_000_000,
    enabled: true,
    updated_at: monetaryStatus.payload.budget_policy.updated_at,
  });
  assert.equal(monetaryStatus.payload.snapshot.pending_count, 1);
  assert.equal(monetaryStatus.payload.snapshot.unresolved[0].reservation_id, crashReservation.payload.reservation_id);
  assert.equal(monetaryStatus.payload.quarantined_routes.length, 1);

  const event = {
    candidate_id: "d1-local-candidate",
    event_id: "d1-local-event-1",
    event_type: "candidate_detected",
    occurred_at: new Date().toISOString(),
    payload: { source: "local-d1-integration", external_writes: false },
  };
  const appended = await api("/v1/paper-ledger", {
    method: "POST", body: { action: "append", event },
  });
  assertStatus(appended, 201, "paper ledger append");
  assert.equal(appended.payload.sequence, 1);
  assert.equal(appended.payload.event_hash.length, 64);

  const replay = await api("/v1/paper-ledger", {
    method: "POST", body: { action: "append", event },
  });
  assertStatus(replay, 200, "paper ledger idempotent replay");
  assert.equal(replay.payload.replay, true);
  assert.equal(replay.payload.event_hash, appended.payload.event_hash);

  const ledger = await api("/v1/paper-ledger?candidate_id=d1-local-candidate");
  assertStatus(ledger, 200, "paper ledger read");
  assert.equal(ledger.payload.event_count, 1);
  assert.equal(ledger.payload.checkpoint_count, 1);
  assert.equal(ledger.payload.head_sequence, 1);

  status = await api("/v1/mobile/status");
  assertStatus(status, 200, "mobile status after D1 writes");
  assert.equal(status.payload.paper_events.find(row => row.event_type === "candidate_detected")?.count, 1);
  assert.equal(status.payload.tasks.find(row => row.status === "succeeded")?.count, 8,
    "four workflow steps plus the legacy task and three dependency-smoke tasks should be durably succeeded");
  assert.equal(status.payload.tasks.find(row => row.status === "failed")?.count, 1,
    "the failed dependency root remains visibly failed");
  assert.equal(status.payload.tasks.find(row => row.status === "blocked")?.count, 2,
    "failed dependency descendants remain blocked");
  assert.equal(status.payload.monetary_ledger.available, true);
  assert.equal(status.payload.monetary_ledger.pending_count, 1);
  assert.equal(status.payload.monetary_ledger.ambiguous_count, 1);
  assert.equal(status.payload.monetary_ledger.daily_spent_usd, 0.25);
  assert.equal(status.payload.model_costs_usd.total, 0.25);
  assert.deepEqual(Object.keys(status.payload.provider_usage).sort(), ["requests", "tokens"]);
  assert.equal("by_provider" in status.payload.model_costs_usd, false);
  assert.equal("unresolved" in status.payload.monetary_ledger, false);

  // Inject reserved rows directly into the isolated local D1 database; ordinary
  // enqueue is intentionally forbidden, and this is not a remote D1 test.
  const reserved = await api("/v1/tasks/enqueue", { method: "POST", body: {
    task_type: "research_brief", title: "reserved", idempotency_key: "research-pilot:rejected", payload: {},
  }});
  assertStatus(reserved, 400, "generic enqueue rejects pilot namespace");
  assert.equal(reserved.payload.error, "reserved_idempotency_key");
  executeLocalSql(`
    INSERT INTO tasks(id,idempotency_key,task_type,title,payload_json,status,created_at,updated_at,priority)
      VALUES ('pilot-local-free','research-pilot:free','research_brief','pilot','{}','pending','2026-01-01','2026-01-01',100),
             ('pilot-local-child','research-pilot:child','research_brief','pilot child','{}','pending','2026-01-01','2026-01-01',100);
    INSERT INTO task_dependencies(task_id,dependency_task_id) VALUES ('pilot-local-child','${failureParent.payload.task.id}');
  `);
  const ignoredPilot = await api("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } });
  assertStatus(ignoredPilot, 200, "generic claim skips pilots");
  assert.equal(ignoredPilot.payload.task, null);
  const pilotRows = await api("/v1/tasks?limit=100");
  assertStatus(pilotRows, 200, "pilot state readback");
  for (const id of ["pilot-local-free", "pilot-local-child"]) {
    const row = pilotRows.payload.tasks.find(task => task.id === id);
    assert.equal(row?.status, "pending", `generic claim and dependency blocking must not mutate ${id}`);
    assert.equal(row.attempts, 0);
  }
  executeLocalSql(`
    INSERT INTO tasks(id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,created_at,updated_at,lease_token,lease_expires_at)
      VALUES ('pilot-local-expired','research-pilot:expired','research_brief','pilot expired','{}','running',1,1,'2000-01-01','2000-01-01','pilot-token','2000-01-01'),
             ('ordinary-local-expired','ordinary-expired','research_brief','ordinary expired','{}','running',1,2,'2000-01-01','2000-01-01','ordinary-token','2000-01-01');
  `);
  const recovery = await api("/v1/tasks/recover", { method: "POST", body: {} });
  assertStatus(recovery, 200, "generic recovery");
  assert.deepEqual(recovery.payload, { requeued: 1, failed: 0 });
  // SQL raises on any failed assertion, including changes to the pilot audit log.
  executeLocalSql(`
    SELECT CASE WHEN
      (SELECT count(*) FROM tasks WHERE id='pilot-local-expired' AND status='running' AND attempts=1 AND lease_token='pilot-token' AND lease_expires_at='2000-01-01')=1
      AND (SELECT count(*) FROM audit_log WHERE task_id='pilot-local-expired')=0
      AND (SELECT count(*) FROM tasks WHERE id='ordinary-local-expired' AND status='pending' AND lease_token IS NULL)=1
      AND (SELECT count(*) FROM audit_log WHERE task_id='ordinary-local-expired' AND event='task_lease_recovered')=1
      THEN 1 ELSE json('invalid-assertion') END;
  `);
  executeLocalSql(`
    INSERT INTO tasks(id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,created_at,updated_at,lease_token,lease_expires_at)
      VALUES ('pilot-local-owned','research-pilot:owned','research_brief','pilot owned','{}','running',1,1,'2000-01-01','2000-01-01','pilot-valid-token','2099-01-01T00:00:00.000Z');
  `);
  for (const action of ["heartbeat", "finish", "retry"]) {
    const attempt = await api(`/v1/tasks/pilot-local-owned/${action}`, {
      method: "POST", body: { lease_token: "pilot-valid-token", status: "succeeded" },
    });
    assertStatus(attempt, 403, `generic ${action} rejects a pilot even with a valid lease`);
    assert.equal(attempt.payload.error, "reserved_task");
  }
  const pilotInvocation = await api("/v1/tasks/pilot-local-owned/invocations", {
    method: "POST", body: { action: "prepare", lease_token: "pilot-valid-token", step_key: "test", task_kind: "research" },
  });
  assertStatus(pilotInvocation, 409, "generic invocation rejects pilot lease");
  executeLocalSql(`
    SELECT CASE WHEN
      (SELECT count(*) FROM tasks WHERE id='pilot-local-owned' AND status='running' AND attempts=1 AND lease_token='pilot-valid-token' AND lease_expires_at='2099-01-01T00:00:00.000Z')=1
      AND (SELECT count(*) FROM audit_log WHERE task_id='pilot-local-owned')=0
      AND (SELECT count(*) FROM task_model_invocations WHERE task_id='pilot-local-owned')=0
      THEN 1 ELSE json('invalid-assertion') END;
  `);
  const disabledCodespacePilot = await api("/v1/codespace/tasks/claim", {
    method: "POST", token: CODESPACE_TOKEN, body: { execution_mode: "dry_run" },
  });
  assertStatus(disabledCodespacePilot, 503, "Codespace remains release-gated");

  const webhookDisabled = await api("/v1/telegram/webhook", {
    method: "POST",
    body: { update_id: 1 },
  });
  assertStatus(webhookDisabled, 503, "Telegram webhook must remain disabled without operator secrets");

  console.log(
    "Local D1 integration passed: migrations, authenticated status, task enqueue/idempotency/lease fencing, " +
    "concurrent token plus global daily/monthly/per-task monetary caps, idempotency conflict/replay, pre-I/O-only release, ambiguous reservations across restart, duplicate/conflicting settlement, quarantine, mobile monetary status and spend breakdown, append-only paper ledger/hash replay, and disabled Telegram webhook.",
  );
} catch (error) {
  throw new Error(String(error?.stack || error) + "\nWrangler logs:\n" + logs);
} finally {
  await stopWorker(server);
  rmSync(root, { recursive: true, force: true });
}
