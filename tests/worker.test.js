import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";

const TOKEN = "test-worker-token";

function request(path, { method = "GET", token = TOKEN, body } = {}) {
  const headers = {};
  if (token) headers.authorization = `Bearer ${token}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  return new Request(`https://engine.example${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function dbMock() {
  const calls = [];
  let insertedId = null;
  return {
    calls,
    prepare(sql) {
      return {
        sql, values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          calls.push({ kind: "first", sql, values: this.values });
          if (sql.includes("SELECT 1")) return { ok: 1 };
          if (sql.includes("SUM(requests)")) return { requests: 0, tokens: 0 };
          if (sql.includes("SELECT until_at FROM quota_cooldowns")) return null;
          if (sql.includes("INSERT INTO quota_usage")) return { id: this.values[0] };
          if (sql.includes("SELECT id,tokens,settled_at FROM quota_usage")) {
            return { id: this.values[0], tokens: 100, settled_at: null };
          }
          if (sql.includes("INSERT INTO quota_audit_events")) return { id: 1 };
          if (sql.includes("UPDATE tasks SET status='running'")) return null;
          if (sql.includes("SELECT id,status,attempts")) return {
            id: "task-1", status: "running", attempts: 1, max_attempts: 2,
            lease_token: "lease-1", lease_expires_at: "2999-01-01T00:00:00.000Z",
          };
          if (sql.includes("WHERE idempotency_key")) {
            return { id: insertedId || "existing-id" };
          }
          return null;
        },
        async run() {
          calls.push({ kind: "run", sql, values: this.values });
          return { meta: { changes: 1 } };
        },
        async all() {
          calls.push({ kind: "all", sql, values: this.values });
          return { results: [] };
        },
      };
    },
    async batch(statements) {
      calls.push({ kind: "batch", statements: statements.map(x => ({ sql: x.sql, values: x.values })) });
      insertedId = statements[0].values[0];
      return [];
    },
  };
}

test("health is read-only and does not require a token", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/health", { token: null }), { DB });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true });
  assert.equal(DB.calls.length, 1);
});

test("task routes reject missing or invalid bearer token", async () => {
  const response = await worker.fetch(request("/v1/tasks", { token: "wrong" }), {
    DB: dbMock(), ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(response.status, 401);
});

test("enqueue validates input and uses parameterized batch statements", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST",
    body: { task_type: "research", title: "Check D1", idempotency_key: "key-1", payload: { q: "hello" } },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 201);
  const batch = DB.calls.find(x => x.kind === "batch");
  assert.equal(batch.statements.length, 2);
  assert.match(batch.statements[0].sql, /INSERT OR IGNORE INTO tasks/);
  assert.deepEqual(batch.statements[0].values.slice(1, 5), ["key-1", "research", "Check D1", '{"q":"hello"}']);
  assert.match(batch.statements[1].sql, /INSERT INTO audit_log/);
});

test("enqueue rejects malformed task payload", async () => {
  const response = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST",
    body: { task_type: "", title: "x", idempotency_key: "k" },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "invalid_task" });
});


test("quota usage snapshot is authenticated and returns durable window counts", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/quota/usage-snapshot", {
    method: "POST", body: { route_id: "groq:primary:openai/gpt-oss-20b" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.deepEqual(result.minute, { requests: 0, tokens: 0 });
  assert.deepEqual(result.daily, { requests: 0, tokens: 0 });
  assert.deepEqual(result.monthly, { requests: 0, tokens: 0 });
  assert.equal(result.cooldown_active, false);
  assert.equal(DB.calls.filter(x => x.kind === "first").length, 4);
});

test("quota routes reject unauthenticated callers and non-POST methods", async () => {
  const denied = await worker.fetch(request("/v1/quota/usage-snapshot", {
    method: "POST", token: null, body: { route_id: "groq:test:model" },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(denied.status, 401);
  const method = await worker.fetch(request("/v1/quota/usage-snapshot", {
    method: "GET",
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(method.status, 405);
});

test("quota reservation is one atomic conditional insert across all windows", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/quota/reserve", {
    method: "POST",
    body: {
      route_id: "groq:primary:openai/gpt-oss-20b", task_id: "task-1",
      estimated_tokens: 300, minute_request_limit: 30, minute_token_limit: 8000,
      daily_request_limit: 1000, daily_token_limit: 200000,
      monthly_request_limit: 10000, monthly_token_limit: 2000000,
      per_task_request_limit: 3, per_task_token_limit: 16000,
    },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.reservation_id, DB.calls.find(x =>
    x.kind === "first" && x.sql.includes("INSERT INTO quota_usage")
  ).values[0]);
  const statement = DB.calls.find(x => x.kind === "first" && x.sql.includes("INSERT INTO quota_usage"));
  assert.match(statement.sql, /INSERT INTO quota_usage[\s\S]+SELECT/);
  assert.match(statement.sql, /minute/);
  assert.match(statement.sql, /NOT EXISTS/);
  assert.ok(statement.values.length >= 30);
});

test("quota reservation rejects exhausted limits without inserting", async () => {
  const DB = dbMock();
  const original = DB.prepare.bind(DB);
  DB.prepare = sql => {
    const statement = original(sql);
    const first = statement.first.bind(statement);
    statement.first = async () => sql.includes("INSERT INTO quota_usage") ? null : first();
    return statement;
  };
  const response = await worker.fetch(request("/v1/quota/reserve", {
    method: "POST",
    body: {
      route_id: "groq:primary:openai/gpt-oss-20b", task_id: "task-1",
      estimated_tokens: 300, minute_request_limit: 30, minute_token_limit: 8000,
      daily_request_limit: 1000, daily_token_limit: 200000,
      monthly_request_limit: 10000, monthly_token_limit: 2000000,
      per_task_request_limit: 3, per_task_token_limit: 16000,
    },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { error: "quota_unavailable" });
});

test("quota reservation validates every cap and rejects booleans", async () => {
  const response = await worker.fetch(request("/v1/quota/reserve", {
    method: "POST",
    body: {
      route_id: "groq:test:model", task_id: "task-1",
      estimated_tokens: 1, minute_request_limit: true, minute_token_limit: 10,
      daily_request_limit: 10, daily_token_limit: 100,
      monthly_request_limit: 10, monthly_token_limit: 100,
      per_task_request_limit: 1, per_task_token_limit: 10,
    },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 400);
});

test("quota settlement is idempotent and updates only an unsettled reservation", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/quota/settle", {
    method: "POST", body: { reservation_id: "reservation-1", actual_tokens: 80 },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true, settled: true });
  assert.ok(DB.calls.some(x => x.kind === "run" && x.sql.includes("UPDATE quota_usage")));
});

test("quota cooldown and audit endpoints persist only bounded metadata", async () => {
  const DB = dbMock();
  const cooldown = await worker.fetch(request("/v1/quota/cooldown", {
    method: "POST", body: { route_id: "groq:test:model", seconds: 900, reason: "http_429" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(cooldown.status, 200);
  const audit = await worker.fetch(request("/v1/quota/audit", {
    method: "POST",
    body: {
      event: "completion_received", route_id: "groq:test:model",
      task_kind: "research", details: { actual_tokens: 12, prompt: "must not persist" },
    },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(audit.status, 200);
  const statement = DB.calls.find(x => x.kind === "first" && x.sql.includes("INSERT INTO quota_audit_events"));
  assert.ok(statement);
  assert.equal(statement.values[3], JSON.stringify({ actual_tokens: 12 }));
});

test("unknown routes are not exposed", async () => {
  const response = await worker.fetch(request("/admin"), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 404);
});


test("claim endpoint is authenticated and returns an empty queue safely", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/claim", { method: "POST", body: {} }), {
    DB, ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { task: null });
});

test("claim endpoint rejects unauthenticated callers", async () => {
  const response = await worker.fetch(request("/v1/tasks/claim", { method: "POST", token: null, body: {} }), {
    DB: dbMock(), ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(response.status, 401);
});

test("heartbeat requires the current lease and records a bounded extension", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/task-1/heartbeat", {
    method: "POST", body: { lease_token: "lease-1" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).ok, true);
  assert.ok(DB.calls.some(x => x.kind === "run" && x.sql.startsWith("UPDATE tasks")));
});

test("finish rejects unsupported terminal states", async () => {
  const response = await worker.fetch(request("/v1/tasks/task-1/finish", {
    method: "POST", body: { lease_token: "lease-1", status: "pending" },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "invalid_status" });
});


test("recover endpoint is authenticated and bounded to stale task selection", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/recover", { method: "POST", body: {} }), {
    DB, ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { requeued: 0, failed: 0 });
  assert.ok(DB.calls.some(x => x.kind === "all" && x.sql.includes("lease_expires_at")));
});

test("recover endpoint rejects unauthenticated callers", async () => {
  const response = await worker.fetch(request("/v1/tasks/recover", {
    method: "POST", token: null, body: {},
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 401);
});


test("scheduled handler runs bounded stale lease recovery", async () => {
  const DB = dbMock();
  await worker.scheduled({ cron: "*/15 * * * *" }, { DB });
  assert.ok(DB.calls.some(x => x.kind === "all" && x.sql.includes("LIMIT 100")));
});


test("authenticated POST diagnostic probe confirms application auth without writes", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/diagnostics/auth", {
    method: "POST", body: {},
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true, authenticated: true });
  assert.equal(DB.calls.length, 0);
});

test("diagnostic probe rejects invalid credentials", async () => {
  const response = await worker.fetch(request("/v1/diagnostics/auth", {
    method: "POST", token: "wrong", body: {},
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 401);
});

test("diagnostic probe allows only POST", async () => {
  const response = await worker.fetch(request("/v1/diagnostics/auth", {
    method: "GET",
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 405);
});
