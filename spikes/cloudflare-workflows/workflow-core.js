const RETRIES = Object.freeze({
  limit: 2,
  delay: "1 second",
  backoff: "exponential",
});

const ALLOWED_PAYLOAD_FIELDS = new Set([
  "task_id",
  "idempotency_key",
  "await_approval",
  "approval_timeout_seconds",
  "lease_expires_at",
]);

export function validateWorkflowPayload(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("invalid workflow payload");
  }
  for (const key of Object.keys(payload)) {
    if (!ALLOWED_PAYLOAD_FIELDS.has(key)) throw new Error("unsupported workflow payload field");
  }
  if (typeof payload.task_id !== "string" ||
      !/^[A-Za-z0-9_-]{1,64}$/.test(payload.task_id)) {
    throw new Error("invalid task_id");
  }
  if (typeof payload.idempotency_key !== "string" ||
      !/^[A-Za-z0-9._:-]{1,128}$/.test(payload.idempotency_key)) {
    throw new Error("invalid idempotency_key");
  }
  const awaitApproval = payload.await_approval === undefined ? false : payload.await_approval;
  if (typeof awaitApproval !== "boolean") throw new Error("invalid await_approval");
  const timeout = payload.approval_timeout_seconds === undefined
    ? 300 : payload.approval_timeout_seconds;
  if (!Number.isInteger(timeout) || timeout < 1 || timeout > 3600) {
    throw new Error("invalid approval timeout");
  }
  const leaseExpiresAt = payload.lease_expires_at === undefined ? null : payload.lease_expires_at;
  if (leaseExpiresAt !== null &&
      (typeof leaseExpiresAt !== "string" || !Number.isFinite(Date.parse(leaseExpiresAt)))) {
    throw new Error("invalid lease_expires_at");
  }
  return {
    task_id: payload.task_id,
    idempotency_key: payload.idempotency_key,
    await_approval: awaitApproval,
    approval_timeout_seconds: timeout,
    lease_expires_at: leaseExpiresAt,
  };
}

export async function createWorkflowInstanceId(taskId, idempotencyKey) {
  if (typeof taskId !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(taskId)) {
    throw new Error("invalid task_id");
  }
  if (typeof idempotencyKey !== "string" ||
      !/^[A-Za-z0-9._:-]{1,128}$/.test(idempotencyKey)) {
    throw new Error("invalid idempotency_key");
  }
  const bytes = new TextEncoder().encode(taskId + "\0" + idempotencyKey);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hex = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
  return "task-" + hex.slice(0, 32);
}

async function durableStep(step, name, callback) {
  return step.do(name, {
    retries: RETRIES,
    timeout: "30 seconds",
  }, callback);
}

export async function runTaskWorkflow(rawPayload, step, adapters) {
  const payload = validateWorkflowPayload(rawPayload);
  const claim = await durableStep(step, "claim-task", () => adapters.claim(payload));
  if (adapters.afterStep) await adapters.afterStep("claim-task", claim);
  if (!claim || claim.status !== "claimed") {
    return { status: "duplicate", task_id: payload.task_id };
  }

  const leaseExpiry = Date.parse(claim.lease_expires_at || payload.lease_expires_at || "");
  const now = adapters.now ? adapters.now() : Date.now();
  if (!Number.isFinite(leaseExpiry) || leaseExpiry <= now) {
    return { status: "lease_expired", task_id: payload.task_id };
  }

  const research = await durableStep(step, "research-stub", () =>
    adapters.research({ task_id: payload.task_id, idempotency_key: payload.idempotency_key }));
  if (adapters.afterStep) await adapters.afterStep("research-stub", research);

  const review = await durableStep(step, "review-stub", () =>
    adapters.review({ task_id: payload.task_id, research }));
  if (adapters.afterStep) await adapters.afterStep("review-stub", review);

  const checkpoint = await durableStep(step, "checkpoint-marker", () =>
    adapters.checkpoint({ task_id: payload.task_id, research, review }));
  if (adapters.afterStep) await adapters.afterStep("checkpoint-marker", checkpoint);

  let approvalId = null;
  if (payload.await_approval) {
    let approval;
    try {
      approval = await step.waitForEvent("await-external-approval", {
        type: "approval",
        timeout: payload.approval_timeout_seconds + " seconds",
      });
    } catch (error) {
      if (/timeout|timed out/i.test(String(error?.message || error))) {
        return { status: "approval_timed_out", task_id: payload.task_id, checkpoint_id: checkpoint.checkpoint_id };
      }
      throw error;
    }
    // The Workers API documents the event payload as the waitForEvent return
    // value. Accept the local emulator's WorkflowEvent wrapper as well, while
    // validating the same strict payload shape in either case.
    const approvalPayload = approval && typeof approval === "object" &&
      approval.payload && typeof approval.payload === "object" ? approval.payload : approval;
    if (!approvalPayload || approvalPayload.approved !== true ||
        typeof approvalPayload.approval_id !== "string" || approvalPayload.approval_id.length < 1 ||
        approvalPayload.approval_id.length > 128) {
      return { status: "blocked", reason: "approval_rejected", task_id: payload.task_id };
    }
    approvalId = approvalPayload.approval_id;
    if (adapters.afterStep) await adapters.afterStep("await-external-approval", approvalPayload);
  }

  const completed = await durableStep(step, "complete-task", () =>
    adapters.complete({
      task_id: payload.task_id,
      idempotency_key: payload.idempotency_key,
      checkpoint_id: checkpoint.checkpoint_id,
      approval_id: approvalId,
    }));
  if (adapters.afterStep) await adapters.afterStep("complete-task", completed);

  return {
    status: "completed",
    task_id: payload.task_id,
    checkpoint_id: checkpoint.checkpoint_id,
    approval_id: approvalId,
    result: completed,
  };
}
