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

let server = null;
let logs = "";

try {
  runMigrations();

  server = spawn("npx", [
    ...WRANGLER, "dev",
    "--config", "wrangler.toml",
    "--local",
    "--ip", "127.0.0.1",
    "--port", String(PORT),
    "--log-level", "error",
    "--persist-to", stateDir,
    "--var", "ENGINE_API_TOKEN:" + TOKEN,
  ], {
    cwd: process.cwd(),
    env: safeEnvironment(),
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32",
  });
  server.stdout.setEncoding("utf8");
  server.stderr.setEncoding("utf8");
  server.stdout.on("data", chunk => { logs = (logs + chunk).slice(-12_000); });
  server.stderr.on("data", chunk => { logs = (logs + chunk).slice(-12_000); });

  await waitForWorker(server, () => logs);

  const unauthenticated = await api("/v1/mobile/status", { token: null });
  assertStatus(unauthenticated, 401, "mobile status must require bearer auth");

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

  const quotaBase = {
    action: "reserve",
    route_id: "d1-local:concurrency",
    task_id: "d1-local-quota-task",
    estimated_tokens: 5,
    daily_request_limit: 10,
    daily_token_limit: 100,
    monthly_request_limit: 20,
    monthly_token_limit: 500,
    minute_request_limit: 2,
    minute_token_limit: 20,
    per_task_request_limit: 3,
    per_task_token_limit: 50,
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
      body: { action: "settle", reservation_id: reservation.payload.reservation_id, actual_tokens: 3 },
    });
    assertStatus(settled, 200, "quota settlement");
  }
  const quota = await api("/v1/quota?route_id=d1-local%3Aconcurrency&task_id=d1-local-quota-task");
  assertStatus(quota, 200, "quota usage read");
  assert.equal(quota.payload.usage.minute.requests, 2);
  assert.equal(quota.payload.usage.minute.tokens, 6);

  const monetaryBase = {
    action: "reserve",
    provider: "d1-local-provider",
    model: "d1-local-model",
    task_kind: "integration_test",
    task_id: "d1-local-monetary-cap-task",
    estimated_cost_nanos: 10_000_000,
    daily_budget_nanos: 1_000_000_000,
    monthly_budget_nanos: 2_000_000_000,
    per_task_budget_nanos: 20_000_000,
  };
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
  const released = await api("/v1/monetary-ledger", {
    method: "POST", body: { action: "release", reservation_id: preCall },
  });
  assertStatus(released, 200, "pre-provider-I/O release");
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
      actual_cost_nanos: 25_000_000, input_tokens: 11, output_tokens: 7,
    },
  });
  assertStatus(settledMoney, 200, "settle actual monetary cost");
  assert.equal(settledMoney.payload.over_budget, true, "actual spend above estimate/task cap must trigger quarantine");
  const duplicateSettlement = await api("/v1/monetary-ledger", {
    method: "POST",
    body: {
      action: "settle", reservation_id: inFlight,
      actual_cost_nanos: 25_000_000, input_tokens: 11, output_tokens: 7,
    },
  });
  assertStatus(duplicateSettlement, 200, "duplicate settlement is idempotent");
  assert.equal(duplicateSettlement.payload.replay, true);
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
    estimated_cost_nanos: 3_000_000,
    per_task_budget_nanos: 20_000_000,
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
  const monetaryStatus = await api("/v1/monetary-ledger");
  assertStatus(monetaryStatus, 200, "read durable monetary status");
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

  const webhookDisabled = await api("/v1/telegram/webhook", {
    method: "POST",
    body: { update_id: 1 },
  });
  assertStatus(webhookDisabled, 503, "Telegram webhook must remain disabled without operator secrets");

  console.log(
    "Local D1 integration passed: migrations, authenticated status, task enqueue/idempotency/lease fencing, " +
    "concurrent token and monetary caps, pre-I/O-only release, ambiguous reservations, idempotent settlement, quarantine, mobile monetary status, append-only paper ledger/hash replay, and disabled Telegram webhook.",
  );
} catch (error) {
  throw new Error(String(error?.stack || error) + "\nWrangler logs:\n" + logs);
} finally {
  if (server && server.exitCode === null && server.signalCode === null) {
    if (process.platform !== "win32" && server.pid) {
      try { process.kill(-server.pid, "SIGTERM"); } catch { /* already exited */ }
    } else {
      server.kill("SIGTERM");
    }
    await Promise.race([
      new Promise(resolve => server.once("exit", resolve)),
      delay(5000),
    ]);
    if (server.exitCode === null && server.signalCode === null) {
      if (process.platform !== "win32" && server.pid) {
        try { process.kill(-server.pid, "SIGKILL"); } catch { /* already exited */ }
      } else {
        server.kill("SIGKILL");
      }
    }
  }
  rmSync(root, { recursive: true, force: true });
}
