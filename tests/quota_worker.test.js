import test from "node:test";
import assert from "node:assert/strict";
import { handleQuota, windows } from "../worker/quota.js";
import entry from "../worker/entry.js";

const TOKEN = "test-worker-token";
const NOW = () => new Date("2026-11-02T12:00:30.000Z");

function req(path, { method = "POST", token = TOKEN, body = {} } = {}) {
  const headers = { "content-type": "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  return new Request(`https://engine.example${path}`, {
    method, headers, body: method === "GET" ? undefined : JSON.stringify(body),
  });
}

function mockDb({ first = [], changes = 1, all = [] } = {}) {
  const calls = [];
  const queue = [...first];
  return {
    calls,
    prepare(sql) {
      return {
        sql, values: [],
        bind(...values) { this.values = values; return this; },
        async first() { calls.push({ kind: "first", sql, values: this.values }); return queue.length ? queue.shift() : null; },
        async run() { calls.push({ kind: "run", sql, values: this.values }); return { meta: { changes } }; },
        async all() { calls.push({ kind: "all", sql, values: this.values }); return { results: all }; },
      };
    },
  };
}

const reservation = (overrides = {}) => ({
  route_id: "groq:primary:openai/gpt-oss-20b", task_id: "task-1", idempotency_key: "idem-1",
  estimated_tokens: 100, request_limit: 10, token_limit: 1000,
  monthly_request_limit: 100, monthly_token_limit: 10000,
  per_task_request_limit: 2, per_task_token_limit: 500,
  minute_request_limit: 5, minute_token_limit: 400, ...overrides,
});

const call = (path, opts, env) => handleQuota(req(path, opts), new URL(`https://engine.example${path}`), env, { now: NOW });

test("quota API rejects missing or wrong credentials and wrong methods", async () => {
  const env = { DB: mockDb(), ENGINE_API_TOKEN: TOKEN };
  assert.equal((await call("/v1/quota/status", { token: null }, env)).status, 401);
  assert.equal((await call("/v1/quota/status", { token: "wrong" }, env)).status, 401);
  assert.equal((await call("/v1/quota/status", { method: "GET" }, env)).status, 405);
  assert.equal((await call("/v1/quota/nope", {}, env)).status, 404);
  assert.equal(env.DB.calls.length, 0);
});

test("windows are computed in UTC from the worker clock", () => {
  assert.deepEqual(windows(NOW()), {
    now: "2026-11-02T12:00:30.000Z", minuteStart: "2026-11-02T11:59:30.000Z",
    dayStart: "2026-11-02T00:00:00.000Z", monthStart: "2026-11-01T00:00:00.000Z",
  });
});

test("reserve validates every limit before touching the database", async () => {
  const env = { DB: mockDb(), ENGINE_API_TOKEN: TOKEN };
  for (const bad of [{ estimated_tokens: 0 }, { request_limit: 1.5 }, { task_id: "" }, { idempotency_key: undefined }, { minute_token_limit: "9" }]) {
    const response = await call("/v1/quota/reserve", { body: reservation(bad) }, env);
    assert.equal(response.status, 400);
  }
  assert.equal(env.DB.calls.length, 0);
});

test("reserve issues one atomic conditional insert with server-side windows", async () => {
  const env = { DB: mockDb({ first: [null, { id: 7 }] }), ENGINE_API_TOKEN: TOKEN };
  const response = await call("/v1/quota/reserve", { body: reservation() }, env);
  assert.equal(response.status, 201);
  assert.deepEqual(await response.json(), { reserved: true, reservation_id: 7 });
  const insert = env.DB.calls[1];
  assert.match(insert.sql, /^\s*INSERT INTO quota_usage/);
  assert.match(insert.sql, /RETURNING id/);
  assert.equal(insert.values.length, 16);
  assert.deepEqual(insert.values.slice(0, 3), ["groq:primary:openai/gpt-oss-20b", 100, "2026-11-02T12:00:30.000Z"]);
  assert.equal(insert.values[5], "2026-11-02T00:00:00.000Z");
  assert.equal(insert.values[8], "2026-11-01T00:00:00.000Z");
  assert.equal(insert.values[11], "2026-11-02T11:59:30.000Z");
});

test("reserve reports unavailable capacity without throwing", async () => {
  const env = { DB: mockDb({ first: [null, null] }), ENGINE_API_TOKEN: TOKEN };
  const response = await call("/v1/quota/reserve", { body: reservation() }, env);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { reserved: false });
});

test("reserve is idempotent for a repeated idempotency key", async () => {
  const env = { DB: mockDb({ first: [{ id: 3 }] }), ENGINE_API_TOKEN: TOKEN };
  const response = await call("/v1/quota/reserve", { body: reservation() }, env);
  assert.deepEqual(await response.json(), { reserved: true, reservation_id: 3, replayed: true });
  assert.equal(env.DB.calls.length, 1);
});

test("settle is idempotent for equal usage and fails closed on conflicting usage", async () => {
  const same = { DB: mockDb({ changes: 0, first: [{ tokens: 9, settled_at: "x" }] }), ENGINE_API_TOKEN: TOKEN };
  assert.deepEqual(await (await call("/v1/quota/settle", { body: { reservation_id: 4, actual_tokens: 9 } }, same)).json(), { ok: true, replayed: true });
  const conflict = { DB: mockDb({ changes: 0, first: [{ tokens: 5, settled_at: "x" }] }), ENGINE_API_TOKEN: TOKEN };
  assert.deepEqual(await (await call("/v1/quota/settle", { body: { reservation_id: 4, actual_tokens: 9 } }, conflict)).json(), { ok: false, error: "conflict" });
  const missing = { DB: mockDb({ changes: 0, first: [null] }), ENGINE_API_TOKEN: TOKEN };
  assert.deepEqual(await (await call("/v1/quota/settle", { body: { reservation_id: 4, actual_tokens: 9 } }, missing)).json(), { ok: false, error: "not_found" });
});

test("cooldown upsert can only extend an existing cooldown", async () => {
  const env = { DB: mockDb(), ENGINE_API_TOKEN: TOKEN };
  const response = await call("/v1/quota/cooldown", { body: { route_id: "r", seconds: 900, reason: "http_429" } }, env);
  assert.equal(response.status, 200);
  assert.equal((await response.json()).until_at, "2026-11-02T12:15:30.000Z");
  assert.match(env.DB.calls[0].sql, /excluded\.until_at > quota_cooldowns\.until_at/);
  assert.equal((await call("/v1/quota/cooldown", { body: { route_id: "r", seconds: 0 } }, env)).status, 400);
});

test("status reports windows and active cooldown", async () => {
  const usage = { minute_requests: 1, minute_tokens: 50, day_requests: 3, day_tokens: 300, month_requests: 9, month_tokens: 900 };
  const env = { DB: mockDb({ first: [usage, { until_at: "2026-11-02T12:10:00.000Z" }] }), ENGINE_API_TOKEN: TOKEN };
  const body = await (await call("/v1/quota/status", { body: { route_id: "r" } }, env)).json();
  assert.deepEqual(body.day, { requests: 3, tokens: 300 });
  assert.deepEqual(body.month, { requests: 9, tokens: 900 });
  assert.equal(body.cooldown_active, true);
});

test("audit events drop unlisted detail keys so prompts can never be stored", async () => {
  const env = { DB: mockDb({ first: [{ id: 1 }] }), ENGINE_API_TOKEN: TOKEN };
  const response = await call("/v1/quota/audit", { body: { event: "quota_reserved", route_id: "r", details: { estimated_tokens: 10, prompt: "secret", reason: "x" } } }, env);
  assert.equal(response.status, 201);
  const stored = JSON.parse(env.DB.calls[0].values[3]);
  assert.deepEqual(stored, { estimated_tokens: 10, reason: "x" });
});

test("storage failures are reported without leaking internals", async () => {
  const DB = { prepare() { throw new Error("D1 secret detail"); } };
  const response = await call("/v1/quota/task-usage", { body: { task_id: "t" } }, { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "storage_unavailable" });
});

test("entry worker routes quota paths and delegates everything else", async () => {
  const env = { DB: mockDb(), ENGINE_API_TOKEN: TOKEN };
  const quota = await entry.fetch(req("/v1/quota/status", { token: null }), env, {});
  assert.equal(quota.status, 401);
  const health = await entry.fetch(new Request("https://engine.example/health"), { DB: { prepare: () => ({ first: async () => ({ ok: 1 }) }) } }, {});
  assert.equal(health.status, 200);
  const unknown = await entry.fetch(new Request("https://engine.example/admin"), env, {});
  assert.equal(unknown.status, 404);
});
