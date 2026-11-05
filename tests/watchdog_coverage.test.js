import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { generateKeyPairSync } from "node:crypto";

const loadModule = () => import("../worker/task-runner-watchdog.js").catch(() => null);

const keyPair = generateKeyPairSync("rsa", {
  modulusLength: 2048,
  publicKeyEncoding: { type: "spki", format: "pem" },
  privateKeyEncoding: { type: "pkcs1", format: "pem" },
});
const privateKeyPem = keyPair.privateKey;
const NOW = Date.parse("2026-11-04T06:00:00.000Z");
const STALE = new Date(NOW - 400 * 24 * 60 * 60 * 1000).toISOString();

function envWith(db) {
  return {
    DB: db,
    GITHUB_APP_WATCHDOG_ENABLED: "enabled",
    GITHUB_APP_ID: "12345",
    GITHUB_APP_INSTALLATION_ID: "67890",
    GITHUB_APP_PRIVATE_KEY: privateKeyPem,
  };
}

function dbMock({ recentIntents = [], failRequested = null } = {}) {
  const intents = new Set(recentIntents);
  const events = [];
  return {
    events,
    intents,
    prepare(sql) {
      return {
        sql,
        values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          return intents.has(this.values[0]) ? { recent: 1 } : null;
        },
        async run() {
          const event = this.values[0];
          events.push(event);
          if (this.values.length === 5) {
            if (intents.has(event)) return { success: true, meta: { changes: 0 } };
            intents.add(event);
            return { success: true, meta: { changes: 1 } };
          }
          if (failRequested && event === failRequested) throw new Error("PRIVATE_DATABASE_DETAIL");
          return { success: true, meta: { changes: 1 } };
        },
      };
    },
  };
}

/**
 * Serves the three endpoints the watchdog touches, keyed per workflow, so a
 * single test can make one workflow unreachable and the rest healthy.
 */
function makeFetch(plan = {}) {
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
    const runs = target.match(/\/actions\/workflows\/([^/]+)\/runs\?/);
    if (runs) {
      const spec = plan[runs[1]] ?? {};
      if (spec.runListStatus && spec.runListStatus !== 200) {
        return new Response("PRIVATE_RESPONSE_BODY", { status: spec.runListStatus });
      }
      return new Response(JSON.stringify({ workflow_runs: spec.runs ?? [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    const dispatch = target.match(/\/actions\/workflows\/([^/]+)\/dispatches$/);
    if (dispatch) {
      const spec = plan[dispatch[1]] ?? {};
      return new Response(null, { status: spec.dispatchStatus ?? 204 });
    }
    throw new Error("unexpected_url:" + target);
  };
  return { fetchImpl, calls };
}

test("coverage names every watched workflow with a cadence-derived threshold", async () => {
  const module = await loadModule();
  const coverage = module.getWatchdogCoverage();
  const byWorkflow = Object.fromEntries(coverage.map(item => [item.workflow, item.stale_after_minutes]));
  assert.equal(byWorkflow["task-runner.yml"], 10);
  assert.equal(byWorkflow["maintenance.yml"], 6 * 60 + 30);
  assert.equal(byWorkflow["research-discovery.yml"], 25 * 60);
  assert.equal(byWorkflow["groq-connection-check.yml"], 7 * 24 * 60 + 12 * 60);
  assert.equal(Object.keys(byWorkflow).length, 4);
});

test("intent events are unique, so one workflow cannot suppress another", async () => {
  const module = await loadModule();
  const events = module.WATCHED_WORKFLOWS.map(target => target.intentEvent);
  assert.equal(new Set(events).size, events.length);
  assert.ok(events.every(event => event.endsWith("_dispatch_intent")));
  const requested = module.WATCHED_WORKFLOWS.map(target => target.requestedEvent);
  assert.equal(new Set(requested).size, requested.length);
});

test("every watched workflow exists, is scheduled, and can be dispatched", async () => {
  const module = await loadModule();
  for (const target of module.WATCHED_WORKFLOWS) {
    const text = readFileSync(new URL(`../.github/workflows/${target.workflow}`, import.meta.url), "utf8");
    const head = text.split("\njobs:", 1)[0];
    assert.match(head, /cron:/, `${target.workflow} must still be scheduled`);
    assert.match(head, /workflow_dispatch:/, `${target.workflow} must accept a dispatch`);
  }
});

test("a stale schedule is recovered for every watched workflow", async () => {
  const module = await loadModule();
  const DB = dbMock();
  const { fetchImpl, calls } = makeFetch();
  const result = await module.dispatchDueWorkflows({ env: envWith(DB), fetchImpl, nowMs: NOW });

  assert.equal(result.status, "dispatched");
  assert.equal(result.dispatched, 4);
  assert.equal(result.failed, 0);
  const dispatched = calls.filter(call => call.url.endsWith("/dispatches")).map(call => call.url);
  for (const target of module.WATCHED_WORKFLOWS) {
    assert.ok(dispatched.some(url => url.endsWith(`/${target.workflow}/dispatches`)), target.workflow);
  }
  for (const target of module.WATCHED_WORKFLOWS) {
    assert.ok(DB.events.includes(target.requestedEvent), target.requestedEvent);
  }
});

test("one installation token serves the whole tick", async () => {
  const module = await loadModule();
  const { fetchImpl, calls } = makeFetch();
  await module.dispatchDueWorkflows({ env: envWith(dbMock()), fetchImpl, nowMs: NOW });
  const tokenCalls = calls.filter(call => call.url.endsWith("/access_tokens"));
  assert.equal(tokenCalls.length, 1);
});

test("a workflow with a recent run is left alone, not dispatched twice", async () => {
  const module = await loadModule();
  const { fetchImpl } = makeFetch({
    "maintenance.yml": { runs: [{ status: "completed", created_at: new Date(NOW - 60 * 1000).toISOString() }] },
  });
  const result = await module.dispatchDueWorkflows({ env: envWith(dbMock()), fetchImpl, nowMs: NOW });
  const maintenance = result.results.find(item => item.workflow === "maintenance.yml");
  assert.equal(maintenance.status, "skipped");
  assert.equal(maintenance.reason, "recent_run");
  assert.equal(result.dispatched, 5);
});

test("an active run suppresses dispatch even when older than the threshold", async () => {
  const module = await loadModule();
  const { fetchImpl } = makeFetch({
    "task-runner.yml": { runs: [{ status: "in_progress", created_at: STALE }] },
  });
  const result = await module.dispatchDueWorkflows({ env: envWith(dbMock()), fetchImpl, nowMs: NOW });
  const taskRunner = result.results.find(item => item.workflow === "task-runner.yml");
  assert.equal(taskRunner.status, "skipped");
  assert.equal(taskRunner.reason, "active_run");
});

test("a broken run-list read does not stop the other workflows", async () => {
  const module = await loadModule();
  const DB = dbMock();
  const { fetchImpl } = makeFetch({ "groq-connection-check.yml": { runListStatus: 500 } });
  const result = await module.dispatchDueWorkflows({ env: envWith(DB), fetchImpl, nowMs: NOW });

  const groq = result.results.find(item => item.workflow === "groq-connection-check.yml");
  assert.equal(groq.status, "failed");
  assert.ok(!groq.error.includes("PRIVATE"), "provider detail must not leak into the result");
  assert.equal(result.dispatched, 5);
  assert.equal(result.failed, 1);
  assert.equal(result.status, "dispatched");
  assert.ok(DB.events.includes("groq_check_dispatch_failed"), "failure must be recorded, not silent");
});

test("a rejected dispatch is reported per workflow and recorded", async () => {
  const module = await loadModule();
  const DB = dbMock();
  const { fetchImpl } = makeFetch({ "maintenance.yml": { dispatchStatus: 422 } });
  const result = await module.dispatchDueWorkflows({ env: envWith(DB), fetchImpl, nowMs: NOW });
  const maintenance = result.results.find(item => item.workflow === "maintenance.yml");
  assert.equal(maintenance.status, "failed");
  assert.match(maintenance.error, /maintenance_dispatch_failed:422/);
  assert.equal(DB.intents.has("maintenance_dispatch_intent"), true);
});

test("an existing intent stops a second dispatch of the same workflow", async () => {
  const module = await loadModule();
  const DB = dbMock({ recentIntents: ["maintenance_dispatch_intent"] });
  const { fetchImpl } = makeFetch();
  const result = await module.dispatchDueWorkflows({ env: envWith(DB), fetchImpl, nowMs: NOW });
  const maintenance = result.results.find(item => item.workflow === "maintenance.yml");
  assert.equal(maintenance.reason, "recent_dispatch_intent");
  assert.equal(result.dispatched, 5);
});

test("without the opt-in switch nothing is dispatched and no network is touched", async () => {
  const module = await loadModule();
  const env = envWith(dbMock());
  delete env.GITHUB_APP_WATCHDOG_ENABLED;
  let calls = 0;
  const result = await module.dispatchDueWorkflows({
    env,
    fetchImpl: async () => { calls += 1; throw new Error("network must not run"); },
    nowMs: NOW,
  });
  assert.equal(result.status, "disabled");
  assert.deepEqual(result.missing, ["GITHUB_APP_WATCHDOG_ENABLED"]);
  assert.equal(calls, 0);
});
