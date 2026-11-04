import test from "node:test";
import assert from "node:assert/strict";

import {
  findCycle, handleWorkflowDispatch, idempotencyKeyFor, taskIdFor, validatePlan,
} from "../worker/workflow-dispatch.js";

const DIGEST = "a".repeat(64);
const url = new URL("https://engine.example/v1/workflows/dispatch");

function post(body) {
  return new Request("https://engine.example/v1/workflows/dispatch", {
    method: "POST",
    headers: { "content-type": "application/json", authorization: "Bearer t" },
    body: JSON.stringify(body),
  });
}

function plan(overrides = {}) {
  return {
    run_id: "run-1",
    workflow_id: "demo-workflow",
    plan_digest: DIGEST,
    plan_version: 1,
    steps: [
      { step_id: "a", task_type: "system_audit", title: "first", payload: { q: 1 } },
      { step_id: "b", task_type: "system_audit", title: "second", payload: {}, depends_on: ["a"] },
    ],
    ...overrides,
  };
}

// A fake D1 that records what a batch would have done and can be told to fail.
function fakeDb({ existingRun = null, failBatch = false, missingTable = false, raceWith = null } = {}) {
  const state = { batched: [], batches: 0, run: existingRun };
  return {
    state,
    prepare(sql) {
      return {
        sql,
        values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          if (missingTable) throw new Error("D1_ERROR: no such table: workflow_runs: SQLITE_ERROR");
          return state.run;
        },
      };
    },
    async batch(statements) {
      state.batches += 1;
      if (failBatch) throw new Error("D1_ERROR: batch aborted");
      if (missingTable) throw new Error("D1_ERROR: no such table: workflow_runs: SQLITE_ERROR");
      if (raceWith) {
        // Another dispatch committed between our read and our batch.
        state.run = raceWith;
        throw new Error("D1_ERROR: UNIQUE constraint failed: workflow_runs.run_id: SQLITE_CONSTRAINT");
      }
      state.batched.push(...statements);
      return [];
    },
  };
}

test("a valid plan is dispatched in exactly one batch", async () => {
  const db = fakeDb();
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 201);
  const body = await response.json();
  assert.equal(body.created, true);
  assert.equal(body.run.status, "dispatched");
  assert.equal(body.run.workflow_id, "demo-workflow");
  assert.deepEqual(body.run.entry_step_ids, ["a"]);
  assert.equal(body.steps.length, 2);
  assert.equal(db.state.batches, 1, "the whole dispatch must be a single transaction");
});

test("run row, tasks, audit rows and edges all travel in that one batch", async () => {
  const db = fakeDb();
  await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  const sql = db.state.batched.map(statement => statement.sql);
  assert.equal(sql.filter(s => s.includes("INSERT INTO workflow_runs")).length, 1);
  assert.equal(sql.filter(s => s.includes("INSERT OR IGNORE INTO tasks")).length, 2);
  assert.equal(sql.filter(s => s.includes("workflow_step_dispatched")).length, 2);
  assert.equal(sql.filter(s => s.includes("INSERT OR IGNORE INTO task_dependencies")).length, 1);
});

test("a failing batch enqueues nothing and reports dispatch_failed", async () => {
  // The guarantee the design exists for: a half-enqueued plan looks like
  // progress and is worse than no dispatch at all.
  const db = fakeDb({ failBatch: true });
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error, "dispatch_failed");
  assert.equal(db.state.batched.length, 0);
});

test("replaying the same run is a no-op, not a duplicate", async () => {
  const db = fakeDb({
    existingRun: {
      run_id: "run-1", workflow_id: "demo-workflow", plan_digest: DIGEST,
      status: "dispatched", step_count: 2, entry_step_ids: '["a"]',
      created_at: "2026-11-04T00:00:00.000Z",
    },
  });
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.created, false);
  assert.deepEqual(body.run.entry_step_ids, ["a"]);
  assert.equal(db.state.batches, 0, "a replay must not write");
});

test("reusing a run id with a different plan is refused, not silently dispatched", async () => {
  const db = fakeDb({
    existingRun: {
      run_id: "run-1", workflow_id: "demo-workflow", plan_digest: "b".repeat(64),
      status: "dispatched", step_count: 2, entry_step_ids: '["a"]',
      created_at: "2026-11-04T00:00:00.000Z",
    },
  });
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, "run_digest_conflict");
  assert.equal(db.state.batches, 0);
});

test("losing a dispatch race reports the winner instead of failing", async () => {
  // Two requests, same run, same digest. The database decides; the loser must
  // not answer 503, because that invites a retry of something already done.
  const db = fakeDb({
    raceWith: {
      run_id: "run-1", workflow_id: "demo-workflow", plan_digest: DIGEST, plan_version: 1,
      status: "dispatched", step_count: 2, entry_step_ids: '["a"]', created_at: "2026-11-04T00:00:00.000Z",
    },
  });
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.created, false);
  assert.ok(Array.isArray(body.run.entry_step_ids));
});

test("losing a race to a different plan is still a conflict", async () => {
  const db = fakeDb({
    raceWith: {
      run_id: "run-1", workflow_id: "demo-workflow", plan_digest: "b".repeat(64), plan_version: 1,
      status: "dispatched", step_count: 2, entry_step_ids: '["a"]', created_at: "2026-11-04T00:00:00.000Z",
    },
  });
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, "run_digest_conflict");
});

test("a deploy without migration 0012 says so instead of failing generically", async () => {
  // 503 would send an operator hunting a storage fault; 200 would be a lie.
  const db = fakeDb({ missingTable: true });
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 404);
  const body = await response.json();
  assert.equal(body.error, "plan_dispatch_unavailable");
  assert.match(body.detail, /0012/);
});

test("a dependency outside the plan is rejected with the offending edge named", async () => {
  const result = await validatePlan(plan({
    steps: [{ step_id: "a", task_type: "t", title: "x", depends_on: ["ghost"] }],
  }));
  assert.equal(result.error, "unknown_step_dependency");
  assert.equal(result.detail, "a->ghost");
});

test("a cycle is rejected and the cyclic steps are named", async () => {
  const result = await validatePlan(plan({
    steps: [
      { step_id: "a", task_type: "t", title: "x", depends_on: ["b"] },
      { step_id: "b", task_type: "t", title: "y", depends_on: ["a"] },
    ],
  }));
  assert.equal(result.error, "cyclic_dependency");
  assert.equal(result.detail, "a,b");
});

test("self-dependency, duplicate steps and duplicate edges are rejected", async () => {
  const selfDep = await validatePlan(plan({
    steps: [{ step_id: "a", task_type: "t", title: "x", depends_on: ["a"] }],
  }));
  assert.equal(selfDep.error, "self_dependency");

  const duplicateStep = await validatePlan(plan({
    steps: [
      { step_id: "a", task_type: "t", title: "x" },
      { step_id: "a", task_type: "t", title: "y" },
    ],
  }));
  assert.equal(duplicateStep.error, "duplicate_step_id");

  const duplicateEdge = await validatePlan(plan({
    steps: [
      { step_id: "a", task_type: "t", title: "x" },
      { step_id: "b", task_type: "t", title: "y", depends_on: ["a", "a"] },
    ],
  }));
  assert.equal(duplicateEdge.error, "duplicate_step_dependency");
});

test("run id, workflow id, digest and step count are validated", async () => {
  assert.equal((await validatePlan(plan({ run_id: "" }))).error, "invalid_run_id");
  assert.equal((await validatePlan(plan({ run_id: "9bad" }))).error, "invalid_run_id");
  assert.equal((await validatePlan(plan({ workflow_id: "" }))).error, "invalid_workflow_id");
  assert.equal((await validatePlan(plan({ plan_digest: "nothex" }))).error, "invalid_plan_digest");
  assert.equal((await validatePlan(plan({ steps: [] }))).error, "invalid_steps");
  assert.equal((await validatePlan(plan({ steps: Array.from({ length: 21 }, (_, index) => ({
    step_id: `s${index}`, task_type: "t", title: "x",
  })) }))).error, "invalid_steps");
});

test("codespace task types stay refused while the executor release is false", async () => {
  const body = plan({ steps: [{ step_id: "a", task_type: "codespace_test", title: "x" }] });
  const types = new Set(["codespace_test"]);
  assert.equal((await validatePlan(body, { codespaceTaskTypes: types })).error, "codespace_executor_disabled");
  assert.ok((await validatePlan(body, { codespaceTaskTypes: types, executorEnabled: true })).plan);
});

test("the idempotency key matches the vector pinned by the Python side", async () => {
  // Same inputs, same key, in both languages. If either derivation drifts, one
  // of the two tests fails — which is the only reason a duplicated algorithm is
  // tolerable at all.
  assert.equal(
    await idempotencyKeyFor("demo-workflow", "run-9", 1, "prepare"),
    "workflow-step:730e3fe09f1dbace1419a3f7cbd2e3008fc7b1c8541a7b95c3e6036c0e2c76af",
  );
});

test("task identities are deterministic, so a replay names the same tasks", () => {
  assert.equal(taskIdFor("run-1", "a"), "wf-run-1-a");
  assert.equal(taskIdFor("run-1", "a"), taskIdFor("run-1", "a"));
  assert.notEqual(taskIdFor("run-1", "a"), taskIdFor("run-2", "a"));
});

test("findCycle is acyclic-aware", () => {
  assert.equal(findCycle([
    { step_id: "a", depends_on: [] },
    { step_id: "b", depends_on: ["a"] },
  ]), null);
  assert.deepEqual(findCycle([
    { step_id: "a", depends_on: ["b"] },
    { step_id: "b", depends_on: ["a"] },
  ]), ["a", "b"]);
});

test("a non-dispatch path is left to the main router", async () => {
  const other = new URL("https://engine.example/v1/tasks");
  assert.equal(await handleWorkflowDispatch(post(plan()), { DB: fakeDb() }, other), null);
});
