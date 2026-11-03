import test from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";

const loadModule = () => import("../worker/task-runner-watchdog.js").catch(() => null);
const keyPair = generateKeyPairSync("rsa", {
  modulusLength: 2048,
  publicKeyEncoding: { type: "spki", format: "pem" },
  privateKeyEncoding: { type: "pkcs1", format: "pem" },
});
const privateKeyPem = keyPair.privateKey;
const NOW = Date.parse("2026-11-03T01:15:00.000Z");

function envWith(db = dbMock()) {
  return {
    DB: db,
    GITHUB_APP_ID: "12345",
    GITHUB_APP_INSTALLATION_ID: "67890",
    GITHUB_APP_PRIVATE_KEY: privateKeyPem,
  };
}

function dbMock() {
  const calls = [];
  return {
    calls,
    prepare(sql) {
      return {
        sql,
        values: [],
        bind(...values) { this.values = values; return this; },
        async run() {
          calls.push({ sql, values: this.values });
          return { success: true, meta: { changes: 1 } };
        },
      };
    },
  };
}

function makeFetch({ runs = [], dispatchStatus = 204, dispatchBody = "", runListStatus = 200 } = {}) {
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    const target = String(url);
    calls.push({ url: target, options });
    if (target === "https://api.github.com/app/installations/67890/access_tokens") {
      return new Response(JSON.stringify({ token: "ghs_short_lived_test_token" }), {
        status: 201,
        headers: { "content-type": "application/json" },
      });
    }
    if (target.startsWith("https://api.github.com/repos/ghith93/raawaa-autonomous-engine/actions/workflows/task-runner.yml/runs?")) {
      if (runListStatus !== 200) return new Response("PRIVATE_RESPONSE_BODY", { status: runListStatus });
      return new Response(JSON.stringify({ workflow_runs: runs }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (target === "https://api.github.com/repos/ghith93/raawaa-autonomous-engine/actions/workflows/task-runner.yml/dispatches") {
      return new Response(dispatchBody, { status: dispatchStatus });
    }
    throw new Error("unexpected_url");
  };
  return { fetchImpl, calls };
}

test("missing GitHub App settings disable the watchdog without network or D1 writes", async () => {
  const module = await loadModule();
  assert.ok(module, "Task Runner watchdog module must exist");
  const DB = dbMock();
  let calls = 0;
  const result = await module.dispatchTaskRunnerIfStale({
    env: { DB },
    fetchImpl: async () => { calls += 1; throw new Error("network must not run"); },
    nowMs: NOW,
  });
  assert.deepEqual(result, { status: "disabled", reason: "missing_configuration" });
  assert.equal(calls, 0);
  assert.equal(DB.calls.length, 0);
});

test("an active Task Runner run suppresses dispatch", async () => {
  const module = await loadModule();
  assert.ok(module, "Task Runner watchdog module must exist");
  const { fetchImpl, calls } = makeFetch({
    runs: [{ event: "workflow_dispatch", status: "in_progress", created_at: new Date(NOW - 60_000).toISOString() }],
  });
  const result = await module.dispatchTaskRunnerIfStale({
    env: envWith(), fetchImpl, nowMs: NOW,
  });
  assert.deepEqual(result, { status: "skipped", reason: "active_run" });
  assert.equal(calls.length, 2);
  assert.equal(calls.some(call => call.url.endsWith("/dispatches")), false);
});

test("a recent schedule run suppresses watchdog dispatch", async () => {
  const module = await loadModule();
  assert.ok(module, "Task Runner watchdog module must exist");
  const { fetchImpl, calls } = makeFetch({
    runs: [{ event: "schedule", status: "completed", created_at: new Date(NOW - 9 * 60_000).toISOString() }],
  });
  const result = await module.dispatchTaskRunnerIfStale({
    env: envWith(), fetchImpl, nowMs: NOW,
  });
  assert.deepEqual(result, { status: "skipped", reason: "recent_schedule" });
  assert.equal(calls.length, 2);
});

test("an absent or stale schedule dispatches only the fixed Task Runner workflow", async () => {
  const module = await loadModule();
  assert.ok(module, "Task Runner watchdog module must exist");
  const DB = dbMock();
  const { fetchImpl, calls } = makeFetch({
    runs: [{ event: "schedule", status: "completed", created_at: new Date(NOW - 30 * 60_000).toISOString() }],
    dispatchStatus: 204,
  });
  const result = await module.dispatchTaskRunnerIfStale({
    env: envWith(DB), fetchImpl, nowMs: NOW, scheduledTime: NOW,
  });
  assert.deepEqual(result, { status: "dispatched", httpStatus: 204 });
  assert.equal(calls.length, 3);
  assert.equal(calls[2].url, "https://api.github.com/repos/ghith93/raawaa-autonomous-engine/actions/workflows/task-runner.yml/dispatches");
  assert.equal(calls[2].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[2].options.body), { ref: "main" });
  assert.equal(DB.calls.length, 1);
  assert.equal(DB.calls[0].values[0], "task_runner_dispatch_requested");
  const audit = JSON.parse(DB.calls[0].values[1]);
  assert.deepEqual(audit, {
    workflow: "task-runner.yml",
    ref: "main",
    http_status: 204,
    scheduled_time: new Date(NOW).toISOString(),
  });
  assert.equal(JSON.stringify(audit).includes("ghs_short_lived_test_token"), false);
});

test("no previous schedule also triggers the fixed workflow dispatch", async () => {
  const module = await loadModule();
  assert.ok(module, "Task Runner watchdog module must exist");
  const { fetchImpl, calls } = makeFetch({ runs: [], dispatchStatus: 200 });
  const result = await module.dispatchTaskRunnerIfStale({
    env: envWith(), fetchImpl, nowMs: NOW,
  });
  assert.deepEqual(result, { status: "dispatched", httpStatus: 200 });
  assert.equal(calls.length, 3);
});

test("GitHub API failures are sanitized and never persist response bodies", async () => {
  const module = await loadModule();
  assert.ok(module, "Task Runner watchdog module must exist");
  const { fetchImpl } = makeFetch({
    runs: [],
    dispatchStatus: 403,
    dispatchBody: "PRIVATE_RESPONSE_BODY",
  });
  await assert.rejects(
    () => module.dispatchTaskRunnerIfStale({ env: envWith(), fetchImpl, nowMs: NOW }),
    error => {
      assert.match(error.message, /task_runner_dispatch_failed:403/);
      assert.equal(error.message.includes("PRIVATE_RESPONSE_BODY"), false);
      return true;
    },
  );
});

test("workflow-run listing errors are sanitized", async () => {
  const module = await loadModule();
  assert.ok(module, "Task Runner watchdog module must exist");
  const { fetchImpl } = makeFetch({ runListStatus: 403 });
  await assert.rejects(
    () => module.dispatchTaskRunnerIfStale({ env: envWith(), fetchImpl, nowMs: NOW }),
    /task_runner_run_list_failed:403/,
  );
});
