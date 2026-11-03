import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";

const TOKEN = "test-neuron-api-token";
const ADMIN = "test-neuron-admin-token";
const MODEL = "@cf/google/gemma-4-26b-a4b-it";
const RESERVATION = "123e4567-e89b-42d3-a456-426614174000";
const IDEMPOTENCY = "123e4567-e89b-42d3-a456-426614174001";

function request(body, token = TOKEN, method = "POST") {
  return new Request("https://engine.example/v1/workers-ai-neurons", {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { "content-type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function dbMock(options = {}) {
  const state = {
    enabled: options.enabled ?? 0,
    insertShouldSucceed: options.insertShouldSucceed ?? true,
    existing: options.existing ?? null,
    reservation: options.reservation ?? null,
    quarantine: options.quarantine ?? null,
    totalNeurons: options.totalNeurons ?? 0,
    calls: [],
  };
  const policy = () => ({
    policy_key: "default",
    daily_cap_neurons: 10000,
    allowed_model_id: MODEL,
    enabled: state.enabled,
    updated_at: "2026-11-03T00:00:00.000Z",
  });
  return {
    state,
    prepare(sql) {
      const statement = {
        sql,
        values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          state.calls.push({ kind: "first", sql, values: this.values });
          if (sql.includes("FROM workers_ai_neuron_policy")) return policy();
          if (sql.includes("WHERE idempotency_key=?")) return state.existing;
          if (sql.includes("WHERE reservation_id=?")) return state.reservation;
          if (sql.includes("FROM workers_ai_neuron_quarantines WHERE model_id=?")) return state.quarantine;
          if (sql.includes("INSERT INTO workers_ai_neuron_reservations")) {
            return state.insertShouldSucceed ? { reservation_id: this.values[0] } : null;
          }
          if (sql.includes("SELECT COALESCE(SUM(")) return { total_neurons: state.totalNeurons };
          return null;
        },
        async run() {
          state.calls.push({ kind: "run", sql, values: this.values });
          if (sql.includes("UPDATE workers_ai_neuron_policy SET enabled=?")) {
            if (this.values[0] === 1 && state.quarantine) return { meta: { changes: 0 } };
            state.enabled = this.values[0];
            return { meta: { changes: 1 } };
          }
          if (sql.includes("UPDATE workers_ai_neuron_reservations SET status='in_flight'")) {
            if (state.reservation?.status !== "reserved" || state.enabled !== 1 || state.quarantine) {
              return { meta: { changes: 0 } };
            }
            state.reservation.status = "in_flight";
            return { meta: { changes: 1 } };
          }
          if (sql.includes("UPDATE workers_ai_neuron_reservations SET status='ambiguous'")) {
            if (state.reservation?.status !== "in_flight") return { meta: { changes: 0 } };
            state.reservation.status = "ambiguous";
            return { meta: { changes: 1 } };
          }
          if (sql.includes("UPDATE workers_ai_neuron_reservations SET status='released'")) {
            if (state.reservation?.status !== "reserved") return { meta: { changes: 0 } };
            state.reservation.status = "released";
            return { meta: { changes: 1 } };
          }
          return { meta: { changes: 1 } };
        },
        async all() {
          state.calls.push({ kind: "all", sql, values: this.values });
          return { results: [] };
        },
      };
      return statement;
    },
    async batch(statements) {
      state.calls.push({
        kind: "batch",
        statements: statements.map(item => ({ sql: item.sql, values: item.values })),
      });
      if (state.reservation) {
        state.reservation.status = "settled";
        state.reservation.actual_neurons = statements[0].values[0];
      }
      if (state.reservation && state.reservation.actual_neurons > state.reservation.estimated_neurons) {
        state.quarantine = { model_id: MODEL, reason: "actual_neurons_exceeded_reservation" };
      }
      return [{ meta: { changes: 1 } }, { meta: { changes: 1 } }];
    },
  };
}

function env(DB, control = "staging-only") {
  return {
    DB,
    ENGINE_API_TOKEN: TOKEN,
    ENGINE_QUOTA_ADMIN_TOKEN: ADMIN,
    WORKERS_AI_LEDGER_CONTROL: control,
  };
}

test("Workers AI Neuron ledger is not exposed outside the Staging-only control", async () => {
  const DB = dbMock({ enabled: 1 });
  const response = await worker.fetch(request({ action: "enable" }), env(DB, ""));
  assert.equal(response.status, 404);
  assert.deepEqual(await response.json(), { error: "not_found" });
});

test("only the separate admin token can enable the Staging Neuron ledger", async () => {
  const DB = dbMock();
  const denied = await worker.fetch(request({ action: "enable" }, TOKEN), env(DB));
  assert.equal(denied.status, 403);
  const allowed = await worker.fetch(request({ action: "enable" }, ADMIN), env(DB));
  assert.equal(allowed.status, 200);
  assert.deepEqual(await allowed.json(), { enabled: true, daily_cap_neurons: 10000 });
  assert.equal(DB.state.enabled, 1);
});

test("reservation fails closed while the policy is disabled", async () => {
  const DB = dbMock({ enabled: 0 });
  const response = await worker.fetch(request({
    action: "reserve", reservation_id: RESERVATION, idempotency_key: IDEMPOTENCY,
    model_id: MODEL, task_kind: "research_brief", task_id: "research-1",
    data_classification: "public", estimated_neurons: 500,
  }), env(DB));
  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), { error: "neuron_ledger_disabled" });
});

test("reservation rejects non-public data and non-allowlisted models", async () => {
  const DB = dbMock({ enabled: 1 });
  const privateData = await worker.fetch(request({
    action: "reserve", reservation_id: RESERVATION, idempotency_key: IDEMPOTENCY,
    model_id: MODEL, task_kind: "research_brief", task_id: "research-1",
    data_classification: "confidential", estimated_neurons: 500,
  }), env(DB));
  assert.equal(privateData.status, 400);
  const wrongModel = await worker.fetch(request({
    action: "reserve", reservation_id: RESERVATION, idempotency_key: IDEMPOTENCY,
    model_id: "@cf/nvidia/nemotron-3-120b-a12b", task_kind: "research_brief",
    task_id: "research-1", data_classification: "public", estimated_neurons: 500,
  }), env(DB));
  assert.equal(wrongModel.status, 400);
});

test("reservation uses a single conditional insert with a fixed 10,000-Neuron daily cap", async () => {
  const DB = dbMock({ enabled: 1, insertShouldSucceed: true });
  const response = await worker.fetch(request({
    action: "reserve", reservation_id: RESERVATION, idempotency_key: IDEMPOTENCY,
    model_id: MODEL, task_kind: "research_brief", task_id: "research-1",
    data_classification: "public", estimated_neurons: 500,
  }), env(DB));
  assert.equal(response.status, 201);
  const insert = DB.state.calls.find(call => call.kind === "first" && call.sql.includes("INSERT INTO workers_ai_neuron_reservations"));
  assert.ok(insert);
  assert.match(insert.sql, /RETURNING reservation_id/);
  assert.match(insert.sql, /daily_cap_neurons/);
  assert.match(insert.sql, /status IN \('reserved','in_flight','ambiguous','settled'\)/);
  assert.equal(insert.values[6], 500);
  assert.equal(insert.values[5], "public");
});

test("reservation fails closed when the daily Neuron cap would be exceeded", async () => {
  const DB = dbMock({ enabled: 1, insertShouldSucceed: false, totalNeurons: 9500 });
  const response = await worker.fetch(request({
    action: "reserve", reservation_id: RESERVATION, idempotency_key: IDEMPOTENCY,
    model_id: MODEL, task_kind: "research_brief", task_id: "research-1",
    data_classification: "public", estimated_neurons: 501,
  }), env(DB));
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { error: "daily_neuron_cap_exceeded" });
});

test("idempotent reservation replay must match the original reservation", async () => {
  const existing = {
    reservation_id: RESERVATION, model_id: MODEL, task_kind: "research_brief",
    task_id: "research-1", data_classification: "public", estimated_neurons: 500,
    status: "reserved",
  };
  const DB = dbMock({ enabled: 1, existing });
  const response = await worker.fetch(request({
    action: "reserve", reservation_id: RESERVATION, idempotency_key: IDEMPOTENCY,
    model_id: MODEL, task_kind: "research_brief", task_id: "research-1",
    data_classification: "public", estimated_neurons: 500,
  }), env(DB));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).replay, true);
});

test("reserved work cannot enter provider I/O after the ledger is disabled", async () => {
  const DB = dbMock({ enabled: 0, reservation: {
    reservation_id: RESERVATION, model_id: MODEL, task_kind: "research_brief",
    task_id: "research-1", status: "reserved", estimated_neurons: 500,
    actual_neurons: null,
  }});
  const response = await worker.fetch(request({ action: "mark_in_flight", reservation_id: RESERVATION }), env(DB));
  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), { error: "neuron_ledger_disabled" });
});

test("quarantined model cannot start another provider attempt", async () => {
  const DB = dbMock({ enabled: 1, quarantine: { model_id: MODEL }, reservation: {
    reservation_id: RESERVATION, model_id: MODEL, task_kind: "research_brief",
    task_id: "research-1", status: "reserved", estimated_neurons: 500,
    actual_neurons: null,
  }});
  const response = await worker.fetch(request({ action: "mark_in_flight", reservation_id: RESERVATION }), env(DB));
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { error: "model_quarantined" });
});

test("ambiguous provider outcome preserves its reservation and cannot be released", async () => {
  const DB = dbMock({ enabled: 1, reservation: {
    reservation_id: RESERVATION, model_id: MODEL, task_kind: "research_brief",
    task_id: "research-1", status: "in_flight", estimated_neurons: 500,
    actual_neurons: null,
  }});
  const ambiguous = await worker.fetch(request({ action: "ambiguous", reservation_id: RESERVATION }), env(DB));
  assert.equal(ambiguous.status, 200);
  assert.equal((await ambiguous.json()).reservation_preserved, true);
  const release = await worker.fetch(request({ action: "release", reservation_id: RESERVATION }), env(DB));
  assert.equal(release.status, 409);
  assert.deepEqual(await release.json(), { error: "release_after_provider_io_forbidden" });
});

test("settlement above estimate quarantines the model and cannot be silently retried", async () => {
  const DB = dbMock({ enabled: 1, reservation: {
    reservation_id: RESERVATION, model_id: MODEL, task_kind: "research_brief",
    task_id: "research-1", status: "in_flight", estimated_neurons: 500,
    actual_neurons: null,
  }});
  const response = await worker.fetch(request({
    action: "settle", reservation_id: RESERVATION, actual_neurons: 600,
  }), env(DB));
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.over_estimate, true);
  assert.equal(body.model_quarantined, true);
  const batch = DB.state.calls.find(call => call.kind === "batch");
  assert.ok(batch);
  assert.match(batch.statements[1].sql, /INSERT OR IGNORE INTO workers_ai_neuron_quarantines/);
  assert.equal(DB.state.quarantine.model_id, MODEL);
});

test("admin token cannot reserve model usage", async () => {
  const DB = dbMock({ enabled: 1 });
  const response = await worker.fetch(request({
    action: "reserve", reservation_id: RESERVATION, idempotency_key: IDEMPOTENCY,
    model_id: MODEL, task_kind: "research_brief", task_id: "research-1",
    data_classification: "public", estimated_neurons: 500,
  }, ADMIN), env(DB));
  assert.equal(response.status, 403);
});
