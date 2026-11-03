import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import {
  buildReserveStatement,
  buildStatusStatement,
  buildStartStatement,
  buildAmbiguousStatement,
  buildSettleStatement,
  buildReleaseStatement,
} from "../worker/monetary-ledger.js";

const token = process.env.CLOUDFLARE_API_TOKEN || "";
const account = process.env.CLOUDFLARE_ACCOUNT_ID || "";
const database = process.env.D1_DATABASE_ID || "";
if (!token || !account || !database) {
  throw new Error("Cloudflare D1 staging credentials are required; no provider credentials are used.");
}
const endpoint = "https://api.cloudflare.com/client/v4/accounts/" +
  encodeURIComponent(account) + "/d1/database/" + encodeURIComponent(database) + "/query";

async function query(sql, params = []) {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      authorization: "Bearer " + token,
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify({ sql, params }),
    signal: AbortSignal.timeout(20000),
  });
  const body = await response.json();
  if (!response.ok || body.success !== true) {
    const errors = Array.isArray(body.errors) ? body.errors.map(item => item.code).join(",") : "unknown";
    throw new Error("Staging D1 query failed HTTP " + response.status + " (" + errors + ")");
  }
  return body.result?.[0] || { results: [], meta: { changes: 0 } };
}

async function run(statement) {
  return query(statement.sql, statement.params);
}

async function first(sql, params = []) {
  const result = await query(sql, params);
  return result.results?.[0] || null;
}

function currentParts() {
  const now = new Date().toISOString();
  return { now, day: now.slice(0, 10), month: now.slice(0, 7) };
}

async function insertPolicy(key, daily, monthly, perTask) {
  await query(
    "INSERT INTO monetary_budget_policies(policy_key,daily_cap_nanos,monthly_cap_nanos,per_task_cap_nanos,enabled) VALUES(?,?,?,?,1)",
    [key, daily, monthly, perTask],
  );
}

async function reserve(key, taskId, estimate, provider = "provider-a", reservationId = randomUUID()) {
  const { now } = currentParts();
  const statement = buildReserveStatement({
    reservation_id: reservationId,
    provider,
    model: "model-test",
    task_kind: "ci_monetary_test",
    task_id: taskId,
    estimated_cost_nanos: estimate,
  }, now, key);
  const result = await run(statement);
  if (result.results?.[0]?.reservation_id) return { accepted: true, reservationId };
  const existing = await first(
    "SELECT reservation_id,status,provider,model,task_kind,task_id,estimated_cost_nanos,policy_key FROM monetary_reservations WHERE reservation_id=?",
    [reservationId],
  );
  if (existing) return { accepted: false, replay: true, existing };
  return { accepted: false, replay: false, existing: null };
}

async function status(key, taskId = null) {
  const { day, month } = currentParts();
  const statement = buildStatusStatement(key, day, month, taskId);
  const row = await first(statement.sql, statement.params);
  assert.ok(row, "budget policy must exist");
  const pending = Number(row.pending_nanos) || 0;
  const dailySettled = Number(row.daily_settled_nanos) || 0;
  const monthlySettled = Number(row.monthly_settled_nanos) || 0;
  const taskSettled = Number(row.task_settled_nanos) || 0;
  const taskPending = Number(row.task_pending_nanos) || 0;
  return {
    pendingCount: Number(row.pending_count) || 0,
    ambiguousCount: Number(row.ambiguous_count) || 0,
    dailyTotal: dailySettled + pending,
    monthlyTotal: monthlySettled + pending,
    taskTotal: taskSettled + taskPending,
    dailyOver: dailySettled + pending > Number(row.daily_cap_nanos),
    monthlyOver: monthlySettled + pending > Number(row.monthly_cap_nanos),
    taskOver: taskSettled + taskPending > Number(row.per_task_cap_nanos),
  };
}

async function transition(statement) {
  const result = await run(statement);
  return Boolean(result.results?.[0]?.reservation_id);
}

const suffix = Date.now() + "-" + randomUUID().slice(0, 8);
const policyKeys = [
  "ci-test-daily-" + suffix,
  "ci-test-monthly-" + suffix,
  "ci-test-task-" + suffix,
  "ci-test-ambiguous-" + suffix,
  "ci-test-settle-" + suffix,
  "ci-test-release-" + suffix,
  "ci-test-over-" + suffix,
  "ci-test-restart-" + suffix,
];
const [dailyKey, monthlyKey, taskKey, ambiguousKey, settleKey, releaseKey, overKey, restartKey] = policyKeys;

try {
  // Remove abandoned test scopes older than one day; never touch the default policy.
  const cutoff = new Date(Date.now() - 86400000).toISOString();
  await query(
    "DELETE FROM monetary_reservations WHERE policy_key LIKE 'ci-test-%' AND julianday(created_at) < julianday(?)",
    [cutoff],
  );
  await query(
    "DELETE FROM monetary_budget_policies WHERE policy_key LIKE 'ci-test-%' AND julianday(created_at) < julianday(?)",
    [cutoff],
  );

  await insertPolicy(dailyKey, 300000000, 2000000000, 2000000000);
  await insertPolicy(monthlyKey, 2000000000, 300000000, 2000000000);
  await insertPolicy(taskKey, 2000000000, 2000000000, 200000000);
  await insertPolicy(ambiguousKey, 2000000000, 2000000000, 200000000);
  await insertPolicy(settleKey, 2000000000, 2000000000, 2000000000);
  await insertPolicy(releaseKey, 2000000000, 2000000000, 2000000000);
  await insertPolicy(overKey, 2000000000, 2000000000, 200000000);
  await insertPolicy(restartKey, 2000000000, 2000000000, 2000000000);

  // Concurrent reservations from different providers must share the same daily cap.
  const daily = await Promise.all(Array.from({ length: 12 }, (_, i) =>
    reserve(dailyKey, "daily-task-" + i, 150000000, "provider-" + (i % 3))));
  assert.equal(daily.filter(item => item.accepted).length, 2, "daily cap must admit exactly two 0.15 USD reservations");
  assert.equal((await status(dailyKey)).dailyTotal, 300000000);

  // Monthly cap is independently exercised with a deliberately higher daily cap.
  const monthly = await Promise.all(Array.from({ length: 12 }, (_, i) =>
    reserve(monthlyKey, "monthly-task-" + i, 150000000, "provider-" + (i % 4))));
  assert.equal(monthly.filter(item => item.accepted).length, 2, "monthly cap must admit exactly two 0.15 USD reservations");
  assert.equal((await status(monthlyKey)).monthlyTotal, 300000000);

  // Per-task cap is global across providers and concurrent runners.
  const perTask = await Promise.all(Array.from({ length: 12 }, (_, i) =>
    reserve(taskKey, "same-task", 150000000, "provider-" + (i % 5))));
  assert.equal(perTask.filter(item => item.accepted).length, 1, "per-task cap must admit exactly one 0.15 USD reservation");
  assert.equal((await status(taskKey, "same-task")).taskTotal, 150000000);

  // Timeout/ambiguous reservation survives independent requests and cannot be released.
  const ambiguous = await reserve(ambiguousKey, "ambiguous-task", 150000000);
  assert.equal(ambiguous.accepted, true);
  const startedAt = new Date().toISOString();
  assert.equal(await transition(buildStartStatement(ambiguous.reservationId, startedAt)), true);
  assert.equal(await transition(buildAmbiguousStatement(ambiguous.reservationId, new Date().toISOString(), "provider_timeout")), true);
  const ambiguousStatus = await status(ambiguousKey, "ambiguous-task");
  assert.equal(ambiguousStatus.ambiguousCount, 1);
  assert.equal(ambiguousStatus.taskTotal, 150000000);
  assert.equal(await transition(buildReleaseStatement(ambiguous.reservationId, new Date().toISOString(), "provider_not_called")), false);
  const blocked = await reserve(ambiguousKey, "ambiguous-task", 100000000, "provider-other");
  assert.equal(blocked.accepted, false, "ambiguous estimate must continue to consume the per-task cap");

  // Reservation IDs are idempotency keys and cannot be reused with different metadata.
  const idempotencyId = randomUUID();
  const firstReservation = await reserve(settleKey, "idempotent-task", 10000000, "provider-a", idempotencyId);
  assert.equal(firstReservation.accepted, true);
  const replayReservation = await reserve(settleKey, "idempotent-task", 10000000, "provider-a", idempotencyId);
  assert.equal(replayReservation.accepted, false);
  assert.equal(replayReservation.replay, true);
  assert.equal(replayReservation.existing.status, "reserved");
  const conflictingReservation = await reserve(settleKey, "other-task", 10000000, "provider-a", idempotencyId);
  assert.equal(conflictingReservation.accepted, false);
  assert.equal(conflictingReservation.existing.task_id, "idempotent-task");

  // Settlement is idempotent; a conflicting second settlement cannot overwrite actual cost.
  const settleReservation = await reserve(settleKey, "settle-task", 50000000);
  assert.equal(settleReservation.accepted, true);
  assert.equal(await transition(buildStartStatement(settleReservation.reservationId, new Date().toISOString())), true);
  const settlementInput = {
    actual_cost_nanos: 25000000,
    input_tokens: 100,
    output_tokens: 40,
    cost_basis: "provider_reported",
  };
  assert.equal(await transition(buildSettleStatement(settleReservation.reservationId, new Date().toISOString(), settlementInput)), true);
  assert.equal(await transition(buildSettleStatement(settleReservation.reservationId, new Date().toISOString(), settlementInput)), false);
  const settledRow = await first(
    "SELECT status,actual_cost_nanos,input_tokens,output_tokens,cost_basis FROM monetary_reservations WHERE reservation_id=?",
    [settleReservation.reservationId],
  );
  assert.equal(settledRow.status, "settled");
  assert.equal(Number(settledRow.actual_cost_nanos), 25000000);
  assert.equal(Number(settledRow.input_tokens), 100);
  assert.equal(Number(settledRow.output_tokens), 40);
  assert.equal(settledRow.cost_basis, "provider_reported");
  assert.equal(await transition(buildSettleStatement(settleReservation.reservationId, new Date().toISOString(), {
    ...settlementInput, actual_cost_nanos: 30000000,
  })), false);
  assert.equal(Number((await first(
    "SELECT actual_cost_nanos FROM monetary_reservations WHERE reservation_id=?",
    [settleReservation.reservationId],
  )).actual_cost_nanos), 25000000);

  // Release is allowed only before provider I/O; a replay is a no-op.
  const releasable = await reserve(releaseKey, "release-task", 10000000);
  assert.equal(releasable.accepted, true);
  assert.equal(await transition(buildReleaseStatement(releasable.reservationId, new Date().toISOString(), "provider_not_called")), true);
  assert.equal(await transition(buildReleaseStatement(releasable.reservationId, new Date().toISOString(), "provider_not_called")), false);
  const started = await reserve(releaseKey, "started-task", 10000000);
  assert.equal(started.accepted, true);
  assert.equal(await transition(buildStartStatement(started.reservationId, new Date().toISOString())), true);
  assert.equal(await transition(buildReleaseStatement(started.reservationId, new Date().toISOString(), "provider_not_called")), false);

  // Actual cost above the estimate/cap is still recorded and the status reports over-budget.
  const over = await reserve(overKey, "over-task", 50000000);
  assert.equal(over.accepted, true);
  assert.equal(await transition(buildStartStatement(over.reservationId, new Date().toISOString())), true);
  assert.equal(await transition(buildSettleStatement(over.reservationId, new Date().toISOString(), {
    actual_cost_nanos: 250000000,
    input_tokens: 20,
    output_tokens: 20,
    cost_basis: "provider_reported",
  })), true);
  assert.equal((await status(overKey, "over-task")).taskOver, true);
  const blockedAfterOverrun = await reserve(overKey, "over-task", 1000000, "provider-other");
  assert.equal(blockedAfterOverrun.accepted, false, "actual over-cap settlement must block subsequent reservations");

  // A new request context after an interrupted runner still sees the in-flight reservation.
  const restart = await reserve(restartKey, "restart-task", 50000000);
  assert.equal(restart.accepted, true);
  assert.equal(await transition(buildStartStatement(restart.reservationId, new Date().toISOString())), true);
  const afterRestart = await first(
    "SELECT status,estimated_cost_nanos,provider_io_started_at FROM monetary_reservations WHERE reservation_id=?",
    [restart.reservationId],
  );
  assert.equal(afterRestart.status, "in_flight");
  assert.equal(Number(afterRestart.estimated_cost_nanos), 50000000);
  assert.ok(afterRestart.provider_io_started_at);

  console.log("Remote Staging D1 monetary tests passed: daily/monthly/per-task concurrency, cross-provider pending reservations, ambiguous timeout, duplicate settlement, pre-I/O release, over-cap recording, and restart persistence.");
} finally {
  for (const key of policyKeys) {
    try { await query("DELETE FROM monetary_reservations WHERE policy_key=?", [key]); } catch { /* cleanup best-effort; stale-scope cleanup runs next time */ }
    try { await query("DELETE FROM monetary_budget_policies WHERE policy_key=?", [key]); } catch { /* cleanup best-effort; stale-scope cleanup runs next time */ }
  }
}
