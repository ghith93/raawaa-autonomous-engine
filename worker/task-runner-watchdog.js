import { createInstallationToken } from "./github-app-auth.js";

const GITHUB_API = "https://api.github.com";
const GITHUB_API_VERSION = "2026-03-10";
const REPOSITORY = "ghith93/raawaa-autonomous-engine";
const WORKFLOW = "task-runner.yml";
const REF = "main";
const SCHEDULE_STALE_AFTER_MS = 10 * 60 * 1000;
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
  const missing = names.filter(name => typeof env?.[name] !== "string" || !env[name].trim());
  return missing;
}

async function getWorkflowRuns({ fetchImpl, token }) {
  const url = GITHUB_API + "/repos/" + REPOSITORY +
    "/actions/workflows/" + WORKFLOW + "/runs?branch=" + encodeURIComponent(REF) + "&per_page=20";
  let response;
  try {
    response = await fetchImpl(url, {
      method: "GET",
      headers: apiHeaders(token),
    });
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

function hasRecentScheduledRun(runs, nowMs) {
  const scheduledRuns = runs
    .filter(run => run?.event === "schedule")
    .sort((left, right) => {
      const leftTime = Date.parse(left?.created_at || "");
      const rightTime = Date.parse(right?.created_at || "");
      return (Number.isFinite(rightTime) ? rightTime : -Infinity) -
        (Number.isFinite(leftTime) ? leftTime : -Infinity);
    });
  if (!scheduledRuns.length) return false;
  const createdAt = Date.parse(scheduledRuns[0]?.created_at || "");
  return Number.isFinite(createdAt) && nowMs - createdAt <= SCHEDULE_STALE_AFTER_MS;
}

async function recordDispatch(env, { httpStatus, scheduledTime, nowMs }) {
  if (!env?.DB || typeof env.DB.prepare !== "function") {
    throw new Error("task_runner_dispatch_audit_unavailable");
  }
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
  if (typeof fetchImpl !== "function") {
    throw new Error("task_runner_fetch_unavailable");
  }
  if (!Number.isFinite(nowMs)) {
    throw new Error("task_runner_clock_invalid");
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
  if (hasRecentScheduledRun(runs, nowMs)) {
    return { status: "skipped", reason: "recent_schedule" };
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

  await recordDispatch(env, {
    httpStatus: response.status,
    scheduledTime,
    nowMs,
  });
  return { status: "dispatched", httpStatus: response.status };
}
