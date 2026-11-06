function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }
  });
}
function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}
function validUuid(value) {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}
async function readBody(request) {
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > 8192) throw new Error("body_too_large");
  try { return JSON.parse(raw); } catch { throw new Error("invalid_json"); }
}
async function leaseOwned(db, taskId, leaseToken, now, workerLane) {
  const task = await db.prepare(
    "SELECT id,status,lease_token,lease_expires_at,execution_lane FROM tasks WHERE id=?"
  ).bind(taskId).first();
  return Boolean(task && task.status === "running" &&
    (task.execution_lane || "general") === workerLane &&
    task.lease_token === leaseToken && task.lease_expires_at && task.lease_expires_at >= now);
}

/**
 * Authenticated, lease-fenced logical model invocation lifecycle.
 * A step identity is unique per task and survives worker retries. Provider routes
 * are attempts under that identity; only explicit rejection permits route rotation.
 */
export async function handleTaskInvocation(request, env, url) {
  const match = url.pathname.match(/^\/v1\/tasks\/([A-Za-z0-9_-]+)\/invocations$/);
  if (!match) return null;
  if (request.method !== "POST") return json({error:"method_not_allowed"},405);
  let input;
  try { input = await readBody(request); }
  catch (error) { return json({error:error.message},400); }
  const taskId = match[1];
  const action = input?.action;
  const leaseToken = input?.lease_token;
  const workerLane = input?.worker_lane || "general";
  if (!validText(leaseToken,200)) return json({error:"invalid_lease"},400);
  const now = new Date().toISOString();
  try {
    if (!(await leaseOwned(env.DB,taskId,leaseToken,now,workerLane))) return json({error:"lease_lost"},409);

    if (action === "prepare") {
      if (!validText(input.step_key,160) || !validText(input.task_kind,100))
        return json({error:"invalid_invocation"},400);
      const invocationId = crypto.randomUUID();
      const requestId = crypto.randomUUID();
      await env.DB.prepare(
        "INSERT OR IGNORE INTO task_model_invocations(invocation_id,task_id,step_key,task_kind,request_id,status,created_at,updated_at) " +
        "SELECT ?,?,?,?,?, 'prepared',?,? WHERE EXISTS (SELECT 1 FROM tasks WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>=?)"
      ).bind(invocationId,taskId,input.step_key.trim(),input.task_kind.trim(),requestId,now,now,taskId,leaseToken,now).run();
      const row = await env.DB.prepare(
        "SELECT invocation_id,task_id,step_key,task_kind,request_id,status,last_route_id FROM task_model_invocations WHERE task_id=? AND step_key=?"
      ).bind(taskId,input.step_key.trim()).first();
      if (!row || row.task_kind !== input.task_kind.trim()) return json({error:"invocation_identity_conflict"},409);
      if (row.status !== "prepared") return json({error:"invocation_replay_blocked",status:row.status},409);
      return json({invocation:row},201);
    }

    if (!validUuid(input.invocation_id)) return json({error:"invalid_invocation_id"},400);
    const invocation = await env.DB.prepare(
      "SELECT * FROM task_model_invocations WHERE invocation_id=? AND task_id=?"
    ).bind(input.invocation_id,taskId).first();
    if (!invocation) return json({error:"invocation_not_found"},404);

    if (action === "start") {
      if (!validText(input.route_id,200)) return json({error:"invalid_route"},400);
      if (invocation.status !== "prepared") return json({error:"invocation_replay_blocked",status:invocation.status},409);
      const batch = await env.DB.batch([
        env.DB.prepare(
          "INSERT INTO task_model_invocation_attempts(invocation_id,route_id,status,created_at,updated_at) " +
          "SELECT ?,?,'in_flight',?,? WHERE EXISTS (SELECT 1 FROM task_model_invocations WHERE invocation_id=? AND task_id=? AND status='prepared') " +
          "AND EXISTS (SELECT 1 FROM tasks WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>=?)"
        ).bind(invocation.invocation_id,input.route_id,now,now,invocation.invocation_id,taskId,taskId,leaseToken,now),
        env.DB.prepare(
          "UPDATE task_model_invocations SET status='in_flight',last_route_id=?,started_at=?,updated_at=? " +
          "WHERE invocation_id=? AND task_id=? AND status='prepared' " +
          "AND EXISTS (SELECT 1 FROM task_model_invocation_attempts WHERE invocation_id=? AND route_id=? AND status='in_flight' AND created_at=?) " +
          "AND EXISTS (SELECT 1 FROM tasks WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>=?)"
        ).bind(input.route_id,now,now,invocation.invocation_id,taskId,invocation.invocation_id,input.route_id,now,taskId,leaseToken,now),
      ]);
      if (batch?.[0]?.meta?.changes !== 1 || batch?.[1]?.meta?.changes !== 1)
        return json({error:"invocation_state_conflict"},409);
      return json({ok:true,status:"in_flight"});
    }

    if (action === "reject") {
      if (!validText(input.route_id,200) || !validText(input.rejection_code,80))
        return json({error:"invalid_rejection"},400);
      if (invocation.status === "prepared") {
        const prior = await env.DB.prepare(
          "SELECT status,rejection_code FROM task_model_invocation_attempts WHERE invocation_id=? AND route_id=?"
        ).bind(invocation.invocation_id,input.route_id).first();
        if (prior?.status === "rejected" && prior.rejection_code === input.rejection_code)
          return json({ok:true,status:"prepared",replay:true});
      }
      if (invocation.status !== "in_flight" || invocation.last_route_id !== input.route_id)
        return json({error:"invocation_state_conflict"},409);
      const batch = await env.DB.batch([
        env.DB.prepare(
          "UPDATE task_model_invocation_attempts SET status='rejected',rejection_code=?,updated_at=? " +
          "WHERE invocation_id=? AND route_id=? AND status='in_flight' " +
          "AND EXISTS (SELECT 1 FROM tasks WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>=?)"
        ).bind(input.rejection_code,now,invocation.invocation_id,input.route_id,taskId,leaseToken,now),
        env.DB.prepare(
          "UPDATE task_model_invocations SET status='prepared',last_route_id=NULL,updated_at=? " +
          "WHERE invocation_id=? AND status='in_flight' AND last_route_id=? " +
          "AND EXISTS (SELECT 1 FROM task_model_invocation_attempts WHERE invocation_id=? AND route_id=? AND status='rejected') " +
          "AND EXISTS (SELECT 1 FROM tasks WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>=?)"
        ).bind(now,invocation.invocation_id,input.route_id,invocation.invocation_id,input.route_id,taskId,leaseToken,now),
      ]);
      if (batch?.[0]?.meta?.changes !== 1 || batch?.[1]?.meta?.changes !== 1)
        return json({error:"invocation_state_conflict"},409);
      return json({ok:true,status:"prepared"});
    }

    if (action === "ambiguous" || action === "settle") {
      const terminal = action === "ambiguous" ? "ambiguous" : "settled";
      if (invocation.status === terminal) {
        const prior = await env.DB.prepare(
          "SELECT status FROM task_model_invocation_attempts WHERE invocation_id=? AND route_id=?"
        ).bind(invocation.invocation_id,invocation.last_route_id).first();
        if (prior?.status === terminal) return json({ok:true,status:terminal,replay:true});
      }
      if (invocation.status !== "in_flight" || !invocation.last_route_id)
        return json({error:"invocation_state_conflict"},409);
      const batch = await env.DB.batch([
        env.DB.prepare(
          "UPDATE task_model_invocation_attempts SET status=?,updated_at=? WHERE invocation_id=? AND route_id=? AND status='in_flight' " +
          "AND EXISTS (SELECT 1 FROM tasks WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>=?)"
        ).bind(terminal,now,invocation.invocation_id,invocation.last_route_id,taskId,leaseToken,now),
        env.DB.prepare(
          "UPDATE task_model_invocations SET status=?,settled_at=?,updated_at=? WHERE invocation_id=? AND task_id=? AND status='in_flight' " +
          "AND EXISTS (SELECT 1 FROM task_model_invocation_attempts WHERE invocation_id=? AND route_id=? AND status=?) " +
          "AND EXISTS (SELECT 1 FROM tasks WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>=?)"
        ).bind(terminal,terminal === "settled" ? now : null,now,invocation.invocation_id,taskId,invocation.invocation_id,invocation.last_route_id,terminal,taskId,leaseToken,now),
      ]);
      if (batch?.[0]?.meta?.changes !== 1 || batch?.[1]?.meta?.changes !== 1)
        return json({error:"invocation_state_conflict"},409);
      return json({ok:true,status:terminal});
    }
    return json({error:"unsupported_action"},400);
  } catch {
    return json({error:"storage_unavailable"},503);
  }
}
