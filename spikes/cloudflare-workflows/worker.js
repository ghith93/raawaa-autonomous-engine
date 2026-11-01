import { WorkflowEntrypoint } from "cloudflare:workers";
import {
  createWorkflowInstanceId,
  runTaskWorkflow,
  validateWorkflowPayload,
} from "./workflow-core.js";

const MAX_BODY_BYTES = 4096;
const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

async function readJson(request) {
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > MAX_BODY_BYTES) throw new Error("body_too_large");
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) throw new Error("body_too_large");
  try { return JSON.parse(raw); } catch { throw new Error("invalid_json"); }
}

function fakeAdapters() {
  return {
    now: () => Date.now(),
    async claim(input) {
      return {
        status: "claimed",
        task_id: input.task_id,
        idempotency_key: input.idempotency_key,
        lease_expires_at: input.lease_expires_at || new Date(Date.now() + 60_000).toISOString(),
      };
    },
    async research(input) {
      return { task_id: input.task_id, result: "research-stub", completed_at: new Date().toISOString() };
    },
    async review(input) {
      return { task_id: input.task_id, decision: "review-stub", research_result: input.research.result };
    },
    async checkpoint(input) {
      return { checkpoint_id: "spike:" + input.task_id, review_decision: input.review.decision };
    },
    async complete(input) {
      return { task_id: input.task_id, status: "completed", checkpoint_id: input.checkpoint_id };
    },
  };
}

export class TaskWorkflow extends WorkflowEntrypoint {
  async run(event, step) {
    return runTaskWorkflow(event.payload, step, fakeAdapters());
  }
}

async function getInstance(env, id) {
  if (!/^task-[a-f0-9]{32}$/.test(id)) return null;
  return env.TASK_WORKFLOW.get(id);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, mode: "local-workflow-spike", production_bindings: false });
    }

    if (url.pathname === "/start") {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      let payload;
      try { payload = validateWorkflowPayload(await readJson(request)); }
      catch (error) {
        return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_workflow_payload" }, 400);
      }
      const id = await createWorkflowInstanceId(payload.task_id, payload.idempotency_key);
      try {
        const existing = await getInstance(env, id);
        if (existing) {
          const status = await existing.status();
          return json({ id, reused: true, status: status.status || status }, 200);
        }
      } catch {
        // No existing instance; create the deterministic instance ID below.
      }
      try {
        const instance = await env.TASK_WORKFLOW.create({
          id,
          params: payload,
          retention: { successRetention: "1 day", errorRetention: "1 day" },
        });
        return json({ id, reused: false, status: (await instance.status()).status || "queued" }, 202);
      } catch {
        // A concurrent duplicate may win between get() and create(). Resolve to
        // that deterministic instance rather than creating a second task result.
        try {
          const existing = await getInstance(env, id);
          if (!existing) throw new Error("instance_missing");
          const status = await existing.status();
          return json({ id, reused: true, status: status.status || status }, 200);
        } catch {
          return json({ error: "workflow_create_failed" }, 503);
        }
      }
    }

    const statusMatch = url.pathname.match(/^\/status\/(task-[a-f0-9]{32})$/);
    if (statusMatch) {
      if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
      try {
        const instance = await getInstance(env, statusMatch[1]);
        if (!instance) return json({ error: "instance_not_found" }, 404);
        const status = await instance.status();
        return json({
          id: statusMatch[1],
          status: status.status || "unknown",
          output: status.output ?? null,
          error: status.error ? { name: status.error.name, message: status.error.message } : null,
          local_step_outputs: Array.isArray(status.__LOCAL_DEV_STEP_OUTPUTS) ? status.__LOCAL_DEV_STEP_OUTPUTS : [],
        });
      } catch {
        return json({ error: "instance_not_found" }, 404);
      }
    }

    const approveMatch = url.pathname.match(/^\/approve\/(task-[a-f0-9]{32})$/);
    if (approveMatch) {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      let input;
      try { input = await readJson(request); } catch { return json({ error: "invalid_json" }, 400); }
      if (!input || input.approved !== true || typeof input.approval_id !== "string" ||
          input.approval_id.length < 1 || input.approval_id.length > 128 ||
          Object.keys(input).some(key => !["approved", "approval_id"].includes(key))) {
        return json({ error: "invalid_approval" }, 400);
      }
      try {
        const instance = await getInstance(env, approveMatch[1]);
        if (!instance) return json({ error: "instance_not_found" }, 404);
        await instance.sendEvent({ type: "approval", payload: input });
        return json({ id: approveMatch[1], accepted: true });
      } catch {
        return json({ error: "approval_not_accepted" }, 409);
      }
    }

    const cancelMatch = url.pathname.match(/^\/cancel\/(task-[a-f0-9]{32})$/);
    if (cancelMatch) {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      try {
        const instance = await getInstance(env, cancelMatch[1]);
        if (!instance) return json({ error: "instance_not_found" }, 404);
        await instance.terminate();
        return json({ id: cancelMatch[1], terminated: true });
      } catch {
        return json({ error: "termination_not_accepted" }, 409);
      }
    }

    const restartMatch = url.pathname.match(/^\/restart\/(task-[a-f0-9]{32})$/);
    if (restartMatch) {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      let input;
      try { input = await readJson(request); } catch { return json({ error: "invalid_json" }, 400); }
      if (input?.from_step !== "review-stub" ||
          Object.keys(input).some(key => key !== "from_step")) {
        return json({ error: "unsupported_restart_point" }, 400);
      }
      try {
        const instance = await getInstance(env, restartMatch[1]);
        if (!instance) return json({ error: "instance_not_found" }, 404);
        await instance.restart({ from: { name: "review-stub" } });
        return json({ id: restartMatch[1], restarted_from: "review-stub" });
      } catch {
        return json({ error: "restart_not_accepted" }, 409);
      }
    }

    return json({ error: "not_found" }, 404);
  },
};
