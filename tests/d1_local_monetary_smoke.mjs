import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const PORT = 8793;
const BASE = "http://127.0.0.1:" + PORT;
const TOKEN = "test-engine-token-monetary-local-only";
const WRANGLER = ["--yes", "wrangler@4.156.1"];
const root = mkdtempSync(join(tmpdir(), "raawaa-monetary-local-"));
const stateDir = join(root, "state");
const homeDir = join(root, "home");
mkdirSync(stateDir, { recursive: true });
mkdirSync(homeDir, { recursive: true });
let server = null;
let logs = "";

function safeEnvironment() {
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
    cwd: process.cwd(), env: safeEnvironment(), encoding: "utf8",
    timeout: 120000, maxBuffer: 2 * 1024 * 1024,
  });
  if (result.error || result.status !== 0) {
    throw new Error("Local monetary D1 migrations failed:\n" +
      (result.stdout || "") + "\n" + (result.stderr || "") +
      (result.error ? "\n" + result.error.message : ""));
  }
}

function startWorker() {
  const child = spawn("npx", [
    ...WRANGLER, "dev", "--config", "wrangler.toml", "--local",
    "--ip", "127.0.0.1", "--port", String(PORT), "--log-level", "error",
    "--persist-to", stateDir, "--var", "ENGINE_API_TOKEN:" + TOKEN,
  ], {
    cwd: process.cwd(), env: safeEnvironment(),
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32",
  });
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", chunk => { logs = (logs + chunk).slice(-20000); });
  child.stderr.on("data", chunk => { logs = (logs + chunk).slice(-20000); });
  return child;
}

async function stopWorker(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform !== "win32" && child.pid) {
    try { process.kill(-child.pid, "SIGTERM"); } catch { /* already exited */ }
  } else child.kill("SIGTERM");
  await Promise.race([new Promise(resolve => child.once("exit", resolve)), delay(5000)]);
  if (child.exitCode === null && child.signalCode === null) {
    if (process.platform !== "win32" && child.pid) {
      try { process.kill(-child.pid, "SIGKILL"); } catch { /* already exited */ }
    } else child.kill("SIGKILL");
  }
}

async function api(path, { method = "GET", body, token = TOKEN } = {}) {
  const headers = { accept: "application/json" };
  if (token) headers.authorization = "Bearer " + token;
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await fetch(BASE + path, {
    method, headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(10000),
  });
  const raw = await response.text();
  let payload;
  try { payload = raw ? JSON.parse(raw) : null; }
  catch { throw new Error("Worker returned non-JSON HTTP " + response.status); }
  return { status: response.status, payload };
}

async function waitForWorker(child) {
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error("Wrangler exited:\n" + logs);
    try {
      const result = await api("/health", { token: null });
      if (result.status === 200 && result.payload?.ok === true) return;
    } catch { /* Worker is still starting */ }
    await delay(250);
  }
  throw new Error("Local Worker did not become ready:\n" + logs);
}

function assertStatus(result, expected, label) {
  assert.equal(result.status, expected, label + ": " + JSON.stringify(result.payload));
}

try {
  runMigrations();
  server = startWorker();
  await waitForWorker(server);

  assertStatus(await api("/v1/monetary-ledger", { token: null }), 401, "monetary ledger authentication");
  let status = await api("/v1/monetary-ledger");
  assertStatus(status, 200, "initial budget status");
  assert.equal(status.payload.caps.daily_nanos, 400000000);
  assert.equal(status.payload.caps.monthly_nanos, 12000000000);
  assert.equal(status.payload.caps.per_task_nanos, 200000000);
  assert.equal(status.payload.pending.count, 0);
  assert.equal("provider" in status.payload, false, "status must not expose provider billing details");
  assert.equal("task_id" in status.payload, false, "status must not expose task identifiers");

  const base = {
    action: "reserve",
    provider: "provider-a",
    model: "model-a",
    task_kind: "research",
    task_id: "local-monetary-concurrency",
    estimated_cost_nanos: 150000000,
    daily_cap_nanos: 400000000,
    monthly_cap_nanos: 12000000000,
    per_task_cap_nanos: 200000000,
  };
  const concurrent = await Promise.all([
    api("/v1/monetary-ledger", { method: "POST", body: { ...base, reservation_id: randomUUID() } }),
    api("/v1/monetary-ledger", { method: "POST", body: { ...base, provider: "provider-b", reservation_id: randomUUID() } }),
    api("/v1/monetary-ledger", { method: "POST", body: { ...base, provider: "provider-c", reservation_id: randomUUID() } }),
  ]);
  assert.deepEqual(concurrent.map(item => item.status).sort((a, b) => a - b), [201, 409, 409],
    "per-task cap must be atomic across providers and concurrent requests");
  const reservationId = concurrent.find(item => item.status === 201).payload.reservation_id;

  const idempotentId = randomUUID();
  const idempotentBody = { ...base, task_id: "local-idempotent-task", estimated_cost_nanos: 10000000, reservation_id: idempotentId };
  assertStatus(await api("/v1/monetary-ledger", { method: "POST", body: idempotentBody }), 201, "idempotent reservation create");
  const idempotentReplay = await api("/v1/monetary-ledger", { method: "POST", body: idempotentBody });
  assertStatus(idempotentReplay, 200, "idempotent reservation replay");
  assert.equal(idempotentReplay.payload.replay, true);
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST", body: { ...idempotentBody, task_id: "different-task" },
  }), 409, "reservation ID cannot be reused with different metadata");
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "release", reservation_id: idempotentId, reason: "provider_not_called" },
  }), 200, "release idempotent test reservation");

  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "mark_started", reservation_id: reservationId },
  }), 200, "mark provider I/O started");
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "release", reservation_id: reservationId, reason: "provider_not_called" },
  }), 409, "release after provider I/O is forbidden");
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "mark_ambiguous", reservation_id: reservationId, reason: "provider_timeout" },
  }), 200, "mark provider timeout ambiguous");
  status = await api("/v1/monetary-ledger?task_id=local-monetary-concurrency");
  assertStatus(status, 200, "ambiguous status read");
  assert.equal(status.payload.pending.count, 1);
  assert.equal(status.payload.pending.ambiguous_count, 1);
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST",
    body: { ...base, estimated_cost_nanos: 100000000, reservation_id: randomUUID() },
  }), 409, "ambiguous reservation continues to consume task budget");

  const settlement = {
    action: "settle", reservation_id: reservationId,
    input_tokens: 5, output_tokens: 7, actual_cost_nanos: 50000000,
    cost_basis: "provider_reported",
  };
  assertStatus(await api("/v1/monetary-ledger", { method: "POST", body: settlement }), 200, "settle after reconciliation");
  const replay = await api("/v1/monetary-ledger", { method: "POST", body: settlement });
  assertStatus(replay, 200, "idempotent settlement replay");
  assert.equal(replay.payload.replay, true);
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST", body: { ...settlement, actual_cost_nanos: 60000000 },
  }), 409, "conflicting settlement rejected");

  const releaseId = randomUUID();
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST",
    body: { ...base, task_id: "local-release-task", estimated_cost_nanos: 50000000, reservation_id: releaseId },
  }), 201, "pre-provider reservation");
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "release", reservation_id: releaseId, reason: "provider_not_called" },
  }), 200, "pre-provider release");
  const releaseReplay = await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "release", reservation_id: releaseId, reason: "provider_not_called" },
  });
  assertStatus(releaseReplay, 200, "idempotent release replay");
  assert.equal(releaseReplay.payload.replay, true);

  const overId = randomUUID();
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST",
    body: { ...base, task_id: "local-over-cap-task", estimated_cost_nanos: 50000000, reservation_id: overId },
  }), 201, "over-cap test reservation");
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "mark_started", reservation_id: overId },
  }), 200, "over-cap test start");
  const over = await api("/v1/monetary-ledger", {
    method: "POST",
    body: {
      action: "settle", reservation_id: overId, input_tokens: 1, output_tokens: 1,
      actual_cost_nanos: 250000000, cost_basis: "provider_reported",
    },
  });
  assertStatus(over, 200, "actual over-cap cost must be recorded");
  assert.equal(over.payload.over_budget, true);
  assert.equal(over.payload.budget_status.per_task.over_budget, true);

  const restartId = randomUUID();
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST",
    body: { ...base, task_id: "local-restart-task", estimated_cost_nanos: 50000000, reservation_id: restartId },
  }), 201, "restart persistence reservation");
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "mark_started", reservation_id: restartId },
  }), 200, "restart persistence start");
  await stopWorker(server);
  server = null;
  logs = "";
  server = startWorker();
  await waitForWorker(server);
  status = await api("/v1/monetary-ledger?task_id=local-restart-task");
  assertStatus(status, 200, "status after Worker restart");
  assert.equal(status.payload.per_task.pending_nanos, 50000000);
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "release", reservation_id: restartId, reason: "provider_not_called" },
  }), 409, "in-flight reservation remains unreleasable after restart");
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "mark_ambiguous", reservation_id: restartId, reason: "settlement_uncertain" },
  }), 200, "persisted in-flight reservation can become ambiguous after restart");
  assertStatus(await api("/v1/monetary-ledger", {
    method: "POST",
    body: {
      action: "settle", reservation_id: restartId, input_tokens: 2, output_tokens: 3,
      actual_cost_nanos: 10000000, cost_basis: "provider_reported",
    },
  }), 200, "settle persisted reservation after restart");

  const mobile = await api("/v1/mobile/status");
  assertStatus(mobile, 200, "mobile status exposes durable monetary budget");
  assert.equal(mobile.payload.monetary_budget.available, true);
  assert.equal(mobile.payload.monetary_budget.pending.count, 0);
  assert.equal(mobile.payload.monetary_budget.pending.ambiguous_count, 0);

  console.log("Local D1 monetary integration passed: atomic cross-provider caps, timeout ambiguity, " +
    "idempotent settlement, pre-I/O release, over-cap recording, and restart persistence.");
} catch (error) {
  throw new Error(String(error?.stack || error) + "\nWrangler logs:\n" + logs);
} finally {
  await stopWorker(server);
  rmSync(root, { recursive: true, force: true });
}
