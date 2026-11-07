import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";
import { generateKeyPairSync } from "node:crypto";

const TOKEN = "test-worker-token";
const CODESPACE_TOKEN = "test-codespace-token-0123456789abcdef0123456789abcdef";
const ADMIN_TOKEN = "test-quota-admin-token";
const QUOTA_LIMITS = JSON.stringify({"groq:main:model":{enabled:true,minute_request_limit:5,minute_token_limit:1000,daily_request_limit:10,daily_token_limit:2000,monthly_request_limit:100,monthly_token_limit:20000,per_task_request_limit:3,per_task_token_limit:300}});

function request(path, { method = "GET", token = TOKEN, body, extraHeaders = {} } = {}) {
  const headers = { ...extraHeaders };
  if (token) headers.authorization = `Bearer ${token}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  return new Request(`https://engine.example${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function dbMock(options = {}) {
  const calls = [];
  let insertedId = null;
  let insertedTask = null;
  let insertedDependencies = [];
  let insertedResearchBatch = null;
  return {
    calls,
    prepare(sql) {
      return {
        sql, values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          calls.push({ kind: "first", sql, values: this.values });
          if (sql.includes("INSERT INTO quota_cooldowns")) return { until_at: this.values[1] };
          if (sql.includes("FROM research_ingest_batches")) return options.researchBatch || insertedResearchBatch;
          if (sql.includes("FROM audit_log")) return null;
          // Claim SQL contains a nested SELECT 1; match the UPDATE before generic probes.
          if (sql.includes("UPDATE tasks SET status='running'")) {
            return sql.includes("task_type IN") ? (options.codespaceClaim || null) : (options.normalClaim || null);
          }
          if (sql.includes("SELECT 1")) return { ok: 1 };
          if (sql.includes("SELECT id,status,attempts")) return options.currentTask || {
            id: "task-1", status: "running", attempts: 1, max_attempts: 2,
            task_type: "research_brief", execution_mode: "not_started", execution_lane: "general", lease_token: "lease-1",
            lease_expires_at: "2999-01-01T00:00:00.000Z",
          };
          if (sql.includes("WHERE idempotency_key")) return options.existingTask || insertedTask;
          return null;
        },
        async run() {
          calls.push({ kind: "run", sql, values: this.values });
          return { meta: { changes: 1 } };
        },
        async all() {
          calls.push({ kind: "all", sql, values: this.values });
          if (sql.includes("FROM audit_log")) return { results: options.auditRows || [] };
          if (sql.includes("SELECT source_key FROM research_candidates WHERE source_key IN")) {
            return { results: options.researchVerifyRows || this.values.map(source_key => ({ source_key })) };
          }
          if (sql.includes("FROM tasks WHERE id IN")) return { results: options.dependencyRows || [] };
          if (sql.includes("FROM task_dependencies")) return { results: options.dependencyEdges || insertedDependencies };
          if (sql.includes("SELECT id,attempts,max_attempts,lease_token FROM tasks")) return { results: options.staleTasks || [] };
          if (sql.includes("WITH RECURSIVE blocked")) return { results: options.blockedTasks || [] };
          return { results: [] };
        },
      };
    },
    async batch(statements) {
      calls.push({ kind: "batch", statements: statements.map(x => ({ sql: x.sql, values: x.values })) });
      const insert = statements[0];
      if (insert.sql.includes("INSERT OR IGNORE INTO research_ingest_batches")) {
        insertedResearchBatch = { payload_sha256: insert.values[1], candidate_count: insert.values[2] };
        const headerChanges = options.researchBatchChanges ?? 1;
        return statements.map((statement, index) => ({
          success: true,
          meta: { changes: index === 0 ? headerChanges : 1 },
        }));
      }
      if (insert.sql.includes("INSERT OR IGNORE INTO tasks")) {
        const v = insert.values;
        insertedId = v[0];
        insertedTask = {
          id: v[0], idempotency_key: v[1], task_type: v[2], title: v[3],
          payload_json: v[4], max_attempts: v[5], priority: v[8],
          dependencies_json: v[9], execution_lane: v[10] || "general", status: "pending", attempts: 0,
        };
        insertedDependencies = statements
          .filter(statement => statement.sql.includes("INSERT OR IGNORE INTO task_dependencies"))
          .map(statement => ({ dependency_task_id: statement.values[1] }));
      }
      return options.batchResults || [];
    },
  };
}

test("specialist stale recovery requires its own capability token", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/recover", {
    method: "POST", body: { worker_lane: "specialist:researcher" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 401);
  assert.deepEqual(await response.json(), { error: "lane_unauthorized" });
  assert.equal(DB.calls.length, 0);
});

test("general stale recovery only selects general-lane leases", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/recover", {
    method: "POST", body: { worker_lane: "general" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  const query = DB.calls.find(call => call.kind === "all" &&
    call.sql.includes("SELECT id,attempts,max_attempts,lease_token"));
  assert.match(query.sql, /execution_lane=\?/);
  assert.deepEqual(query.values.slice(1), ["general"]);
});

test("task readback defaults to the general lane", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks?limit=10"), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  const query = DB.calls.find(call => call.kind === "all" && call.sql.includes("FROM tasks WHERE execution_lane"));
  assert.match(query.sql, /execution_lane=\?/);
  assert.deepEqual(query.values, ["general", 10]);
});

test("specialist task readback requires its lane capability", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks?limit=10&worker_lane=specialist%3Aresearcher"), {
    DB, ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(response.status, 401);
  assert.equal(DB.calls.length, 0);
});

test("specialist task readback returns only its lane", async () => {
  const laneToken = "researcher-lane-token-0123456789abcdef0123456789";
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks?limit=10&worker_lane=specialist%3Aresearcher", {
    extraHeaders: { "x-engine-lane-token": laneToken },
  }), { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_RESEARCHER_LANE_TOKEN: laneToken });
  assert.equal(response.status, 200);
  const query = DB.calls.find(call => call.kind === "all" && call.sql.includes("FROM tasks WHERE execution_lane"));
  assert.deepEqual(query.values, ["specialist:researcher", 10]);
});

test("specialist claim requires a dedicated lane capability token", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run", worker_lane: "specialist:researcher" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 401);
  assert.equal(DB.calls.length, 0);
});

test("specialist lane capability tokens cannot be reused across lanes", async () => {
  const sharedLaneToken = "shared-specialist-token-0123456789abcdef0123456789";
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run", worker_lane: "specialist:researcher" },
    extraHeaders: { "x-engine-lane-token": sharedLaneToken },
  }), {
    DB, ENGINE_API_TOKEN: TOKEN,
    ENGINE_RESEARCHER_LANE_TOKEN: sharedLaneToken,
    ENGINE_REVIEWER_LANE_TOKEN: sharedLaneToken,
  });
  assert.equal(response.status, 401);
  assert.equal(DB.calls.length, 0);
});

test("specialist capability cannot reuse admin or Codespace credentials", async () => {
  const shared = "shared-lane-secret-0123456789abcdef0123456789";
  for (const env of [
    { ENGINE_RESEARCHER_LANE_TOKEN: shared, ENGINE_QUOTA_ADMIN_TOKEN: shared },
    { ENGINE_RESEARCHER_LANE_TOKEN: shared, ENGINE_CODESPACE_TOKEN: shared },
  ]) {
    const DB = dbMock();
    const response = await worker.fetch(request("/v1/tasks/claim", {
      method: "POST", body: { execution_mode: "dry_run", worker_lane: "specialist:researcher" },
      extraHeaders: { "x-engine-lane-token": shared },
    }), { DB, ENGINE_API_TOKEN: TOKEN, ...env });
    assert.equal(response.status, 401);
    assert.equal(DB.calls.length, 0);
  }
});

test("specialist claim filters the requested lane and task type", async () => {
  const laneToken = "researcher-lane-token-0123456789abcdef0123456789";
  const DB = dbMock({ normalClaim: {
    id: "research-task", task_type: "research_brief", execution_lane: "specialist:researcher",
    status: "running", attempts: 1, lease_token: "lease-r",
  } });
  const response = await worker.fetch(request("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run", worker_lane: "specialist:researcher" },
    extraHeaders: { "x-engine-lane-token": laneToken },
  }), { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_RESEARCHER_LANE_TOKEN: laneToken });
  assert.equal(response.status, 200);
  const claim = DB.calls.find(call => call.kind === "first" &&
    call.sql.includes("UPDATE tasks SET status='running'"));
  assert.match(claim.sql, /execution_lane=\?/);
  assert.match(claim.sql, /task_type=\?/);
  assert.ok(claim.values.includes("specialist:researcher"));
  assert.ok(claim.values.includes("research_brief"));
});

test("enqueue persists the selected specialist lane", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST", body: { task_type: "research_brief", title: "Research",
      idempotency_key: "research-lane", execution_lane: "specialist:researcher" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 201);
  const insert = DB.calls.find(call => call.kind === "batch").statements[0];
  assert.equal(insert.values[10], "specialist:researcher");
  assert.match(insert.sql, /execution_lane/);
});

test("generic consumer cannot heartbeat a specialist lease", async () => {
  const DB = dbMock({ currentTask: {
    id: "task-1", status: "running", attempts: 1, max_attempts: 2,
    task_type: "research_brief", execution_mode: "dry_run",
    execution_lane: "specialist:researcher", lease_token: "lease-1",
    lease_expires_at: "2999-01-01T00:00:00.000Z",
  } });
  const response = await worker.fetch(request("/v1/tasks/task-1/heartbeat", {
    method: "POST", body: { lease_token: "lease-1", worker_lane: "general" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), { error: "lane_ownership_lost" });
  assert.equal(DB.calls.some(call => call.kind === "run"), false);
});

test("enqueue rejects specialist task type and lane mismatch", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST", body: { task_type: "content_draft", title: "Bad lane",
      idempotency_key: "bad-lane", execution_lane: "specialist:researcher" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "task_type_lane_mismatch" });
  assert.equal(DB.calls.length, 0);
});

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

test("audit route requires authentication and rejects writes", async () => {
  const unauthenticated = await worker.fetch(request("/v1/audit", { token: "wrong" }), {
    DB: dbMock(), ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(unauthenticated.status, 401);
  const write = await worker.fetch(request("/v1/audit", { method: "POST", body: {} }), {
    DB: dbMock(), ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(write.status, 405);
});

test("audit route returns parsed bounded events with optional exact task filter", async () => {
  const DB = dbMock({ auditRows: [
    { id: 9, task_id: "task-1", event: "task_succeeded", details_json: '{"status":"succeeded"}', created_at: "2026-11-05T00:00:02.000Z" },
    { id: 8, task_id: "task-1", event: "task_claimed", details_json: "not-json", created_at: "2026-11-05T00:00:01.000Z" },
  ] });
  const response = await worker.fetch(request("/v1/audit?limit=5&task_id=task-1"), {
    DB, ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.deepEqual(payload.events.map(e => e.event), ["task_succeeded", "task_claimed"]);
  assert.deepEqual(payload.events[0].details, { status: "succeeded" });
  assert.equal(payload.events[1].details, null, "malformed legacy details stay null");
  const query = DB.calls.find(x => x.kind === "all");
  assert.match(query.sql, /WHERE task_id=\?/);
  assert.deepEqual(query.values, ["task-1", 5]);
});

test("audit route validates limit and task_id", async () => {
  const env = { DB: dbMock(), ENGINE_API_TOKEN: TOKEN };
  assert.equal((await worker.fetch(request("/v1/audit?limit=0"), env)).status, 400);
  assert.equal((await worker.fetch(request("/v1/audit?limit=101"), env)).status, 400);
  assert.equal((await worker.fetch(request("/v1/audit?task_id=bad%20id"), env)).status, 400);
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
  assert.equal(batch.statements[0].values[8], 50);
  assert.equal(batch.statements[0].values[9], "[]");
  assert.match(batch.statements[1].sql, /INSERT INTO audit_log/);
});

test("enqueue persists priority and validated dependency edges", async () => {
  const DB = dbMock({ dependencyRows: [{ id: "parent-1", task_type: "research_brief" }] });
  const response = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST",
    body: {
      task_type: "content_draft", title: "Dependent", idempotency_key: "child-key",
      priority: 90, depends_on: ["parent-1"], payload: {},
    },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 201);
  const payload = await response.json();
  assert.equal(payload.task.priority, 90);
  assert.deepEqual(payload.task.depends_on, ["parent-1"]);
  const batch = DB.calls.find(call => call.kind === "batch");
  assert.equal(batch.statements.length, 3);
  assert.match(batch.statements[2].sql, /INSERT OR IGNORE INTO task_dependencies/);
});

test("enqueue rejects dependencies assigned to the disabled Codespace executor", async () => {
  const DB = dbMock({ dependencyRows: [{ id: "codespace-parent", task_type: "codespace_test" }] });
  const response = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST",
    body: { task_type: "research", title: "Dependent", idempotency_key: "codespace-child",
      depends_on: ["codespace-parent"] },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "unsupported_dependency_executor" });
});

test("enqueue rejects out-of-range priority and unknown dependencies", async () => {
  const invalidPriority = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST",
    body: { task_type: "research", title: "Bad", idempotency_key: "bad-priority", priority: 101 },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(invalidPriority.status, 400);
  const missingDependency = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST",
    body: { task_type: "research", title: "Bad dependency", idempotency_key: "bad-dep", depends_on: ["missing"] },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(missingDependency.status, 400);
  assert.deepEqual(await missingDependency.json(), { error: "unknown_task_dependency" });
  const blankDependency = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST",
    body: { task_type: "research", title: "Bad blank dependency", idempotency_key: "bad-blank-dep", depends_on: ["   "] },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(blankDependency.status, 400);
  assert.deepEqual(await blankDependency.json(), { error: "invalid_task" });
});

test("enqueue rejects an idempotency key reused with a different priority", async () => {
  const DB = dbMock({ existingTask: {
    id: "existing", task_type: "research", title: "Same", payload_json: "{}",
    max_attempts: 2, priority: 50, dependencies_json: "[]",
  } });
  const response = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST",
    body: { task_type: "research", title: "Same", idempotency_key: "same-key",
      payload: {}, priority: 90 },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { error: "idempotency_conflict" });
  assert.equal(DB.calls.some(call => call.kind === "batch"), false);
});

test("enqueue replay binds type, title, payload and attempt policy", async () => {
  const existingTask = {
    id: "existing", task_type: "research_brief", title: "Stable",
    payload_json: '{"nested":{"a":1,"b":2},"list":[1,2]}',
    max_attempts: 3, priority: 50, dependencies_json: "[]", execution_lane: "general",
  };
  const base = { task_type: "research_brief", title: "Stable", idempotency_key: "key",
    payload: { list: [1, 2], nested: { b: 2, a: 1 } }, max_attempts: 3 };
  for (const [change, status] of [
    [{}, 200], [{task_type: "content_draft"}, 409], [{title: "Changed"}, 409],
    [{payload: {list: [2, 1], nested: {a: 1, b: 2}}}, 409], [{max_attempts: 2}, 409],
  ]) {
    const DB = dbMock({existingTask});
    const response = await worker.fetch(request("/v1/tasks/enqueue", {
      method: "POST", body: {...base, ...change},
    }), {DB, ENGINE_API_TOKEN: TOKEN});
    assert.equal(response.status, status);
    assert.equal(DB.calls.some(call => call.kind === "batch"), false);
  }
  const corrupt = dbMock({existingTask: {...existingTask, payload_json: "invalid"}});
  const response = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST", body: base,
  }), {DB: corrupt, ENGINE_API_TOKEN: TOKEN});
  assert.equal(response.status, 409);
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

test("process dispatch route is authenticated and refuses absent or non-process task leases", async () => {
  const unauthenticated = await worker.fetch(request("/v1/tasks/task-1/process-dispatches", {
    method: "POST", token: null, body: { action: "prepare" },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(unauthenticated.status, 401);

  const DB = dbMock();
  const wrongTask = await worker.fetch(request("/v1/tasks/task-1/process-dispatches", {
    method: "POST", body: { action: "prepare", lease_token: "lease-1" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(wrongTask.status, 409);
  assert.equal(DB.calls.some(call => call.kind === "run"), false);
});


function recoveryDb(overrides = {}) {
  const calls = [];
  const current = {
    id: "probe-123",
    idempotency_key: "staging-cron-supervised-probe:20261105",
    task_type: "supervised_probe",
    title: "Scheduled Staging read-only process probe",
    payload_json: JSON.stringify({ operation: "python_platform_probe" }),
    status: "running",
    attempts: 1,
    max_attempts: 2,
    execution_mode: "dry_run",
    priority: 100,
    dependencies_json: "[]",
    result_json: null,
    lease_token: "old-lease",
    lease_expires_at: "2000-01-01T00:00:00.000Z",
    ...overrides.current,
  };
  const state = {
    taskStatus: current.status,
    dispatchStatus: overrides.dispatch?.status ?? "in_flight",
    auditCount: 0,
    taskTransitions: 0,
    updatedAt: null,
  };
  return {
    calls, state,
    async batch(statements) {
      calls.push({ kind: "batch", statements: statements.map(x => ({ sql: x.sql, values: x.values })) });
      return statements.map(stmt => {
        if (stmt.sql.includes("UPDATE task_process_dispatches")) {
          const changes = state.dispatchStatus === "in_flight" && state.taskStatus === "running" &&
            current.attempts >= current.max_attempts ? 1 : 0;
          if (changes) state.dispatchStatus = "ambiguous";
          return { meta: { changes } };
        }
        if (stmt.sql.includes("UPDATE tasks SET status=?")) {
          const changes = state.taskStatus === "running" ? 1 : 0;
          if (changes) {
            state.taskStatus = stmt.values[0]; state.updatedAt = stmt.values[2];
            state.taskTransitions += 1;
          }
          return { meta: { changes } };
        }
        if (stmt.sql.includes("INSERT INTO audit_log")) {
          // The audit insert is gated on the pre-transition running row.
          const changes = state.taskStatus === "running" ? 1 : 0;
          if (changes) state.auditCount += 1;
          return { meta: { changes } };
        }
        return { meta: { changes: 0 } };
      });
    },
    prepare(sql) {
      return {
        sql, values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          calls.push({ kind: "first", sql, values: this.values });
          if (sql.includes("FROM tasks WHERE id=?") && sql.includes("idempotency_key")) {
            return { ...current, status: state.taskStatus };
          }
          if (sql.includes("FROM task_process_dispatches")) return { status: state.dispatchStatus };
          if (sql.includes("SELECT status,attempts,max_attempts")) {
            return overrides.latest ?? {
              status: state.taskStatus, attempts: current.attempts, max_attempts: current.max_attempts,
            };
          }
          return null;
        },
        async run() {
          calls.push({ kind: "run", sql, values: this.values });
          return { meta: { changes: 1 } };
        },
      };
    },
  };
}

test("exact supervised-probe recovery requires authentication, POST, and an empty body", async () => {
  const path = "/v1/tasks/probe-123/recover-supervised-probe";
  const unauthenticated = await worker.fetch(request(path, {
    method: "POST", token: null, body: {},
  }), { DB: recoveryDb(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(unauthenticated.status, 401);
  const wrongMethod = await worker.fetch(request(path, { method: "GET" }), {
    DB: recoveryDb(), ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(wrongMethod.status, 405);
  const extraInput = await worker.fetch(request(path, {
    method: "POST", body: { task_type: "anything" },
  }), { DB: recoveryDb(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(extraInput.status, 400);
});

test("exact supervised-probe recovery requeues only an expired exact task and preserves in-flight dispatch", async () => {
  const DB = recoveryDb();
  const response = await worker.fetch(request(
    "/v1/tasks/probe-123/recover-supervised-probe", { method: "POST", body: {} },
  ), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    status: "pending", recovered: true, replay: false, attempts: 1,
    max_attempts: 2, dispatch_status: "in_flight",
  });
  const batch = DB.calls.find(call => call.kind === "batch");
  assert.ok(batch, "recovery transition must be one atomic D1 batch");
  assert.equal(batch.statements.length, 3);
  const update = batch.statements.find(stmt => stmt.sql.includes("UPDATE tasks SET status=?"));
  assert.ok(update.sql.includes("WHERE id=?"));
  assert.ok(update.sql.includes("AND lease_token=?"));
  assert.ok(update.sql.includes("AND lease_expires_at<?"));
  assert.equal(update.values[0], "pending");
  assert.equal(update.values[3], "probe-123");
  const audit = batch.statements.find(stmt => stmt.sql.includes("INSERT INTO audit_log"));
  assert.equal(audit.values[0], "supervised_probe_lease_recovered");
  assert.equal(DB.state.taskTransitions, 1);
  assert.equal(DB.state.auditCount, 1);
  assert.equal(DB.state.dispatchStatus, "in_flight", "requeue preserves the in-flight dispatch");
});

test("concurrent exact recovery yields one transition and one replay without a second lease", async () => {
  const DB = recoveryDb();
  const path = "/v1/tasks/probe-123/recover-supervised-probe";
  const [first, second] = await Promise.all([
    worker.fetch(request(path, { method: "POST", body: {} }), { DB, ENGINE_API_TOKEN: TOKEN }),
    worker.fetch(request(path, { method: "POST", body: {} }), { DB, ENGINE_API_TOKEN: TOKEN }),
  ]);
  const bodies = [await first.json(), await second.json()];
  const recovered = bodies.filter(body => body.recovered === true);
  const replays = bodies.filter(body => body.recovered === false && body.replay === true);
  assert.equal(recovered.length, 1, "exactly one concurrent caller transitions the task");
  assert.equal(replays.length, 1, "the other caller gets a deterministic replay");
  assert.equal(DB.state.taskTransitions, 1);
  assert.equal(DB.state.auditCount, 1, "concurrent recovery writes exactly one audit event");
});

test("exact recovery rejects a different task envelope and a non-expired lease without writes", async () => {
  for (const overrides of [
    { current: { task_type: "system_audit" } },
    { current: { idempotency_key: "other-task:20261105" } },
    { current: { payload_json: JSON.stringify({ operation: "other" }) } },
    { current: { lease_expires_at: "2999-01-01T00:00:00.000Z" } },
  ]) {
    const DB = recoveryDb(overrides);
    const response = await worker.fetch(request(
      "/v1/tasks/probe-123/recover-supervised-probe", { method: "POST", body: {} },
    ), { DB, ENGINE_API_TOKEN: TOKEN });
    assert.ok([403, 409].includes(response.status));
    assert.equal(DB.calls.some(call => call.sql?.includes("UPDATE tasks SET status=?")), false);
  }
});

test("exact recovery fails the normal one-attempt probe without enabling a retry", async () => {
  const DB = recoveryDb({ current: { max_attempts: 1 } });
  const response = await worker.fetch(request(
    "/v1/tasks/probe-123/recover-supervised-probe", { method: "POST", body: {} },
  ), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.status, "failed");
  assert.equal(result.recovered, true);
  assert.equal(result.dispatch_status, "ambiguous");
  const batch = DB.calls.find(call => call.kind === "batch");
  assert.ok(batch, "exhausted recovery must be one atomic D1 batch");
  const dispatchQuarantine = batch.statements.find(stmt =>
    stmt.sql.includes("UPDATE task_process_dispatches"));
  assert.ok(dispatchQuarantine.sql.includes("status='in_flight'"));
  assert.ok(dispatchQuarantine.sql.includes("attempts>=max_attempts"));
  const update = batch.statements.find(stmt => stmt.sql.includes("UPDATE tasks SET status=?"));
  assert.equal(update.values[0], "failed");
  assert.equal(DB.state.dispatchStatus, "ambiguous");
  assert.equal(DB.state.auditCount, 1);
});

test("simultaneous general claims can acquire a pending D1 task only once", async () => {
  // Model D1's atomic UPDATE ... WHERE status='pending' ... RETURNING behavior.
  // Both HTTP requests race through the real Worker handler; the mock commits
  // the first conditional update synchronously before the second can observe it.
  const calls = [];
  const state = { status: "pending", attempts: 0, auditEvents: 0 };
  const DB = {
    calls,
    prepare(sql) {
      return {
        sql, values: [],
        bind(...values) { this.values = values; return this; },
        async all() {
          calls.push({ kind: "all", sql, values: this.values });
          return { results: [] };
        },
        async first() {
          calls.push({ kind: "first", sql, values: this.values });
          if (!sql.includes("UPDATE tasks SET status='running'")) return null;
          if (state.status !== "pending") return null;
          state.status = "running";
          state.attempts += 1;
          return {
            id: "contended-task", idempotency_key: "contended-key",
            task_type: "research_brief", title: "Contended task",
            payload_json: "{}", status: "running", attempts: state.attempts,
            max_attempts: 2, execution_mode: this.values[0],
            priority: 50, dependencies_json: "[]",
            lease_token: this.values[2], lease_expires_at: this.values[3],
          };
        },
        async run() {
          calls.push({ kind: "run", sql, values: this.values });
          if (sql.includes("INSERT INTO audit_log")) state.auditEvents += 1;
          return { meta: { changes: 1 } };
        },
      };
    },
  };
  const env = { DB, ENGINE_API_TOKEN: TOKEN };
  const [left, right] = await Promise.all([
    worker.fetch(request("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } }), env),
    worker.fetch(request("/v1/tasks/claim", { method: "POST", body: { execution_mode: "dry_run" } }), env),
  ]);
  assert.equal(left.status, 200);
  assert.equal(right.status, 200);
  const bodies = await Promise.all([left.json(), right.json()]);
  assert.equal(bodies.filter(body => body.task?.id === "contended-task").length, 1);
  assert.equal(bodies.filter(body => body.task === null).length, 1);
  assert.equal(state.status, "running");
  assert.equal(state.attempts, 1);
  assert.equal(state.auditEvents, 1);
  const claimStatements = calls.filter(call =>
    call.kind === "first" && call.sql.includes("UPDATE tasks SET status='running'"));
  assert.equal(claimStatements.length, 2);
  for (const call of claimStatements) {
    assert.match(call.sql, /WHERE id=\(SELECT candidate\.id FROM tasks candidate/);
    assert.match(call.sql, /AND status='pending'/);
    assert.match(call.sql, /RETURNING id/);
  }
});

test("claim endpoint is authenticated and returns an empty queue safely", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/claim", { method: "POST", body: {} }), {
    DB, ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { task: null });
});

test("claim preserves compatibility with an empty request body", async () => {
  const response = await worker.fetch(request("/v1/tasks/claim", { method: "POST" }), {
    DB: dbMock(), ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { task: null });
});

test("claim persists and returns a validated execution mode", async () => {
  const DB = dbMock({ normalClaim: {
    id: "task-1", status: "running", attempts: 1, execution_mode: "dry_run",
  } });
  const response = await worker.fetch(request("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.task.execution_mode, "dry_run");
  const claim = DB.calls.find(x => x.kind === "first" && x.sql.includes("UPDATE tasks SET status='running'"));
  assert.match(claim.sql, /execution_mode=\?/);
  assert.equal(claim.values[0], "dry_run");
});

test("claim uses priority ordering and same-mode dependency eligibility", async () => {
  const DB = dbMock({ normalClaim: { id: "ready", status: "running", priority: 90 } });
  const response = await worker.fetch(request("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "dry_run" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  const claim = DB.calls.find(call => call.kind === "first" && call.sql.includes("UPDATE tasks SET status='running'"));
  assert.match(claim.sql, /ORDER BY candidate\.priority DESC, candidate\.created_at ASC, candidate\.id ASC/);
  assert.match(claim.sql, /dependency\.execution_mode <> \?/);
  assert.ok(DB.calls.some(call => call.kind === "all" && call.sql.includes("WITH RECURSIVE blocked")));
});

test("claim rejects invalid execution modes before database writes", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/claim", {
    method: "POST", body: { execution_mode: "shell" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "invalid_execution_mode" });
  assert.equal(DB.calls.some(x => x.kind === "first" && x.sql.includes("UPDATE tasks SET status='running'")), false);
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

test("a dispatched step whose run and step ids contain underscores can heartbeat, finish, and retry", async () => {
  // Both run_id and step_id accept underscores. taskIdFor() emits
  // wf-<run_id>-<step_id>, so ordinary task action routes must accept both.
  // The separate disabled Codespace route grammar must remain unchanged.
  const taskId = "wf-run_2026-11-04-verify_ci";
  const actions = [
    ["heartbeat", { lease_token: "lease-1" }],
    ["finish", { lease_token: "lease-1", status: "succeeded", result: { mode: "dry_run" } }],
    ["retry", { lease_token: "lease-1", error: "retry test" }],
  ];
  for (const [action, body] of actions) {
    const DB = dbMock();
    const response = await worker.fetch(request(`/v1/tasks/${taskId}/${action}`, {
      method: "POST", body,
    }), { DB, ENGINE_API_TOKEN: TOKEN });
    assert.notEqual(response.status, 404, `${action} must route for ${taskId}`);
    assert.equal(response.status, 200, `${action} returned ${response.status}`);
    const update = DB.calls.find(x => x.kind === "run" && x.sql.startsWith("UPDATE tasks"));
    assert.ok(update.values.includes(taskId), `${action} must bind the routed task id`);
  }
});

test("a task id with underscores reaches the authenticated invocation lifecycle", async () => {
  const taskId = "wf-run_2026-11-04-verify_ci";
  const calls = [];
  const DB = {
    prepare(sql) {
      return {
        sql,
        values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          calls.push({ kind: "first", sql, values: this.values });
          if (sql.includes("SELECT id,status,lease_token,lease_expires_at FROM tasks")) {
            return {
              id: taskId, status: "running", lease_token: "lease-1",
              lease_expires_at: "2999-01-01T00:00:00.000Z",
            };
          }
          if (sql.includes("SELECT invocation_id,task_id,step_key,task_kind,request_id,status,last_route_id")) {
            return {
              invocation_id: "00000000-0000-4000-8000-000000000001",
              task_id: taskId,
              step_key: "model-step",
              task_kind: "research_brief",
              request_id: "00000000-0000-4000-8000-000000000002",
              status: "prepared",
              last_route_id: null,
            };
          }
          return null;
        },
        async run() {
          calls.push({ kind: "run", sql, values: this.values });
          return { meta: { changes: 1 } };
        },
      };
    },
  };
  const response = await worker.fetch(request(`/v1/tasks/${taskId}/invocations`, {
    method: "POST",
    body: { action: "prepare", lease_token: "lease-1", step_key: "model-step", task_kind: "research_brief" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 201);
  const payload = await response.json();
  assert.equal(payload.invocation.task_id, taskId);
  assert.ok(calls.some(call => call.values.includes(taskId)));
});

test("Codespace action grammar does not expand to underscore-bearing task ids", async () => {
  const response = await worker.fetch(request("/v1/codespace/tasks/task_code/heartbeat", {
    method: "POST", token: CODESPACE_TOKEN, body: { lease_token: "lease-1" },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
  assert.equal(response.status, 404);
});

test("a task id that is not an identifier is still not routable", async () => {
  for (const bad of ["task%20id", "task;drop", "../task"]) {
    const response = await worker.fetch(request(`/v1/tasks/${bad}/heartbeat`, {
      method: "POST", body: { lease_token: "lease-1" },
    }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
    assert.ok([400, 404].includes(response.status), `${bad} returned ${response.status}`);
  }
});

test("finish rejects unsupported terminal states", async () => {
  const response = await worker.fetch(request("/v1/tasks/task-1/finish", {
    method: "POST", body: { lease_token: "lease-1", status: "pending" },
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "invalid_status" });
});


test("finish derives execution mode from the persisted result", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/task-1/finish", {
    method: "POST",
    body: { lease_token: "lease-1", status: "succeeded", result: { mode: "dry_run" } },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  const update = DB.calls.find(x => x.kind === "run" && x.sql.startsWith("UPDATE tasks"));
  assert.match(update.sql, /execution_mode=\?/);
  assert.equal(update.values[3], "dry_run");
});

test("finish rejects invalid and not-started execution modes without mutating task", async () => {
  for (const execution_mode of ["shell", "not_started"]) {
    const DB = dbMock();
    const response = await worker.fetch(request("/v1/tasks/task-1/finish", {
      method: "POST",
      body: { lease_token: "lease-1", status: "succeeded", execution_mode },
    }), { DB, ENGINE_API_TOKEN: TOKEN });
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { error: "invalid_execution_mode" });
    assert.equal(DB.calls.some(x => x.kind === "run" && x.sql.startsWith("UPDATE tasks")), false);
  }
});

test("recover endpoint is authenticated and scopes stale selection and writes to the general lane", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/recover", { method: "POST", body: {} }), {
    DB, ENGINE_API_TOKEN: TOKEN,
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { requeued: 0, failed: 0 });
  const selection = DB.calls.find(x => x.kind === "all" && x.sql.includes("lease_expires_at"));
  assert.match(selection.sql, /execution_lane=\?/);
  assert.equal(selection.values.length, 2);
  assert.ok(Number.isFinite(Date.parse(selection.values[0])));
  assert.equal(selection.values[1], "general");
  const write = DB.calls.find(x => x.kind === "batch")?.statements?.find(x => x.sql.includes("UPDATE tasks SET status=?"));
  if (write) {
    assert.match(write.sql, /execution_lane=\?/);
    assert.deepEqual(write.values.slice(-2), ["general", "general"]);
  }
});

test("recovery counts only confirmed transitions and guards audit against lost races", async () => {
  const staleTasks = [
    {id: "lost-race", attempts: 1, max_attempts: 2, lease_token: "old"},
    {id: "winner", attempts: 2, max_attempts: 2, lease_token: "old2"},
  ];
  const DB = dbMock({staleTasks, batchResults: [
    {success: true, meta: {changes: 0}}, {success: true, meta: {changes: 0}},
    {success: true, meta: {changes: 1}}, {success: true, meta: {changes: 1}},
  ]});
  const response = await worker.fetch(request("/v1/tasks/recover", {method: "POST", body: {}}),
    {DB, ENGINE_API_TOKEN: TOKEN});
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {requeued: 0, failed: 1});
  const statements = DB.calls.find(call => call.kind === "batch").statements;
  assert.match(statements[1].sql, /changes\(\)=1/);
  assert.match(statements[3].sql, /changes\(\)=1/);
  const invalid = dbMock({staleTasks, batchResults: []});
  const failed = await worker.fetch(request("/v1/tasks/recover", {method: "POST", body: {}}),
    {DB: invalid, ENGINE_API_TOKEN: TOKEN});
  assert.equal(failed.status, 503);
  const inconsistent = dbMock({staleTasks: [staleTasks[0]], batchResults: [
    {success: true, meta: {changes: 1}}, {success: true, meta: {changes: 0}},
  ]});
  const mismatch = await worker.fetch(request("/v1/tasks/recover", {method: "POST", body: {}}),
    {DB: inconsistent, ENGINE_API_TOKEN: TOKEN});
  assert.equal(mismatch.status, 503);
});

test("specialist stale recovery requires and applies its own capability", async () => {
  const laneToken = "researcher-lane-token-0123456789abcdef0123456789";
  const deniedDB = dbMock();
  const denied = await worker.fetch(request("/v1/tasks/recover", {
    method: "POST", body: { worker_lane: "specialist:researcher" },
  }), { DB: deniedDB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(denied.status, 401);
  assert.equal(deniedDB.calls.length, 0);

  const DB = dbMock();
  const response = await worker.fetch(request("/v1/tasks/recover", {
    method: "POST", body: { worker_lane: "specialist:researcher" },
    extraHeaders: { "x-engine-lane-token": laneToken },
  }), { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_RESEARCHER_LANE_TOKEN: laneToken });
  assert.equal(response.status, 200);
  const selection = DB.calls.find(x => x.kind === "all" && x.sql.includes("lease_expires_at"));
  assert.equal(selection.values.length, 2);
  assert.equal(selection.values[1], "specialist:researcher");
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
    if (/\/actions\/workflows\/[^/]+\/runs\?/.test(target)) {
      return new Response(JSON.stringify({ workflow_runs: [] }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    }
    if (/\/actions\/workflows\/[^/]+\/dispatches$/.test(target)) {
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
  // One installation token, then a run-list read and a dispatch per watched
  // workflow: the fallback must cover every dropped schedule, not only the
  // task runner, and it must not re-authenticate once per workflow.
  const tokens = calls.filter(call => call.url.endsWith("/access_tokens"));
  const dispatches = calls.filter(call => call.url.endsWith("/dispatches"));
  const runLists = calls.filter(call => call.url.includes("/runs?"));
  assert.equal(tokens.length, 1);
  assert.equal(runLists.length, 4);
  assert.equal(dispatches.length, 4);
  assert.ok(dispatches.some(call => call.url.endsWith("/actions/workflows/task-runner.yml/dispatches")));
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

test("research ingestion is authenticated, batch-idempotent and stores normalized GitHub metadata", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates", { method: "POST", body: { batch_id: "test-batch", candidates: [{ source: "github", full_name: "org/agent", url: "https://github.com/org/agent", description: "agent", stars: 12, license_spdx: "MIT", topics: ["agents"] }] } }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 201);
  assert.deepEqual(await response.json(), { accepted_count: 1, sources: ["github"], batch_id: "test-batch", replayed: false });
  const batch = DB.calls.find(x => x.kind === "batch");
  assert.match(batch.statements[0].sql, /research_ingest_batches/);
  const event = batch.statements.find(x => x.sql.includes("research_ingest_events"));
  const candidate = batch.statements.find(x => x.sql.includes("INSERT INTO research_candidates"));
  assert.match(event.sql, /INSERT OR IGNORE INTO research_ingest_events/);
  assert.match(candidate.sql, /WHERE changes\(\)=1 ON CONFLICT\(source_key\) DO UPDATE/);
  assert.equal(candidate.values[0], "github:org/agent");
  assert.match(candidate.sql, /\x27unreviewed\x27/);
});

test("research ingestion replay does not increment candidate discovery count", async () => {
  const DB = dbMock({ researchBatchChanges: 0 });
  const response = await worker.fetch(request("/v1/research/candidates", {
    method: "POST",
    body: { batch_id: "same-run:1", candidates: [{
      source: "github", full_name: "org/repo", url: "https://github.com/org/repo",
    }] },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    accepted_count: 1, sources: ["github"], batch_id: "same-run:1", replayed: true,
  });
  const batch = DB.calls.find(x => x.kind === "batch");
  const candidate = batch.statements.find(x => x.sql.includes("INSERT INTO research_candidates"));
  assert.match(candidate.sql, /WHERE changes\(\)=1/);
});

test("research ingestion rejects a reused batch ID with a changed payload", async () => {
  const DB = dbMock({ researchBatchChanges: 0, researchBatch: { payload_sha256: "f".repeat(64), candidate_count: 1 } });
  const response = await worker.fetch(request("/v1/research/candidates", {
    method: "POST",
    body: { batch_id: "same-run:1", candidates: [{
      source: "github", full_name: "org/changed", url: "https://github.com/org/changed",
    }] },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { error: "batch_id_payload_conflict" });
});

test("research exact-key verification returns only requested candidate keys", async () => {
  const DB = dbMock();
  const keys = ["github:org/one", "arxiv:2601.01234"];
  const response = await worker.fetch(request("/v1/research/candidates/verify", {
    method: "POST", body: { source_keys: keys },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { matched_keys: keys });
  const query = DB.calls.find(x => x.kind === "all" &&
    x.sql.includes("SELECT source_key FROM research_candidates WHERE source_key IN"));
  assert.deepEqual(query.values, keys);
});

test("research ingestion normalizes Crossref DOI and rejects arbitrary URL targets", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates", { method: "POST", body: { batch_id: "test-batch", candidates: [{ source: "crossref", doi: "10.1234/agent-paper", title: "Agent research", url: "https://evil.example/article", authors: ["A Researcher"], license_urls: ["https://creativecommons.org/licenses/by/4.0/"] }] } }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 201);
  const batch = DB.calls.find(x => x.kind === "batch");
  const statement = batch.statements.find(x => x.sql.includes("INSERT INTO research_candidates"));
  assert.equal(statement.values[0], "crossref:10.1234/agent-paper");
  assert.equal(statement.values[3], "https://doi.org/10.1234/agent-paper");
});

test("research ingestion stores bounded arXiv metadata without abstracts or full text", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates", { method: "POST", body: { batch_id: "test-batch", candidates: [{
    source: "arxiv", arxiv_id: "2601.01234v2", title: "Agent safety research",
    url: "https://arxiv.org/abs/2601.01234v2", authors: ["A Researcher"],
    published: "2026-01-02T00:00:00Z", categories: ["cs.AI"],
    abstract_retrieved: false, full_text_retrieved: false,
  }] } }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 201);
  const statement = DB.calls.find(x => x.kind === "batch").statements.find(x => x.sql.includes("INSERT INTO research_candidates"));
  assert.equal(statement.values[0], "arxiv:2601.01234v2");
  assert.equal(statement.values[1], "arxiv");
  assert.equal(statement.values[3], "https://arxiv.org/abs/2601.01234v2");
  const metadata = JSON.parse(statement.values[4]);
  assert.equal(metadata.abstract_retrieved, false);
  assert.equal(metadata.full_text_retrieved, false);
});

test("research ingestion rejects noncanonical arXiv hosts and ports before writes", async () => {
  for (const url of [
    "https://evil.example/abs/2601.01234v2",
    "https://arxiv.org:444/abs/2601.01234v2",
  ]) {
    const DB = dbMock();
    const response = await worker.fetch(request("/v1/research/candidates", {
      method: "POST",
      body: { batch_id: "test-batch", candidates: [{
        source: "arxiv", arxiv_id: "2601.01234v2", title: "Agent research", url,
      }] },
    }), { DB, ENGINE_API_TOKEN: TOKEN });
    assert.equal(response.status, 400, url);
    assert.equal(DB.calls.length, 0, url);
  }
});

test("research ingestion stores SearXNG HTTPS metadata and rejects IP-literal URLs", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates", { method: "POST", body: { batch_id: "test-batch", candidates: [{
    source: "searxng", title: "Agent security", url: "https://docs.example.org/agent-security?ref=search",
    snippet: "Untrusted search snippet", engines: ["brave", "duckduckgo"],
  }] } }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 201);
  const statement = DB.calls.find(x => x.kind === "batch").statements.find(x => x.sql.includes("INSERT INTO research_candidates"));
  assert.equal(statement.values[0], "searxng:https://docs.example.org/agent-security?ref=search");
  assert.equal(statement.values[1], "searxng");
  const metadata = JSON.parse(statement.values[4]);
  assert.equal(metadata.content_fetched, false);
  assert.equal(metadata.license_verified, false);

  for (const url of ["https://127.0.0.1/admin", "https://metadata.localhost/admin", "https://[::1]/admin"]) {
    const invalidDB = dbMock();
    const invalid = await worker.fetch(request("/v1/research/candidates", { method: "POST", body: { batch_id: "test-batch", candidates: [{
      source: "searxng", title: "Private target", url,
    }] } }), { DB: invalidDB, ENGINE_API_TOKEN: TOKEN });
    assert.equal(invalid.status, 400, url);
    assert.equal(invalidDB.calls.length, 0, url);
  }
});

test("research ingestion rejects invalid repository URL before database writes", async () => {
  const DB = dbMock();
  const response = await worker.fetch(request("/v1/research/candidates", { method: "POST", body: { batch_id: "test-batch", candidates: [{ source: "github", full_name: "org/agent", url: "https://evil.example/org/agent" }] } }), { DB, ENGINE_API_TOKEN: TOKEN });
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
  const response = await worker.fetch(request("/v1/research/candidates", { method: "POST", token: null, body: { batch_id: "test-batch", candidates: [{ source: "github", full_name: "org/repo", url: "https://github.com/org/repo" }] } }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
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

test("Codespace executor remains hard-disabled until sandbox issue #440 is satisfied", async () => {
  const DB = dbMock();
  for (const [path, method, body] of [
    ["/v1/codespace/status", "GET", undefined],
    ["/v1/codespace/tasks/claim", "POST", {}],
    ["/v1/codespace/tasks/task-code/heartbeat", "POST", { lease_token: "lease-1" }],
    ["/v1/codespace/tasks/task-code/finish", "POST", { lease_token: "lease-1", status: "succeeded" }],
  ]) {
    const response = await worker.fetch(request(path, {
      method, token: CODESPACE_TOKEN, body,
    }), { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
    assert.equal(response.status, 503, path);
    assert.deepEqual(await response.json(), { error: "codespace_executor_disabled" });
  }
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

test("Codespace task types cannot be enqueued before sandbox release", async () => {
  const DB = dbMock();
  for (const taskType of ["codespace_inspect", "codespace_test"]) {
    const response = await worker.fetch(request("/v1/tasks/enqueue", {
      method: "POST",
      token: TOKEN,
      body: {
        task_type: taskType,
        title: "Codespaces task",
        idempotency_key: "codespace-disabled-" + taskType,
        payload: { operation: "inspect_repo" },
      },
    }), { DB, ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
    assert.equal(response.status, 503, taskType);
    assert.deepEqual(await response.json(), { error: "codespace_executor_disabled" });
  }
  assert.equal(DB.calls.length, 0);
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

test("legacy Task Runner cannot mutate Codespace task types", async () => {
  const codeTask = {
    id: "task-code", status: "running", attempts: 1, max_attempts: 2,
    task_type: "codespace_test", lease_token: "lease-1",
    lease_expires_at: "2999-01-01T00:00:00.000Z",
  };
  const response = await worker.fetch(request("/v1/tasks/task-code/heartbeat", {
    method: "POST", token: TOKEN, body: { lease_token: "lease-1" },
  }), { DB: dbMock({ currentTask: codeTask }), ENGINE_API_TOKEN: TOKEN,
    ENGINE_CODESPACE_TOKEN: CODESPACE_TOKEN });
  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), { error: "executor_mismatch" });
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

test("Codespace release gate blocks even a weak configured token", async () => {
  const response = await worker.fetch(request("/v1/codespace/status", { token: "short-token" }), {
    DB: dbMock(), ENGINE_API_TOKEN: TOKEN, ENGINE_CODESPACE_TOKEN: "short-token",
  });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "codespace_executor_disabled" });
});

test("a storage failure on claim tells the caller why D1 rejected the write", async () => {
  // The 2026-11-04 outage surfaced as a bare "HTTP 503": the runner could not
  // record a cause anywhere, so every later investigation started from zero.
  const failure = new Error("D1_ERROR: too many SQL variables: SQLITE_ERROR\n  at claim");
  const DB = {
    prepare() { throw failure; },
    async batch() { throw failure; },
  };
  const response = await worker.fetch(request("/v1/tasks/claim", {
    method: "POST", token: TOKEN, body: { execution_mode: "dry_run" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  const body = await response.json();

  assert.equal(response.status, 503);
  assert.equal(body.error, "storage_unavailable");
  assert.ok(body.detail.includes("D1_ERROR"), body.detail);
  assert.ok(!body.detail.includes("\n"), "detail must be one line");
  assert.ok(body.detail.length <= 240, "detail must stay bounded");
  assert.ok(!body.detail.toLowerCase().includes("bearer"), "no credentials in detail");
});

test("an enqueue storage failure also reports its reason", async () => {
  const failure = new Error("D1_ERROR: database is locked");
  const DB = {
    prepare() { throw failure; },
    async batch() { throw failure; },
  };
  const response = await worker.fetch(request("/v1/tasks/enqueue", {
    method: "POST", token: TOKEN,
    body: { task_type: "system_audit", title: "Storage probe", idempotency_key: "storage-probe-1" },
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  const body = await response.json();
  assert.equal(response.status, 503);
  assert.ok(body.detail.includes("database is locked"), body.detail);
});

test("workflow dispatch requires the bearer token", async () => {
  // The route enqueues tasks, so it must sit behind the same guard as
  // /v1/tasks/enqueue — an unauthenticated dispatch would be a free queue write.
  const response = await worker.fetch(new Request("https://engine.example/v1/workflows/dispatch", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ run_id: "r", workflow_id: "w", plan_digest: "a".repeat(64), steps: [] }),
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 401);
});

test("schema diagnostics report every table the Worker depends on", async () => {
  const response = await worker.fetch(request("/v1/diagnostics/schema", {
    method: "GET", token: TOKEN,
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.ok, true);
  assert.deepEqual(body.missing, []);
  assert.ok(body.checked.includes("task_dependencies"), "task_dependencies must be probed");
  assert.ok(body.checked.includes("workflow_runs"), "workflow_runs must be probed");
  assert.ok(body.checked.includes("task_process_dispatches"), "process dispatch ledger must be probed");
  assert.ok(body.checked.includes("tasks.execution_lane"), "execution lane migration must be probed");
  assert.equal(body.checked.length, 9);
});

test("schema diagnostics name the table a deploy forgot to migrate", async () => {
  const DB = {
    prepare(sql) {
      if (sql.includes("FROM task_dependencies")) {
        throw new Error("D1_ERROR: no such table: task_dependencies: SQLITE_ERROR");
      }
      return { async all() { return { results: [] }; } };
    },
    async batch() { return []; },
  };
  const response = await worker.fetch(request("/v1/diagnostics/schema", {
    method: "GET", token: TOKEN,
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  const body = await response.json();
  assert.equal(response.status, 503);
  assert.equal(body.ok, false);
  assert.deepEqual(body.missing, ["task_dependencies"]);
  assert.ok(body.reasons.task_dependencies.includes("no such table"));
});

test("schema diagnostics reject a non-GET method and an unauthenticated caller", async () => {
  const wrongMethod = await worker.fetch(request("/v1/diagnostics/schema", {
    method: "POST", token: TOKEN, body: {},
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(wrongMethod.status, 405);

  const unauthenticated = await worker.fetch(request("/v1/diagnostics/schema", {
    method: "GET", token: "wrong-token",
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(unauthenticated.status, 401);
});

test("GET /v1/tasks supports optional status and task_id filters and rejects invalid filter values", async () => {
  const db = dbMock();
  const filtered = await worker.fetch(request("/v1/tasks?limit=25&status=pending&task_id=task-abc_123", {
    method: "GET", token: TOKEN,
  }), { DB: db, ENGINE_API_TOKEN: TOKEN });
  assert.equal(filtered.status, 200);
  const query = db.calls.find(c => c.kind === "all" && c.sql.includes("FROM tasks WHERE"));
  assert.ok(query, "expected filtered tasks SELECT");
  assert.ok(query.sql.includes("status=?"));
  assert.ok(query.sql.includes("id=?"));
  assert.deepEqual(query.values, ["general", "pending", "task-abc_123", 25]);

  const badStatus = await worker.fetch(request("/v1/tasks?status=invalid", {
    method: "GET", token: TOKEN,
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(badStatus.status, 400);
  assert.deepEqual(await badStatus.json(), { error: "invalid_status" });

  const badTaskId = await worker.fetch(request("/v1/tasks?task_id=bad%20id!", {
    method: "GET", token: TOKEN,
  }), { DB: dbMock(), ENGINE_API_TOKEN: TOKEN });
  assert.equal(badTaskId.status, 400);
  assert.deepEqual(await badTaskId.json(), { error: "invalid_task_id" });
});

