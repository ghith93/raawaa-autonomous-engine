import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";
import { generateKeyPairSync } from "node:crypto";

const TOKEN = "test-worker-token";
const CODESPACE_TOKEN = "test-codespace-token-0123456789abcdef0123456789abcdef";
const ADMIN_TOKEN = "test-quota-admin-token";
const QUOTA_LIMITS = JSON.stringify({"groq:main:model":{enabled:true,minute_request_limit:5,minute_token_limit:1000,daily_request_limit:10,daily_token_limit:2000,monthly_request_limit:100,monthly_token_limit:20000,per_task_request_limit:3,per_task_token_limit:300}});

function request(path, { method = "GET", token = TOKEN, body } = {}) {
  const headers = {};
  if (token) headers.authorization = `Bearer ${token}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  return new Request(`https://engine.example${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function dbMock(options = {}) {
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
          if (sql.includes("INSERT INTO quota_cooldowns")) return { until_at: this.values[1] };
          if (sql.includes("FROM audit_log")) return null;
          if (sql.includes("SELECT 1")) return { ok: 1 };
          if (sql.includes("UPDATE tasks SET status='running'")) {
            return sql.includes("task_type IN") ? (options.codespaceClaim || null) : (options.normalClaim || null);
          }
          if (sql.includes("SELECT id,status,attempts")) return options.currentTask || {
            id: "task-1", status: "running", attempts: 1, max_attempts: 2,
            task_type: "research_brief", lease_token: "lease-1",
            lease_expires_at: "2999-01-01T00:00:00.000Z",
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

test("unknown routes are not exposed", async () => {
  const response = await worker.fetch(request("/admin"), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 404);
});

test("task invocation lifecycle route is authenticated", async () => {
  const response = await worker.fetch(request("/v1/tasks/task-1/invocations", {
    method: "POST", token: null, body: { action: "prepare" },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 401);
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


test("scheduled handler runs bounded stale lease recovery and records a heartbeat", async () => {
  const DB = dbMock();
  await worker.scheduled({ cron: "*/15 * * * *" }, { DB });
  assert.ok(DB.calls.some(x => x.kind === "all" && x.sql.includes("LIMIT 100")));
  const heartbeat = DB.calls.find(x => x.kind === "run" && x.sql.includes("INSERT INTO audit_log"));
  assert.ok(heartbeat, "scheduled invocation must be recorded in the audit ledger");
  assert.equal(heartbeat.values[0], "worker_cron_tick");
  assert.deepEqual(JSON.parse(heartbeat.values[1]), {
    cron: "*/15 * * * *", requeued: 0, failed: 0,
  });
  assert.ok(Number.isFinite(Date.parse(heartbeat.values[2])));
});


test("Task Runner watchdog Cron is dormant without App settings and does not run lease recovery", async () => {
  const DB = dbMock();
  await worker.scheduled(
    { cron: "1,6,11,16,21,26,31,36,41,46,51,56 * * * *", scheduledTime: 1_800_000_000_000 },
    { DB },
  );
  assert.equal(DB.calls.length, 0);
});

test("offset watchdog Cron dispatches after the primary Task Runner phase", async () => {
  const DB = dbMock();
  const { privateKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
    publicKeyEncoding: { type: "spki", format: "pem" },
    privateKeyEncoding: { type: "pkcs1", format: "pem" },
  });
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const target = String(url);
    calls.push({ url: target, options });
    if (target.endsWith("/app/installations/67890/access_tokens")) {
      return new Response(JSON.stringify({ token: "ghs_test_watchdog_token" }), {
        status: 201, headers: { "content-type": "application/json" },
      });
    }
    if (target.includes("/actions/workflows/task-runner.yml/runs?")) {
      return new Response(JSON.stringify({ workflow_runs: [] }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    }
    if (target.endsWith("/actions/workflows/task-runner.yml/dispatches")) {
      return new Response(null, { status: 204 });
    }
    throw new Error("unexpected_url");
  };
  try {
    await worker.scheduled(
      { cron: "1,6,11,16,21,26,31,36,41,46,51,56 * * * *", scheduledTime: 1_800_000_000_000 },
      {
        DB,
        GITHUB_APP_WATCHDOG_ENABLED: "enabled",
        GITHUB_APP_ID: "12345",
        GITHUB_APP_INSTALLATION_ID: "67890",
        GITHUB_APP_PRIVATE_KEY: privateKey,
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls.length, 3);
  assert.equal(calls[2].url.endsWith("/actions/workflows/task-runner.yml/dispatches"), true);
  assert.ok(DB.calls.some(call => call.kind === "run" && call.values[0] === "task_runner_dispatch_intent"));
});

test("15-minute recovery Cron never calls GitHub App dispatch", async () => {
  const DB = dbMock();
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    throw new Error("GitHub fetch must not run on the recovery Cron");
  };
  try {
    await worker.scheduled(
      { cron: "*/15 * * * *", scheduledTime: 1_800_000_000_000 },
      {
        DB,
        GITHUB_APP_ID: "12345",
        GITHUB_APP_INSTALLATION_ID: "67890",
        GITHUB_APP_PRIVATE_KEY: "unused-on-recovery-cron",
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls, 0);
  assert.ok(DB.calls.some(x => x.kind === "all" && x.sql.includes("LIMIT 100")));
  assert.ok(DB.calls.some(x => x.kind === "run" && x.values[0] === "worker_cron_tick"));
});

test("scheduled handler fails visibly when the heartbeat audit write fails", async () => {
  const DB = dbMock();
  const prepare = DB.prepare.bind(DB);
  DB.prepare = sql => {
    const statement = prepare(sql);
    if (sql.includes("INSERT INTO audit_log")) {
      statement.run = async () => { throw new Error("audit storage unavailable"); };
    }
    return statement;
  };
  await assert.rejects(
    () => worker.scheduled({ cron: "*/15 * * * *" }, { DB }),
    /audit storage unavailable/,
  );
});

test("scheduled handler rejects an unsuccessful D1 heartbeat result", async () => {
  const DB = dbMock();
  const prepare = DB.prepare.bind(DB);
  DB.prepare = sql => {
    const statement = prepare(sql);
    if (sql.includes("INSERT INTO audit_log")) {
      statement.run = async () => ({ success: false });
    }
    return statement;
  };
  await assert.rejects(
    () => worker.scheduled({ cron: "*/15 * * * *" }, { DB }),
    /Worker Cron heartbeat insert failed/,
  );
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

test("research ingestion is authenticated and stores normalized GitHub metadata", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates", { method: "POST", body: { candidates: [{ source: "github", full_name: "org/agent", url: "https://github.com/org/agent", description: "agent", stars: 12, license_spdx: "MIT", topics: ["agents"] }] } }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 201);
  assert.deepEqual(await response.json(), { accepted_count: 1, sources: ["github"] });
  const batch = DB.calls.find(x => x.kind === "batch");
  assert.match(batch.statements[0].sql, /INSERT INTO research_candidates/);
  assert.match(batch.statements[0].sql, /ON CONFLICT\(source_key\) DO UPDATE/);
  assert.equal(batch.statements[0].values[0], "github:org/agent");
  assert.match(batch.statements[0].sql, /\x27unreviewed\x27/);
});

test("research ingestion normalizes Crossref DOI and rejects arbitrary URL targets", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates", { method: "POST", body: { candidates: [{ source: "crossref", doi: "10.1234/agent-paper", title: "Agent research", url: "https://evil.example/article", authors: ["A Researcher"], license_urls: ["https://creativecommons.org/licenses/by/4.0/"] }] } }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 201);
  const batch = DB.calls.find(x => x.kind === "batch");
  assert.equal(batch.statements[0].values[0], "crossref:10.1234/agent-paper");
  assert.equal(batch.statements[0].values[3], "https://doi.org/10.1234/agent-paper");
});

test("research ingestion rejects invalid repository URL before database writes", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates", { method: "POST", body: { candidates: [{ source: "github", full_name: "org/agent", url: "https://evil.example/org/agent" }] } }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "invalid_candidate" });
  assert.equal(DB.calls.length, 0);
});

test("research candidate listing is authenticated and bounded", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates?limit=20&status=unreviewed"), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { candidates: [] });
  assert.ok(DB.calls.some(x => x.kind === "all" && x.sql.includes("WHERE review_status=?") && x.values[0] === "unreviewed"));
});

test("research candidate review is explicit and audited", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates/review", { method: "POST", body: { source_key: "github:org/agent", review_status: "accepted", note: "Licence and security reviewed." } }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).review_status, "accepted");
  assert.ok(DB.calls.some(x => x.kind === "run" && x.sql.startsWith("UPDATE research_candidates")));
  assert.ok(DB.calls.some(x => x.kind === "run" && x.sql.includes("INSERT INTO audit_log") && x.values[0] === "research_candidate_reviewed"));
});

test("research candidate review rejects unsupported state changes", async () => {
  const response = await worker.fetch(request("/v1/research/candidates/review", { method: "POST", body: { source_key: "github:org/agent", review_status: "auto_install" } }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 400);
});

test("research ingestion rejects unauthenticated callers", async () => {
  const response = await worker.fetch(request("/v1/research/candidates", { method: "POST", token: null, body: { candidates: [{ source: "github", full_name: "org/repo", url: "https://github.com/org/repo" }] } }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 401);
});

test("research candidate listing rejects invalid filters", async () => {
  const response = await worker.fetch(request("/v1/research/candidates?limit=1000&status=unknown"), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 400);
});

test("research listing omits large metadata by default and includes it only on request", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates?limit=20&status=all"), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  const defaultQuery = DB.calls.find(x => x.kind === "all");
  assert.doesNotMatch(defaultQuery.sql, /metadata_json/);
  const detailedDB = dbMock();
  await worker.fetch(request("/v1/research/candidates?limit=20&include_metadata=true"), { DB: detailedDB, ENGINE_API_TOKEN: TOKEN });
  const detailedQuery = detailedDB.calls.find(x => x.kind === "all");
  assert.match(detailedQuery.sql, /metadata_json/);
});

test("research keys-only listing returns only stable candidate keys", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates?status=all&limit=100&keys_only=true"), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  const query = DB.calls.find(x => x.kind === "all");
  assert.match(query.sql, /SELECT source_key FROM research_candidates/);
  assert.doesNotMatch(query.sql, /metadata_json|review_note|title/);
});


test("quota endpoints require the Worker bearer token", async () => {
  const response = await worker.fetch(request("/v1/quota?route_id=groq:main:model", { token: "wrong" }), {
    DB: dbMock(), ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(response.status, 401);
});

test("quota reservation rejects routes absent from server policy before touching D1", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/quota", {
    method: "POST",
    body: { action: "reserve", route_id: "unconfigured:route:model", task_id: "task-1", estimated_tokens: 10 },
  }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), { error: "route_not_configured" });
  assert.equal(DB.calls.length, 0);
});

test("quota reservation is one conditional parameterized insert", async () => {
  const calls = [];
  const DB = {
    prepare(sql) {
      return {
        values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          calls.push({ sql, values: this.values });
          return { id: 77 };
        },
      };
    },
  };
  const response = await worker.fetch(request("/v1/quota", {
    method: "POST",
    body: { action: "reserve", route_id: "groq:main:model", task_id: "task-1",
      estimated_tokens: 10, daily_request_limit: 1, daily_token_limit: 1,
      monthly_request_limit: 1, monthly_token_limit: 1, minute_request_limit: 1,
      minute_token_limit: 1, per_task_request_limit: 1, per_task_token_limit: 1 },
  }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(response.status, 201);
  const payload = await response.json();
  assert.equal(payload.reservation_id, 77);
  assert.equal(typeof payload.occurred_at, "string");
  assert.equal(calls.length, 1);
  assert.match(calls[0].sql, /^\s*INSERT INTO quota_usage/);
  assert.match(calls[0].sql, /NOT EXISTS \(SELECT 1 FROM quota_cooldowns/);
  assert.match(calls[0].sql, /RETURNING id/);
  assert.equal(calls[0].values.length, 32);
  assert.ok(calls[0].values.includes(2000));
  assert.ok(calls[0].values.includes(20000));
  assert.ok(calls[0].values.includes(300));
});

test("quota rejects oversized streaming bodies before buffering them fully", async () => {
  let canceled = false;
  const oversized = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(8193));
    },
    cancel() {
      canceled = true;
    },
  });
  const response = await worker.fetch(new Request("https://engine.example/v1/quota", {
    method: "POST",
    headers: {
      authorization: `Bearer ${TOKEN}`,
      "content-type": "application/json",
    },
    body: oversized,
    duplex: "half",
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "invalid_body" });
  assert.equal(canceled, true, "oversized request stream must be canceled at the limit");
});

test("quota administrative operations reject the shared runner token", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/quota", { method: "POST", body: { action: "cool_down", route_id: "groq:main:model", seconds: 60 } }),
    { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_QUOTA_ADMIN_TOKEN: ADMIN_TOKEN });
  assert.equal(response.status, 403);
  assert.equal(DB.calls.length, 0);
});

test("quota admin token is restricted to quota admin operations", async () => {
  const DB = {
    prepare() {
      return {
        bind() { return this; },
        async first() { return { until_at: "2099-01-01T00:00:00.000Z" }; },
      };
    },
  };
  const adminResponse = await worker.fetch(request("/v1/quota", { method: "POST", token: ADMIN_TOKEN, body: { action: "cool_down", route_id: "groq:main:model", seconds: 60 } }),
    { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_QUOTA_ADMIN_TOKEN: ADMIN_TOKEN });
  assert.equal(adminResponse.status, 200);
  const taskResponse = await worker.fetch(request("/v1/tasks", { token: ADMIN_TOKEN }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN, ENGINE_QUOTA_ADMIN_TOKEN: ADMIN_TOKEN });
  assert.equal(taskResponse.status, 401);
});

test("quota admin scope fails closed if configured equal to the shared runner token", async () => {
  const response = await worker.fetch(request("/v1/quota", { method: "POST", body: { action: "cool_down", route_id: "groq:main:model", seconds: 60 } }),
    { DB: dbMock(), ENGINE_API_TOKEN: TOKEN, ENGINE_QUOTA_ADMIN_TOKEN: TOKEN });
  assert.equal(response.status, 403);
});

test("quota admin token cannot reserve runner quota", async () => {
  const response = await worker.fetch(request("/v1/quota", { method: "POST", token: ADMIN_TOKEN, body: { action: "reserve", route_id: "groq:main:model", task_id: "task-1", estimated_tokens: 1 } }),
    { DB: dbMock(), ENGINE_API_TOKEN: TOKEN, ENGINE_QUOTA_ADMIN_TOKEN: ADMIN_TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(response.status, 403);
});

test("quota reservation rejects a server-disabled provider route", async () => {
  const DB = dbMock();
  const disabled = JSON.stringify({ "groq:main:model": { enabled: false, minute_request_limit: 5, minute_token_limit: 1000, daily_request_limit: 10, daily_token_limit: 2000, monthly_request_limit: 100, monthly_token_limit: 20000, per_task_request_limit: 3, per_task_token_limit: 300 } });
  const response = await worker.fetch(request("/v1/quota", { method: "POST", body: { action: "reserve", route_id: "groq:main:model", task_id: "task-1", estimated_tokens: 10 } }),
    { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: disabled });
  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), { error: "route_disabled" });
  assert.equal(DB.calls.length, 0);
});

test("quota settlement rejects a token amount above the server-owned task cap", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/quota", { method: "POST", body: { action: "settle", reservation_id: 5, route_id: "groq:main:model", task_id: "task-1", actual_tokens: 301 } }),
    { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(response.status, 400);
  assert.equal(DB.calls.length, 0);
});

test("quota settlement cannot cross task or route ownership", async () => {
  const calls = [];
  const DB = { prepare(sql) { return { values: [], bind(...values) { this.values = values; return this; },
    async run() { calls.push({ kind: "run", sql, values: this.values }); return { meta: { changes: 0 } }; },
    async first() { calls.push({ kind: "first", sql, values: this.values }); return null; } }; } };
  const response = await worker.fetch(request("/v1/quota", { method: "POST", body: { action: "settle", reservation_id: 5, route_id: "groq:main:model", task_id: "different-task", actual_tokens: 42 } }),
    { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(response.status, 404);
  assert.deepEqual(await response.json(), { error: "reservation_not_found" });
  assert.ok(calls[0].sql.includes("route_id=? AND task_id=?"));
  assert.deepEqual(calls[0].values.slice(2), [5, "groq:main:model", "different-task"]);
});

test("quota cooldown cannot shorten an existing cooldown", async () => {
  const existingUntil = "2099-01-01T00:00:00.000Z";
  let sql = "";
  const DB = {
    prepare(statement) {
      sql = statement;
      return {
        bind(...values) { this.values = values; return this; },
        async first() { return { until_at: existingUntil }; },
      };
    },
  };
  const response = await worker.fetch(request("/v1/quota", {
    method: "POST", token: ADMIN_TOKEN,
    body: { action: "cool_down", route_id: "groq:main:model", seconds: 60, reason: "short_retry" },
  }), { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_QUOTA_ADMIN_TOKEN: ADMIN_TOKEN });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).until_at, existingUntil);
  assert.match(sql, /until_at=MAX\(quota_cooldowns\.until_at,excluded\.until_at\)/);
  assert.match(sql, /CASE WHEN excluded\.until_at>quota_cooldowns\.until_at THEN excluded\.reason ELSE quota_cooldowns\.reason END/);
  assert.match(sql, /RETURNING until_at/);
});

test("quota settlement retries identical usage and rejects conflicting usage", async () => {
  let existing = { tokens: 42, settled_at: "2026-11-02T00:00:00.000Z" };
  const DB = {
    prepare(sql) {
      return {
        values: [],
        bind(...values) { this.values = values; return this; },
        async run() { return { meta: { changes: 0 } }; },
        async first() { return existing; },
      };
    },
  };
  const replay = await worker.fetch(request("/v1/quota", {
    method: "POST", body: { action: "settle", reservation_id: 5, route_id: "groq:main:model", task_id: "task-1", actual_tokens: 42 },
  }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(replay.status, 200);
  assert.equal((await replay.json()).replay, true);
  const conflict = await worker.fetch(request("/v1/quota", {
    method: "POST", body: { action: "settle", reservation_id: 5, route_id: "groq:main:model", task_id: "task-1", actual_tokens: 43 },
  }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(conflict.status, 409);
  assert.deepEqual(await conflict.json(), { error: "settlement_conflict" });
});


function paperLedgerDbMock() {
  const calls = [];
  let head = null;
  let event = null;
  return {
    calls,
    prepare(sql) {
      return {
        sql, values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          calls.push({ kind: "first", sql, values: this.values });
          if (sql.includes("SELECT * FROM paper_events WHERE event_id")) return event;
          if (sql.includes("SELECT sequence,event_hash FROM paper_candidate_heads")) return head;
          return null;
        },
        async run() {
          calls.push({ kind: "run", sql, values: this.values });
          if (sql.includes("INSERT OR IGNORE INTO paper_candidate_heads")) {
            if (!head) head = {
              candidate_id: this.values[0], sequence: 0, event_hash: this.values[1],
            };
          }
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
      const [update, insert] = statements;
      if (head && update.values[3] === head.candidate_id && head.sequence === update.values[4] && head.event_hash === update.values[5]) {
        head = { candidate_id: update.values[3], sequence: update.values[0], event_hash: update.values[1] };
        event = Object.fromEntries([
          "candidate_id","event_id","event_type","occurred_at","chain_id","block_number","block_hash",
          "strategy_id","source_pool","destination_pool","token_in","token_out","gross_profit_raw",
          "gas_used","gas_price_wei","gas_cost_profit_raw","net_profit_raw","payload_json",
        ].map((key, i) => [key, insert.values[i] ?? null]));
        event.sequence = update.values[0];
        event.previous_hash = update.values[5];
        event.event_hash = update.values[1];
        return [{ meta: { changes: 1 } }, { meta: { changes: 1 } }, { meta: { changes: 1 } }];
      }
      return [{ meta: { changes: 0 } }, { meta: { changes: 0 } }, { meta: { changes: 0 } }];
    },
  };
}

test("paper ledger append is authenticated and writes CAS head, event, and checkpoint", async () => {
  const DB = paperLedgerDbMock();
  const response = await worker.fetch(request("/v1/paper-ledger", {
    method: "POST",
    body: { action: "append", event: {
      candidate_id: "candidate-1", event_id: "event-1", event_type: "candidate_detected",
      occurred_at: "2026-11-02T11:00:00Z", payload: { source: "café" },
    } },
  }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(response.status, 201);
  const result = await response.json();
  assert.equal(result.sequence, 1);
  assert.equal(result.event_hash.length, 64);
  const batch = DB.calls.find(x => x.kind === "batch");
  assert.equal(batch.statements.length, 3);
  assert.match(batch.statements[0].sql, /WHERE candidate_id=\? AND sequence=\? AND event_hash=\?/);
  assert.match(batch.statements[1].sql, /INSERT INTO paper_events/);
  assert.equal(batch.statements[1].values[17], String.raw`{"source":"caf\u00e9"}`);
  assert.match(batch.statements[2].sql, /INSERT INTO paper_event_checkpoints/);
});

test("paper ledger rejects missing block provenance for outcome events before writes", async () => {
  const DB = paperLedgerDbMock();
  const response = await worker.fetch(request("/v1/paper-ledger", {
    method: "POST",
    body: { action: "append", event: {
      candidate_id: "candidate-1", event_id: "event-2", event_type: "outcome_observed",
      occurred_at: "2026-11-02T11:00:00Z", payload: {},
    } },
  }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(response.status, 400);
  assert.equal(DB.calls.length, 0);
});

test("paper ledger rejects unauthenticated append requests", async () => {
  const DB = paperLedgerDbMock();
  const response = await worker.fetch(request("/v1/paper-ledger", {
    method: "POST", token: null,
    body: { action: "append", event: {} },
  }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(response.status, 401);
  assert.equal(DB.calls.length, 0);
});

test("paper ledger rejects arbitrary event types and malformed raw integer strings", async () => {
  for (const event of [
    { candidate_id: "candidate-1", event_id: "event-3", event_type: "execute_trade", occurred_at: "2026-11-02T11:00:00Z", payload: {} },
    { candidate_id: "candidate-1", event_id: "event-4", event_type: "candidate_detected", occurred_at: "2026-11-02T11:00:00Z", gas_used: 12, payload: {} },
  ]) {
    const DB = paperLedgerDbMock();
    const response = await worker.fetch(request("/v1/paper-ledger", {
      method: "POST", body: { action: "append", event },
    }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
    assert.equal(response.status, 400);
    assert.equal(DB.calls.length, 0);
  }
});


test("paper ledger rejects non-canonical payload numbers and naive timestamps before writes", async () => {
  const invalidEvents = [
    { candidate_id: "candidate-1", event_id: "float-1", event_type: "candidate_detected",
      occurred_at: "2026-11-02T11:00:00Z", payload: { fraction: 1.5 } },
    { candidate_id: "candidate-1", event_id: "unsafe-1", event_type: "candidate_detected",
      occurred_at: "2026-11-02T11:00:00Z", payload: { unsafe: 9007199254740992 } },
    { candidate_id: "candidate-1", event_id: "naive-1", event_type: "candidate_detected",
      occurred_at: "2026-11-02T11:00:00", payload: {} },
  ];
  for (const event of invalidEvents) {
    const DB = paperLedgerDbMock();
    const response = await worker.fetch(request("/v1/paper-ledger", {
      method: "POST", body: { action: "append", event },
    }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
    assert.equal(response.status, 400);
    assert.equal(DB.calls.length, 0);
  }
});

test("paper ledger rejects raw-unit integers outside uint256 bounds", async () => {
  const DB = paperLedgerDbMock();
  const response = await worker.fetch(request("/v1/paper-ledger", {
    method: "POST",
    body: { action: "append", event: {
      candidate_id: "candidate-1", event_id: "too-large", event_type: "route_simulated",
      occurred_at: "2026-11-02T11:00:00Z", chain_id: 1, block_number: 1,
      block_hash: "0x" + "a".repeat(64), strategy_id: "v2",
      source_pool: "0x" + "1".repeat(40), destination_pool: "0x" + "2".repeat(40),
      token_in: "0x" + "3".repeat(40), token_out: "0x" + "4".repeat(40),
      gross_profit_raw: "115792089237316195423570985008687907853269984665640564039457584007913129639936",
      payload: {},
    } },
  }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "invalid_gross_profit_raw" });
  assert.equal(DB.calls.length, 0);
});


function concurrentPaperLedgerDbMock() {
  const state = { head: null, events: new Map(), checkpoints: [] };
  let headReads = 0;
  let releaseHeadReads;
  const bothHeadReads = new Promise(resolve => { releaseHeadReads = resolve; });
  let batchTail = Promise.resolve();
  return {
    state,
    prepare(sql) {
      return {
        sql, values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          if (sql.includes("SELECT * FROM paper_events WHERE event_id")) {
            return state.events.get(this.values[0]) || null;
          }
          if (sql.includes("SELECT sequence,event_hash FROM paper_candidate_heads")) {
            const snapshot = { ...state.head };
            headReads += 1;
            if (headReads === 2) releaseHeadReads();
            await bothHeadReads;
            return snapshot;
          }
          return null;
        },
        async run() {
          if (sql.includes("INSERT OR IGNORE INTO paper_candidate_heads") && !state.head) {
            state.head = { candidate_id: this.values[0], sequence: 0, event_hash: this.values[1] };
          }
          return { meta: { changes: 1 } };
        },
      };
    },
    async batch(statements) {
      const previous = batchTail;
      let release;
      batchTail = new Promise(resolve => { release = resolve; });
      await previous;
      try {
        const [update, insert, checkpoint] = statements;
        const [nextSequence, nextHash, _now, candidateId, oldSequence, oldHash] = update.values;
        let updateChanges = 0;
        if (state.head.candidate_id === candidateId &&
            state.head.sequence === oldSequence && state.head.event_hash === oldHash) {
          state.head = { candidate_id: candidateId, sequence: nextSequence, event_hash: nextHash };
          updateChanges = 1;
        }
        const insertValues = insert.values;
        const recordFields = [
          "candidate_id", "event_id", "event_type", "occurred_at", "chain_id", "block_number",
          "block_hash", "strategy_id", "source_pool", "destination_pool", "token_in", "token_out",
          "gross_profit_raw", "gas_used", "gas_price_wei", "gas_cost_profit_raw", "net_profit_raw",
          "payload_json",
        ];
        const expectedHead = [insertValues[21], insertValues[22], insertValues[23]];
        let insertChanges = 0;
        if (state.head.candidate_id === expectedHead[0] &&
            state.head.sequence === expectedHead[1] && state.head.event_hash === expectedHead[2] &&
            !state.events.has(insertValues[1])) {
          const event = Object.fromEntries(recordFields.map((key, index) => [key, insertValues[index] ?? null]));
          event.sequence = insertValues[18];
          event.previous_hash = insertValues[19];
          event.event_hash = insertValues[20];
          state.events.set(event.event_id, event);
          insertChanges = 1;
        }
        let checkpointChanges = 0;
        const checkpointValues = checkpoint.values;
        if (state.events.has(checkpointValues[2])) {
          state.checkpoints.push({
            sequence: checkpointValues[1], event_id: checkpointValues[2], event_hash: checkpointValues[3],
          });
          checkpointChanges = 1;
        }
        return [
          { meta: { changes: updateChanges } },
          { meta: { changes: insertChanges } },
          { meta: { changes: checkpointChanges } },
        ];
      } finally {
        release();
      }
    },
  };
}

test("concurrent appends for one candidate cannot fork its sequence or hash chain", async () => {
  const DB = concurrentPaperLedgerDbMock();
  const makeRequest = eventId => request("/v1/paper-ledger", {
    method: "POST",
    body: { action: "append", event: {
      candidate_id: "candidate-race", event_id: eventId, event_type: "candidate_detected",
      occurred_at: "2026-11-02T11:00:00Z", payload: { event: eventId },
    } },
  });
  const responses = await Promise.all([
    worker.fetch(makeRequest("race-a"), { DB, ENGINE_API_TOKEN: TOKEN }),
    worker.fetch(makeRequest("race-b"), { DB, ENGINE_API_TOKEN: TOKEN }),
  ]);
  assert.deepEqual(responses.map(response => response.status).sort(), [201, 409]);
  assert.equal(DB.state.head.sequence, 1);
  assert.equal(DB.state.events.size, 1);
  assert.equal(DB.state.checkpoints.length, 1);
});


test("paper ledger canonicalizes supplementary Unicode keys in Python code-point order", async () => {
  const DB = paperLedgerDbMock();
  const payload = { "\u{10000}": 2, "\uE000": 1, a: 3 };
  const response = await worker.fetch(request("/v1/paper-ledger", {
    method: "POST",
    body: { action: "append", event: {
      candidate_id: "candidate-unicode", event_id: "unicode-1",
      event_type: "candidate_detected", occurred_at: "2026-11-02T11:00:00Z", payload,
    } },
  }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(response.status, 201);
  const batch = DB.calls.find(call => call.kind === "batch");
  assert.equal(batch.statements[1].values[17], String.raw`{"a":3,"\ue000":1,"\ud800\udc00":2}`);
});


test("paper ledger canonicalizes integer-like object keys lexicographically like Python", async () => {
  const DB = paperLedgerDbMock();
  const response = await worker.fetch(request("/v1/paper-ledger", {
    method: "POST",
    body: { action: "append", event: {
      candidate_id: "candidate-numeric-keys", event_id: "numeric-keys-1",
      event_type: "candidate_detected", occurred_at: "2026-11-02T11:00:00Z",
      payload: { "2": "two", "10": "ten", a: "letter" },
    } },
  }), { DB, ENGINE_API_TOKEN: TOKEN, QUOTA_ROUTE_LIMITS_JSON: QUOTA_LIMITS });
  assert.equal(response.status, 201);
  const batch = DB.calls.find(call => call.kind === "batch");
  assert.equal(batch.statements[1].values[17], String.raw`{"10":"ten","2":"two","a":"letter"}`);
});

test("Codespace status is read-only and requires its distinct scoped token", async () => {
  const DB = dbMock();
  const denied = await worker.fetch(request("/v1/codespace/status", { token: TOKEN }), {
    DB, ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN,
  });
  assert.equal(denied.status, 401);
  const allowed = await worker.fetch(request("/v1/codespace/status", {
    token: CODESPACE_TOKEN,
  }), { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
  assert.equal(allowed.status, 200);
  assert.deepEqual(await allowed.json(), {
    ok: true, executor: "codespace", capabilities: ["inspect", "test"],
    task_types: ["codespace_inspect", "codespace_test"], max_lease_seconds: 900,
  });
  assert.equal(DB.calls.length, 0);
});

test("Codespace token cannot access general task, quota, or recovery routes", async () => {
  for (const path of ["/v1/tasks", "/v1/tasks/claim", "/v1/tasks/recover", "/v1/quota"]) {
    const post = path.endsWith("/claim") || path.endsWith("/recover");
    const response = await worker.fetch(request(path, {
      method: post ? "POST" : "GET", token: CODESPACE_TOKEN, body: post ? {} : undefined,
    }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
    assert.equal(response.status, 401, path);
  }
});

test("Codespace claim is isolated to its allowlisted task types", async () => {
  const claim = {
    id: "task-code", idempotency_key: "code-1", task_type: "codespace_test",
    title: "Run tests", payload_json: '{"operation":"python_tests"}',
    status: "running", attempts: 1, max_attempts: 2,
  };
  const DB = dbMock({ codespaceClaim: claim });
  const response = await worker.fetch(request("/v1/codespace/tasks/claim", {
    method: "POST", token: CODESPACE_TOKEN, body: {},
  }), { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).task.task_type, "codespace_test");
  const claimSql = DB.calls.find(x => x.kind === "first" && x.sql.includes("UPDATE tasks SET status='running'")).sql;
  assert.match(claimSql, /task_type IN \('codespace_inspect','codespace_test'\)/);
});

test("legacy Task Runner claim explicitly excludes Codespace tasks", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/claim", {
    method: "POST", body: {},
  }), { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { task: null });
  const claimSql = DB.calls.find(x => x.kind === "first" && x.sql.includes("UPDATE tasks SET status='running'")).sql;
  assert.match(claimSql, /task_type NOT IN \('codespace_inspect','codespace_test'\)/);
});

test("Codespace lease actions can only mutate Codespace task types", async () => {
  const codeTask = {
    id: "task-code", status: "running", attempts: 1, max_attempts: 2,
    task_type: "codespace_test", lease_token: "lease-1",
    lease_expires_at: "2999-01-01T00:00:00.000Z",
  };
  const DB = dbMock({ currentTask: codeTask });
  const response = await worker.fetch(request("/v1/codespace/tasks/task-code/heartbeat", {
    method: "POST", token: CODESPACE_TOKEN, body: { lease_token: "lease-1" },
  }), { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
  assert.equal(response.status, 200);
  const denied = await worker.fetch(request("/v1/tasks/task-code/heartbeat", {
    method: "POST", token: TOKEN, body: { lease_token: "lease-1" },
  }), { DB: dbMock({ currentTask: codeTask }), ENGINE_API_TOKEN: TOKEN,
    ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
  assert.equal(denied.status, 403);
  assert.deepEqual(await denied.json(), { error: "executor_mismatch" });
});

test("Codespace token must differ from shared API and quota-admin tokens", async () => {
  const response = await worker.fetch(request("/v1/codespace/status", { token: TOKEN }), {
    DB: dbMock(), ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: TOKEN,
  });
  assert.equal(response.status, 401);
});

test("Codespace principal has no retry route", async () => {
  const response = await worker.fetch(request("/v1/codespace/tasks/task-code/retry", {
    method: "POST", token: CODESPACE_TOKEN, body: { lease_token: "lease-1" },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
  assert.equal(response.status, 404);
});

test("existing Task Runner retains its fenced retry route", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/task-1/retry", {
    method: "POST", token: TOKEN, body: { lease_token: "lease-1", error: "retry test" },
  }), { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true, status: "pending" });
});

test("Codespace principal rejects a weak server-side token", async () => {
  const response = await worker.fetch(request("/v1/codespace/status", { token: "short-token" }), {
    DB: dbMock(), ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: "short-token",
  });
  assert.equal(response.status, 401);
});
