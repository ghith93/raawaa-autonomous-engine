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
