import { createInstallationToken } from "./github-app-auth.js";

const GITHUB_API = "https://api.github.com";
const GITHUB_API_VERSION = "2026-03-10";
const REPOSITORY = "ghith93/raawaa-autonomous-engine";
const WORKFLOW = "task-runner.yml";
const REF = "main";
const DISPATCH_COOLDOWN_MS = 10 * 60 * 1000;
const ACTIVE_STATUSES = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);

function apiHeaders(token) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: "Bearer " + token,
    "X-GitHub-Api-Version": GITHUB_API_VERSION,
    "User-Agent": "RaawaaAutonomousEngine/1.0",
  };
}

function appConfiguration(env) {
  const names = ["GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "GITHUB_APP_PRIVATE_KEY"];
  return names.filter(name => typeof env?.[name] !== "string" || !env[name].trim());
}

function requireAuditBinding(env) {
  if (!env?.DB || typeof env.DB.prepare !== "function") {
    throw new Error("task_runner_dispatch_audit_unavailable");
  }
}

async function getWorkflowRuns({ fetchImpl, token }) {
  const url = GITHUB_API + "/repos/" + REPOSITORY +
    "/actions/workflows/" + WORKFLOW + "/runs?branch=" + encodeURIComponent(REF) + "&per_page=20";
  let response;
  try {
    response = await fetchImpl(url, { method: "GET", headers: apiHeaders(token) });
  } catch {
    throw new Error("task_runner_run_list_failed");
  }
  if (!response?.ok) {
    const status = Number.isInteger(response?.status) ? response.status : 0;
    throw new Error("task_runner_run_list_failed:" + status);
  }
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("task_runner_run_list_invalid");
  }
  if (!payload || !Array.isArray(payload.workflow_runs)) {
    throw new Error("task_runner_run_list_invalid");
  }
  return payload.workflow_runs;
}

function hasActiveRun(runs) {
  return runs.some(run => ACTIVE_STATUSES.has(run?.status));
}

function hasRecentTaskRunnerRun(runs, nowMs) {
  return runs.some(run => {
    const createdAt = Date.parse(run?.created_at || "");
    return Number.isFinite(createdAt) && nowMs - createdAt <= DISPATCH_COOLDOWN_MS;
  });
}

async function hasRecentDispatchIntent(env, nowMs) {
  requireAuditBinding(env);
  try {
    const recent = await env.DB.prepare(
      "SELECT 1 AS recent FROM audit_log WHERE event = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
    ).bind(
      "task_runner_dispatch_intent",
      new Date(nowMs - DISPATCH_COOLDOWN_MS).toISOString(),
    ).first();
    return Boolean(recent);
  } catch {
    throw new Error("task_runner_dispatch_audit_read_failed");
  }
}

async function reserveDispatchIntent(env, { scheduledTime, nowMs }) {
  requireAuditBinding(env);
  const details = {
    workflow: WORKFLOW,
    ref: REF,
    scheduled_time: Number.isFinite(scheduledTime)
      ? new Date(scheduledTime).toISOString()
      : null,
  };
  try {
    // A single conditional INSERT is the cross-isolate claim: D1 serializes writes,
    // so concurrent Cron invocations cannot both reserve the same cooldown window.
    const result = await env.DB.prepare(
      "INSERT INTO audit_log(task_id,event,details_json,created_at) " +
      "SELECT NULL,?,?,? WHERE NOT EXISTS (" +
      "SELECT 1 FROM audit_log WHERE event = ? AND created_at >= ?)",
    ).bind(
      "task_runner_dispatch_intent",
      JSON.stringify(details),
      new Date(nowMs).toISOString(),
      "task_runner_dispatch_intent",
      new Date(nowMs - DISPATCH_COOLDOWN_MS).toISOString(),
    ).run();
    if (result?.success === false) throw new Error("audit_write_failed");
    if (Number.isInteger(result?.meta?.changes) && result.meta.changes === 0) {
      return false;
    }
    return true;
  } catch {
    throw new Error("task_runner_dispatch_audit_failed");
  }
}

async function recordDispatch(env, { httpStatus, scheduledTime, nowMs }) {
  requireAuditBinding(env);
  const details = {
    workflow: WORKFLOW,
    ref: REF,
    http_status: httpStatus,
    scheduled_time: Number.isFinite(scheduledTime)
      ? new Date(scheduledTime).toISOString()
      : null,
  };
  try {
    const result = await env.DB.prepare(
      "INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(NULL,?,?,?)",
    ).bind(
      "task_runner_dispatch_requested",
      JSON.stringify(details),
      new Date(nowMs).toISOString(),
    ).run();
    if (result?.success === false) throw new Error("audit_write_failed");
  } catch {
    throw new Error("task_runner_dispatch_audit_failed");
  }
}

export async function dispatchTaskRunnerIfStale({
  env,
  fetchImpl = globalThis.fetch,
  cryptoImpl = globalThis.crypto,
  nowMs = Date.now(),
  scheduledTime,
}) {
  if (appConfiguration(env).length) {
    return { status: "disabled", reason: "missing_configuration" };
  }
  requireAuditBinding(env);
  if (typeof fetchImpl !== "function") {
    throw new Error("task_runner_fetch_unavailable");
  }
  if (!Number.isFinite(nowMs)) {
    throw new Error("task_runner_clock_invalid");
  }
  if (await hasRecentDispatchIntent(env, nowMs)) {
    return { status: "skipped", reason: "recent_dispatch_intent" };
  }

  const token = await createInstallationToken({
    appId: env.GITHUB_APP_ID,
    installationId: env.GITHUB_APP_INSTALLATION_ID,
    privateKeyPem: env.GITHUB_APP_PRIVATE_KEY,
    fetchImpl,
    cryptoImpl,
    nowSeconds: Math.floor(nowMs / 1000),
  });
  const runs = await getWorkflowRuns({ fetchImpl, token });

  if (hasActiveRun(runs)) {
    return { status: "skipped", reason: "active_run" };
  }
  if (hasRecentTaskRunnerRun(runs, nowMs)) {
    return { status: "skipped", reason: "recent_task_runner_run" };
  }
  if (!await reserveDispatchIntent(env, { scheduledTime, nowMs })) {
    return { status: "skipped", reason: "recent_dispatch_intent" };
  }

  let response;
  try {
    response = await fetchImpl(
      GITHUB_API + "/repos/" + REPOSITORY +
        "/actions/workflows/" + WORKFLOW + "/dispatches",
      {
        method: "POST",
        headers: {
          ...apiHeaders(token),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: REF }),
      },
    );
  } catch {
    throw new Error("task_runner_dispatch_failed");
  }
  if (![200, 204].includes(response?.status)) {
    const status = Number.isInteger(response?.status) ? response.status : 0;
    throw new Error("task_runner_dispatch_failed:" + status);
  }

  await recordDispatch(env, { httpStatus: response.status, scheduledTime, nowMs });
  return { status: "dispatched", httpStatus: response.status };
}
