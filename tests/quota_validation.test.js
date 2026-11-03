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
