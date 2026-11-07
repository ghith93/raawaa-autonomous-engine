import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

// Freeze the fixture's date slots so a run spanning UTC midnight cannot collide.
const FIXTURE_NOW = Date.now();
const PORT = 8792;
const BASE = "http://127.0.0.1:" + PORT;
const TOKEN = "test-engine-token-local-only";
const CODESPACE_TOKEN = "test-codespace-token-0123456789abcdef0123456789abcdef";
const RESEARCHER_TOKEN = "local-researcher-token-0123456789abcdef0123456789";
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

function queryLocalSql(sql) {
  const result = spawnSync("npx", [
    ...WRANGLER, "d1", "execute", "raawaa-autonomous-e",
    "--local", "--json", "--command", sql, "--config", "wrangler.toml",
    "--persist-to", stateDir,
  ], {
    cwd: process.cwd(), env: safeEnvironment(), encoding: "utf8",
    timeout: 30_000, maxBuffer: 1024 * 1024,
  });
  if (result.error || result.status !== 0) {
    throw new Error("Local-only D1 read failed.\n" + (result.stdout || "") + "\n" +
      (result.stderr || "") + (result.error ? "\n" + result.error.message : ""));
  }
  const parsed = JSON.parse(result.stdout);
  const resultSets = Array.isArray(parsed) ? parsed : [parsed];
  return resultSets.flatMap(item => Array.isArray(item?.results) ? item.results : []);
}

async function api(path, { method = "GET", body, token = TOKEN, laneToken } = {}) {
  const headers = { accept: "application/json" };
  if (laneToken) headers["x-engine-lane-token"] = laneToken;
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
    "--var", "ENGINE_RESEARCHER_LANE_TOKEN:" + RESEARCHER_TOKEN,
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

function launchCrashFixture(mode, markerPath, extraArgs = []) {
  const child = spawn("python3", [
    "tests/fixtures/remote_process_crash_worker.py", mode, markerPath, ...extraArgs,
  ], {
    cwd: process.cwd(),
    env: {
      ...safeEnvironment(),
      RAAWAA_LOCAL_WORKER_URL: BASE,
      RAAWAA_LOCAL_WORKER_TOKEN: TOKEN,
      PYTHONDONTWRITEBYTECODE: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", chunk => { output = (output + chunk).slice(-8000); });
  child.stderr.on("data", chunk => { output = (output + chunk).slice(-8000); });
  child.fixtureOutput = () => output;
  return child;
}

async function waitForMarker(child, markerPath, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (existsSync(markerPath) && readFileSync(markerPath, "utf8").trim()) return;
    if (child.exitCode !== null) {
      throw new Error("Crash fixture exited before its child started:\n" + child.fixtureOutput());
    }
    await delay(50);
  }
  throw new Error("Trusted crash fixture child did not start:\n" + child.fixtureOutput());
}

async function waitForExit(child, timeoutMs = 10_000) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  let timer;
  const exit = new Promise(resolve => child.once("close", () => resolve(true)));
  if (child.exitCode !== null || child.signalCode !== null) return;
  const timeout = new Promise(resolve => { timer = setTimeout(() => resolve(false), timeoutMs); });
  const exited = await Promise.race([exit, timeout]);
  clearTimeout(timer);
  if (!exited) {
    child.kill("SIGKILL");
    await Promise.race([exit, delay(3000)]);
    throw new Error("Crash fixture failed to exit before timeout:\\n" + child.fixtureOutput());
  }
}

function killCrashChildren(pids) {
  for (const pid of pids) {
    if (Number.isInteger(pid) && pid > 1 && pid !== process.pid) {
      try { process.kill(pid, "SIGKILL"); } catch { /* already exited */ }
    }
  }
}

let server = null;
let logs = "";
let crashMarkerPath = null;
const crashChildPids = new Set();

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

  // Real concurrent claim contention against the local D1-backed Worker:
  // two independent HTTP requests race for one pending row. D1 must serialize
  // the conditional UPDATE so exactly one worker receives its lease.
  const contentionTask = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "system_audit",
      title: "Local D1 concurrent claim contention",
      payload: { purpose: "prove single lease under simultaneous claims", external_writes: false },
      idempotency_key: "d1-local-concurrent-claim-" + randomUUID(),
      max_attempts: 1,
      priority: 100,
    },
  });
  assertStatus(contentionTask, 201, "enqueue concurrent-claim fixture");
  const [claimA, claimB] = await Promise.all([
    api("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } }),
    api("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } }),
  ]);
  assertStatus(claimA, 200, "first concurrent claim response");
  assertStatus(claimB, 200, "second concurrent claim response");
  const competingClaims = [claimA.payload, claimB.payload];
  const winners = competingClaims.filter(item => item.task?.id === contentionTask.payload.task.id);
  const losers = competingClaims.filter(item => item.task === null);
  assert.equal(winners.length, 1, "exactly one concurrent D1 claimant owns the task");
  assert.equal(losers.length, 1, "the competing D1 claimant sees no claimable task");
  assert.equal(typeof winners[0].lease_token, "string");
  const contentionReadback = await api("/v1/tasks?limit=100");
  assertStatus(contentionReadback, 200, "concurrent-claim durable readback");
  const contentionRow = contentionReadback.payload.tasks.find(
    item => item.id === contentionTask.payload.task.id,
  );
  assert.equal(contentionRow.status, "running");
  assert.equal(contentionRow.attempts, 1, "a race must not increment attempts twice");
  assertStatus(await api("/v1/tasks/" + contentionTask.payload.task.id + "/finish", {
    method: "POST",
    body: {
      lease_token: winners[0].lease_token,
      status: "succeeded",
      result: { mode: "dry_run", concurrent_claim_winner_count: 1 },
    },
  }), 200, "finish the single winning D1 lease");
  const contentionAudit = await api("/v1/audit?limit=20&task_id=" + contentionTask.payload.task.id);
  assertStatus(contentionAudit, 200, "concurrent-claim audit readback");
  assert.equal(
    contentionAudit.payload.events.filter(item => item.event === "task_claimed").length,
    1,
    "one concurrent claim produces exactly one durable claim event",
  );

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
  const reordered = await api("/v1/tasks/enqueue", { method: "POST", body: {
    ...taskPayload, payload: { external_writes: false, purpose: "verify real local D1 persistence" },
  } });
  assertStatus(reordered, 200, "reordered JSON payload replay");
  for (const change of [
    { task_type: "research_brief" }, { title: "Different title" },
    { payload: { purpose: "different", external_writes: false } }, { max_attempts: 2 },
  ]) {
    const conflict = await api("/v1/tasks/enqueue", { method: "POST", body: { ...taskPayload, ...change } });
    assertStatus(conflict, 409, "conflicting task manifest");
    assert.equal(conflict.payload.error, "idempotency_conflict");
  }
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
  const auditReadback = await api("/v1/audit?limit=10&task_id=" + enqueued.payload.task.id);
  assertStatus(auditReadback, 200, "audit readback for finished task");
  const auditNames = auditReadback.payload.events.map(row => row.event);
  for (const required of ["task_enqueued", "task_claimed", "task_succeeded"]) {
    assert.ok(auditNames.includes(required), "audit readback includes " + required);
  }
  assert.ok(auditReadback.payload.events.every(row => row.task_id === enqueued.payload.task.id),
    "audit task filter is exact");
  const auditUnauthenticated = await api("/v1/audit", { token: null });
  assertStatus(auditUnauthenticated, 401, "audit readback requires authentication");
  status = await api("/v1/mobile/status");
  assertStatus(status, 200, "mobile execution-mode aggregate");
  assert.equal(status.payload.execution_modes.counts.dry_run, dryRunBefore + 1);

  const staleLease = await api("/v1/tasks/" + enqueued.payload.task.id + "/heartbeat", {
    method: "POST",
    body: { lease_token: claimed.payload.lease_token },
  });
  assertStatus(staleLease, 409, "finished task must reject stale lease");

  // The specialist lane requires both its capability and exact task identity.
  const specialistExactTask = await api("/v1/tasks/enqueue", {method: "POST", body: {
    task_type: "research_brief", title: "Exact specialist", payload: {},
    execution_lane: "specialist:researcher", idempotency_key: "d1-specialist-exact-" + randomUUID(),
  }});
  assertStatus(specialistExactTask, 201, "enqueue exact specialist");
  const specialistExactPath = "/v1/tasks/" + specialistExactTask.payload.task.id + "/claim-exact";
  const wrongLaneExact = await api(specialistExactPath, {method: "POST", body: {}});
  assertStatus(wrongLaneExact, 200, "general lane refuses specialist task");
  assert.equal(wrongLaneExact.payload.task, null);
  const specialistClaim = await api(specialistExactPath, {method: "POST", laneToken: RESEARCHER_TOKEN,
    body: {worker_lane: "specialist:researcher"}});
  assertStatus(specialistClaim, 200, "specialist exact claim");
  assert.equal(specialistClaim.payload.task.id, specialistExactTask.payload.task.id);
  assertStatus(await api("/v1/tasks/" + specialistExactTask.payload.task.id + "/finish", {
    method: "POST", laneToken: RESEARCHER_TOKEN, body: {worker_lane: "specialist:researcher",
      lease_token: specialistClaim.payload.lease_token, status: "succeeded", result: {mode: "dry_run"}},
  }), 200, "finish specialist exact fixture");

  // Exact-ID and generic claimers race against the same real local D1 row.
  const exactRaceTask = await api("/v1/tasks/enqueue", {method: "POST", body: {
    task_type: "system_audit", title: "Exact claim contention", payload: {},
    idempotency_key: "d1-exact-race-" + randomUUID(), max_attempts: 1,
  }});
  assertStatus(exactRaceTask, 201, "enqueue exact claim fixture");
  const exactRacePath = "/v1/tasks/" + exactRaceTask.payload.task.id + "/claim-exact";
  assertStatus(await api(exactRacePath, {method: "POST", token: null, body: {}}), 401, "exact claim requires auth");
  assertStatus(await api(exactRacePath, {method: "POST", body: {worker_lane: "specialist:researcher"}}), 401, "exact claim requires lane token");
  assertStatus(await api(exactRacePath, {method: "POST", body: {execution_mode: "live_inference"}}), 400, "exact claim rejects live mode");
  const raceClaims = await Promise.all([
    api(exactRacePath, {method: "POST", body: {execution_mode: "dry_run"}}),
    api("/v1/tasks/claim", {method: "POST", body: {execution_mode: "dry_run"}}),
  ]);
  raceClaims.forEach(result => assertStatus(result, 200, "exact versus generic claim"));
  const exactRaceWinners = raceClaims.filter(result => result.payload.task !== null);
  assert.equal(exactRaceWinners.length, 1);
  assert.equal(exactRaceWinners[0].payload.task.id, exactRaceTask.payload.task.id);
  assert.equal(exactRaceWinners[0].payload.task.attempts, 1);
  assertStatus(await api("/v1/tasks/" + exactRaceTask.payload.task.id + "/finish", {method: "POST", body: {
    lease_token: exactRaceWinners[0].payload.lease_token, status: "succeeded", result: {mode: "dry_run"},
  }}), 200, "finish exact claim race fixture");
  const exactRaceAudit = await api("/v1/audit?limit=20&task_id=" + exactRaceTask.payload.task.id);
  assert.equal(exactRaceAudit.payload.events.filter(item => item.event === "task_claimed").length, 1);
  const missingExact = await api("/v1/tasks/missing-exact/claim-exact", {method: "POST", body: {}});
  assertStatus(missingExact, 200, "missing exact claim");
  assert.equal(missingExact.payload.task, null);

  // The actual Python worker and HTTP adapter complete only the requested ID.
  const targetedWorkerTask = await api("/v1/tasks/enqueue", {method: "POST", body: {
    task_type: "research_brief", title: "Targeted worker proof", payload: {topic: "local proof"},
    idempotency_key: "d1-target-worker-" + randomUUID(), priority: 1,
  }});
  const targetedWorkerOther = await api("/v1/tasks/enqueue", {method: "POST", body: {
    task_type: "system_audit", title: "Higher priority remains pending", payload: {},
    idempotency_key: "d1-target-other-" + randomUUID(), priority: 100,
  }});
  assertStatus(targetedWorkerTask, 201, "enqueue targeted worker fixture");
  assertStatus(targetedWorkerOther, 201, "enqueue targeted worker sentinel");
  const targetedPython = spawnSync("python3", ["-c", `
import json, os
from engine.remote_task_store import RemoteTaskStore
from engine.worker import run_once
base = os.environ['LOCAL_URL']
if not base.startswith('http://127.0.0.1:'):
    raise SystemExit('local test requires loopback')
# Test fixture only: production RemoteTaskStore continues to require HTTPS.
store = RemoteTaskStore.__new__(RemoteTaskStore)
store.base_url, store.token = base, os.environ['LOCAL_TOKEN']
store.access_client_id = store.access_client_secret = store.lane_token = ''
store.timeout, store.reject_redirects = 10.0, True
print(json.dumps(run_once(store, environ={}, task_id=os.environ['LOCAL_TASK_ID'])))
`], {cwd: process.cwd(), env: {...safeEnvironment(), LOCAL_URL: BASE, LOCAL_TOKEN: TOKEN,
    LOCAL_TASK_ID: targetedWorkerTask.payload.task.id, PYTHONDONTWRITEBYTECODE: "1"},
    encoding: "utf8", timeout: 30000});
  assert.equal(targetedPython.status, 0, targetedPython.stderr);
  const targetedResult = JSON.parse(targetedPython.stdout.trim());
  assert.equal(targetedResult.id, targetedWorkerTask.payload.task.id);
  assert.equal(targetedResult.status, "succeeded");
  assert.equal(targetedResult.mode, "dry_run");
  const sentinelRead = await api("/v1/tasks?task_id=" + targetedWorkerOther.payload.task.id);
  assert.equal(sentinelRead.payload.tasks[0].status, "pending");
  const sentinelClaim = await api("/v1/tasks/claim", {method: "POST", body: {execution_mode: "dry_run"}});
  assert.equal(sentinelClaim.payload.task.id, targetedWorkerOther.payload.task.id);
  assertStatus(await api("/v1/tasks/" + targetedWorkerOther.payload.task.id + "/finish", {method: "POST", body: {
    lease_token: sentinelClaim.payload.lease_token, status: "succeeded", result: {mode: "dry_run"},
  }}), 200, "finish targeted worker sentinel");

  // A narrowly scoped supervised claim targets one exact safe task and must
  // not sweep or claim unrelated D1 queue rows.
  const failedDependency = await api("/v1/tasks/enqueue", {
    method: "POST", body: { task_type: "system_audit", title: "Specific-claim failed dependency",
      payload: {}, idempotency_key: "d1-specific-claim-parent-" + randomUUID(),
      priority: 100, max_attempts: 1 },
  });
  assertStatus(failedDependency, 201, "enqueue specific-claim dependency fixture");
  const failedDependencyClaim = await api("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run" },
  });
  assert.equal(failedDependencyClaim.payload.task.id, failedDependency.payload.task.id);
  assertStatus(await api("/v1/tasks/" + failedDependency.payload.task.id + "/finish", {
    method: "POST", body: { lease_token: failedDependencyClaim.payload.lease_token,
      status: "failed", error: "specific-claim test fixture" },
  }), 200, "mark specific-claim dependency failed");
  const untouchedDependent = await api("/v1/tasks/enqueue", {
    method: "POST", body: { task_type: "system_audit", title: "Must not be swept by specific claim",
      payload: {}, idempotency_key: "d1-specific-claim-dependent-" + randomUUID(),
      priority: 100, max_attempts: 1, depends_on: [failedDependency.payload.task.id] },
  });
  assertStatus(untouchedDependent, 201, "enqueue untouched dependent fixture");
  const unrelatedPending = await api("/v1/tasks/enqueue", {
    method: "POST", body: { task_type: "system_audit", title: "Must not be claimed by specific claim",
      payload: {}, idempotency_key: "d1-specific-claim-unrelated-" + randomUUID(),
      priority: 100, max_attempts: 1 },
  });
  assertStatus(unrelatedPending, 201, "enqueue unrelated pending fixture");
  const specificProbe = await api("/v1/tasks/enqueue", {
    method: "POST", body: { task_type: "supervised_probe", title: "Exact supervised claim",
      payload: { operation: "python_platform_probe" },
      idempotency_key: "d1-specific-claim-probe-" + randomUUID(), priority: 1, max_attempts: 1 },
  });
  assertStatus(specificProbe, 201, "enqueue exact supervised probe");
  const blockedExact = await api("/v1/tasks/" + untouchedDependent.payload.task.id + "/claim-exact", {method: "POST", body: {}});
  assertStatus(blockedExact, 200, "exact claim refuses unresolved dependency");
  assert.equal(blockedExact.payload.task, null);
  const probeExact = await api("/v1/tasks/" + specificProbe.payload.task.id + "/claim-exact", {method: "POST", body: {}});
  assertStatus(probeExact, 200, "generic exact claim refuses supervised probe");
  assert.equal(probeExact.payload.task, null);
  const wrongMethodClaim = await api("/v1/tasks/" + specificProbe.payload.task.id +
    "/claim-supervised-probe", { method: "GET" });
  assertStatus(wrongMethodClaim, 405, "specific supervised claim is POST-only");
  const wrongTypeClaim = await api("/v1/tasks/" + unrelatedPending.payload.task.id +
    "/claim-supervised-probe", { method: "POST" });
  assertStatus(wrongTypeClaim, 200, "specific supervised route refuses another task type");
  assert.equal(wrongTypeClaim.payload.task, null);
  const unauthenticatedSpecificClaim = await api("/v1/tasks/" + specificProbe.payload.task.id +
    "/claim-supervised-probe", { method: "POST", token: null });
  assertStatus(unauthenticatedSpecificClaim, 401, "specific supervised claim requires bearer auth");
  const specificClaim = await api("/v1/tasks/" + specificProbe.payload.task.id +
    "/claim-supervised-probe", { method: "POST" });
  assertStatus(specificClaim, 200, "claim only the exact supervised probe");
  assert.equal(specificClaim.payload.task.id, specificProbe.payload.task.id);
  assert.equal(specificClaim.payload.task.status, "running");
  assert.equal(specificClaim.payload.task.execution_mode, "dry_run");
  assert.equal(typeof specificClaim.payload.lease_token, "string");
  const supervisedLeaseMs = Date.parse(specificClaim.payload.lease_expires_at) - Date.now();
  assert.ok(supervisedLeaseMs > 0 && supervisedLeaseMs <= 120_000,
    "specific supervised lease is short and bounded to two minutes");
  const duplicateSpecificClaim = await api("/v1/tasks/" + specificProbe.payload.task.id +
    "/claim-supervised-probe", { method: "POST" });
  assertStatus(duplicateSpecificClaim, 200, "exact supervised task can only be claimed once");
  assert.equal(duplicateSpecificClaim.payload.task, null);
  const specificReadback = await api("/v1/tasks?limit=100");
  assertStatus(specificReadback, 200, "specific claim isolation readback");
  const rowsAfterSpecificClaim = new Map(specificReadback.payload.tasks.map(task => [task.id, task]));
  assert.equal(rowsAfterSpecificClaim.get(unrelatedPending.payload.task.id).status, "pending",
    "specific claim did not take an unrelated higher-priority task");
  assert.equal(rowsAfterSpecificClaim.get(untouchedDependent.payload.task.id).status, "pending",
    "specific claim did not mutate unrelated failed-dependency rows");
  assertStatus(await api("/v1/tasks/" + specificProbe.payload.task.id + "/finish", {
    method: "POST", body: { lease_token: specificClaim.payload.lease_token,
      status: "succeeded", result: { local_specific_claim_test: true } },
  }), 200, "finish exact supervised claim fixture");
  const drainUnrelated = await api("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run" },
  });
  assert.equal(drainUnrelated.payload.task.id, unrelatedPending.payload.task.id,
    "general queue can still claim unrelated tasks after exact-claim proof");
  assertStatus(await api("/v1/tasks/" + unrelatedPending.payload.task.id + "/finish", {
    method: "POST", body: { lease_token: drainUnrelated.payload.lease_token,
      status: "succeeded", result: { mode: "dry_run" } },
  }), 200, "finish unrelated claim fixture");

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

  // Finish the model-route fixture before running process-specific D1 checks.
  const invocationFinished = await api("/v1/tasks/" + invocationTaskId + "/finish", {
    method: "POST", body: { lease_token: invocationLease, status: "blocked", error: "model lifecycle smoke complete" },
  });
  assertStatus(invocationFinished, 200, "finish model invocation fixture task");

  // The process-dispatch ledger has a distinct route/schema and a typed result.
  const processTask = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: { task_type: "supervised_probe", title: "Process dispatch lifecycle",
      payload: { operation: "python_platform_probe" },
      idempotency_key: "d1-local-process-" + randomUUID(), max_attempts: 2, priority: 100 },
  });
  assertStatus(processTask, 201, "enqueue process dispatch task");
  const processClaim = await api("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } });
  assertStatus(processClaim, 200, "claim process dispatch task");
  const processTaskId = processTask.payload.task.id;
  assert.equal(processClaim.payload.task.id, processTaskId, "highest-priority process task is claimed");
  const processLease = processClaim.payload.lease_token;
  const processPath = "/v1/tasks/" + processTaskId + "/process-dispatches";
  const processIdentity = {
    action: "prepare", lease_token: processLease,
    dispatch_id: randomUUID().replaceAll("-", ""),
    step_key: "supervised_process:v1", operation: "python_platform_probe",
    operation_version: "process-supervisor:v1", operation_fingerprint: "a".repeat(64),
    lease_fingerprint: "b".repeat(16),
  };
  const concurrentPrepare = await Promise.all([
    api(processPath, { method: "POST", body: processIdentity }),
    api(processPath, { method: "POST", body: processIdentity }),
  ]);
  assert.deepEqual(concurrentPrepare.map(item => item.status), [200, 200]);
  assert.equal(concurrentPrepare[0].payload.dispatch.dispatch_id, processIdentity.dispatch_id);
  assert.equal(concurrentPrepare[1].payload.dispatch.dispatch_id, processIdentity.dispatch_id);
  const changedFingerprint = await api(processPath, {
    method: "POST", body: { ...processIdentity, operation_fingerprint: "c".repeat(64) },
  });
  assertStatus(changedFingerprint, 409, "operation fingerprint cannot change for one task/step");

  // A process killed after the durable prepare may resume before start.
  await stopWorker(server);
  server = launchWorker();
  await waitForWorker(server, () => logs);
  const prepareAfterRestart = await api(processPath, { method: "POST", body: processIdentity });
  assertStatus(prepareAfterRestart, 200, "prepared identity survives Worker restart");
  assert.equal(prepareAfterRestart.payload.dispatch.status, "prepared");
  const processStarted = await api(processPath, {
    method: "POST", body: { action: "start", lease_token: processLease, dispatch_id: processIdentity.dispatch_id },
  });
  assertStatus(processStarted, 200, "durable process start");
  const duplicateProcessStart = await api(processPath, {
    method: "POST", body: { action: "start", lease_token: processLease, dispatch_id: processIdentity.dispatch_id },
  });
  assertStatus(duplicateProcessStart, 409, "process start is at most once");

  // A worker crash after start leaves in_flight; restart never authorizes replay.
  await stopWorker(server);
  server = launchWorker();
  await waitForWorker(server, () => logs);
  const inFlightAfterRestart = await api(processPath, { method: "POST", body: processIdentity });
  assertStatus(inFlightAfterRestart, 200, "in-flight identity survives Worker restart");
  assert.equal(inFlightAfterRestart.payload.dispatch.status, "in_flight");
  const replayStart = await api(processPath, {
    method: "POST", body: { action: "start", lease_token: processLease, dispatch_id: processIdentity.dispatch_id },
  });
  assertStatus(replayStart, 409, "started process cannot be replayed");

  const processOutcome = {
    classification: "succeeded", exit_code: 0, signal_number: null,
    timed_out: false, cancelled: false, output_truncated: false,
    stdout: "local D1 process outcome", stderr: "", duration_seconds: 0.02,
    descendants_killed: 0,
  };
  const processSettled = await api(processPath, {
    method: "POST", body: { action: "settle", lease_token: processLease,
      dispatch_id: processIdentity.dispatch_id, outcome: processOutcome },
  });
  assertStatus(processSettled, 200, "persist versioned process outcome");
  const settleResponseReplay = await api(processPath, {
    method: "POST", body: { action: "settle", lease_token: processLease,
      dispatch_id: processIdentity.dispatch_id, outcome: processOutcome },
  });
  assertStatus(settleResponseReplay, 200, "lost settle response is idempotently reconcilable");
  assert.equal(settleResponseReplay.payload.replay, true);
  const conflictingSettle = await api(processPath, {
    method: "POST", body: { action: "settle", lease_token: processLease,
      dispatch_id: processIdentity.dispatch_id,
      outcome: { ...processOutcome, exit_code: 1, classification: "failed" } },
  });
  assertStatus(conflictingSettle, 409, "settled process outcome is immutable");
  await stopWorker(server);
  server = launchWorker();
  await waitForWorker(server, () => logs);
  const settledAfterRestart = await api(processPath, { method: "POST", body: processIdentity });
  assertStatus(settledAfterRestart, 200, "settled outcome survives Worker restart");
  assert.equal(settledAfterRestart.payload.dispatch.status, "settled");
  assert.deepEqual(settledAfterRestart.payload.dispatch.outcome, processOutcome);
  const processFinished = await api("/v1/tasks/" + processTaskId + "/finish", {
    method: "POST", body: { lease_token: processLease, status: "succeeded", result: { process: processOutcome } },
  });
  assertStatus(processFinished, 200, "finish process task after durable settlement");

  // Two concurrent task claims cannot share one lease or dispatch identity.
  const concurrentProcessTasks = await Promise.all(["claim-a", "claim-b"].map(key => api("/v1/tasks/enqueue", {
    method: "POST", body: { task_type: "supervised_probe", title: "Concurrent process claim",
      payload: { operation: "python_platform_probe" }, idempotency_key: "d1-local-" + key + "-" + randomUUID(),
      max_attempts: 2, priority: 99 },
  })));
  for (const item of concurrentProcessTasks) assertStatus(item, 201, "enqueue concurrent process task");
  const concurrentClaims = await Promise.all([
    api("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } }),
    api("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } }),
  ]);
  assert.deepEqual(concurrentClaims.map(item => item.status), [200, 200]);
  const claimedIds = concurrentClaims.map(item => item.payload.task.id).sort();
  const expectedClaimIds = concurrentProcessTasks.map(item => item.payload.task.id).sort();
  assert.deepEqual(claimedIds, expectedClaimIds, "concurrent claims are disjoint and complete");
  for (const claim of concurrentClaims) {
    const taskId = claim.payload.task.id;
    const path = "/v1/tasks/" + taskId + "/process-dispatches";
    const identity = { ...processIdentity, action: "prepare", lease_token: claim.payload.lease_token,
      dispatch_id: randomUUID().replaceAll("-", ""), operation_fingerprint: "d".repeat(64) };
    const preparedDispatch = await api(path, { method: "POST", body: identity });
    assertStatus(preparedDispatch, 200, "prepare independently claimed process dispatch");
    const startedDispatch = await api(path, { method: "POST", body: { action: "start",
      lease_token: claim.payload.lease_token, dispatch_id: identity.dispatch_id } });
    assertStatus(startedDispatch, 200, "start independently claimed process dispatch");
    const ambiguousDispatch = await api(path, { method: "POST", body: { action: "ambiguous",
      lease_token: claim.payload.lease_token, dispatch_id: identity.dispatch_id, detail: "test-interrupted" } });
    assertStatus(ambiguousDispatch, 200, "quarantine uncertain process dispatch");
    const finished = await api("/v1/tasks/" + taskId + "/finish", { method: "POST",
      body: { lease_token: claim.payload.lease_token, status: "blocked", error: "local ambiguity test" } });
    assertStatus(finished, 200, "finish quarantined process task");
  }

  // A lost lease after start is fenced; its replacement worker sees in_flight and
  // can only quarantine it, never start a second process.
  const staleTask = await api("/v1/tasks/enqueue", {
    method: "POST", body: { task_type: "supervised_probe", title: "Stale lease process dispatch",
      payload: { operation: "python_platform_probe" }, idempotency_key: "d1-local-stale-" + randomUUID(),
      max_attempts: 3, priority: 100 },
  });
  assertStatus(staleTask, 201, "enqueue stale lease dispatch task");
  const staleClaim = await api("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } });
  assertStatus(staleClaim, 200, "claim stale lease dispatch task");
  const staleId = staleTask.payload.task.id;
  const stalePath = "/v1/tasks/" + staleId + "/process-dispatches";
  const staleIdentity = { ...processIdentity, lease_token: staleClaim.payload.lease_token,
    dispatch_id: randomUUID().replaceAll("-", ""), operation_fingerprint: "e".repeat(64) };
  assertStatus(await api(stalePath, { method: "POST", body: staleIdentity }), 200, "prepare before stale lease");
  assertStatus(await api(stalePath, { method: "POST", body: { action: "start",
    lease_token: staleClaim.payload.lease_token, dispatch_id: staleIdentity.dispatch_id } }), 200, "start before stale lease");
  executeLocalSql("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00.000Z' WHERE id='" + staleId + "'");
  const staleRecoveries = await Promise.all([
    api("/v1/tasks/recover", { method: "POST", body: {} }),
    api("/v1/tasks/recover", { method: "POST", body: {} }),
  ]);
  staleRecoveries.forEach(result => assertStatus(result, 200, "recover expired supervised task lease"));
  assert.equal(staleRecoveries.reduce((sum, result) => sum + result.payload.requeued, 0), 1,
    "concurrent recovery reports one actual transition");
  const recoveryAudit = await api("/v1/audit?limit=30&task_id=" + staleId);
  assertStatus(recoveryAudit, 200, "read recovery audit");
  assert.equal(recoveryAudit.payload.events.filter(event => event.event === "task_lease_recovered").length, 1,
    "concurrent recovery produces one audit event");
  const replacementClaim = await api("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } });
  assertStatus(replacementClaim, 200, "replacement worker claims stale task");
  assert.equal(replacementClaim.payload.task.id, staleId);
  const staleSettle = await api(stalePath, { method: "POST", body: { action: "settle",
    lease_token: staleClaim.payload.lease_token, dispatch_id: staleIdentity.dispatch_id,
    outcome: processOutcome } });
  assertStatus(staleSettle, 409, "lost lease cannot settle process result");
  const replacementPrepare = await api(stalePath, { method: "POST", body: {
    ...staleIdentity, lease_token: replacementClaim.payload.lease_token,
  } });
  assertStatus(replacementPrepare, 200, "replacement observes durable in-flight identity");
  assert.equal(replacementPrepare.payload.dispatch.status, "in_flight");
  const staleReplayStart = await api(stalePath, { method: "POST", body: { action: "start",
    lease_token: replacementClaim.payload.lease_token, dispatch_id: staleIdentity.dispatch_id } });
  assertStatus(staleReplayStart, 409, "replacement cannot duplicate uncertain process");
  const staleQuarantine = await api(stalePath, { method: "POST", body: { action: "ambiguous",
    lease_token: replacementClaim.payload.lease_token, dispatch_id: staleIdentity.dispatch_id,
    detail: "lease_lost_after_start" } });
  assertStatus(staleQuarantine, 200, "replacement quarantines stale in-flight dispatch");
  const quarantineReplay = await api(stalePath, { method: "POST", body: { action: "ambiguous",
    lease_token: replacementClaim.payload.lease_token, dispatch_id: staleIdentity.dispatch_id,
    detail: "lease_lost_after_start" } });
  assertStatus(quarantineReplay, 200, "ambiguous state replay is idempotent");
  assert.equal(quarantineReplay.payload.replay, true);
  assertStatus(await api("/v1/tasks/" + staleId + "/finish", { method: "POST",
    body: { lease_token: replacementClaim.payload.lease_token, status: "blocked", error: "ambiguous dispatch" } }),
  200, "finish stale task without replay");

  // End-to-end Python adapter + real supervised child + local D1 Worker API.
  const pythonDispatch = spawnSync("python3", ["tests/fixtures/remote_process_dispatch_smoke.py"], {
    cwd: process.cwd(), env: { ...safeEnvironment(), PYTHONPATH: process.cwd(),
      RAAWAA_LOCAL_WORKER_URL: BASE, RAAWAA_LOCAL_WORKER_TOKEN: TOKEN },
    encoding: "utf8", timeout: 30_000,
    maxBuffer: 2 * 1024 * 1024,
  });
  if (pythonDispatch.error || pythonDispatch.status !== 0) {
    throw new Error("Python remote process dispatch smoke failed.\n" +
      (pythonDispatch.stdout || "") + "\n" + (pythonDispatch.stderr || "") +
      (pythonDispatch.error ? "\n" + pythonDispatch.error.message : ""));
  }
  const pythonDispatchResult = JSON.parse(pythonDispatch.stdout.trim());
  assert.equal(pythonDispatchResult.task_status, "succeeded");
  assert.equal(pythonDispatchResult.classification, "succeeded");
  assert.equal(pythonDispatchResult.replayed, false);
  assert.match(pythonDispatchResult.dispatch_id, /^[0-9a-f]{32}$/);

  // Deterministic child-process fault injection across the real local D1 Worker:
  // start one trusted fixture child, kill its worker before settle, expire only
  // that task lease, recover the exact daily probe, then prove the next owner
  // quarantines the durable in-flight dispatch without starting another child.
  const crashSlot = new Date(FIXTURE_NOW).toISOString().slice(0, 10).replaceAll("-", "");
  const crashKey = "staging-cron-supervised-probe:" + crashSlot;
  const crashTask = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "supervised_probe",
      title: "Scheduled Staging read-only process probe",
      payload: { operation: "python_platform_probe" },
      idempotency_key: crashKey,
      max_attempts: 2,
      priority: 100,
    },
  });
  assertStatus(crashTask, 201, "enqueue bounded daily crash-recovery fixture");
  const crashTaskId = crashTask.payload.task.id;
  crashMarkerPath = join(root, "remote-child-starts.txt");
  const firstCrashWorker = launchCrashFixture("start", crashMarkerPath);
  await waitForMarker(firstCrashWorker, crashMarkerPath);
  const firstChildPid = Number(readFileSync(crashMarkerPath, "utf8").trim().split(/\\s+/)[0]);
  assert.ok(Number.isInteger(firstChildPid) && firstChildPid > 1);
  crashChildPids.add(firstChildPid);
  const firstLeaseRows = queryLocalSql(
    "SELECT lease_token FROM tasks WHERE id='" + crashTaskId + "'",
  );
  assert.equal(firstLeaseRows.length, 1);
  const oldCrashLease = firstLeaseRows[0].lease_token;
  assert.equal(typeof oldCrashLease, "string");
  const firstDispatchRows = queryLocalSql(
    "SELECT task_id,status,dispatch_id FROM task_process_dispatches WHERE task_id='" + crashTaskId + "'",
  );
  assert.equal(firstDispatchRows.length, 1);
  assert.equal(firstDispatchRows[0].status, "in_flight");
  assert.equal(firstDispatchRows[0].task_id, crashTaskId);
  let crashTasks = await api("/v1/tasks?limit=100");
  assertStatus(crashTasks, 200, "read task before injected crash");
  let crashTaskRow = crashTasks.payload.tasks.find(task => task.id === crashTaskId);
  assert.equal(crashTaskRow.status, "running");
  assert.equal(crashTaskRow.attempts, 1);
  firstCrashWorker.kill("SIGKILL");
  await waitForExit(firstCrashWorker);
  try { process.kill(firstChildPid, "SIGKILL"); } catch { /* fixture child already exited */ }
  await delay(100);
  crashChildPids.delete(firstChildPid);

  executeLocalSql(
    "UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00.000Z' " +
    "WHERE id='" + crashTaskId + "' AND idempotency_key='" + crashKey + "' AND status='running'",
  );
  const recoveryPath = "/v1/tasks/" + crashTaskId + "/recover-supervised-probe";
  const lostRecoveryResponse = await fetch(BASE + recoveryPath, {
    method: "POST",
    headers: { authorization: "Bearer " + TOKEN, "content-type": "application/json" },
    body: "{}",
    signal: AbortSignal.timeout(10_000),
  });
  assert.equal(lostRecoveryResponse.status, 200, "first recovery request reaches the Worker");
  // Client-discarded successful response: this approximates a lost reply at
  // the client (the Worker committed and the client received the response,
  // then discarded its body). It is NOT a transport-level dropped response;
  // no claim of a commit-then-drop network fault is made here.
  await lostRecoveryResponse.text();
  const recoveredProbe = await api(recoveryPath, { method: "POST", body: {} });
  assertStatus(recoveredProbe, 200, "retry after a lost recovery response");
  assert.deepEqual(recoveredProbe.payload, {
    status: "pending", recovered: false, replay: true, attempts: 1,
    max_attempts: 2,
  });
  crashTasks = await api("/v1/tasks?limit=100");
  crashTaskRow = crashTasks.payload.tasks.find(task => task.id === crashTaskId);
  assert.equal(crashTaskRow.status, "pending");
  assert.equal(crashTaskRow.attempts, 1);
  const retainedDispatch = queryLocalSql(
    "SELECT task_id,status,dispatch_id FROM task_process_dispatches WHERE task_id='" + crashTaskId + "'",
  );
  assert.equal(retainedDispatch.length, 1, "recovery preserves exactly one dispatch row");
  assert.equal(retainedDispatch[0].status, "in_flight");
  assert.equal(retainedDispatch[0].dispatch_id, firstDispatchRows[0].dispatch_id);

  const auditBeforeReplay = queryLocalSql(
    "SELECT COUNT(*) AS count FROM audit_log WHERE task_id='" + crashTaskId +
      "' AND event='supervised_probe_lease_recovered'",
  )[0].count;
  const duplicateRecovery = await api(
    "/v1/tasks/" + crashTaskId + "/recover-supervised-probe", { method: "POST", body: {} },
  );
  assertStatus(duplicateRecovery, 200, "duplicate recovery is idempotent");
  assert.equal(duplicateRecovery.payload.recovered, false);
  assert.equal(duplicateRecovery.payload.replay, true);
  const auditAfterReplay = queryLocalSql(
    "SELECT COUNT(*) AS count FROM audit_log WHERE task_id='" + crashTaskId +
      "' AND event='supervised_probe_lease_recovered'",
  )[0].count;
  assert.equal(auditAfterReplay, auditBeforeReplay, "duplicate recovery adds no second recovery audit");

  const crashProcessPath = "/v1/tasks/" + crashTaskId + "/process-dispatches";
  const oldLeaseHeartbeat = await api("/v1/tasks/" + crashTaskId + "/heartbeat", {
    method: "POST", body: { lease_token: oldCrashLease },
  });
  assertStatus(oldLeaseHeartbeat, 409, "recovered task rejects stale heartbeat");
  const oldLeaseFinish = await api("/v1/tasks/" + crashTaskId + "/finish", {
    method: "POST", body: { lease_token: oldCrashLease, status: "succeeded", result: { stale: true } },
  });
  assertStatus(oldLeaseFinish, 409, "recovered task rejects stale finish/overwrite");
  const processRows = queryLocalSql(
    "SELECT dispatch_id,operation_fingerprint,lease_fingerprint,status FROM task_process_dispatches " +
      "WHERE task_id='" + crashTaskId + "'",
  );
  const crashIdentity = processRows[0];
  const crashStaleSettle = await api(crashProcessPath, { method: "POST", body: {
    action: "settle", lease_token: oldCrashLease, dispatch_id: crashIdentity.dispatch_id,
    outcome: processOutcome,
  } });
  assertStatus(crashStaleSettle, 409, "lost lease cannot settle the original child result");

  const claimFixture = launchCrashFixture("claim", crashMarkerPath, [crashTaskId]);
  await waitForExit(claimFixture, 10_000);
  assert.equal(claimFixture.exitCode, 0,
    "new owner claims only the recovered exact task: " + claimFixture.fixtureOutput());
  const recoveredClaim = JSON.parse(claimFixture.fixtureOutput().trim());
  assert.equal(recoveredClaim.task_id, crashTaskId);
  assert.equal(recoveredClaim.attempts, 2);
  assert.notEqual(recoveredClaim.lease_token, oldCrashLease);
  const staleOwnerOverwrite = await api("/v1/tasks/" + crashTaskId + "/finish", {
    method: "POST", body: { lease_token: oldCrashLease, status: "failed", error: "stale overwrite" },
  });
  assertStatus(staleOwnerOverwrite, 409, "stale owner cannot overwrite the new owner's task");

  const resumedWorker = launchCrashFixture("resume", crashMarkerPath,
    [crashTaskId, recoveredClaim.lease_token]);
  await waitForExit(resumedWorker, 15_000);
  assert.equal(resumedWorker.exitCode, 0,
    "replacement worker quarantines the ambiguous dispatch: " + resumedWorker.fixtureOutput());
  const resumedSummary = JSON.parse(resumedWorker.fixtureOutput().trim().split("\\n").at(-1));
  assert.equal(resumedSummary.status, "blocked");
  assert.equal(resumedSummary.classification, "ambiguous");
  const childStartPids = readFileSync(crashMarkerPath, "utf8").trim().split(/\\s+/).map(Number);
  for (const pid of childStartPids.slice(1)) crashChildPids.add(pid);
  assert.equal(childStartPids.length, 1, "the recovery path must not start a second child");
  const quarantinedDispatch = queryLocalSql(
    "SELECT status FROM task_process_dispatches WHERE task_id='" + crashTaskId + "'",
  );
  assert.equal(quarantinedDispatch[0].status, "ambiguous");

  const duplicateDailyEnqueue = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "supervised_probe",
      title: "Scheduled Staging read-only process probe",
      payload: { operation: "python_platform_probe" },
      idempotency_key: crashKey,
      max_attempts: 2,
      priority: 100,
    },
  });
  assertStatus(duplicateDailyEnqueue, 200, "same UTC daily schedule slot is idempotent");
  assert.equal(duplicateDailyEnqueue.payload.created, false);
  assert.equal(duplicateDailyEnqueue.payload.task.id, crashTaskId);
  assert.equal(readFileSync(crashMarkerPath, "utf8").trim().split(/\\s+/).length, 1);

  // Exact production envelope (max_attempts=1): after a child start and worker
  // death, recovery must terminally fail the task, quarantine the dispatch as
  // ambiguous, and never start a second child — without raising the retry budget.
  const exactSlot = new Date(FIXTURE_NOW - 86_400_000).toISOString().slice(0, 10).replaceAll("-", "");
  const exactKey = "staging-cron-supervised-probe:" + exactSlot;
  const exactTask = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "supervised_probe",
      title: "Scheduled Staging read-only process probe",
      payload: { operation: "python_platform_probe" },
      idempotency_key: exactKey,
      max_attempts: 1,
      priority: 100,
    },
  });
  assertStatus(exactTask, 201, "enqueue exact one-attempt production envelope fixture");
  const exactTaskId = exactTask.payload.task.id;
  const exactMarkerPath = join(root, "exact-child-starts.txt");
  const exactWorker = launchCrashFixture("start", exactMarkerPath);
  await waitForMarker(exactWorker, exactMarkerPath);
  const exactChildPid = Number(readFileSync(exactMarkerPath, "utf8").trim().split(/\s+/)[0]);
  assert.ok(Number.isInteger(exactChildPid) && exactChildPid > 1);
  crashChildPids.add(exactChildPid);
  exactWorker.kill("SIGKILL");
  await waitForExit(exactWorker);
  try { process.kill(exactChildPid, "SIGKILL"); } catch { /* fixture child already exited */ }
  await delay(100);
  crashChildPids.delete(exactChildPid);
  executeLocalSql(
    "UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00.000Z' " +
    "WHERE id='" + exactTaskId + "' AND idempotency_key='" + exactKey + "' AND status='running'",
  );
  const exactRecovery = await api(
    "/v1/tasks/" + exactTaskId + "/recover-supervised-probe", { method: "POST", body: {} },
  );
  assertStatus(exactRecovery, 200, "recover exact one-attempt envelope after the crash");
  assert.deepEqual(exactRecovery.payload, {
    status: "failed", recovered: true, replay: false, attempts: 1,
    max_attempts: 1, dispatch_status: "ambiguous",
  });
  const exactDispatch = queryLocalSql(
    "SELECT status FROM task_process_dispatches WHERE task_id='" + exactTaskId + "'",
  );
  assert.equal(exactDispatch[0].status, "ambiguous");
  assert.equal(readFileSync(exactMarkerPath, "utf8").trim().split(/\s+/).length, 1,
    "the one-attempt recovery must not start a second child");

  // Concurrent recovery race on a real local D1 row: two simultaneous
  // recoveries of one expired lease produce exactly one transition and one
  // audit event; the loser is a deterministic replay, never a second lease.
  const raceSlot = new Date(FIXTURE_NOW - 2 * 86_400_000).toISOString().slice(0, 10).replaceAll("-", "");
  const raceKey = "staging-cron-supervised-probe:" + raceSlot;
  const raceTask = await api("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "supervised_probe",
      title: "Scheduled Staging read-only process probe",
      payload: { operation: "python_platform_probe" },
      idempotency_key: raceKey,
      max_attempts: 2,
      priority: 100,
    },
  });
  assertStatus(raceTask, 201, "enqueue concurrent-recovery race fixture");
  const raceTaskId = raceTask.payload.task.id;
  const raceClaim = await api(
    "/v1/tasks/" + raceTaskId + "/claim-supervised-probe", { method: "POST", body: {} },
  );
  assertStatus(raceClaim, 200, "claim concurrent-recovery race fixture");
  executeLocalSql(
    "UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00.000Z' " +
    "WHERE id='" + raceTaskId + "' AND status='running'",
  );
  const racePath = BASE + "/v1/tasks/" + raceTaskId + "/recover-supervised-probe";
  const raceHeaders = { authorization: "Bearer " + TOKEN, "content-type": "application/json" };
  const [raceFirst, raceSecond] = await Promise.all([
    fetch(racePath, { method: "POST", headers: raceHeaders, body: "{}", signal: AbortSignal.timeout(10_000) }),
    fetch(racePath, { method: "POST", headers: raceHeaders, body: "{}", signal: AbortSignal.timeout(10_000) }),
  ]);
  const raceBodies = [await raceFirst.json(), await raceSecond.json()];
  assert.equal(raceBodies.filter(body => body.recovered === true).length, 1,
    "exactly one concurrent recovery transitions the task");
  assert.equal(raceBodies.filter(body => body.recovered === false).length, 1,
    "the losing concurrent recovery is a deterministic replay/lease_lost");
  const raceAudits = queryLocalSql(
    "SELECT COUNT(*) AS count FROM audit_log WHERE task_id='" + raceTaskId +
      "' AND event='supervised_probe_lease_recovered'",
  )[0].count;
  assert.equal(raceAudits, 1, "concurrent recovery writes exactly one audit event");
  const raceRows = queryLocalSql(
    "SELECT status,lease_token FROM tasks WHERE id='" + raceTaskId + "'",
  );
  assert.equal(raceRows[0].status, "pending");
  assert.equal(raceRows[0].lease_token, null, "recovery clears the lease; no second lease is issued");

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

  const researchBatchId = "d1-local:" + randomUUID();
  const researchPayload = {
    batch_id: researchBatchId,
    candidates: [{
      source: "github",
      full_name: "local/research-idempotency",
      url: "https://github.com/local/research-idempotency",
      description: "Local D1 idempotency fixture",
      stars: 1,
      license_spdx: "MIT",
      topics: ["local-test"],
    }],
  };
  const researchInserted = await api("/v1/research/candidates", {
    method: "POST", body: researchPayload,
  });
  assertStatus(researchInserted, 201, "persist research candidate batch");
  assert.equal(researchInserted.payload.replayed, false);
  const researchReplayed = await api("/v1/research/candidates", {
    method: "POST", body: researchPayload,
  });
  assertStatus(researchReplayed, 200, "replay research candidate batch");
  assert.equal(researchReplayed.payload.replayed, true);
  const researchConflict = await api("/v1/research/candidates", {
    method: "POST",
    body: {
      batch_id: researchBatchId,
      candidates: [{
        source: "github",
        full_name: "local/changed-payload",
        url: "https://github.com/local/changed-payload",
      }],
    },
  });
  assertStatus(researchConflict, 409, "changed payload under a reused research batch ID");
  const researchVerified = await api("/v1/research/candidates/verify", {
    method: "POST", body: { source_keys: ["github:local/research-idempotency"] },
  });
  assertStatus(researchVerified, 200, "exact-key research D1 readback");
  assert.deepEqual(researchVerified.payload.matched_keys, ["github:local/research-idempotency"]);
  const researchRow = queryLocalSql("SELECT discovery_count,review_status FROM research_candidates WHERE source_key='github:local/research-idempotency'")[0];
  assert.equal(researchRow.discovery_count, 1, "batch replay must not inflate discovery_count");
  assert.equal(researchRow.review_status, "unreviewed", "ingestion replay must preserve review state");

  status = await api("/v1/mobile/status");
  assertStatus(status, 200, "mobile status after D1 writes");
  assert.equal(status.payload.paper_events.find(row => row.event_type === "candidate_detected")?.count, 1);
  assert.equal(status.payload.tasks.find(row => row.status === "succeeded")?.count, 17,
    "workflow, dependency, supervised-process, exact-claim, and concurrent-claim fixtures should be durably succeeded");
  assert.equal(status.payload.tasks.find(row => row.status === "failed")?.count, 3,
    "failed dependency roots remain visibly failed");
  assert.equal(status.payload.tasks.find(row => row.status === "blocked")?.count, 8,
    "failed dependencies and no-replay supervised tasks remain blocked");
  assert.equal(status.payload.monetary_ledger.available, true);
  assert.equal(status.payload.monetary_ledger.pending_count, 1);
  assert.equal(status.payload.monetary_ledger.ambiguous_count, 1);
  assert.equal(status.payload.monetary_ledger.daily_spent_usd, 0.25);
  assert.equal(status.payload.model_costs_usd.total, 0.25);
  assert.deepEqual(Object.keys(status.payload.provider_usage).sort(), ["requests", "tokens"]);
  assert.equal("by_provider" in status.payload.model_costs_usd, false);
  assert.equal("unresolved" in status.payload.monetary_ledger, false);

  const webhookDisabled = await api("/v1/telegram/webhook", {
    method: "POST",
    body: { update_id: 1 },
  });
  assertStatus(webhookDisabled, 404, "Retired Telegram webhook must not exist");

  console.log(
    "Local D1 integration passed: migrations, authenticated status, task enqueue/idempotency/lease fencing, " +
    "process-dispatch prepare/start/settle and ambiguity, concurrent claims, stale-lease fencing and restart no-replay, " +
    "exact daily-probe recovery with interrupted-child fault injection and no second child start, " +
    "exact one-attempt production-envelope crash recovery and concurrent-recovery race, Python remote adapter, " +
    "monetary/quota and paper ledgers, research batch idempotency/exact-key readback, and removed Telegram webhook.",
  );
} catch (error) {
  throw new Error(String(error?.stack || error) + "\nWrangler logs:\n" + logs);
} finally {
  await stopWorker(server);
  killCrashChildren(crashChildPids);
  rmSync(root, { recursive: true, force: true });
}
