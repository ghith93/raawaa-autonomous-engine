import test from "node:test";
import assert from "node:assert/strict";
import {
  createWorkflowInstanceId,
  runTaskWorkflow,
  validateWorkflowPayload,
} from "../spikes/cloudflare-workflows/workflow-core.js";

const NOW = Date.parse("2026-11-02T12:00:00.000Z");

function makeAdapters(overrides = {}) {
  const calls = { claim: 0, research: 0, review: 0, checkpoint: 0, complete: 0 };
  const idempotentClaims = new Map();
  const adapters = {
    now: () => NOW,
    async claim(input) {
      calls.claim += 1;
      if (idempotentClaims.has(input.idempotency_key)) return idempotentClaims.get(input.idempotency_key);
      const result = {
        status: "claimed",
        task_id: input.task_id,
        idempotency_key: input.idempotency_key,
        lease_expires_at: input.lease_expires_at || new Date(NOW + 60_000).toISOString(),
      };
      idempotentClaims.set(input.idempotency_key, result);
      return result;
    },
    async research(input) { calls.research += 1; return { task_id: input.task_id, result: "research-stub" }; },
    async review(input) { calls.review += 1; return { task_id: input.task_id, decision: "review-stub" }; },
    async checkpoint(input) { calls.checkpoint += 1; return { checkpoint_id: "spike:" + input.task_id }; },
    async complete(input) { calls.complete += 1; return { task_id: input.task_id, status: "completed" }; },
    calls,
    async afterStep() {},
    ...overrides,
  };
  return adapters;
}

function mockStep({ cache = new Map(), approval = { approved: true, approval_id: "approval-1" }, failWait = false } = {}) {
  const invocations = [];
  return {
    cache,
    invocations,
    async do(name, options, callback) {
      if (cache.has(name)) return cache.get(name);
      invocations.push({ name, options });
      const retries = options?.retries?.limit ?? 0;
      let attempt = 0;
      while (true) {
        try {
          const result = await callback();
          cache.set(name, result);
          return result;
        } catch (error) {
          if (attempt >= retries) throw error;
          attempt += 1;
        }
      }
    },
    async waitForEvent(name, options) {
      invocations.push({ name, options, kind: "waitForEvent" });
      if (failWait) throw new Error("wait_for_event_timeout");
      return approval;
    },
  };
}

const basePayload = {
  task_id: "task-123",
  idempotency_key: "idem-123",
  lease_expires_at: new Date(NOW + 60_000).toISOString(),
};

test("validates a strict bounded workflow payload", () => {
  assert.deepEqual(validateWorkflowPayload(basePayload), {
    task_id: "task-123",
    idempotency_key: "idem-123",
    await_approval: false,
    approval_timeout_seconds: 300,
    lease_expires_at: basePayload.lease_expires_at,
  });
  assert.throws(() => validateWorkflowPayload({ ...basePayload, command: "rm -rf /" }), /unsupported/);
  assert.throws(() => validateWorkflowPayload({ ...basePayload, task_id: "../escape" }), /task_id/);
  assert.throws(() => validateWorkflowPayload({ ...basePayload, idempotency_key: "" }), /idempotency/);
  assert.throws(() => validateWorkflowPayload({ ...basePayload, approval_timeout_seconds: 0 }), /timeout/);
});

test("workflow instance IDs are deterministic and scoped by task plus idempotency key", async () => {
  const first = await createWorkflowInstanceId("task-123", "idem-123");
  const duplicate = await createWorkflowInstanceId("task-123", "idem-123");
  const distinct = await createWorkflowInstanceId("task-123", "idem-456");
  assert.equal(first, duplicate);
  assert.notEqual(first, distinct);
  assert.match(first, /^task-[a-f0-9]{32}$/);
  assert.ok(first.length <= 100);
});

test("runs claim, research, review, checkpoint, approval and completion in order", async () => {
  const adapters = makeAdapters();
  const step = mockStep({ approval: { approved: true, approval_id: "approval-42" } });
  const result = await runTaskWorkflow({ ...basePayload, await_approval: true }, step, adapters);
  assert.equal(result.status, "completed");
  assert.equal(result.approval_id, "approval-42");
  assert.deepEqual(step.invocations.map(x => x.name), [
    "claim-task", "research-stub", "review-stub", "checkpoint-marker",
    "await-external-approval", "complete-task",
  ]);
  assert.deepEqual(adapters.calls, { claim: 1, research: 1, review: 1, checkpoint: 1, complete: 1 });
});

test("completed steps are reused after a simulated crash and fresh workflow invocation", async () => {
  const adapters = makeAdapters();
  const cache = new Map();
  const firstStep = mockStep({ cache });
  let crashed = false;
  adapters.afterStep = async name => {
    if (name === "research-stub" && !crashed) {
      crashed = true;
      throw new Error("simulated_process_interruption");
    }
  };
  await assert.rejects(
    runTaskWorkflow(basePayload, firstStep, adapters),
    /simulated_process_interruption/,
  );
  adapters.afterStep = async () => {};
  const resumedStep = mockStep({ cache });
  const result = await runTaskWorkflow(basePayload, resumedStep, adapters);
  assert.equal(result.status, "completed");
  assert.equal(adapters.calls.claim, 1);
  assert.equal(adapters.calls.research, 1);
  assert.equal(adapters.calls.review, 1);
  assert.equal(adapters.calls.checkpoint, 1);
  assert.equal(adapters.calls.complete, 1);
});

test("duplicate idempotency keys reuse the same claim record", async () => {
  const adapters = makeAdapters();
  const first = await adapters.claim({ ...basePayload });
  const second = await adapters.claim({ ...basePayload });
  assert.deepEqual(second, first);
  assert.equal(adapters.calls.claim, 2);
  assert.equal(first.idempotency_key, "idem-123");
});

test("expired lease stops before research or review", async () => {
  const adapters = makeAdapters();
  const step = mockStep();
  const result = await runTaskWorkflow({
    ...basePayload,
    lease_expires_at: new Date(NOW - 1).toISOString(),
  }, step, adapters);
  assert.equal(result.status, "lease_expired");
  assert.equal(adapters.calls.research, 0);
  assert.equal(adapters.calls.review, 0);
  assert.equal(adapters.calls.complete, 0);
});

test("approval rejection blocks completion", async () => {
  const adapters = makeAdapters();
  const step = mockStep({ approval: { approved: false, approval_id: "approval-no" } });
  const result = await runTaskWorkflow({ ...basePayload, await_approval: true }, step, adapters);
  assert.equal(result.status, "blocked");
  assert.equal(result.reason, "approval_rejected");
  assert.equal(adapters.calls.complete, 0);
});

test("approval timeout blocks completion and is observable", async () => {
  const adapters = makeAdapters();
  const step = mockStep({ failWait: true });
  const result = await runTaskWorkflow({ ...basePayload, await_approval: true }, step, adapters);
  assert.equal(result.status, "approval_timed_out");
  assert.equal(adapters.calls.complete, 0);
  const wait = step.invocations.find(x => x.kind === "waitForEvent");
  assert.equal(wait.options.type, "approval");
  assert.equal(wait.options.timeout, "300 seconds");
});

test("step retries are explicit, bounded and terminal failures propagate", async () => {
  let attempts = 0;
  const adapters = makeAdapters({
    async research() {
      attempts += 1;
      throw new Error("deterministic_terminal_failure");
    },
  });
  const step = mockStep();
  await assert.rejects(runTaskWorkflow(basePayload, step, adapters), /deterministic_terminal_failure/);
  assert.equal(attempts, 3);
  const researchStep = step.invocations.find(x => x.name === "research-stub");
  assert.deepEqual(researchStep.options.retries, { limit: 2, delay: "1 second", backoff: "exponential" });
});
