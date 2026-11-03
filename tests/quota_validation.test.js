import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";

const TOKEN = "quota-validation-token";

function makeDb() {
  const calls = [];
  return {
    calls,
    prepare(sql) {
      return {
        values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          calls.push({ sql, values: this.values });
          return { id: 9 };
        },
        async all() {
          calls.push({ sql, values: this.values });
          return { results: [] };
        },
        async run() {
          calls.push({ sql, values: this.values });
          return { meta: { changes: 1 } };
        },
      };
    },
  };
}

async function post(body, DB) {
  return worker.fetch(new Request("https://engine.example/v1/quota", {
    method: "POST",
    headers: {
      authorization: `Bearer ${TOKEN}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  }), { DB, ENGINE_API_TOKEN: TOKEN });
}

test("quota task usage telemetry is scoped to both route and task", async () => {
  const DB = makeDb();
  const response = await worker.fetch(new Request(
    "https://engine.example/v1/quota?route_id=groq%3Aprimary%3Amodel-x&task_id=task-42",
    { headers: { authorization: `Bearer ${TOKEN}` } },
  ), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.deepEqual(payload.usage.task, { requests: 0, tokens: 0 });
  const taskUsage = DB.calls.find(call =>
    call.sql.includes("FROM quota_usage WHERE route_id=? AND task_id=?")
  );
  assert.ok(taskUsage);
  assert.deepEqual(taskUsage.values, ["groq:primary:model-x", "task-42"]);
});

test("quota audit event rejects non-string route and task metadata as client errors", async () => {
  for (const body of [
    { action: "audit_event", event: "provider_error", route_id: { secret: "x" } },
    { action: "audit_event", event: "provider_error", task_kind: 12 },
    { action: "audit_event", event: "provider_error", details: ["not", "an", "object"] },
  ]) {
    const DB = makeDb();
    const response = await post(body, DB);
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { error: "invalid_audit_event" });
    assert.equal(DB.calls.length, 0);
  }
});

test("quota audit event accepts bounded metadata and writes parameterized values", async () => {
  const DB = makeDb();
  const response = await post({
    action: "audit_event",
    event: "provider_error",
    route_id: "groq:main:model",
    task_kind: "research",
    details: {
      reason: "rate_limited",
      cost_basis: "verified_profile_estimate",
      profile_estimated_cost_usd: 0,
      provider_reported_cost_available: false,
      error_code: "model_permission_blocked_org",
      secret: "must_not_be_stored",
    },
  }, DB);
  assert.equal(response.status, 201);
  assert.deepEqual(await response.json(), { id: 9 });
  assert.equal(DB.calls.length, 1);
  assert.deepEqual(JSON.parse(DB.calls[0].values[3]), {
    reason: "rate_limited",
    cost_basis: "verified_profile_estimate",
    profile_estimated_cost_usd: 0,
    provider_reported_cost_available: false,
    error_code: "model_permission_blocked_org",
  });
});


const ROUTE = "groq:primary:openai/gpt-oss-20b";

test("quota reserve uses server-owned profile limits and returns an opaque settlement key", async () => {
  const DB = makeDb();
  const response = await post({
    action: "reserve", route_id: ROUTE, task_id: "task-42", estimated_tokens: 12,
    daily_request_limit: 999999, daily_token_limit: 999999999,
    monthly_request_limit: 999999, monthly_token_limit: 999999999,
    minute_request_limit: 999999, minute_token_limit: 999999999,
    per_task_request_limit: 999999, per_task_token_limit: 999999999,
  }, DB);
  assert.equal(response.status, 201);
  const payload = await response.json();
  assert.equal(payload.reservation_id, 9);
  assert.equal(typeof payload.settlement_key, "string");
  assert.ok(payload.settlement_key.length > 20);
  const insert = DB.calls.find(call => call.sql.includes("INSERT INTO quota_usage"));
  assert.ok(insert.sql.includes("settlement_key"));
  assert.equal(insert.values[9], 1000); // profile daily request cap
  assert.equal(insert.values[13], 200000); // profile daily token cap
  assert.equal(insert.values[16], 10000); // profile monthly request cap
  assert.equal(insert.values[20], 2000000); // profile monthly token cap
  assert.equal(insert.values[23], 30); // profile rolling-minute request cap
  assert.equal(insert.values[27], 8000); // profile rolling-minute token cap
  assert.equal(insert.values[29], 3); // profile per-task request cap
  assert.equal(insert.values[33], 16000); // profile per-task token cap
});

test("quota reserve rejects unknown routes even when caller supplies permissive limits", async () => {
  const DB = makeDb();
  const response = await post({
    action: "reserve", route_id: "attacker:unlisted:model", task_id: "task-42",
    estimated_tokens: 1, daily_request_limit: 999999,
  }, DB);
  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), { error: "unknown_quota_route" });
  assert.equal(DB.calls.length, 0);
});

test("quota settlement requires route, task, and opaque reservation proof", async () => {
  const DB = makeDb();
  const response = await post({
    action: "settle", reservation_id: 9, actual_tokens: 1,
  }, DB);
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "invalid_settlement" });
  assert.equal(DB.calls.length, 0);
});

test("direct quota record mutation is disabled", async () => {
  const DB = makeDb();
  const response = await post({
    action: "record", route_id: ROUTE, tokens: 999999,
  }, DB);
  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), { error: "unsupported_action" });
  assert.equal(DB.calls.length, 0);
});
