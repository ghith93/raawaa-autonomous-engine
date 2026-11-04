// Atomic dispatch of a validated workflow plan into the task queue.
//
// The guarantee this file exists to provide:
//
//   either every step of the plan is enqueued with its dependency edges and a
//   workflow_runs row records the run, or nothing is enqueued at all.
//
// It is achievable because D1's `batch()` executes as one SQL transaction: when
// any statement fails the whole sequence rolls back. So the entire dispatch —
// run row, tasks, dependency edges, audit rows — is one batch. There is no
// partial state to reconcile and no 'dispatching' status to recover from.
//
// Two replay properties follow from that, and both are tested:
//
//   * task ids and idempotency keys are derived deterministically from
//     (workflow_id, run_id, plan_version, step_id) — the same derivation the
//     manifest uses — so a replayed dispatch is a no-op per step rather than
//     a duplicate;
//   * a replay that reuses a run_id with a *different* plan digest is refused
//     with 409 rather than silently dispatching a different workflow under an
//     identity that already means something.
//
// What this does NOT do: authorise execution. Tasks are enqueued as `pending`
// and pass through exactly the same claim, lease and policy gates as any task
// enqueued one at a time. Executor release stays false.

const MAX_STEPS = 20;
const MAX_TITLE = 240;
const MAX_TASK_TYPE = 100;
const MAX_RUN_ID = 128;
const MAX_PAYLOAD_BYTES = 16 * 1024;
const DIGEST_RE = /^[0-9a-f]{64}$/;
const IDENTIFIER_RE = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
// The same four types engine/workflow_manifest.py will turn into a manifest.
// Anything else is refused here rather than enqueued as an unknown task type.
const WORKFLOW_TASK_TYPES = new Set(["research_brief", "code_review", "content_draft", "system_audit"]);

// A deploy that predates migration 0012 has no workflow_runs table. That is a
// missing capability, not a storage fault: answering 503 would send an operator
// looking for a database problem that does not exist, and answering 200 would be
// a lie. 404 says precisely "this deployment cannot dispatch plans yet".
function isMissingTable(error) {
  return /no such table/i.test(String(error?.message || error));
}

// A D1 statement error is a definite rollback: the batch aborted and nothing
// committed. Anything else (transport loss, isolate death, a timeout) leaves the
// outcome genuinely unknown and must not be reported as "failed" — that invites
// a retry of work that may already exist.
function isDefiniteRollback(error) {
  const message = String(error?.message || error);
  return /SQLITE_|D1_ERROR|constraint failed|syntax error/i.test(message);
}

function isUniqueConflict(error) {
  return /UNIQUE constraint failed/i.test(String(error?.message || error));
}

const RUN_COLUMNS = "run_id,workflow_id,plan_digest,steps_digest,plan_version,status,step_count," +
  "entry_step_ids,step_ids,created_at";

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

// Content binding for a run. Deliberately built from the normalized steps in a
// fixed order with a fixed field order, so the same plan always yields the same
// digest on any runtime.
export async function stepsDigestFor(steps) {
  const canonical = JSON.stringify(steps.map(step => [
    step.step_id, step.task_type, step.title, step.payload_json,
    step.priority, step.max_attempts, step.depends_on,
  ]));
  return sha256Hex(canonical);
}

async function readRun(env, runId) {
  const row = await env.DB.prepare(`SELECT ${RUN_COLUMNS} FROM workflow_runs WHERE run_id=?`).bind(runId).first();
  if (!row) return null;
  return {
    ...row,
    entry_step_ids: JSON.parse(row.entry_step_ids),
    step_ids: JSON.parse(row.step_ids),
  };
}

// A replay is only a no-op when it names the same run *and* the same content.
// Same plan_digest with different steps would otherwise return someone else's
// dispatch as if it were yours.
// A replay is the same dispatch only when the whole identity matches. Digests
// alone are not enough: `stepsDigestFor` hashes step content, not workflow_id or
// plan_version, and both of those feed the derived task ids and idempotency
// keys. Without this check, the same run_id and digests under a different
// workflow_id or version would be answered "already done" while naming a
// different set of tasks.
function replayOf(existing, plan, stepsDigest) {
  if (existing.workflow_id !== plan.workflow_id || existing.plan_version !== plan.plan_version) {
    return reply({
      error: "run_identity_conflict",
      detail: `stored ${existing.workflow_id}/${existing.plan_version} != ${plan.workflow_id}/${plan.plan_version}`,
    }, 409);
  }
  if (existing.plan_digest !== plan.digest) return reply({ error: "run_digest_conflict" }, 409);
  if (existing.steps_digest !== stepsDigest) return reply({ error: "step_contract_conflict" }, 409);
  return reply({ run: existing, created: false }, 200);
}

function reply(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}

// Deterministic identity: the same (run, step) always names the same task, in
// any process, on any retry.
//
// No truncation. `run_id` and `step_id` are each allowed 64 characters, so the
// combined id can reach 132; a `.slice(0, 128)` made two distinct long step ids
// collapse onto one task id — a lossy identity in exactly the path
// (reconciliation) that exists to prove nothing was lost. SQLite TEXT has no
// length limit, so there was never a reason to shorten it.
//
// Uniqueness within a run is what the rest of the system needs: step ids are
// unique per run and run_id is the key of workflow_runs, so two steps of one run
// can never share an id. Across runs the separator is theoretically ambiguous
// (run "a" + step "b-c" reads the same as run "a-b" + step "c"); that case is not
// silently merged — the strict INSERT meets the tasks primary key, the batch
// rolls back, and the caller gets 409. A collision has to fail closed; it does
// not have to be impossible.
export function taskIdFor(runId, stepId) {
  return `wf-${runId}-${stepId}`;
}

// The idempotency key is NOT invented here. engine/workflow_manifest.py already
// derives one per step from (workflow_id, run_id, plan_version, step_id); a
// second scheme in the Worker would mean two identities for one task and a
// replay that duplicates instead of no-opping. The derivation is implemented in
// both languages and pinned by the same test vector on each side.
export async function idempotencyKeyFor(workflowId, runId, planVersion, stepId) {
  return `workflow-step:${await sha256Hex(`${workflowId}\u0000${runId}\u0000${planVersion}\u0000${stepId}`)}`;
}

// Kahn's algorithm. Returns null when acyclic, or the remaining cyclic ids.
export function findCycle(steps) {
  const byId = new Map(steps.map(step => [step.step_id, step]));
  const indegree = new Map(steps.map(step => [step.step_id, 0]));
  for (const step of steps) {
    for (const dependency of step.depends_on) {
      if (byId.has(dependency)) indegree.set(step.step_id, indegree.get(step.step_id) + 1);
    }
  }
  const ready = [...indegree.entries()].filter(([, count]) => count === 0).map(([id]) => id);
  const seen = [];
  while (ready.length) {
    const id = ready.shift();
    seen.push(id);
    for (const step of steps) {
      if (step.depends_on.includes(id)) {
        indegree.set(step.step_id, indegree.get(step.step_id) - 1);
        if (indegree.get(step.step_id) === 0) ready.push(step.step_id);
      }
    }
  }
  return seen.length === steps.length ? null : steps.map(s => s.step_id).filter(id => !seen.includes(id));
}

export async function validatePlan(input, { codespaceTaskTypes = new Set(), executorEnabled = false } = {}) {
  const runId = input?.run_id;
  const workflowId = input?.workflow_id;
  const digest = input?.plan_digest;
  const planVersion = input?.plan_version ?? 1;
  const steps = input?.steps;

  if (!validText(runId, MAX_RUN_ID) || !IDENTIFIER_RE.test(runId.trim())) {
    return { error: "invalid_run_id" };
  }
  if (typeof workflowId !== "string" || !IDENTIFIER_RE.test(workflowId)) {
    return { error: "invalid_workflow_id" };
  }
  // Deliberately opaque: this boundary checks the *shape* of plan_digest and
  // treats it as the caller's run identity. It does not recompute the canonical
  // planner digest — the Worker never receives the plan, only its steps, and
  // re-deriving it would mean a second planner in JavaScript. Content
  // consistency is enforced separately by steps_digest, and identity by
  // workflow_id + plan_version + run_id in replayOf().
  if (typeof digest !== "string" || !DIGEST_RE.test(digest)) {
    return { error: "invalid_plan_digest" };
  }
  if (!Number.isInteger(planVersion) || planVersion < 1 || planVersion > 1000) {
    return { error: "invalid_plan_version" };
  }
  if (!Array.isArray(steps) || steps.length < 1 || steps.length > MAX_STEPS) {
    return { error: "invalid_steps" };
  }

  const normalized = [];
  const seen = new Set();
  for (const raw of steps) {
    const stepId = raw?.step_id;
    if (typeof stepId !== "string" || !IDENTIFIER_RE.test(stepId)) return { error: "invalid_step_id" };
    if (seen.has(stepId)) return { error: "duplicate_step_id" };
    seen.add(stepId);

    if (!validText(raw?.task_type, MAX_TASK_TYPE)) return { error: "invalid_step_task_type" };
    if (codespaceTaskTypes.has(raw.task_type) && !executorEnabled) {
      return { error: "codespace_executor_disabled" };
    }
    if (!WORKFLOW_TASK_TYPES.has(raw.task_type)) return { error: "unsupported_step_task_type", detail: raw.task_type };
    if (!validText(raw?.title, MAX_TITLE)) return { error: "invalid_step_title" };

    const payload = raw?.payload ?? {};
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return { error: "invalid_step_payload" };
    }
    let payloadJson;
    try {
      payloadJson = JSON.stringify(payload);
    } catch {
      return { error: "invalid_step_payload" };
    }
    if (new TextEncoder().encode(payloadJson).byteLength > MAX_PAYLOAD_BYTES) {
      return { error: "step_payload_too_large" };
    }

    const dependsOn = raw?.depends_on ?? [];
    if (!Array.isArray(dependsOn) || dependsOn.length > MAX_STEPS) return { error: "invalid_step_dependencies" };
    if (dependsOn.some(dep => typeof dep !== "string" || !IDENTIFIER_RE.test(dep))) {
      return { error: "invalid_step_dependencies" };
    }
    if (new Set(dependsOn).size !== dependsOn.length) return { error: "duplicate_step_dependency" };
    if (dependsOn.includes(stepId)) return { error: "self_dependency" };

    const priority = raw?.priority ?? 50;
    const maxAttempts = raw?.max_attempts ?? 2;
    if (!Number.isInteger(priority) || priority < 0 || priority > 100) return { error: "invalid_step_priority" };
    if (!Number.isInteger(maxAttempts) || maxAttempts < 1 || maxAttempts > 5) {
      return { error: "invalid_step_max_attempts" };
    }

    const taskId = taskIdFor(runId.trim(), stepId);

    normalized.push({
      step_id: stepId,
      key: await idempotencyKeyFor(workflowId, runId.trim(), planVersion, stepId),
      id: taskId,
      task_type: raw.task_type.trim(),
      title: raw.title.trim(),
      payload_json: payloadJson,
      priority,
      max_attempts: maxAttempts,
      depends_on: [...dependsOn].sort(),
    });
  }

  // A dependency must be a step in this plan. An edge to something outside the
  // plan would be invisible: the dependency is never enqueued, the child blocks
  // forever, and the queue looks stuck rather than misconfigured.
  const ids = new Set(normalized.map(step => step.step_id));
  for (const step of normalized) {
    for (const dependency of step.depends_on) {
      if (!ids.has(dependency)) return { error: "unknown_step_dependency", detail: `${step.step_id}->${dependency}` };
    }
  }

  const cyclic = findCycle(normalized);
  if (cyclic) return { error: "cyclic_dependency", detail: cyclic.join(",") };

  const entrySteps = normalized.filter(step => step.depends_on.length === 0).map(step => step.step_id).sort();
  return {
    plan: {
      run_id: runId.trim(), workflow_id: workflowId, digest, plan_version: planVersion,
      steps: normalized, entry_steps: entrySteps,
    },
  };
}

// Read-only reconciliation for a caller whose dispatch response was lost, and
// for anyone asking "did this run actually enqueue everything?". It reports what
// is recorded; it never repairs, retries or writes.
export async function handleWorkflowRunLookup(request, env, url) {
  const match = /^\/v1\/workflows\/([A-Za-z][A-Za-z0-9_-]{0,127})$/.exec(url.pathname);
  if (!match || match[1] === "dispatch") return null;
  if (request.method !== "GET") return reply({ error: "method_not_allowed" }, 405);

  const runId = match[1];
  try {
    const run = await readRun(env, runId);
    if (!run) return reply({ error: "run_not_found" }, 404);
    const ids = run.step_ids.map(stepId => taskIdFor(runId, stepId));
    let tasks = [];
    if (ids.length) {
      const placeholders = ids.map(() => "?").join(",");
      const result = await env.DB.prepare(
        `SELECT id,status,attempts,idempotency_key FROM tasks WHERE id IN (${placeholders}) ORDER BY id`
      ).bind(...ids).all();
      tasks = (result?.results || []).map(row => ({
        step_id: ids.includes(row.id) ? run.step_ids[ids.indexOf(row.id)] : null,
        task_id: row.id,
        status: row.status,
        attempts: row.attempts,
        idempotency_key: row.idempotency_key,
      }));
    }
    return reply({ run, steps: tasks, complete: tasks.length === run.step_count }, 200);
  } catch (error) {
    if (isMissingTable(error)) {
      return reply({ error: "plan_dispatch_unavailable", detail: "migration 0012 not applied" }, 404);
    }
    return reply({ error: "storage_unavailable", detail: String(error?.message || "").slice(0, 120) }, 503);
  }
}

export async function handleWorkflowDispatch(request, env, url, options = {}) {
  if (url.pathname !== "/v1/workflows/dispatch") return null;
  if (request.method !== "POST") return reply({ error: "method_not_allowed" }, 405);

  let input;
  try {
    input = await request.json();
  } catch {
    return reply({ error: "invalid_json" }, 400);
  }

  const validated = await validatePlan(input, options);
  if (validated.error) {
    const body = { error: validated.error };
    if (validated.detail) body.detail = validated.detail;
    return reply(body, 400);
  }
  const plan = validated.plan;
  const stepsDigest = await stepsDigestFor(plan.steps);

  try {
    const existing = await readRun(env, plan.run_id);
    if (existing) return replayOf(existing, plan, stepsDigest);
  } catch (error) {
    if (isMissingTable(error)) {
      return reply({ error: "plan_dispatch_unavailable", detail: "migration 0012 not applied" }, 404);
    }
    return reply({ error: "storage_unavailable", detail: String(error?.message || "").slice(0, 120) }, 503);
  }

  const now = new Date().toISOString();
  const statements = [
    env.DB.prepare(
      "INSERT INTO workflow_runs(run_id,workflow_id,plan_digest,steps_digest,plan_version,status,step_count," +
      "entry_step_ids,step_ids,created_at,updated_at) VALUES(?,?,?,?,?,'dispatched',?,?,?,?,?)"
    ).bind(plan.run_id, plan.workflow_id, plan.digest, stepsDigest, plan.plan_version, plan.steps.length,
      JSON.stringify(plan.entry_steps), JSON.stringify(plan.steps.map(step => step.step_id)), now, now),
  ];

  for (const step of plan.steps) {
    statements.push(
      env.DB.prepare(
        "INSERT INTO tasks(id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts," +
        "created_at,updated_at,execution_mode,priority,dependencies_json) VALUES(?,?,?,?,?,'pending',0,?,?,?,'not_started',?,?)"
      ).bind(step.id, step.key, step.task_type, step.title, step.payload_json, step.max_attempts, now, now,
        step.priority, JSON.stringify(step.depends_on.map(dep => taskIdFor(plan.run_id, dep)).sort()))
    );
    statements.push(
      env.DB.prepare(
        "INSERT INTO audit_log(task_id,event,details_json,created_at) " +
        "SELECT id,'workflow_step_dispatched',?,? FROM tasks WHERE id=? AND idempotency_key=?"
      ).bind(JSON.stringify({ run_id: plan.run_id, step_id: step.step_id, plan_digest: plan.digest }),
        now, step.id, step.key)
    );
    for (const dependency of step.depends_on) {
      statements.push(
        env.DB.prepare(
          "INSERT OR IGNORE INTO task_dependencies(task_id,dependency_task_id) VALUES(?,?)"
        ).bind(step.id, taskIdFor(plan.run_id, dependency))
      );
    }
  }

  try {
    // One transaction. Any failure here rolls the whole dispatch back, which is
    // the entire point: a half-enqueued plan is worse than an unenqueued one,
    // because it looks like progress.
    await env.DB.batch(statements);
  } catch (error) {
    // The batch rolled back, so nothing was enqueued. Report the missing
    // capability rather than a generic failure when that is the cause.
    if (isMissingTable(error)) {
      return reply({ error: "plan_dispatch_unavailable", detail: "migration 0012 not applied" }, 404);
    }
    // Two dispatches of the same run can pass the read above at the same moment.
    // The loss is decided by a uniqueness constraint, and the loser rolled back
    // completely — so it reports what the winner actually did rather than a
    // spurious failure that invites a third, identical retry.
    if (isUniqueConflict(error)) {
      try {
        const winner = await readRun(env, plan.run_id);
        if (winner) return replayOf(winner, plan, stepsDigest);
        // No run owns this identity, so the collision is with a task key that
        // already means something else. Fail closed rather than share it.
        return reply({ error: "step_contract_conflict", detail: String(error?.message || "").slice(0, 120) }, 409);
      } catch {
        // fall through
      }
    }
    if (!isDefiniteRollback(error)) {
      return reply({
        error: "dispatch_outcome_unknown",
        detail: "reconcile with GET /v1/workflows/:run_id before retrying; do not create a new run id",
      }, 504);
    }
    return reply({ error: "dispatch_failed", detail: String(error?.message || "").slice(0, 120) }, 503);
  }

  return reply({
    run: {
      run_id: plan.run_id,
      workflow_id: plan.workflow_id,
      plan_digest: plan.digest,
      steps_digest: stepsDigest,
      plan_version: plan.plan_version,
      status: "dispatched",
      step_count: plan.steps.length,
      entry_step_ids: plan.entry_steps,
      step_ids: plan.steps.map(step => step.step_id),
      created_at: now,
    },
    steps: plan.steps.map(step => ({ step_id: step.step_id, task_id: step.id, depends_on: step.depends_on })),
    created: true,
  }, 201);
}
