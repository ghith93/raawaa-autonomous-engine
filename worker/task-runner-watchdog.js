import { createInstallationToken } from "./github-app-auth.js";

const GITHUB_API = "https://api.github.com";
const GITHUB_API_VERSION = "2026-03-10";
const REPOSITORY = "ghith93/raawaa-autonomous-engine";
const REF = "main";

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

const ACTIVE_STATUSES = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);

/**
 * Workflows the Worker dispatches when GitHub's `schedule` event misses.
 *
 * GitHub documents that scheduled runs "can be delayed during periods of high
 * loads" and that queued jobs may be dropped outright; independent measurement
 * puts a fifteen-minute workflow at roughly 64% of its slots, with gaps of
 * over two hours.
 * The watchdog exists because a missed schedule produces no failure at all.
 *
 * `staleAfterMs` is each workflow's own cadence plus slack, never shorter: the
 * point is to recover a *missed* run, not to add a second schedule on top of a
 * working one. `intentEvent` must be distinct per workflow, otherwise a dispatch
 * of the six-hourly maintenance job would suppress the ten-minute task runner
 * for six hours.
 */
export const WATCHED_WORKFLOWS = Object.freeze([
  {
    workflow: "task-runner.yml",
    label: "task_runner",
    // GitHub cron */5; recover at ten minutes without a run.
    staleAfterMs: 10 * MINUTE,
    intentEvent: "task_runner_dispatch_intent",
    requestedEvent: "task_runner_dispatch_requested",
  },
  {
    workflow: "maintenance.yml",
    label: "maintenance",
    // Cron 17 */6 * * *; a run older than 6h30m means the tick was dropped.
    staleAfterMs: 6 * HOUR + 30 * MINUTE,
    intentEvent: "maintenance_dispatch_intent",
    requestedEvent: "maintenance_dispatch_requested",
  },
  {
    workflow: "research-discovery.yml",
    label: "research_discovery",
    // Cron 31 5 * * *; daily, plus an hour of slack.
    staleAfterMs: 25 * HOUR,
    intentEvent: "research_discovery_dispatch_intent",
    requestedEvent: "research_discovery_dispatch_requested",
  },
  {
    workflow: "cex-public-market-smoke.yml",
    label: "cex_smoke",
    // Cron 17 7 * * *; daily, plus an hour of slack.
    staleAfterMs: 25 * HOUR,
    intentEvent: "cex_smoke_dispatch_intent",
    requestedEvent: "cex_smoke_dispatch_requested",
  },
  {
    workflow: "v3-live-readonly-smoke.yml",
    label: "v3_smoke",
    // Cron 17 4 * * *; daily, plus an hour of slack.
    staleAfterMs: 25 * HOUR,
    intentEvent: "v3_smoke_dispatch_intent",
    requestedEvent: "v3_smoke_dispatch_requested",
  },
  {
    workflow: "groq-connection-check.yml",
    label: "groq_check",
    // Cron 17 10 * * 1; weekly, plus twelve hours of slack.
    staleAfterMs: 7 * DAY + 12 * HOUR,
    intentEvent: "groq_check_dispatch_intent",
    requestedEvent: "groq_check_dispatch_requested",
  },
]);

const TASK_RUNNER_TARGET = WATCHED_WORKFLOWS[0];

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
  if (env?.GITHUB_APP_WATCHDOG_ENABLED !== "enabled") {
    missing.push("GITHUB_APP_WATCHDOG_ENABLED");
  }
  return missing;
}

export function getTaskRunnerWatchdogReadiness(env) {
  const missing = appConfiguration(env);
  return { ready: missing.length === 0, missing };
}

/** What the watchdog would dispatch, for the readiness panel and for operators. */
export function getWatchdogCoverage() {
  return WATCHED_WORKFLOWS.map(target => ({
    workflow: target.workflow,
    stale_after_minutes: Math.round(target.staleAfterMs / MINUTE),
  }));
}

function auditBindingAvailable(env) {
  return Boolean(env?.DB && typeof env.DB.prepare === "function");
}

async function hasRecentDispatchIntent(env, { event, staleAfterMs, nowMs }) {
  const cutoff = new Date(nowMs - staleAfterMs).toISOString();
  try {
    const row = await env.DB.prepare(
      "SELECT 1 AS recent FROM audit_log WHERE event = ? AND created_at >= ? LIMIT 1",
    ).bind(event, cutoff).first();
    return Boolean(row);
  } catch {
    throw new Error("task_runner_dispatch_intent_lookup_failed");
  }
}

async function getWorkflowRuns({ fetchImpl, token, workflow, label }) {
  const url = GITHUB_API + "/repos/" + REPOSITORY +
    "/actions/workflows/" + workflow + "/runs?branch=" + encodeURIComponent(REF) + "&per_page=20";
  let response;
  try {
    response = await fetchImpl(url, {
      method: "GET",
      headers: apiHeaders(token),
    });
  } catch {
    throw new Error(label + "_run_list_failed");
  }
  if (!response?.ok) {
    const status = Number.isInteger(response?.status) ? response.status : 0;
    throw new Error(label + "_run_list_failed:" + status);
  }
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(label + "_run_list_invalid");
  }
  if (!payload || !Array.isArray(payload.workflow_runs)) {
    throw new Error(label + "_run_list_invalid");
  }
  return payload.workflow_runs;
}

function hasActiveRun(runs) {
  return runs.some(run => ACTIVE_STATUSES.has(run?.status));
}

function hasRecentRun(runs, nowMs, staleAfterMs) {
  return runs.some(run => {
    const createdAt = Date.parse(run?.created_at || "");
    return Number.isFinite(createdAt) && nowMs - createdAt <= staleAfterMs;
  });
}

async function reserveDispatchIntent(env, { scheduledTime, nowMs, target }) {
  const nowIso = new Date(nowMs).toISOString();
  const cutoff = new Date(nowMs - target.staleAfterMs).toISOString();
  const details = {
    workflow: target.workflow,
    ref: REF,
    scheduled_time: Number.isFinite(scheduledTime)
      ? new Date(scheduledTime).toISOString()
      : null,
  };
  let result;
  try {
    result = await env.DB.prepare(
      "INSERT INTO audit_log(task_id,event,details_json,created_at) " +
      "SELECT NULL,?,?,? WHERE NOT EXISTS (" +
      "SELECT 1 FROM audit_log WHERE event = ? AND created_at >= ?)",
    ).bind(
      target.intentEvent,
      JSON.stringify(details),
      nowIso,
      target.intentEvent,
      cutoff,
    ).run();
  } catch {
    throw new Error(target.label + "_dispatch_intent_write_failed");
  }
  if (result?.success === false) {
    throw new Error(target.label + "_dispatch_intent_write_failed");
  }
  const changes = result?.meta?.changes;
  if (changes === 0) return false;
  if (changes !== 1) {
    throw new Error(target.label + "_dispatch_intent_write_unverified");
  }
  return true;
}

async function recordDispatch(env, { httpStatus, scheduledTime, nowMs, target }) {
  const details = {
    workflow: target.workflow,
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
      target.requestedEvent,
      JSON.stringify(details),
      new Date(nowMs).toISOString(),
    ).run();
    if (result?.success === false || result?.meta?.changes !== 1) {
      throw new Error("audit_write_failed");
    }
  } catch {
    // The pre-dispatch intent remains durable and suppresses another attempt.
    throw new Error(target.label + "_dispatch_audit_failed");
  }
}

async function recordDispatchFailure(env, { target, error, nowMs }) {
  try {
    await env.DB.prepare(
      "INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(NULL,?,?,?)",
    ).bind(
      target.label + "_dispatch_failed",
      JSON.stringify({ workflow: target.workflow, error: String(error?.message ?? error).slice(0, 200) }),
      new Date(nowMs).toISOString(),
    ).run();
  } catch {
    // Reporting must never turn one failed dispatch into a thrown cron.
  }
}

/**
 * Dispatch one workflow if it is stale, and skip it if it is not.
 *
 * The decision order is deliberate: an active run wins over a stale one, and the
 * durable intent is reserved *before* the dispatch call so a retried cron cannot
 * dispatch twice.
 */
async function dispatchWorkflowIfStale({
  env,
  target,
  fetchImpl = globalThis.fetch,
  cryptoImpl = globalThis.crypto,
  nowMs = Date.now(),
  scheduledTime,
  tokenProvider,
}) {
  if (typeof fetchImpl !== "function") {
    throw new Error(target.label + "_fetch_unavailable");
  }
  if (!Number.isFinite(nowMs)) {
    throw new Error(target.label + "_clock_invalid");
  }

  if (await hasRecentDispatchIntent(env, {
    event: target.intentEvent,
    staleAfterMs: target.staleAfterMs,
    nowMs,
  })) {
    return { workflow: target.workflow, status: "skipped", reason: "recent_dispatch_intent" };
  }

  const token = tokenProvider
    ? await tokenProvider()
    : await createInstallationToken({
      appId: env.GITHUB_APP_ID,
      installationId: env.GITHUB_APP_INSTALLATION_ID,
      privateKeyPem: env.GITHUB_APP_PRIVATE_KEY,
      fetchImpl,
      cryptoImpl,
      nowSeconds: Math.floor(nowMs / 1000),
    });
  const runs = await getWorkflowRuns({
    fetchImpl,
    token,
    workflow: target.workflow,
    label: target.label,
  });

  if (hasActiveRun(runs)) {
    return { workflow: target.workflow, status: "skipped", reason: "active_run" };
  }
  if (hasRecentRun(runs, nowMs, target.staleAfterMs)) {
    return {
      workflow: target.workflow,
      status: "skipped",
      reason: target.label === TASK_RUNNER_TARGET.label
        ? "recent_task_runner_run"
        : "recent_run",
    };
  }

  if (!await reserveDispatchIntent(env, { scheduledTime, nowMs, target })) {
    return { workflow: target.workflow, status: "skipped", reason: "recent_dispatch_intent" };
  }

  let response;
  try {
    response = await fetchImpl(
      GITHUB_API + "/repos/" + REPOSITORY +
        "/actions/workflows/" + target.workflow + "/dispatches",
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
    throw new Error(target.label + "_dispatch_failed");
  }
  if (![200, 204].includes(response?.status)) {
    const status = Number.isInteger(response?.status) ? response.status : 0;
    throw new Error(target.label + "_dispatch_failed:" + status);
  }

  await recordDispatch(env, {
    httpStatus: response.status,
    scheduledTime,
    nowMs,
    target,
  });
  return { workflow: target.workflow, status: "dispatched", httpStatus: response.status };
}

/**
 * Dispatch the task runner if its GitHub schedule has gone quiet.
 *
 * Kept as the single-workflow entry point because the readiness panel and the
 * existing guidance describe it that way; the loop now lives in
 * ``dispatchDueWorkflows``.
 */
export async function dispatchTaskRunnerIfStale({
  env,
  fetchImpl = globalThis.fetch,
  cryptoImpl = globalThis.crypto,
  nowMs = Date.now(),
  scheduledTime,
}) {
  if (!getTaskRunnerWatchdogReadiness(env).ready) {
    return { status: "disabled", reason: "missing_configuration" };
  }
  if (!auditBindingAvailable(env)) {
    throw new Error("task_runner_dispatch_audit_unavailable");
  }

  const result = await dispatchWorkflowIfStale({
    env,
    target: TASK_RUNNER_TARGET,
    fetchImpl,
    cryptoImpl,
    nowMs,
    scheduledTime,
  });
  if (result.status === "dispatched") {
    return { status: "dispatched", httpStatus: result.httpStatus };
  }
  return { status: result.status, reason: result.reason };
}

/**
 * Dispatch every watched workflow whose schedule has gone quiet.
 *
 * One workflow's failure must not stop the others: a broken run-list read for
 * the weekly Groq check is no reason to stop recovering the task runner. Each
 * failure is returned *and* recorded so it is visible rather than silent.
 */
export async function dispatchDueWorkflows({
  env,
  fetchImpl = globalThis.fetch,
  cryptoImpl = globalThis.crypto,
  nowMs = Date.now(),
  scheduledTime,
  targets = WATCHED_WORKFLOWS,
}) {
  const readiness = getTaskRunnerWatchdogReadiness(env);
  if (!readiness.ready) {
    return { status: "disabled", reason: "missing_configuration", missing: readiness.missing, results: [] };
  }
  if (!auditBindingAvailable(env)) {
    throw new Error("task_runner_dispatch_audit_unavailable");
  }
  if (typeof fetchImpl !== "function") {
    throw new Error("task_runner_fetch_unavailable");
  }
  if (!Number.isFinite(nowMs)) {
    throw new Error("task_runner_clock_invalid");
  }

  // One installation token for the whole tick: six dispatches must not mean six
  // token exchanges against the same installation.
  let tokenPromise = null;
  const tokenProvider = () => {
    if (!tokenPromise) {
      tokenPromise = createInstallationToken({
        appId: env.GITHUB_APP_ID,
        installationId: env.GITHUB_APP_INSTALLATION_ID,
        privateKeyPem: env.GITHUB_APP_PRIVATE_KEY,
        fetchImpl,
        cryptoImpl,
        nowSeconds: Math.floor(nowMs / 1000),
      });
    }
    return tokenPromise;
  };

  const results = [];
  for (const target of targets) {
    try {
      results.push(await dispatchWorkflowIfStale({
        env, target, fetchImpl, cryptoImpl, nowMs, scheduledTime, tokenProvider,
      }));
    } catch (error) {
      const message = String(error?.message ?? error).slice(0, 200);
      await recordDispatchFailure(env, { target, error, nowMs });
      results.push({ workflow: target.workflow, status: "failed", error: message });
    }
  }

  const dispatched = results.filter(item => item.status === "dispatched");
  const failed = results.filter(item => item.status === "failed");
  const status = dispatched.length ? "dispatched" : failed.length ? "failed" : "skipped";
  return { status, dispatched: dispatched.length, failed: failed.length, results };
}
