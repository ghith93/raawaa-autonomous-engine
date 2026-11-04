import test from "node:test";
import assert from "node:assert/strict";

import {
  findCycle, handleWorkflowDispatch, handleWorkflowRunLookup, idempotencyKeyFor,
  stepsDigestFor, taskIdFor, validatePlan,
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
      { step_id: "b", task_type: "code_review", title: "second", payload: {}, depends_on: ["a"] },
    ],
    ...overrides,
  };
}

async function normalizedDigest(body = plan()) {
  const validated = await validatePlan(body);
  assert.ok(validated.plan, validated.error);
  return stepsDigestFor(validated.plan.steps);
}

function runRow(overrides = {}) {
  return {
    run_id: "run-1", workflow_id: "demo-workflow", plan_digest: DIGEST,
    plan_version: 1, status: "dispatched", step_count: 2, entry_step_ids: '["a"]',
    step_ids: '["a","b"]',
    created_at: "2026-11-04T00:00:00.000Z", ...overrides,
  };
}

// A fake D1 that records what a batch would have done, and can fail at a chosen
// statement position or simulate a lost commit.
function fakeDb({ run = null, failAt = null, failWith = null, commitThenFail = false } = {}) {
  const state = { batched: [], batches: 0, run };
  return {
    state,
    prepare(sql) {
      return {
        sql,
        values: [],
        bind(...values) { this.values = values; return this; },
        async first() { return state.run; },
      };
    },
    // D1 executes a batch as one transaction: statements are staged and only
    // become visible when the whole sequence succeeds.
    async batch(statements) {
      state.batches += 1;
      if (commitThenFail) {
        // Committed, but the response never made it back to the caller.
        throw new Error("Network connection lost.");
      }
      const staged = [];
      for (const [index, statement] of statements.entries()) {
        if (failAt === index) {
          throw new Error(failWith || `D1_ERROR: statement ${index} failed: SQLITE_ERROR`);
        }
        staged.push(statement);
      }
      state.batched.push(...staged);
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
  assert.match(body.run.steps_digest, /^[0-9a-f]{64}$/);
  assert.deepEqual(body.run.entry_step_ids, ["a"]);
  assert.equal(body.steps.length, 2);
  assert.equal(db.state.batches, 1, "the whole dispatch must be a single transaction");
});

test("run row, tasks, audit rows and edges all travel in that one batch", async () => {
  const db = fakeDb();
  await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  const sql = db.state.batched.map(statement => statement.sql);
  assert.equal(sql.filter(s => s.includes("INSERT INTO workflow_runs")).length, 1);
  assert.equal(sql.filter(s => s.includes("INSERT INTO tasks")).length, 2);
  assert.equal(sql.filter(s => s.includes("workflow_step_dispatched")).length, 2);
  assert.equal(sql.filter(s => s.includes("INSERT OR IGNORE INTO task_dependencies")).length, 1);
});

test("a failure at any statement position enqueues nothing", async () => {
  // Every position, not just the last: a guarantee that only holds for the last
  // statement is not a guarantee about the batch.
  const positions = 1 /* run row */ + 2 * 2 /* task + audit per step */ + 1 /* one edge */;
  for (let position = 0; position < positions; position += 1) {
    const db = fakeDb({ failAt: position });
    const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
    assert.equal(response.status, 503, `position ${position} must fail closed`);
    const body = await response.json();
    assert.equal(body.error, "dispatch_failed");
    assert.equal(db.state.batches, 1, `position ${position} must make exactly one batch attempt`);
    assert.equal(db.state.batched.length, 0, `position ${position} left ${db.state.batched.length} statements applied`);
  }
});

test("a lost response is reported as unknown, not as failure", async () => {
  // If the batch may have committed, answering "failed" invites a retry that
  // duplicates work. The caller must reconcile first.
  const db = fakeDb({ commitThenFail: true });
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 504);
  const body = await response.json();
  assert.equal(body.error, "dispatch_outcome_unknown");
  assert.match(body.detail, /reconcile/i);
});

test("replaying the same run is a no-op, not a duplicate", async () => {
  const db = fakeDb({ run: runRow({ steps_digest: await normalizedDigest() }) });
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.created, false);
  assert.deepEqual(body.run.entry_step_ids, ["a"]);
  assert.equal(db.state.batches, 0, "a replay must not write");
});

test("same run id with a different plan digest is refused", async () => {
  const db = fakeDb({ run: runRow({ plan_digest: "b".repeat(64), steps_digest: await normalizedDigest() }) });
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, "run_digest_conflict");
  assert.equal(db.state.batches, 0);
});

test("same run id and plan digest but different steps is a contract conflict", async () => {
  // plan_digest alone does not bind content; the stored steps digest does.
  const db = fakeDb({ run: runRow({ steps_digest: "c".repeat(64) }) });
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, "step_contract_conflict");
  assert.equal(db.state.batches, 0);
});

test("losing a dispatch race reports the winner instead of failing", async () => {
  const digest = await normalizedDigest();
  const db = fakeDb({ commitThenFail: false });
  db.batch = async () => {
    db.state.run = runRow({ steps_digest: digest });
    throw new Error("D1_ERROR: UNIQUE constraint failed: workflow_runs.run_id: SQLITE_CONSTRAINT");
  };
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.created, false);
});

test("losing a race to a different plan is still a conflict", async () => {
  const db = fakeDb();
  db.batch = async () => {
    db.state.run = runRow({ plan_digest: "b".repeat(64), steps_digest: "c".repeat(64) });
    throw new Error("D1_ERROR: UNIQUE constraint failed: workflow_runs.run_id: SQLITE_CONSTRAINT");
  };
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, "run_digest_conflict");
});

test("a key collision with no owning run fails closed", async () => {
  const db = fakeDb();
  db.batch = async () => {
    throw new Error("D1_ERROR: UNIQUE constraint failed: tasks.idempotency_key: SQLITE_CONSTRAINT");
  };
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, "step_contract_conflict");
});

test("a deploy without migration 0012 says so instead of failing generically", async () => {
  const db = fakeDb({ failAt: 0, failWith: "D1_ERROR: no such table: workflow_runs: SQLITE_ERROR" });
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 404);
  const body = await response.json();
  assert.equal(body.error, "plan_dispatch_unavailable");
  assert.match(body.detail, /0012/);
});

test("a dependency outside the plan is rejected with the offending edge named", async () => {
  const result = await validatePlan(plan({
    steps: [{ step_id: "a", task_type: "system_audit", title: "x", depends_on: ["ghost"] }],
  }));
  assert.equal(result.error, "unknown_step_dependency");
  assert.equal(result.detail, "a->ghost");
});

test("a cycle is rejected and the cyclic steps are named", async () => {
  const result = await validatePlan(plan({
    steps: [
      { step_id: "a", task_type: "system_audit", title: "x", depends_on: ["b"] },
      { step_id: "b", task_type: "code_review", title: "y", depends_on: ["a"] },
    ],
  }));
  assert.equal(result.error, "cyclic_dependency");
  assert.equal(result.detail, "a,b");
});

test("self-dependency, duplicate steps and duplicate edges are rejected", async () => {
  const selfDep = await validatePlan(plan({
    steps: [{ step_id: "a", task_type: "system_audit", title: "x", depends_on: ["a"] }],
  }));
  assert.equal(selfDep.error, "self_dependency");

  const duplicateStep = await validatePlan(plan({
    steps: [
      { step_id: "a", task_type: "system_audit", title: "x" },
      { step_id: "a", task_type: "code_review", title: "y" },
    ],
  }));
  assert.equal(duplicateStep.error, "duplicate_step_id");

  const duplicateEdge = await validatePlan(plan({
    steps: [
      { step_id: "a", task_type: "system_audit", title: "x" },
      { step_id: "b", task_type: "code_review", title: "y", depends_on: ["a", "a"] },
    ],
  }));
  assert.equal(duplicateEdge.error, "duplicate_step_dependency");
});

test("only the manifest's task types are accepted", async () => {
  // A dispatcher that accepts any string moves task-type validation into the
  // queue, where a typo becomes a task nothing can execute.
  const result = await validatePlan(plan({
    steps: [{ step_id: "a", task_type: "whatever_you_like", title: "x" }],
  }));
  assert.equal(result.error, "unsupported_step_task_type");
  assert.equal(result.detail, "whatever_you_like");
});

test("run id, workflow id, digest and step count are validated", async () => {
  assert.equal((await validatePlan(plan({ run_id: "" }))).error, "invalid_run_id");
  assert.equal((await validatePlan(plan({ run_id: "9bad" }))).error, "invalid_run_id");
  assert.equal((await validatePlan(plan({ workflow_id: "" }))).error, "invalid_workflow_id");
  assert.equal((await validatePlan(plan({ plan_digest: "nothex" }))).error, "invalid_plan_digest");
  assert.equal((await validatePlan(plan({ steps: [] }))).error, "invalid_steps");
  assert.equal((await validatePlan(plan({ steps: Array.from({ length: 21 }, (_, index) => ({
    step_id: `s${index}`, task_type: "system_audit", title: "x",
  })) }))).error, "invalid_steps");
});

test("codespace task types are never valid workflow steps", async () => {
  // Two independent reasons, in order: the executor is disabled, and even when
  // it is not, a codespace type is not a workflow step type.
  const body = plan({ steps: [{ step_id: "a", task_type: "codespace_test", title: "x" }] });
  const types = new Set(["codespace_test"]);
  assert.equal((await validatePlan(body, { codespaceTaskTypes: types })).error, "codespace_executor_disabled");
  assert.equal(
    (await validatePlan(body, { codespaceTaskTypes: types, executorEnabled: true })).error,
    "unsupported_step_task_type",
  );
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

test("the steps digest is stable and content-sensitive", async () => {
  const first = await normalizedDigest();
  assert.equal(await normalizedDigest(), first, "the same plan must digest identically");
  const changed = await normalizedDigest(plan({
    steps: [
      { step_id: "a", task_type: "system_audit", title: "first", payload: { q: 2 } },
      { step_id: "b", task_type: "code_review", title: "second", payload: {}, depends_on: ["a"] },
    ],
  }));
  assert.notEqual(changed, first, "a changed payload must change the digest");
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

test("maximum-length identifiers keep distinct task ids", async () => {
  // Both fields allow 64 characters, so the id can reach 132. Truncating at 128
  // made two distinct long step ids share one task id — in the reconciliation
  // path, which exists to prove that nothing was lost.
  const runId = "r".repeat(64);
  const stepA = "a".repeat(64);
  const stepB = "a".repeat(63) + "b";
  const result = await validatePlan(plan({
    run_id: runId,
    steps: [
      { step_id: stepA, task_type: "system_audit", title: "first" },
      { step_id: stepB, task_type: "system_audit", title: "second" },
    ],
  }));
  assert.ok(result.plan, result.error);
  const ids = result.plan.steps.map(step => step.id);
  assert.equal(ids[0], `wf-${runId}-${stepA}`);
  assert.notEqual(ids[0], ids[1], "distinct steps must never share a task id");
  assert.ok(ids.every(id => id.length > 128), "and the ids are not silently shortened");
});

test("the separator ambiguity across runs fails closed at the database, not silently", async () => {
  // run "a" + step "b-c" and run "a-b" + step "c" both render as `wf-a-b-c`.
  // Uniqueness inside a run is guaranteed; across runs the strict INSERT is what
  // refuses to merge two identities, so the route must not use INSERT OR IGNORE.
  const first = await validatePlan(plan({
    run_id: "a", steps: [{ step_id: "b-c", task_type: "system_audit", title: "x" }],
  }));
  const second = await validatePlan(plan({
    run_id: "a-b", steps: [{ step_id: "c", task_type: "system_audit", title: "y" }],
  }));
  assert.equal(first.plan.steps[0].id, second.plan.steps[0].id);

  const db = fakeDb();
  db.batch = async () => {
    throw new Error("D1_ERROR: UNIQUE constraint failed: tasks.id: SQLITE_CONSTRAINT");
  };
  const response = await handleWorkflowDispatch(post(plan({
    run_id: "a-b", steps: [{ step_id: "c", task_type: "system_audit", title: "y" }],
  })), { DB: db }, url);
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, "step_contract_conflict");
});

test("a replay under a different workflow id or version is an identity conflict", async () => {
  const digest = await normalizedDigest();
  for (const override of [{ workflow_id: "other-workflow" }, { plan_version: 2 }]) {
    const db = fakeDb({ run: runRow({ steps_digest: digest }) });
    const response = await handleWorkflowDispatch(post(plan(override)), { DB: db }, url);
    assert.equal(response.status, 409, JSON.stringify(override));
    assert.equal((await response.json()).error, "run_identity_conflict");
    assert.equal(db.state.batches, 0);
  }
});

test("the concurrent-conflict path enforces the same identity rule", async () => {
  // The uniqueness-conflict reconciliation calls replayOf too; it must not be a
  // second, weaker door.
  const digest = await normalizedDigest();
  const db = fakeDb();
  db.batch = async () => {
    db.state.run = runRow({ workflow_id: "other-workflow", steps_digest: digest });
    throw new Error("D1_ERROR: UNIQUE constraint failed: workflow_runs.run_id: SQLITE_CONSTRAINT");
  };
  const response = await handleWorkflowDispatch(post(plan()), { DB: db }, url);
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, "run_identity_conflict");
});

test("the lookup route reconciles a lost response without writing", async () => {
  const digest = await normalizedDigest();
  const db = fakeDb({ run: runRow({ steps_digest: digest }) });
  const rows = [
    { id: "wf-run-1-a", status: "pending", attempts: 0, idempotency_key: "workflow-step:x" },
    { id: "wf-run-1-b", status: "pending", attempts: 0, idempotency_key: "workflow-step:y" },
  ];
  db.prepare = sql => ({
    sql,
    values: [],
    bind(...values) { this.values = values; return this; },
    async first() { return db.state.run; },
    async all() { return { results: rows }; },
  });
  const response = await handleWorkflowRunLookup(
    new Request("https://engine.example/v1/workflows/run-1", { method: "GET" }),
    { DB: db },
    new URL("https://engine.example/v1/workflows/run-1"),
  );
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.complete, true);
  assert.deepEqual(body.steps.map(step => step.step_id).sort(), ["a", "b"]);
  assert.equal(db.state.batches, 0, "reconciliation must not write");
});

test("the lookup route reports an unknown run rather than inventing one", async () => {
  const db = fakeDb();
  const response = await handleWorkflowRunLookup(
    new Request("https://engine.example/v1/workflows/never-ran", { method: "GET" }),
    { DB: db },
    new URL("https://engine.example/v1/workflows/never-ran"),
  );
  assert.equal(response.status, 404);
  assert.equal((await response.json()).error, "run_not_found");
});

test("the lookup route does not shadow dispatch", async () => {
  const db = fakeDb();
  const lookup = await handleWorkflowRunLookup(
    post(plan()), { DB: db }, new URL("https://engine.example/v1/workflows/dispatch"),
  );
  assert.equal(lookup, null, "POST /dispatch belongs to the dispatch handler");
});

test("a non-dispatch path is left to the main router", async () => {
  const other = new URL("https://engine.example/v1/tasks");
  assert.equal(await handleWorkflowDispatch(post(plan()), { DB: fakeDb() }, other), null);
});
