import { handleQuota } from "./quota.js";
import { handleMonetaryLedger } from "./monetary-ledger.js";
import { handlePaperLedger } from "./paper-ledger.js";
import { handleTaskInvocation } from "./task-invocations.js";
import { handleTaskProcessDispatch } from "./task-process-dispatches.js";
import { handleWorkflowDispatch, handleWorkflowRunLookup } from "./workflow-dispatch.js";
import { handleMobilePage, handleMobileStatus } from "./mobile-status.js";
import { dispatchDueWorkflows } from "./task-runner-watchdog.js";
const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const MAX_BODY_BYTES = 32 * 1024;

// A storage failure must travel to the authenticated caller with a bounded
// reason. Swallowing it made the 2026-11-04 claim outage ("HTTP 503" and
// nothing else) indistinguishable from any other unavailability, and D1 was
// never told to anyone. The reason is truncated and flattened; no SQL text of
// the request, headers, or credentials are included.
const MAX_ERROR_DETAIL = 240;
function storageDetail(error) {
  const text = error && error.message ? String(error.message) : String(error ?? "unknown");
  return text.replace(/\s+/g, " ").trim().slice(0, MAX_ERROR_DETAIL);
}

// Tables the Worker's own SQL depends on. A deploy that ships code referencing a
// table no migration created is invisible until the first write fails: on
// 2026-11-04 the claim route joined task_dependencies eight hours before that
// table existed, and the queue answered 503 while every read stayed healthy.
const REQUIRED_SCHEMA_TABLES = Object.freeze([
  "tasks",
  "task_dependencies",
  "audit_log",
  "schema_migrations",
  "monetary_reservations",
  "task_model_invocations",
  "task_process_dispatches",
  "workflow_runs",
]);

async function schemaProbe(env) {
  const missing = [];
  const reasons = {};
  for (const table of REQUIRED_SCHEMA_TABLES) {
    try {
      await env.DB.prepare(`SELECT 1 FROM ${table} LIMIT 1`).all();
    } catch (error) {
      missing.push(table);
      reasons[table] = storageDetail(error);
    }
  }
  try {
    await env.DB.prepare("SELECT execution_lane FROM tasks LIMIT 1").all();
  } catch (error) {
    missing.push("tasks.execution_lane");
    reasons["tasks.execution_lane"] = storageDetail(error);
  }
  return {
    ok: missing.length === 0,
    checked: [...REQUIRED_SCHEMA_TABLES, "tasks.execution_lane"],
    missing,
    reasons,
  };
}
// Release remains hard-disabled until issue #440 adversarial sandbox criteria pass.
const CODESPACE_EXECUTOR_RELEASE_ENABLED = false;
const CODESPACE_TASK_TYPES = new Set(["codespace_inspect", "codespace_test"]);
const CODESPACE_TASK_SQL = "'codespace_inspect','codespace_test'";

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function bearer(request) {
  const value = request.headers.get("authorization") || "";
  return value.startsWith("Bearer ") ? value.slice(7) : "";
}

function constantTimeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !a || !b) return false;
  let diff = a.length ^ b.length;
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i++) {
    diff |= (a.charCodeAt(i % a.length) || 0) ^ (b.charCodeAt(i % b.length) || 0);
  }
  return diff === 0;
}

async function authorized(request, env) {
  return Boolean(env.ENGINE_API_TOKEN) &&
    constantTimeEqual(bearer(request), env.ENGINE_API_TOKEN);
}

async function readJson(request) {
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > MAX_BODY_BYTES) throw new Error("body_too_large");
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) throw new Error("body_too_large");
  try { return JSON.parse(raw); } catch { throw new Error("invalid_json"); }
}

async function readOptionalJson(request) {
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > MAX_BODY_BYTES) throw new Error("body_too_large");
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) throw new Error("body_too_large");
  if (!raw.trim()) return {};
  try { return JSON.parse(raw); } catch { throw new Error("invalid_json"); }
}


function safeResearchString(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}

function normalizeResearchCandidate(item) {
  if (!item || typeof item !== "object" || Array.isArray(item)) return null;
  const now = new Date().toISOString();
  if (item.source === "github") {
    const fullName = item.full_name;
    if (!safeResearchString(fullName, 200) || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(fullName)) return null;
    let parsed;
    try { parsed = new URL(item.url); } catch { return null; }
    if (parsed.protocol !== "https:" || parsed.hostname !== "github.com" || parsed.pathname.replace(/\/$/, "") !== "/" + fullName || parsed.search || parsed.hash) return null;
    const metadata = {
      description: typeof item.description === "string" ? item.description.slice(0, 1000) : "",
      stars: Number.isInteger(item.stars) && item.stars >= 0 ? Math.min(item.stars, 1000000000) : 0,
      language: typeof item.language === "string" ? item.language.slice(0, 100) : null,
      license_spdx: typeof item.license_spdx === "string" ? item.license_spdx.slice(0, 100) : null,
      pushed_at: typeof item.pushed_at === "string" ? item.pushed_at.slice(0, 40) : null,
      archived: item.archived === true,
      topics: Array.isArray(item.topics) ? item.topics.filter(x => typeof x === "string").slice(0, 20).map(x => x.slice(0, 100)) : [],
      discovered_via: Array.isArray(item.discovered_via) ? item.discovered_via.filter(x => typeof x === "string").slice(0, 10).map(x => x.slice(0, 100)) : [],
    };
    return { source_key: "github:" + fullName.toLowerCase(), source: "github", title: fullName, url: "https://github.com/" + fullName, metadata_json: JSON.stringify(metadata), now };
  }
  if (item.source === "crossref") {
    const doi = item.doi;
    if (!safeResearchString(doi, 250) || !/^10\.\d{4,9}\/[A-Za-z0-9._;()/:+-]+$/i.test(doi)) return null;
    if (!safeResearchString(item.title, 1000)) return null;
    const metadata = {
      authors: Array.isArray(item.authors) ? item.authors.filter(x => typeof x === "string").slice(0, 5).map(x => x.slice(0, 200)) : [],
      published: typeof item.published === "string" ? item.published.slice(0, 30) : null,
      container_title: typeof item.container_title === "string" ? item.container_title.slice(0, 300) : null,
      citation_count: Number.isInteger(item.citation_count) && item.citation_count >= 0 ? Math.min(item.citation_count, 1000000000) : 0,
      license_urls: Array.isArray(item.license_urls) ? item.license_urls.filter(x => typeof x === "string" && x.startsWith("https://")).slice(0, 3).map(x => x.slice(0, 300)) : [],
      work_type: typeof item.work_type === "string" ? item.work_type.slice(0, 100) : null,
      abstract_retrieved: false,
    };
    return { source_key: "crossref:" + doi.toLowerCase(), source: "crossref", title: item.title, url: "https://doi.org/" + doi, metadata_json: JSON.stringify(metadata), now };
  }
  if (item.source === "arxiv") {
    const arxivId = item.arxiv_id;
    const validId = typeof arxivId === "string"
      && /^(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?\/\d{7}(?:v\d+)?)$/i.test(arxivId);
    if (!validId || !safeResearchString(item.title, 1000)) return null;
    let parsed;
    try { parsed = new URL(item.url); } catch { return null; }
    if (parsed.protocol !== "https:" || parsed.hostname !== "arxiv.org"
        || parsed.pathname !== "/abs/" + arxivId || parsed.search || parsed.hash || parsed.port
        || parsed.username || parsed.password) return null;
    const metadata = {
      authors: Array.isArray(item.authors) ? item.authors.filter(x => typeof x === "string").slice(0, 8).map(x => x.slice(0, 200)) : [],
      published: typeof item.published === "string" ? item.published.slice(0, 40) : null,
      updated: typeof item.updated === "string" ? item.updated.slice(0, 40) : null,
      categories: Array.isArray(item.categories) ? item.categories.filter(x => typeof x === "string").slice(0, 8).map(x => x.slice(0, 80)) : [],
      primary_category: typeof item.primary_category === "string" ? item.primary_category.slice(0, 80) : null,
      doi: typeof item.doi === "string" ? item.doi.slice(0, 160) : null,
      abstract_retrieved: false,
      full_text_retrieved: false,
      license_verified: false,
    };
    return { source_key: "arxiv:" + arxivId.toLowerCase(), source: "arxiv", title: item.title, url: "https://arxiv.org/abs/" + arxivId, metadata_json: JSON.stringify(metadata), now };
  }
  if (item.source === "searxng") {
    if (!safeResearchString(item.title, 1000) || !safeResearchString(item.url, 1000)) return null;
    let parsed;
    try { parsed = new URL(item.url); } catch { return null; }
    if (parsed.protocol !== "https:" || !parsed.hostname || parsed.username || parsed.password
        || parsed.hash || parsed.hostname === "localhost"
        || parsed.hostname.endsWith(".local") || parsed.hostname.endsWith(".internal")
        || parsed.hostname.endsWith(".localhost")) return null;
    const host = parsed.hostname.toLowerCase();
    // Research links are stored, never fetched; reject all IP-literal hosts conservatively.
    if (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(host) || host.includes(":")) return null;
    const sourceKey = "searxng:" + item.url.slice(0, 900);
    const metadata = {
      snippet: typeof item.snippet === "string" ? item.snippet.slice(0, 500) : "",
      engines: Array.isArray(item.engines) ? item.engines.filter(x => typeof x === "string").slice(0, 5).map(x => x.slice(0, 80)) : [],
      category: typeof item.category === "string" ? item.category.slice(0, 80) : null,
      license_verified: false,
      content_fetched: false,
    };
    return { source_key: sourceKey, source: "searxng", title: item.title.slice(0, 1000), url: item.url, metadata_json: JSON.stringify(metadata), now };
  }
  return null;
}

async function listResearchCandidates(url, env) {
  const rawLimit = Number(url.searchParams.get("limit") || 50);
  const status = url.searchParams.get("status") || "all";
  const allowed = ["all", "unreviewed", "accepted", "rejected", "deferred"];
  const metadataFlag = url.searchParams.get("include_metadata") || "false";
  const keysOnlyFlag = url.searchParams.get("keys_only") || "false";
  if (!Number.isInteger(rawLimit) || rawLimit < 1 || rawLimit > 100 || !allowed.includes(status) || !["true", "false"].includes(metadataFlag) || !["true", "false"].includes(keysOnlyFlag) || (metadataFlag === "true" && keysOnlyFlag === "true")) return json({ error: "invalid_query" }, 400);
  const columns = keysOnlyFlag === "true" ? "source_key" : metadataFlag === "true"
    ? "source_key,source,title,url,metadata_json,review_status,review_note,first_seen_at,last_seen_at,reviewed_at,discovery_count,updated_at"
    : "source_key,source,title,url,review_status,review_note,first_seen_at,last_seen_at,reviewed_at,discovery_count,updated_at";
  try {
    const result = status === "all"
      ? await env.DB.prepare("SELECT " + columns + " FROM research_candidates ORDER BY last_seen_at DESC LIMIT ?").bind(rawLimit).all()
      : await env.DB.prepare("SELECT " + columns + " FROM research_candidates WHERE review_status=? ORDER BY last_seen_at DESC LIMIT ?").bind(status, rawLimit).all();
    return json({ candidates: result.results || [] });
  } catch { return json({ error: "storage_unavailable" }, 503); }
}

async function ingestResearchCandidates(request, env) {
  let input;
  try { input = await readJson(request); }
  catch (error) { return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" }, 400); }
  if (!Array.isArray(input?.candidates) || input.candidates.length < 1 || input.candidates.length > 25) return json({ error: "invalid_candidates" }, 400);
  const normalized = input.candidates.map(normalizeResearchCandidate);
  if (normalized.some(item => item === null)) return json({ error: "invalid_candidate" }, 400);
  const statements = normalized.map(item => env.DB.prepare("INSERT INTO research_candidates(source_key,source,title,url,metadata_json,review_status,first_seen_at,last_seen_at,discovery_count,updated_at) VALUES(?,?,?,?,?,'unreviewed',?,?,1,?) ON CONFLICT(source_key) DO UPDATE SET title=excluded.title,url=excluded.url,metadata_json=excluded.metadata_json,last_seen_at=excluded.last_seen_at,discovery_count=research_candidates.discovery_count+1,updated_at=excluded.updated_at").bind(item.source_key, item.source, item.title, item.url, item.metadata_json, item.now, item.now, item.now));
  const sources = [...new Set(normalized.map(item => item.source))];
  statements.push(env.DB.prepare("INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(NULL,?,?,?)").bind("research_candidates_ingested", JSON.stringify({ count: normalized.length, sources }), new Date().toISOString()));
  try {
    await env.DB.batch(statements);
    return json({ accepted_count: normalized.length, sources }, 201);
  } catch { return json({ error: "storage_unavailable" }, 503); }
}

async function reviewResearchCandidate(request, env) {
  let input;
  try { input = await readJson(request); }
  catch (error) { return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" }, 400); }
  const sourceKey = input?.source_key;
  const status = input?.review_status;
  const note = input?.note === undefined ? "" : input.note;
  if (!safeResearchString(sourceKey, 300) || !["accepted", "rejected", "deferred"].includes(status) || typeof note !== "string" || note.length > 1000) return json({ error: "invalid_review" }, 400);
  const now = new Date().toISOString();
  try {
    const result = await env.DB.prepare("UPDATE research_candidates SET review_status=?,review_note=?,reviewed_at=?,updated_at=? WHERE source_key=?").bind(status, note, now, now, sourceKey).run();
    if (!result.meta?.changes) return json({ error: "candidate_not_found" }, 404);
    await env.DB.prepare("INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(NULL,?,?,?)").bind("research_candidate_reviewed", JSON.stringify({ source_key: sourceKey, review_status: status, note_length: note.length }), now).run();
    return json({ ok: true, source_key: sourceKey, review_status: status, reviewed_at: now });
  } catch { return json({ error: "storage_unavailable" }, 503); }
}

async function recoverStaleTasks(env, workerLane = null) {
  const nowIso = new Date().toISOString();
  const laneFilter = workerLane === null ? "" : " AND execution_lane=?";
  const staleStatement = env.DB.prepare(
    "SELECT id,attempts,max_attempts,lease_token FROM tasks WHERE status='running' AND (lease_expires_at IS NULL OR lease_expires_at < ?)" +
    laneFilter + " ORDER BY updated_at,id LIMIT 100"
  );
  const stale = await (workerLane === null ? staleStatement.bind(nowIso) :
    staleStatement.bind(nowIso, workerLane)).all();
  const statements = [];
  for (const task of stale.results || []) {
    const status = task.attempts < task.max_attempts ? "pending" : "failed";
    statements.push(env.DB.prepare(
      "UPDATE tasks SET status=?,error=?,updated_at=?,lease_token=NULL,lease_expires_at=NULL WHERE id=? AND status='running' AND lease_token IS ? AND (lease_expires_at IS NULL OR lease_expires_at < ?)"
    ).bind(status, "Recovered expired worker lease.", nowIso, task.id, task.lease_token, nowIso));
    statements.push(env.DB.prepare(
      "INSERT INTO audit_log(task_id,event,details_json,created_at) SELECT id,'task_lease_recovered',?,? FROM tasks WHERE id=? AND status=? AND updated_at=?"
    ).bind(JSON.stringify({ new_status: status, attempt: task.attempts, max_attempts: task.max_attempts }), nowIso, task.id, status, nowIso));
  }
  if (statements.length) await env.DB.batch(statements);
  const requeued = (stale.results || []).filter(t => t.attempts < t.max_attempts).length;
  const failed = (stale.results || []).length - requeued;
  return { requeued, failed };
}

const EXECUTION_LANES = new Set(["general", "specialist:researcher", "specialist:reviewer", "specialist:marketer", "specialist:operator", "specialist:mobile_tester"]);
const SPECIALIST_TASK_LANES = {
  research_brief: "specialist:researcher",
  code_review: "specialist:reviewer",
  content_draft: "specialist:marketer",
  system_audit: "specialist:operator",
  mobile_test: "specialist:mobile_tester",
};
const SPECIALIST_LANE_TOKEN_BINDINGS = {
  "specialist:researcher": "ENGINE_RESEARCHER_LANE_TOKEN",
  "specialist:reviewer": "ENGINE_REVIEWER_LANE_TOKEN",
  "specialist:marketer": "ENGINE_MARKETER_LANE_TOKEN",
  "specialist:operator": "ENGINE_OPERATOR_LANE_TOKEN",
  "specialist:mobile_tester": "ENGINE_MOBILE_TESTER_LANE_TOKEN",
};

function laneTokenAuthorized(request, env, lane) {
  if (lane === "general") return true;
  const binding = SPECIALIST_LANE_TOKEN_BINDINGS[lane];
  const expected = binding ? env[binding] : "";
  const supplied = request.headers.get("x-engine-lane-token") || "";
  const reusedByAnotherLane = Object.entries(SPECIALIST_LANE_TOKEN_BINDINGS).some(
    ([otherLane, otherBinding]) => otherLane !== lane &&
      typeof env[otherBinding] === "string" && env[otherBinding] === expected
  );
  return typeof expected === "string" && expected.length >= 32 &&
    expected !== env.ENGINE_API_TOKEN && !reusedByAnotherLane &&
    constantTimeEqual(supplied, expected);
}

function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}

export default {
  async scheduled(controller, env) {
    const cron = typeof controller?.cron === "string" ? controller.cron : "";
    if (cron === "1,6,11,16,21,26,31,36,41,46,51,56 * * * *") {
      // Every watched workflow, not only the task runner: a dropped GitHub
      // schedule produces no failure notification, so silence is the symptom.
      await dispatchDueWorkflows({
        env,
        scheduledTime: controller?.scheduledTime,
      });
      return;
    }
    if (cron !== "*/15 * * * *") return;

    const recovery = await recoverStaleTasks(env);
    const occurredAt = new Date().toISOString();
    const heartbeat = await env.DB.prepare(
      "INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(NULL,?,?,?)"
    ).bind("worker_cron_tick", JSON.stringify({
      cron,
      requeued: recovery.requeued,
      failed: recovery.failed,
    }), occurredAt).run();
    if (heartbeat?.success === false) {
      throw new Error("Worker Cron heartbeat insert failed");
    }
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    const mobilePage = handleMobilePage(request, url);
    if (mobilePage) return mobilePage;
    if (request.method === "GET" && url.pathname === "/health") {
      try {
        const row = await env.DB.prepare("SELECT 1 AS ok").first();
        if (!row || row.ok !== 1) return json({ ok: false }, 503);
        return json({ ok: true });
      } catch {
        return json({ ok: false }, 503);
      }
    }



    if (url.pathname !== "/v1/tasks" && url.pathname !== "/v1/audit" &&
        url.pathname !== "/v1/tasks/enqueue" &&
        url.pathname !== "/v1/tasks/claim" && url.pathname !== "/v1/codespace/status" &&
        url.pathname !== "/v1/codespace/tasks/claim" &&
        !/^\/v1\/codespace\/tasks\/[A-Za-z0-9-]+\/(heartbeat|finish)$/.test(url.pathname) &&
        url.pathname !== "/v1/tasks/recover" &&
        url.pathname !== "/v1/diagnostics/auth" &&
        url.pathname !== "/v1/diagnostics/schema" &&
        url.pathname !== "/v1/research/candidates" &&
        url.pathname !== "/v1/quota" &&
        url.pathname !== "/v1/monetary-ledger" &&
        url.pathname !== "/v1/paper-ledger" &&
        url.pathname !== "/v1/mobile/status" &&
        url.pathname !== "/v1/workflows/dispatch" &&
        !/^\/v1\/workflows\/[A-Za-z][A-Za-z0-9_-]{0,127}$/.test(url.pathname) &&
        !/^\/v1\/tasks\/[A-Za-z0-9_-]+\/invocations$/.test(url.pathname) &&
        !/^\/v1\/tasks\/[A-Za-z0-9_-]+\/process-dispatches$/.test(url.pathname) &&
        !/^\/v1\/tasks\/[A-Za-z0-9_-]{1,128}\/claim-supervised-probe$/.test(url.pathname) &&
        !/^\/v1\/tasks\/[A-Za-z0-9_-]{1,128}\/recover-supervised-probe$/.test(url.pathname) &&
        url.pathname !== "/v1/research/candidates/review" &&
        !/^\/v1\/tasks\/[A-Za-z0-9_-]+\/(heartbeat|finish|retry)$/.test(url.pathname)) {
      return json({ error: "not_found" }, 404);
    }
    const codespaceClaimRoute = url.pathname === "/v1/codespace/tasks/claim";
    const codespaceStatusRoute = url.pathname === "/v1/codespace/status";
    const codespaceActionRoute = /^\/v1\/codespace\/tasks\/[A-Za-z0-9-]+\/(heartbeat|finish)$/.test(url.pathname);
    const codespaceRoute = codespaceClaimRoute || codespaceStatusRoute || codespaceActionRoute;
    if (codespaceRoute && !CODESPACE_EXECUTOR_RELEASE_ENABLED) {
      return json({ error: "codespace_executor_disabled" }, 503);
    }
    const codespaceAuthorized = typeof env.ENGINE_CODESPACE_TOKEN === "string" &&
      env.ENGINE_CODESPACE_TOKEN.length >= 32 &&
      env.ENGINE_CODESPACE_TOKEN !== env.ENGINE_API_TOKEN &&
      env.ENGINE_CODESPACE_TOKEN !== env.ENGINE_QUOTA_ADMIN_TOKEN &&
      constantTimeEqual(bearer(request), env.ENGINE_CODESPACE_TOKEN);
    const quotaAdminAccess = url.pathname === "/v1/quota" &&
      Boolean(env.ENGINE_QUOTA_ADMIN_TOKEN) && env.ENGINE_QUOTA_ADMIN_TOKEN !== env.ENGINE_API_TOKEN &&
      constantTimeEqual(bearer(request), env.ENGINE_QUOTA_ADMIN_TOKEN);
    if (codespaceRoute ? !codespaceAuthorized : (!(await authorized(request, env)) && !quotaAdminAccess)) {
      return json({ error: "unauthorized" }, 401);
    }
    if (codespaceStatusRoute) {
      if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
      return json({ ok: true, executor: "codespace", capabilities: ["inspect", "test"],
        task_types: [...CODESPACE_TASK_TYPES], max_lease_seconds: 900 });
    }

    // Only reachable after the bearer guard above: dispatch enqueues tasks, so it
    // must sit behind the same authentication as /v1/tasks/enqueue. It records
    // the run and its steps in one D1 batch and executes nothing.
    const runLookupResponse = await handleWorkflowRunLookup(request, env, url);
    if (runLookupResponse) return runLookupResponse;

    const dispatchResponse = await handleWorkflowDispatch(request, env, url, {
      codespaceTaskTypes: CODESPACE_TASK_TYPES,
      executorEnabled: CODESPACE_EXECUTOR_RELEASE_ENABLED,
    });
    if (dispatchResponse) return dispatchResponse;

    const mobileStatusResponse = await handleMobileStatus(request, env, url);
    if (mobileStatusResponse) return mobileStatusResponse;

    const quotaResponse = await handleQuota(request, env, url);
    if (quotaResponse) return quotaResponse;
    const monetaryResponse = await handleMonetaryLedger(request, env, url);
    if (monetaryResponse) return monetaryResponse;
    const paperLedgerResponse = await handlePaperLedger(request, env, url);
    if (paperLedgerResponse) return paperLedgerResponse;
    const processDispatchResponse = await handleTaskProcessDispatch(request, env, url);
    if (processDispatchResponse) return processDispatchResponse;
    const invocationResponse = await handleTaskInvocation(request, env, url);
    if (invocationResponse) return invocationResponse;

    if (url.pathname === "/v1/research/candidates" && request.method === "GET") return listResearchCandidates(url, env);
    if (url.pathname === "/v1/research/candidates" && request.method === "POST") return ingestResearchCandidates(request, env);
    if (url.pathname === "/v1/research/candidates/review" && request.method === "POST") return reviewResearchCandidate(request, env);

    // Authenticated, read-only POST probe used to distinguish application auth
    // from edge blocks before the runner mutates task state.
    if (url.pathname === "/v1/diagnostics/auth") {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      return json({ ok: true, authenticated: true });
    }

    if (url.pathname === "/v1/diagnostics/schema") {
      if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
      const probe = await schemaProbe(env);
      return json(probe, probe.ok ? 200 : 503);
    }

    if (request.method === "GET" && url.pathname === "/v1/tasks") {
      const rawLimit = Number(url.searchParams.get("limit") || 50);
      if (!Number.isInteger(rawLimit) || rawLimit < 1 || rawLimit > 100) {
        return json({ error: "invalid_limit" }, 400);
      }
      try {
        const result = await env.DB.prepare(
          "SELECT id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,created_at,updated_at,execution_mode,priority,dependencies_json,execution_lane FROM tasks ORDER BY created_at DESC,id DESC LIMIT ?"
        ).bind(rawLimit).all();
        const tasks = (result.results || []).map(task => {
          let dependsOn = [];
          try {
            const parsed = JSON.parse(task.dependencies_json || "[]");
            if (Array.isArray(parsed)) dependsOn = parsed;
          } catch { /* malformed legacy metadata stays empty and is not executable */ }
          return { ...task, depends_on: dependsOn };
        });
        return json({ tasks });
      } catch {
        return json({ error: "storage_unavailable" }, 503);
      }
    }

    // Authenticated, bounded read-back of the audit ledger. The task list
    // already exposes payload/result JSON to the same bearer, so returning
    // parsed audit details here adds no new sensitivity class; it makes
    // claim/finish/recovery evidence independently inspectable (P1 gap).
    if (url.pathname === "/v1/audit") {
      if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
      const rawLimit = Number(url.searchParams.get("limit") || 50);
      if (!Number.isInteger(rawLimit) || rawLimit < 1 || rawLimit > 100) {
        return json({ error: "invalid_limit" }, 400);
      }
      const taskId = url.searchParams.get("task_id");
      if (taskId !== null && !/^[A-Za-z0-9_-]{1,128}$/.test(taskId)) {
        return json({ error: "invalid_task_id" }, 400);
      }
      try {
        const statement = taskId === null
          ? env.DB.prepare(
              "SELECT id,task_id,event,details_json,created_at FROM audit_log ORDER BY id DESC LIMIT ?"
            ).bind(rawLimit)
          : env.DB.prepare(
              "SELECT id,task_id,event,details_json,created_at FROM audit_log WHERE task_id=? ORDER BY id DESC LIMIT ?"
            ).bind(taskId, rawLimit);
        const result = await statement.all();
        const events = (result.results || []).map(row => {
          let details = null;
          try { details = JSON.parse(row.details_json); } catch { /* malformed legacy rows stay null */ }
          return { id: row.id, task_id: row.task_id, event: row.event, details, created_at: row.created_at };
        });
        return json({ events });
      } catch {
        return json({ error: "storage_unavailable" }, 503);
      }
    }

    if (request.method === "POST" && url.pathname === "/v1/tasks/recover") {
      let recoveryInput;
      try { recoveryInput = await readOptionalJson(request); }
      catch (error) {
        return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" }, 400);
      }
      if (!recoveryInput || typeof recoveryInput !== "object" || Array.isArray(recoveryInput)) {
        return json({ error: "invalid_recovery_request" }, 400);
      }
      const workerLane = recoveryInput.worker_lane ?? "general";
      if (!EXECUTION_LANES.has(workerLane)) return json({ error: "invalid_worker_lane" }, 400);
      if (!laneTokenAuthorized(request, env, workerLane)) return json({ error: "lane_unauthorized" }, 401);
      try { return json(await recoverStaleTasks(env, workerLane)); }
      catch { return json({ error: "storage_unavailable" }, 503); }
    }

    // Claim one pending task atomically. The lease token is generated server-side
    // and returned only to the authenticated runner that claimed the task.
    if (request.method === "POST" &&
        (url.pathname === "/v1/tasks/claim" || url.pathname === "/v1/codespace/tasks/claim")) {
      const codespaceClaim = url.pathname === "/v1/codespace/tasks/claim";
      let claimInput;
      try { claimInput = await readOptionalJson(request); }
      catch (error) {
        return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" }, 400);
      }
      const executionMode = claimInput?.execution_mode ?? "unknown";
      const workerLane = codespaceClaim ? "general" : (claimInput?.worker_lane ?? "general");
      if (!EXECUTION_LANES.has(workerLane)) return json({ error: "invalid_worker_lane" }, 400);
      if (!laneTokenAuthorized(request, env, workerLane)) return json({ error: "lane_unauthorized" }, 401);
      if (!["unknown", "dry_run", "live_inference"].includes(executionMode)) {
        return json({ error: "invalid_execution_mode" }, 400);
      }
      const taskTypeFilter = codespaceClaim
        ? `AND task_type IN (${CODESPACE_TASK_SQL})`
        : `AND task_type NOT IN (${CODESPACE_TASK_SQL})`;
      const laneTaskFilter = workerLane === "general"
        ? "AND execution_lane='general'"
        : "AND execution_lane=? AND task_type=?";
      const laneBindings = workerLane === "general" ? [] : [workerLane, Object.keys(SPECIALIST_TASK_LANES).find(type => SPECIALIST_TASK_LANES[type] === workerLane)];
      const leaseSeconds = 900;
      const now = new Date();
      const nowIso = now.toISOString();
      const expires = new Date(now.getTime() + leaseSeconds * 1000).toISOString();
      const leaseToken = crypto.randomUUID();
      try {
        const blockedResult = await env.DB.prepare(`
          WITH RECURSIVE blocked(id) AS (
            SELECT child.id FROM tasks child
            JOIN task_dependencies d ON d.task_id=child.id
            JOIN tasks dependency ON dependency.id=d.dependency_task_id
            WHERE child.status='pending' AND child.execution_lane=? AND dependency.status IN ('failed','blocked')
            UNION
            SELECT child.id FROM tasks child
            JOIN task_dependencies d ON d.task_id=child.id
            JOIN blocked b ON b.id=d.dependency_task_id
            WHERE child.status='pending' AND child.execution_lane=?
          )
          SELECT id FROM blocked ORDER BY id LIMIT 50
        `).bind(workerLane, workerLane).all();
        const blockedIds = (blockedResult.results || []).map(row => row.id);
        if (blockedIds.length) {
          const blockedStatements = [];
          for (const blockedId of blockedIds) {
            blockedStatements.push(env.DB.prepare(
              "INSERT INTO audit_log(task_id,event,details_json,created_at) SELECT id,'task_blocked_by_dependency',?,? FROM tasks WHERE id=? AND status='pending'"
            ).bind(JSON.stringify({ reason: "dependency_failed_or_blocked" }), nowIso, blockedId));
            blockedStatements.push(env.DB.prepare(
              "UPDATE tasks SET status='blocked',error='Dependency failed or was blocked.',updated_at=?,lease_token=NULL,lease_expires_at=NULL WHERE id=? AND status='pending'"
            ).bind(nowIso, blockedId));
          }
          await env.DB.batch(blockedStatements);
        }
        const claim = await env.DB.prepare(`
          UPDATE tasks SET status='running', attempts=attempts+1, execution_mode=?, updated_at=?,
            lease_token=?, lease_expires_at=?
          WHERE id=(SELECT candidate.id FROM tasks candidate WHERE candidate.status='pending' ${taskTypeFilter} ${laneTaskFilter}
            AND NOT EXISTS (
              SELECT 1 FROM task_dependencies d
              JOIN tasks dependency ON dependency.id=d.dependency_task_id
              WHERE d.task_id=candidate.id
                AND (dependency.status <> 'succeeded' OR dependency.execution_mode <> ?)
            )
            ORDER BY candidate.priority DESC, candidate.created_at ASC, candidate.id ASC LIMIT 1)
            AND status='pending' ${taskTypeFilter} ${laneTaskFilter}
          RETURNING id,idempotency_key,task_type,title,payload_json,status,attempts,
            max_attempts,created_at,updated_at,execution_mode,priority,dependencies_json,execution_lane,lease_token,lease_expires_at
        `).bind(executionMode, nowIso, leaseToken, expires, ...laneBindings, executionMode, ...laneBindings).first();
        if (!claim) return json({ task: null });
        await env.DB.prepare(
          "INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(?,?,?,?)"
        ).bind(claim.id, "task_claimed", JSON.stringify({
          attempt: claim.attempts, lease_expires_at: expires, execution_mode: executionMode,
          execution_lane: workerLane
        }), nowIso).run();
        return json({ task: claim, lease_token: leaseToken, lease_expires_at: expires });
      } catch (error) {
        return json({ error: "storage_unavailable", detail: storageDetail(error) }, 503);
      }
    }

    const supervisedProbeClaim = url.pathname.match(
      /^\/v1\/tasks\/([A-Za-z0-9_-]{1,128})\/claim-supervised-probe$/
    );
    if (supervisedProbeClaim) {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      const taskId = supervisedProbeClaim[1];
      const now = new Date();
      const nowIso = now.toISOString();
      const leaseSeconds = 120;
      const expires = new Date(now.getTime() + leaseSeconds * 1000).toISOString();
      const leaseToken = crypto.randomUUID();
      try {
        // This narrow claim is for one exact supervised_probe row only. Unlike
        // the general queue claim it never sweeps or mutates unrelated tasks.
        const claim = await env.DB.prepare(`
          UPDATE tasks SET status='running', attempts=attempts+1, execution_mode='dry_run',
            updated_at=?, lease_token=?, lease_expires_at=?
          WHERE id=? AND status='pending' AND task_type='supervised_probe'
            AND attempts < max_attempts
            AND NOT EXISTS (
              SELECT 1 FROM task_dependencies d
              JOIN tasks dependency ON dependency.id=d.dependency_task_id
              WHERE d.task_id=tasks.id
                AND (dependency.status <> 'succeeded' OR dependency.execution_mode <> 'dry_run')
            )
          RETURNING id,idempotency_key,task_type,title,payload_json,status,attempts,
            max_attempts,created_at,updated_at,execution_mode,priority,dependencies_json,
            lease_token,lease_expires_at
        `).bind(nowIso, leaseToken, expires, taskId).first();
        if (!claim) return json({ task: null });
        await env.DB.prepare(
          "INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(?,?,?,?)"
        ).bind(claim.id, "task_claimed", JSON.stringify({
          attempt: claim.attempts, lease_expires_at: expires,
          execution_mode: "dry_run", claim_scope: "supervised_probe_id",
        }), nowIso).run();
        return json({ task: claim, lease_token: leaseToken, lease_expires_at: expires });
      } catch (error) {
        return json({ error: "storage_unavailable", detail: storageDetail(error) }, 503);
      }
    }

    const supervisedProbeRecovery = url.pathname.match(
      /^\/v1\/tasks\/([A-Za-z0-9_-]{1,128})\/recover-supervised-probe$/
    );
    if (supervisedProbeRecovery) {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      let recoveryInput;
      try { recoveryInput = await readJson(request); }
      catch (error) {
        return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" }, 400);
      }
      if (!recoveryInput || typeof recoveryInput !== "object" || Array.isArray(recoveryInput) ||
          Object.keys(recoveryInput).length !== 0) {
        return json({ error: "invalid_recovery_request" }, 400);
      }
      const taskId = supervisedProbeRecovery[1];
      const nowIso = new Date().toISOString();
      try {
        const current = await env.DB.prepare(
          "SELECT id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts," +
          "execution_mode,priority,dependencies_json,result_json,lease_token,lease_expires_at " +
          "FROM tasks WHERE id=?"
        ).bind(taskId).first();
        if (!current) return json({ error: "task_not_found" }, 404);
        let probePayload;
        try { probePayload = JSON.parse(current.payload_json); }
        catch { probePayload = null; }
        const exactTask = current.task_type === "supervised_probe" &&
          current.title === "Scheduled Staging read-only process probe" &&
          current.idempotency_key &&
          /^staging-cron-supervised-probe:[0-9]{8}$/.test(current.idempotency_key) &&
          probePayload && typeof probePayload === "object" && !Array.isArray(probePayload) &&
          Object.keys(probePayload).length === 1 && probePayload.operation === "python_platform_probe" &&
          current.execution_mode === "dry_run" && current.priority === 100 &&
          (current.dependencies_json === "[]" || current.dependencies_json === undefined) &&
          Number.isInteger(current.attempts) && Number.isInteger(current.max_attempts) &&
          current.attempts >= 1 && current.max_attempts >= 1 && current.max_attempts <= 2 &&
          current.attempts <= current.max_attempts && current.result_json == null;
        if (!exactTask) return json({ error: "probe_envelope_rejected" }, 403);
        if (current.status === "pending" && current.attempts < current.max_attempts) {
          return json({ status: "pending", recovered: false, replay: true,
            attempts: current.attempts, max_attempts: current.max_attempts });
        }
        if (["succeeded", "failed", "blocked"].includes(current.status)) {
          return json({ status: current.status, recovered: false, replay: true,
            attempts: current.attempts, max_attempts: current.max_attempts });
        }
        if (current.status !== "running" || typeof current.lease_token !== "string" ||
            !current.lease_token || typeof current.lease_expires_at !== "string" ||
            !Number.isFinite(Date.parse(current.lease_expires_at)) || current.lease_expires_at >= nowIso) {
          return json({ error: "lease_not_expired_or_invalid" }, 409);
        }

        const dispatch = await env.DB.prepare(
          "SELECT status FROM task_process_dispatches WHERE task_id=? AND step_key='supervised_process:v1'"
        ).bind(taskId).first();
        const nextStatus = current.attempts < current.max_attempts ? "pending" : "failed";
        const initialDispatchStatus = dispatch?.status ?? "absent";
        const expectedDispatchStatus = nextStatus === "failed" && initialDispatchStatus === "in_flight"
          ? "ambiguous" : initialDispatchStatus;
        // Atomic transition: the exhausted-dispatch quarantine, the task
        // lease/status transition, and the audit row commit or roll back
        // together in one D1 batch. The quarantine is gated on the same
        // expired-lease predicate as the task update, and the audit insert
        // only fires when the task row now carries this transition's
        // status+timestamp, so a concurrent recovery cannot create a second
        // transition or audit event and an interrupted Worker cannot leave a
        // half-transitioned task/dispatch/audit triple behind.
        // Statement order inside the batch matters: the audit insert is
        // gated on the pre-transition (running, expired-lease) row, so a
        // concurrent second batch — which observes the already-transitioned
        // row — inserts no audit even within the same millisecond.
        const batch = await env.DB.batch([
          env.DB.prepare(
            "INSERT INTO audit_log(task_id,event,details_json,created_at) " +
            "SELECT id,?,?,? FROM tasks WHERE id=? AND status='running' " +
            "AND task_type='supervised_probe' AND idempotency_key=? " +
            "AND lease_token=? AND lease_expires_at=? AND lease_expires_at<?"
          ).bind("supervised_probe_lease_recovered", JSON.stringify({
            new_status: nextStatus,
            attempt: current.attempts,
            max_attempts: current.max_attempts,
            dispatch_status: expectedDispatchStatus,
            recovery_scope: "exact_supervised_probe_id",
          }), nowIso, taskId, current.idempotency_key,
            current.lease_token, current.lease_expires_at, nowIso),
          env.DB.prepare(
            "UPDATE task_process_dispatches SET status='ambiguous',detail=?,updated_at=? " +
            "WHERE task_id=? AND step_key='supervised_process:v1' AND status='in_flight' " +
            "AND EXISTS (SELECT 1 FROM tasks WHERE id=? AND status='running' AND lease_token=? " +
            "AND lease_expires_at=? AND lease_expires_at<? AND attempts>=max_attempts)"
          ).bind("lease_expired_attempt_budget_exhausted", nowIso, taskId, taskId,
            current.lease_token, current.lease_expires_at, nowIso),
          env.DB.prepare(
            "UPDATE tasks SET status=?,error=?,updated_at=?,lease_token=NULL,lease_expires_at=NULL " +
            "WHERE id=? AND status='running' AND task_type='supervised_probe' AND idempotency_key=? " +
            "AND lease_token=? AND lease_expires_at=? AND lease_expires_at<?"
          ).bind(nextStatus, "Recovered expired supervised probe lease.", nowIso, taskId,
            current.idempotency_key, current.lease_token, current.lease_expires_at, nowIso),
        ]);
        if (batch?.[2]?.meta?.changes !== 1) {
          const latest = await env.DB.prepare(
            "SELECT status,attempts,max_attempts FROM tasks WHERE id=?"
          ).bind(taskId).first();
          if (latest?.status === "pending" && latest.attempts < latest.max_attempts) {
            return json({ status: "pending", recovered: false, replay: true,
              attempts: latest.attempts, max_attempts: latest.max_attempts });
          }
          return json({ error: "lease_lost" }, 409);
        }
        if (batch?.[0]?.meta?.changes !== 1) {
          return json({ error: "recovery_audit_failed" }, 503);
        }
        const latestDispatch = await env.DB.prepare(
          "SELECT status FROM task_process_dispatches WHERE task_id=? AND step_key='supervised_process:v1'"
        ).bind(taskId).first();
        const dispatchStatus = latestDispatch?.status ?? "absent";
        if (nextStatus === "failed" && initialDispatchStatus === "in_flight" &&
            dispatchStatus === "in_flight") {
          return json({ error: "dispatch_quarantine_failed" }, 503);
        }
        return json({ status: nextStatus, recovered: true, replay: false,
          attempts: current.attempts, max_attempts: current.max_attempts,
          dispatch_status: dispatchStatus });
      } catch (error) {
        return json({ error: "storage_unavailable", detail: storageDetail(error) }, 503);
      }
    }

    const taskAction = url.pathname.match(/^\/v1\/tasks\/([A-Za-z0-9_-]+)\/(heartbeat|finish|retry)$/);
    const codespaceTaskAction = url.pathname.match(/^\/v1\/codespace\/tasks\/([A-Za-z0-9_-]+)\/(heartbeat|finish)$/);
    if (request.method === "POST" && (taskAction || codespaceTaskAction)) {
      const codespaceAction = Boolean(codespaceTaskAction);
      const [, taskId, action] = codespaceAction ? codespaceTaskAction : taskAction;
      let input;
      try { input = await readJson(request); }
      catch (error) {
        return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" }, 400);
      }
      const leaseToken = input?.lease_token;
      const workerLane = codespaceAction ? "general" : (input?.worker_lane ?? "general");
      if (!EXECUTION_LANES.has(workerLane)) return json({ error: "invalid_worker_lane" }, 400);
      if (!laneTokenAuthorized(request, env, workerLane)) return json({ error: "lane_unauthorized" }, 401);
      if (!validText(leaseToken, 200)) return json({ error: "invalid_lease" }, 400);
      const nowIso = new Date().toISOString();
      try {
        const current = await env.DB.prepare(
          "SELECT id,status,attempts,max_attempts,task_type,execution_mode,execution_lane,lease_token,lease_expires_at FROM tasks WHERE id=?"
        ).bind(taskId).first();
        if (!current) return json({ error: "task_not_found" }, 404);
        if (CODESPACE_TASK_TYPES.has(current.task_type) !== codespaceAction) {
          return json({ error: "executor_mismatch" }, 403);
        }
        if ((current.execution_lane || "general") !== workerLane) return json({ error: "lane_ownership_lost" }, 403);
        if (current.status !== "running" || current.lease_token !== leaseToken ||
            !current.lease_expires_at || current.lease_expires_at < nowIso) {
          return json({ error: "lease_lost" }, 409);
        }

        if (action === "heartbeat") {
          const seconds = 900;
          const expires = new Date(Date.now() + seconds * 1000).toISOString();
          const result = await env.DB.prepare(
            "UPDATE tasks SET updated_at=?,lease_expires_at=? WHERE id=? AND status='running' AND execution_lane=? AND lease_token=? AND lease_expires_at>=?"
          ).bind(nowIso, expires, taskId, workerLane, leaseToken, nowIso).run();
          if (!result.meta?.changes) return json({ error: "lease_lost" }, 409);
          await env.DB.prepare(
            "INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(?,?,?,?)"
          ).bind(taskId, "task_heartbeat", JSON.stringify({ lease_expires_at: expires }), nowIso).run();
          return json({ ok: true, lease_expires_at: expires });
        }

        let status, resultJson = null, error = null;
        if (action === "finish") {
          status = input.status;
          if (!["succeeded", "failed", "blocked"].includes(status)) {
            return json({ error: "invalid_status" }, 400);
          }
          if (input.result !== undefined) {
            if (!input.result || typeof input.result !== "object" || Array.isArray(input.result)) {
              return json({ error: "invalid_result" }, 400);
            }
            resultJson = JSON.stringify(input.result);
            if (new TextEncoder().encode(resultJson).byteLength > 16 * 1024) {
              return json({ error: "result_too_large" }, 400);
            }
          }
          if (input.error !== undefined) {
            if (typeof input.error !== "string" || input.error.length > 2000) {
              return json({ error: "invalid_error" }, 400);
            }
            error = input.error;
          }
        } else {
          status = current.attempts < current.max_attempts ? "pending" : "failed";
          error = typeof input.error === "string" ? input.error.slice(0, 2000) : "Task retry requested.";
        }

        let executionMode = current.execution_mode || "unknown";
        if (input.execution_mode !== undefined) {
          if (!["unknown", "dry_run", "live_inference"].includes(input.execution_mode)) {
            return json({ error: "invalid_execution_mode" }, 400);
          }
          executionMode = input.execution_mode;
        } else if (resultJson) {
          let parsedResult;
          try { parsedResult = JSON.parse(resultJson); } catch { parsedResult = null; }
          if (["dry_run", "live_inference"].includes(parsedResult?.mode)) {
            executionMode = parsedResult.mode;
          }
        }
        const update = await env.DB.prepare(
          "UPDATE tasks SET status=?,result_json=?,error=?,execution_mode=?,updated_at=?,lease_token=NULL,lease_expires_at=NULL WHERE id=? AND status='running' AND execution_lane=? AND lease_token=? AND lease_expires_at>=?"
        ).bind(status, resultJson, error, executionMode, nowIso, taskId, workerLane, leaseToken, nowIso).run();
        if (!update.meta?.changes) return json({ error: "lease_lost" }, 409);
        await env.DB.prepare(
          "INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(?,?,?,?)"
        ).bind(taskId, action === "retry" ? (status === "pending" ? "task_retry_scheduled" : "task_failed") : "task_" + status,
          JSON.stringify({ error, execution_mode: executionMode }), nowIso).run();
        return json({ ok: true, status });
      } catch {
        return json({ error: "storage_unavailable" }, 503);
      }
    }

    if (request.method !== "POST" || url.pathname !== "/v1/tasks/enqueue") {
      return json({ error: "method_not_allowed" }, 405);
    }

    let input;
    try { input = await readJson(request); }
    catch (error) {
      return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" }, 400);
    }

    const taskType = input?.task_type;
    if (CODESPACE_TASK_TYPES.has(taskType) && !CODESPACE_EXECUTOR_RELEASE_ENABLED) {
      return json({ error: "codespace_executor_disabled" }, 503);
    }
    const title = input?.title;
    const key = input?.idempotency_key;
    const payload = input?.payload ?? {};
    const maxAttempts = input?.max_attempts ?? 2;
    const prioritySupplied = input?.priority !== undefined;
    const dependenciesSupplied = input?.depends_on !== undefined;
    const priority = input?.priority ?? 50;
    const rawDependencies = input?.depends_on ?? [];
    const executionLane = input?.execution_lane ?? "general";
    if (!EXECUTION_LANES.has(executionLane)) return json({ error: "invalid_execution_lane" }, 400);
    if (executionLane !== "general" && SPECIALIST_TASK_LANES[taskType] !== executionLane) {
      return json({ error: "task_type_lane_mismatch" }, 400);
    }
    if (!validText(taskType, 100) || !validText(title, 240) ||
        !validText(key, 200) || !payload || typeof payload !== "object" ||
        Array.isArray(payload) || !Number.isInteger(maxAttempts) ||
        maxAttempts < 1 || maxAttempts > 5 || !Number.isInteger(priority) ||
        priority < 0 || priority > 100 || !Array.isArray(rawDependencies) ||
        rawDependencies.length > 20 ||
        rawDependencies.some(dep => !validText(dep, 128)) ||
        new Set(rawDependencies).size !== rawDependencies.length) {
      return json({ error: "invalid_task" }, 400);
    }
    const dependsOn = [...rawDependencies].sort();
    const dependenciesJson = JSON.stringify(dependsOn);

    let payloadJson;
    try {
      payloadJson = JSON.stringify(payload);
      if (new TextEncoder().encode(payloadJson).byteLength > 16 * 1024) {
        return json({ error: "payload_too_large" }, 400);
      }
    } catch {
      return json({ error: "invalid_payload" }, 400);
    }

    const normalizedTask = {
      task_type: taskType.trim(), title: title.trim(), payload_json: payloadJson,
      max_attempts: maxAttempts, priority, dependencies_json: dependenciesJson, execution_lane: executionLane,
    };
    const matchesTask = task => task &&
      (task.execution_lane || "general") === normalizedTask.execution_lane &&
      (!prioritySupplied || task.priority === normalizedTask.priority) &&
      (!dependenciesSupplied || (task.dependencies_json || "[]") === normalizedTask.dependencies_json);
    const readDependencies = async taskId => {
      const rows = await env.DB.prepare(
        "SELECT dependency_task_id FROM task_dependencies WHERE task_id=? ORDER BY dependency_task_id"
      ).bind(taskId).all();
      return (rows.results || []).map(row => row.dependency_task_id);
    };

    const id = crypto.randomUUID();
    const now = new Date().toISOString();
    try {
      const existing = await env.DB.prepare(
        "SELECT id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,created_at,updated_at,execution_mode,priority,dependencies_json,execution_lane FROM tasks WHERE idempotency_key=?"
      ).bind(key).first();
      if (existing) {
        if (!matchesTask(existing)) return json({ error: "idempotency_conflict" }, 409);
        const existingDependencies = await readDependencies(existing.id);
        if (dependenciesSupplied && JSON.stringify(existingDependencies) !== JSON.stringify(dependsOn)) {
          return json({ error: "dependency_manifest_conflict" }, 409);
        }
        return json({ task: { ...existing, depends_on: existingDependencies }, created: false }, 200);
      }

      if (dependsOn.length) {
        const placeholders = dependsOn.map(() => "?").join(",");
        const dependencyResult = await env.DB.prepare(
          `SELECT id,task_type FROM tasks WHERE id IN (${placeholders})`
        ).bind(...dependsOn).all();
        const dependencyRows = dependencyResult.results || [];
        if (dependencyRows.length !== dependsOn.length) {
          return json({ error: "unknown_task_dependency" }, 400);
        }
        if (dependencyRows.some(row => CODESPACE_TASK_TYPES.has(row.task_type))) {
          return json({ error: "unsupported_dependency_executor" }, 400);
        }
      }

      const insert = env.DB.prepare(
        "INSERT OR IGNORE INTO tasks(id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,created_at,updated_at,execution_mode,priority,dependencies_json,execution_lane) VALUES(?,?,?,?,?,'pending',0,?,?,?,'not_started',?,?,?)"
      ).bind(id, key, normalizedTask.task_type, normalizedTask.title, payloadJson,
        maxAttempts, now, now, priority, dependenciesJson, executionLane);
      const audit = env.DB.prepare(
        "INSERT INTO audit_log(task_id,event,details_json,created_at) SELECT id,'task_enqueued',?,? FROM tasks WHERE idempotency_key=? AND id=?"
      ).bind(JSON.stringify({ task_type: normalizedTask.task_type, priority, depends_on: dependsOn }), now, key, id);
      const edgeStatements = dependsOn.map(dependencyId => env.DB.prepare(
        "INSERT OR IGNORE INTO task_dependencies(task_id,dependency_task_id) VALUES(?,?)"
      ).bind(id, dependencyId));
      await env.DB.batch([insert, audit, ...edgeStatements]);

      const task = await env.DB.prepare(
        "SELECT id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,created_at,updated_at,execution_mode,priority,dependencies_json,execution_lane FROM tasks WHERE idempotency_key=?"
      ).bind(key).first();
      if (!task) return json({ error: "storage_unavailable" }, 503);
      if (!matchesTask(task)) return json({ error: "idempotency_conflict" }, 409);
      const storedDependencies = await readDependencies(task.id);
      if (JSON.stringify(storedDependencies) !== JSON.stringify(dependsOn)) {
        return json({ error: "dependency_manifest_conflict" }, 409);
      }
      return json({ task: { ...task, depends_on: storedDependencies }, created: task.id === id }, task.id === id ? 201 : 200);
    } catch (error) {
      return json({ error: "storage_unavailable", detail: storageDetail(error) }, 503);
    }
  }
};
