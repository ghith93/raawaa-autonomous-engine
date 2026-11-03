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

  const claimed = await api("/v1/tasks/claim", { method: "POST", body: {} });
  assertStatus(claimed, 200, "task claim");
  assert.equal(claimed.payload.task.id, enqueued.payload.task.id);
  assert.equal(typeof claimed.payload.lease_token, "string");

  const finished = await api("/v1/tasks/" + enqueued.payload.task.id + "/finish", {
    method: "POST",
    body: {
      lease_token: claimed.payload.lease_token,
      status: "succeeded",
      result: { local_d1_verified: true },
    },
  });
  assertStatus(finished, 200, "task finish");
  assert.equal(finished.payload.status, "succeeded");

  const staleLease = await api("/v1/tasks/" + enqueued.payload.task.id + "/heartbeat", {
    method: "POST",
    body: { lease_token: claimed.payload.lease_token },
  });
  assertStatus(staleLease, 409, "finished task must reject stale lease");

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
  assert.equal(status.payload.tasks.find(row => row.status === "succeeded")?.count, 1);
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
