import { getTaskRunnerWatchdogReadiness } from "./task-runner-watchdog.js";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Raawaa Engine · Mobile Status</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#101315;color:#edf1ed}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at top,#26332f 0,#101315 55%);padding:24px 16px 48px}
main{max-width:760px;margin:0 auto}.eyebrow{color:#9fb9a8;letter-spacing:.16em;text-transform:uppercase;font-size:11px}
h1{font-size:clamp(26px,6vw,38px);margin:8px 0 4px;font-weight:650}.muted{color:#a7b1aa;font-size:13px}
.panel,.metric{background:#1a2020;border:1px solid #34403a;border-radius:14px;padding:16px;margin-top:14px}
label{display:block;font-size:13px;color:#b8c4bc;margin-bottom:8px}input{width:100%;padding:13px;border-radius:9px;border:1px solid #53645a;background:#101615;color:#fff;font-size:16px}
button{margin-top:10px;padding:12px 16px;border:0;border-radius:9px;background:#b9d1bd;color:#152019;font-weight:650;font-size:14px}
button.secondary{background:#34413a;color:#e8eee9;margin-left:8px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:14px}
.metric{margin:0;min-width:0}.metric span{display:block;color:#9eaaa2;font-size:12px}.metric strong{display:block;font-size:23px;margin-top:5px;overflow-wrap:anywhere}
h2{font-size:16px;margin:0 0 10px}.rows{display:grid;gap:8px}.item{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid #303a34;padding:7px 0;font-size:13px}.item:last-child{border:0}.value{text-align:right;color:#d6e1d8;overflow-wrap:anywhere}
#message{min-height:20px;margin-top:10px;color:#d5b98b;font-size:13px}.hidden{display:none!important}
@media(max-width:420px){body{padding:18px 12px 32px}.grid{grid-template-columns:1fr 1fr}.metric{padding:12px}.metric strong{font-size:20px}}
</style>
</head>
<body><main>
<div class="eyebrow">RAAWAA AUTONOMOUS ENGINE</div><h1>Mobile control</h1>
<p class="muted">Read-only operational snapshot · Cloudflare Worker + D1</p>
<section class="panel" id="auth">
<label for="token">Engine API access token</label><input id="token" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Paste the operator token">
<button id="connect">Connect securely</button><div id="message" role="status"></div>
<p class="muted">The token stays in this page's memory only. It is not saved to browser storage.</p>
</section>
<section id="dashboard" class="hidden">
<div class="grid">
<div class="metric"><span>Pending tasks</span><strong id="pending">—</strong></div>
<div class="metric"><span>Running tasks</span><strong id="running">—</strong></div>
<div class="metric"><span>Completed tasks</span><strong id="succeeded">—</strong></div>
<div class="metric"><span>Failed / blocked</span><strong id="failed">—</strong></div>
</div>
<section class="panel"><h2>Paper research events</h2><div id="paper" class="rows"></div></section>
<section class="panel"><h2>Provider usage · D1</h2><div id="usage" class="rows"></div></section>
<section class="panel"><h2>Recorded model inference costs · USD</h2><div id="modelCosts" class="rows"></div><p class="muted">Recorded provider costs only; external or unreported spend is not inferred.</p></section>
<section class="panel"><h2>Durable monetary budget ledger · D1</h2><div id="monetaryLedger" class="rows"></div><p class="muted">Unresolved reservations count against all budget windows. A pending reservation is never released automatically.</p></section>
<section class="panel"><h2>Durable task-step invocations · D1</h2><div id="taskInvocations" class="rows"></div><p class="muted">Aggregate lifecycle counts only. Prompts, task IDs, request IDs and lease tokens are never exposed.</p></section>
<section class="panel"><h2>Latest market snapshot</h2><div id="snapshot" class="muted">—</div></section>
<section class="panel"><h2>Latest explicit research scan</h2><div id="lastScan" class="rows"></div></section>
<section class="panel"><h2>Research source freshness</h2><div id="sourceFreshness" class="rows"></div></section><section class="panel"><h2>Latest paper event</h2><div id="latest" class="muted">—</div></section><section class="panel"><h2>Telegram control readiness</h2><div id="telegramSchema" class="rows"></div><p class="muted">Schema presence only; webhook activation still requires operator-managed secrets and Telegram configuration.</p></section><section class="panel"><h2>Worker Cron heartbeat</h2><div id="workerCron" class="rows"></div></section><section class="panel"><h2>Task Runner watchdog</h2><div id="watchdog" class="rows"></div><p class="muted">Readiness only; secret values are never returned. Dispatch history is read from the D1 audit ledger.</p></section><section class="panel"><h2>GitHub CI health · Staging snapshot</h2><div id="ci" class="rows"></div></section>
<section class="panel"><h2>Default Task Runner inference gate</h2><div id="killSwitch" class="rows"></div><p class="muted">This reports the committed default Task Runner policy, not one-off Staging smoke workflows.</p></section>
<section class="panel"><h2>Not yet connected to this status feed</h2><div id="gaps" class="rows"></div></section>
<button id="refresh">Refresh</button><button class="secondary" id="disconnect">Disconnect</button>
<p class="muted" id="updated"></p>
</section>
</main>
<script>
let accessToken = "";
const $ = id => document.getElementById(id);
const escapeText = value => String(value ?? "—");
function rows(target, values, empty) {
  $(target).replaceChildren();
  if (!values.length) { const p=document.createElement("div");p.className="muted";p.textContent=empty;$(target).append(p);return; }
  for (const [name,value] of values) { const row=document.createElement("div");row.className="item";const a=document.createElement("span");a.textContent=name;const b=document.createElement("span");b.className="value";b.textContent=escapeText(value);row.append(a,b);$(target).append(row); }
}
async function load() {
  $("message").textContent="Loading status…";
  const response=await fetch("/v1/mobile/status",{headers:{authorization:"Bearer "+accessToken},cache:"no-store"});
  if(response.status===401){accessToken="";$("token").value="";$("dashboard").classList.add("hidden");$("auth").classList.remove("hidden");$("message").textContent="Access denied. Check the operator token.";return;}
  if(!response.ok) throw new Error("Status request failed ("+response.status+")");
  const data=await response.json();
  const tasks=Object.fromEntries((data.tasks||[]).map(x=>[x.status,x.count]));
  $("pending").textContent=tasks.pending||0;$("running").textContent=tasks.running||0;$("succeeded").textContent=tasks.succeeded||0;
  $("failed").textContent=(tasks.failed||0)+(tasks.blocked||0);
  rows("paper",(data.paper_events||[]).map(x=>[x.event_type,x.count]),"No paper events recorded");
  const usage=data.provider_usage||{};
  rows("usage",[
    ["Requests",usage.requests||0],
    ["Tokens",usage.tokens||0]
  ],"No D1 provider usage recorded");
  const costs=data.model_costs_usd||{};
  rows("modelCosts",[
    ["Total recorded", "$"+(Number(costs.total)||0).toFixed(6)]
  ],"No recorded model inference costs");
  const money=data.monetary_ledger||{};
  rows("monetaryLedger",money.available?[
    ["Daily hard cap","$"+Number(money.daily_budget_usd||0).toFixed(2)],
    ["Daily settled spend","$"+Number(money.daily_spent_usd||0).toFixed(9)],
    ["Monthly hard cap","$"+Number(money.monthly_budget_usd||0).toFixed(2)],
    ["Monthly settled spend","$"+Number(money.monthly_spent_usd||0).toFixed(9)],
    ["Per-task hard cap","$"+Number(money.per_task_budget_usd||0).toFixed(2)],
    ["Pending / ambiguous reserve","$"+Number(money.pending_reserved_usd||0).toFixed(9)],
    ["Unresolved reservations",money.pending_count||0],
    ["Quarantined routes",money.quarantined_routes||0],
    ["Hard-cap policy enabled",money.budget_policy_enabled?"Yes":"No"]
  ]:[],data.unavailable_metrics?.monetary_ledger||"D1 monetary ledger unavailable");
  const invocations=data.task_invocations||{};
  const invocationCounts=invocations.counts||{};
  rows("taskInvocations",invocations.available?[
    ["Prepared",invocationCounts.prepared||0],
    ["In flight",invocationCounts.in_flight||0],
    ["Ambiguous",invocationCounts.ambiguous||0],
    ["Settled",invocationCounts.settled||0],
    ["Unresolved",invocations.unresolved_count||0]
  ]:[],data.unavailable_metrics?.task_invocations||"D1 invocation lifecycle unavailable");
  const scan=data.last_scan;
  rows("lastScan",scan?[["Status",scan.status],["Scan ID",scan.scan_id],["Completed",scan.completed_at],["GitHub requests",scan.requests_made],["Crossref requests",scan.academic_requests_made],["GitHub candidates",scan.candidate_count],["Academic candidates",scan.academic_candidate_count],["Unavailable queries",scan.unavailable_query_count],["Recorded at",scan.recorded_at]]:[],data.unavailable_metrics?.last_scan||"No explicit scan recorded");
  const freshness=data.source_freshness||{};
  rows("sourceFreshness",freshness.sources?Object.entries(freshness.sources).map(([source,value])=>[source.toUpperCase(),value.status+" · "+value.age_seconds+"s old · "+value.item_count+" items"]):[],data.unavailable_metrics?.source_freshness||freshness.status||"Unknown");
  $("snapshot").textContent=data.latest_market_snapshot?data.latest_market_snapshot.candidate_id+" · chain "+(data.latest_market_snapshot.chain_id??"—")+" · block "+(data.latest_market_snapshot.block_number??"—")+" · "+data.latest_market_snapshot.occurred_at:"No market snapshot recorded";
  $("latest").textContent=data.latest_paper_event?data.latest_paper_event.event_type+" · "+data.latest_paper_event.candidate_id+" · "+data.latest_paper_event.occurred_at:"No paper event recorded";
  rows("telegramSchema",[[ "Webhook receipt schema", data.telegram_webhook_schema?.status || "Unknown" ]],"Schema status unavailable");
  const cron=data.worker_cron;
  rows("workerCron",cron?[
    ["Last invocation",cron.last_seen_at],
    ["Schedule",cron.cron],
    ["Expired leases requeued / failed",(cron.requeued??"—")+" / "+(cron.failed??"—")]
  ]:[],data.unavailable_metrics?.worker_cron || "No Worker Cron heartbeat recorded yet");
  const watchdog=data.task_runner_watchdog||{};
  rows("watchdog",[
    ["Configuration",watchdog.ready?"Ready":"Disabled / incomplete"],
    ["Missing settings",(watchdog.missing||[]).join(", ")||"None"],
    ["Last dispatch request",watchdog.last_dispatch_requested?.created_at||"None recorded"],
    ["Last dispatch intent",watchdog.last_dispatch_intent_at||"None recorded"]
  ],"Watchdog status unavailable");
  const ci=data.ci_health||null;
  rows("ci",ci?[
    ["Status",ci.status+" · "+ci.conclusion],
    ["Run","#"+ci.run_number+" ("+ci.run_id+")"],
    ["Commit",String(ci.head_sha||"").slice(0,8)],
    ["Recorded",ci.recorded_at]
  ]:[],data.unavailable_metrics?.ci_health||"No valid CI snapshot recorded");
  const gate=data.kill_switch||null;
  rows("killSwitch",gate?[
    ["Status",gate.status],
    ["Scope",gate.scope],
    ["Policy mode",gate.policy_mode],
    ["Live inference default",gate.live_inference_default],
    ["Paid model calls",gate.allow_paid_model_calls?"Allowed":"Blocked"],
    ["Paid fallback",gate.paid_fallback_enabled?"Enabled":"Disabled"],
    ["External writes",gate.allow_external_writes?"Allowed":"Blocked"],
    ["Recorded",gate.recorded_at]
  ]:[],data.unavailable_metrics?.kill_switch||"No valid kill-switch snapshot recorded");
  rows("gaps",Object.entries(data.unavailable_metrics||{}),"No unavailable metrics");
  $("updated").textContent="Snapshot generated: "+data.generated_at;
  $("message").textContent="Connected · read-only";
  $("auth").classList.add("hidden");$("dashboard").classList.remove("hidden");
}
$("connect").addEventListener("click",async()=>{accessToken=$("token").value.trim();if(!accessToken){$("message").textContent="Enter the operator token.";return;}try{await load();}catch(error){$("message").textContent=error.message;}});
$("refresh").addEventListener("click",async()=>{try{await load();}catch(error){$("message").textContent=error.message;}});
$("disconnect").addEventListener("click",()=>{accessToken="";$("token").value="";$("dashboard").classList.add("hidden");$("auth").classList.remove("hidden");$("message").textContent="Disconnected.";});
</script></body></html>`;

export function handleMobilePage(request, url) {
  if (url.pathname !== "/mobile") return null;
  if (request.method !== "GET") return new Response("Method not allowed", { status: 405 });
  return new Response(PAGE, { status: 200, headers: {
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  }});
}

export async function handleMobileStatus(request, env, url) {
  if (url.pathname !== "/v1/mobile/status") return null;
  if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
  try {
    const [tasks, migrations, workerCron, watchdogEvents, ciHealthRow, killSwitchRow, scanRows, sourceRows] = await Promise.all([
      env.DB.prepare("SELECT status,COUNT(*) AS count FROM tasks GROUP BY status").all(),
      env.DB.prepare("SELECT version FROM schema_migrations WHERE version IN ('0003_durable_quota_ledger','0004_paper_event_ledger','0005_telegram_webhook_receipts','0006_monetary_budget_ledger','0007_task_model_invocations')").all(),
      env.DB.prepare("SELECT created_at,details_json FROM audit_log WHERE task_id IS NULL AND event='worker_cron_tick' ORDER BY created_at DESC LIMIT 1").first(),
      env.DB.prepare("SELECT event,created_at,details_json FROM audit_log WHERE task_id IS NULL AND event IN ('task_runner_dispatch_requested','task_runner_dispatch_intent') ORDER BY created_at DESC LIMIT 2").all(),
      env.DB.prepare("SELECT created_at,details_json FROM audit_log WHERE task_id IS NULL AND event='ci_health_snapshot' ORDER BY created_at DESC LIMIT 1").first(),
      env.DB.prepare("SELECT created_at,details_json FROM audit_log WHERE task_id IS NULL AND event='kill_switch_snapshot' ORDER BY created_at DESC LIMIT 1").first(),
      env.DB.prepare("SELECT created_at,details_json FROM audit_log WHERE task_id IS NULL AND event='research_scan_completed' ORDER BY created_at DESC LIMIT 1").first(),
      env.DB.prepare("SELECT created_at,details_json FROM audit_log WHERE task_id IS NULL AND event='research_source_observation' ORDER BY created_at DESC LIMIT 20").all(),
    ]);
    const applied = new Set((migrations.results || []).map(row => row.version));
    let taskInvocations = null;
    if (applied.has("0007_task_model_invocations")) {
      try {
        const result = await env.DB.prepare(
          "SELECT status,COUNT(*) AS count FROM task_model_invocations GROUP BY status"
        ).all();
        const counts = { prepared: 0, in_flight: 0, ambiguous: 0, settled: 0 };
        for (const row of result.results || []) {
          if (!Object.hasOwn(counts, row.status) || !Number.isSafeInteger(Number(row.count)) || Number(row.count) < 0) {
            throw new Error("invalid_invocation_aggregate");
          }
          counts[row.status] = Number(row.count);
        }
        taskInvocations = {
          available: true,
          counts,
          unresolved_count: counts.prepared + counts.in_flight + counts.ambiguous,
        };
      } catch {
        taskInvocations = { available: false };
      }
    }
    const watchdogReadiness = getTaskRunnerWatchdogReadiness(env);
    const watchdogHistory = (watchdogEvents.results || []).map(row => {
      if (!["task_runner_dispatch_requested", "task_runner_dispatch_intent"].includes(row.event)
          || typeof row.created_at !== "string" || !Number.isFinite(Date.parse(row.created_at))) return null;
      let details;
      try { details = JSON.parse(row.details_json || "{}"); } catch { return null; }
      if (!details || typeof details !== "object" || Array.isArray(details)) return null;
      return {
        event: row.event,
        created_at: row.created_at,
        workflow: details.workflow === "task-runner.yml" ? details.workflow : null,
        ref: details.ref === "main" ? details.ref : null,
        http_status: Number.isInteger(details.http_status) ? details.http_status : null,
      };
    }).filter(Boolean);
    const lastDispatchRequested = watchdogHistory.find(row => row.event === "task_runner_dispatch_requested") || null;
    const lastDispatchIntent = watchdogHistory.find(row => row.event === "task_runner_dispatch_intent") || null;
    const telemetryAge = row => {
      if (!row || typeof row.created_at !== "string" || !Number.isFinite(Date.parse(row.created_at))) return null;
      const age = Date.now() - Date.parse(row.created_at);
      return age >= 0 ? age : null;
    };

    const safeScanId = value => typeof value === "string" && value.length <= 160 && /^[A-Za-z0-9_.:-]+$/.test(value);
    const validTimestamp = value => typeof value === "string" && value.length <= 40 &&
      /(?:Z|[+-]\\d{2}:\\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
    const researchScan = (() => {
      const unknown = { value: null, unavailable: "No valid research scan has been recorded" };
      if (!scanRows) return unknown;
      const age = telemetryAge(scanRows);
      if (age === null) return { value: null, unavailable: "Latest research scan timestamp is invalid" };
      let details;
      try { details = JSON.parse(scanRows.details_json || "{}"); } catch { return { value: null, unavailable: "Latest research scan metadata is invalid" }; }
      const count = value => Number.isSafeInteger(value) && value >= 0;
      if (!details || details.workflow !== "research-discovery.yml" || details.scan_type !== "github_crossref" ||
          !safeScanId(details.scan_id) || !["success","partial","failure"].includes(details.status) ||
          !Number.isSafeInteger(details.run_id) || !Number.isSafeInteger(details.run_number) ||
          !Number.isSafeInteger(details.run_attempt) || typeof details.head_sha !== "string" ||
          !/^[0-9a-f]{40}$/.test(details.head_sha) ||
          !validTimestamp(details.started_at) || !validTimestamp(details.completed_at) ||
          Date.parse(details.completed_at) < Date.parse(details.started_at) ||
          !["requests_made","academic_requests_made","candidate_count","academic_candidate_count","unavailable_query_count"].every(key => count(details[key]))) {
        return { value: null, unavailable: "Latest research scan metadata is invalid" };
      }
      const stale = age > 24*60*60*1000;
      return { value: {
        scan_id: details.scan_id, scan_type: details.scan_type, status: stale ? "stale" : details.status,
        run_id: details.run_id, run_number: details.run_number, run_attempt: details.run_attempt,
        head_sha: details.head_sha, started_at: details.started_at, completed_at: details.completed_at,
        requests_made: details.requests_made, academic_requests_made: details.academic_requests_made,
        candidate_count: details.candidate_count, academic_candidate_count: details.academic_candidate_count,
        unavailable_query_count: details.unavailable_query_count, recorded_at: scanRows.created_at,
        freshness: stale ? "stale" : "fresh",
      }, unavailable: null };
    })();
    const sourceFreshness = (() => {
      const values = {};
      for (const row of sourceRows.results || []) {
        if (typeof row.created_at !== "string" || !Number.isFinite(Date.parse(row.created_at))) continue;
        let details;
        try { details = JSON.parse(row.details_json || "{}"); } catch { continue; }
        if (!details || !["github","crossref"].includes(details.source) || values[details.source] ||
            !researchScan.value || details.scan_id !== researchScan.value.scan_id ||
            !["fresh","unavailable"].includes(details.status) || !validTimestamp(details.observed_at) ||
            !Number.isSafeInteger(details.freshness_hours) || details.freshness_hours < 1 ||
            !Number.isSafeInteger(details.request_count) || details.request_count < 0 ||
            !Number.isSafeInteger(details.item_count) || details.item_count < 0) continue;
        const age = Date.now() - Date.parse(details.observed_at);
        if (age < 0) continue;
        const stale = age > details.freshness_hours * 60 * 60 * 1000;
        values[details.source] = {
          status: details.status === "unavailable" ? "unavailable" : stale ? "stale" : "fresh",
          observed_at: details.observed_at, age_seconds: Math.floor(age/1000),
          freshness_hours: details.freshness_hours, request_count: details.request_count,
          item_count: details.item_count,
          ...(details.status === "unavailable" && typeof details.reason_code === "string" ? {reason_code:details.reason_code} : {}),
        };
      }
      const present = ["github","crossref"].filter(source => values[source]);
      const statuses = Object.values(values).map(item => item.status);
      const aggregate = present.length !== 2 ? "unknown" :
        (statuses.every(status=>status==="fresh") ? "fresh" :
         statuses.includes("fresh") || statuses.includes("unavailable") && statuses.includes("stale") ? "partial" :
         statuses.includes("stale") ? "stale" : "unavailable");
      return { value: present.length ? values : null, status: aggregate };
    })();
    const ciHealth = (() => {
      const age = telemetryAge(ciHealthRow);
      if (age === null) return null;
      let details;
      try { details = JSON.parse(ciHealthRow.details_json || "{}"); } catch { return null; }
      const conclusions = new Set(["success", "failure", "incomplete"]);
      const sha = details?.head_sha;
      const jobs = details?.jobs;
      if (
        !details || details.workflow !== "Engine CI" || details.event !== "push" ||
        details.branch !== "main" || !conclusions.has(details.conclusion) ||
        !Number.isSafeInteger(details.run_id) || !Number.isSafeInteger(details.run_number) ||
        typeof sha !== "string" || !/^[0-9a-f]{40,64}$/.test(sha) ||
        !jobs || typeof jobs !== "object" || Array.isArray(jobs) ||
        !["test", "staging_migrate", "staging_worker_verify"].every(key =>
          ["success", "failure", "cancelled", "skipped"].includes(jobs[key]))
      ) return null;
      const safeJobs = {
        test: jobs.test,
        staging_migrate: jobs.staging_migrate,
        staging_worker_verify: jobs.staging_worker_verify,
      };
      return {
        available: true,
        status: age > 24 * 60 * 60 * 1000 ? "stale" : details.conclusion,
        conclusion: details.conclusion,
        workflow: "Engine CI",
        run_id: details.run_id,
        run_number: details.run_number,
        head_sha: sha,
        jobs: safeJobs,
        recorded_at: ciHealthRow.created_at,
        freshness: age > 24 * 60 * 60 * 1000 ? "stale" : "fresh",
      };
    })();
    const killSwitch = (() => {
      const age = telemetryAge(killSwitchRow);
      if (age === null) return null;
      let details;
      try { details = JSON.parse(killSwitchRow.details_json || "{}"); } catch { return null; }
      if (
        !details || details.scope !== "scheduled_task_runner_default" ||
        !["engaged", "not_engaged"].includes(details.status) ||
        !["safe_dry_run", "unknown"].includes(details.policy_mode) ||
        !["disabled", "enabled", "unknown"].includes(details.live_inference_default) ||
        typeof details.allow_paid_model_calls !== "boolean" ||
        typeof details.paid_fallback_enabled !== "boolean" ||
        typeof details.allow_external_writes !== "boolean" ||
        typeof details.source_sha !== "string" || !/^[0-9a-f]{40,64}$/.test(details.source_sha)
      ) return null;
      return {
        available: true,
        status: age > 24 * 60 * 60 * 1000 ? "stale" : details.status,
        scope: details.scope,
        policy_mode: details.policy_mode,
        live_inference_default: details.live_inference_default,
        allow_paid_model_calls: details.allow_paid_model_calls,
        paid_fallback_enabled: details.paid_fallback_enabled,
        allow_external_writes: details.allow_external_writes,
        source_sha: details.source_sha,
        recorded_at: killSwitchRow.created_at,
        freshness: age > 24 * 60 * 60 * 1000 ? "stale" : "fresh",
      };
    })();
    const workerCronStatus = (() => {
      const invalid = { value: null, unavailable: "Latest Worker Cron heartbeat metadata is invalid" };
      if (!workerCron) return { value: null, unavailable: "No Worker Cron heartbeat has been recorded yet" };
      if (typeof workerCron.created_at !== "string" || !Number.isFinite(Date.parse(workerCron.created_at))) return invalid;
      let details;
      try { details = JSON.parse(workerCron.details_json || "{}"); } catch { return invalid; }
      if (!details || typeof details !== "object" || Array.isArray(details)) return invalid;
      const count = value => Number.isInteger(value) && value >= 0 ? value : null;
      return {
        value: {
          last_seen_at: workerCron.created_at,
          cron: typeof details.cron === "string" && details.cron.length <= 100 ? details.cron : null,
          requeued: count(details.requeued),
          failed: count(details.failed),
        },
        unavailable: null,
      };
    })();
    const [paper, latest, snapshot, usage, modelCosts, monetary] = await Promise.all([
      applied.has("0004_paper_event_ledger")
        ? env.DB.prepare("SELECT event_type,COUNT(*) AS count FROM paper_events GROUP BY event_type ORDER BY event_type").all()
        : Promise.resolve({ results: [] }),
      applied.has("0004_paper_event_ledger")
        ? env.DB.prepare("SELECT event_type,candidate_id,occurred_at FROM paper_events ORDER BY occurred_at DESC,event_id DESC LIMIT 1").first()
        : Promise.resolve(null),
      applied.has("0004_paper_event_ledger")
        ? env.DB.prepare("SELECT candidate_id,occurred_at,chain_id,block_number FROM paper_events WHERE event_type='snapshot_captured' ORDER BY occurred_at DESC,event_id DESC LIMIT 1").first()
        : Promise.resolve(null),
      applied.has("0003_durable_quota_ledger")
        ? env.DB.prepare("SELECT route_id,COUNT(*) AS requests,COALESCE(SUM(tokens),0) AS tokens FROM quota_usage GROUP BY route_id ORDER BY route_id").all()
        : Promise.resolve({ results: [] }),
      applied.has("0006_monetary_budget_ledger")
        ? env.DB.prepare("SELECT provider,COUNT(*) AS requests,COALESCE(SUM(input_tokens),0) AS input_tokens,COALESCE(SUM(output_tokens),0) AS output_tokens,COALESCE(SUM(actual_cost_nanos),0)/1000000000.0 AS cost_usd FROM monetary_reservations WHERE status='settled' GROUP BY provider ORDER BY provider").all()
        : env.DB.prepare("SELECT provider,COUNT(*) AS requests,COALESCE(SUM(input_tokens),0) AS input_tokens,COALESCE(SUM(output_tokens),0) AS output_tokens,COALESCE(SUM(cost_usd),0) AS cost_usd FROM model_usage GROUP BY provider ORDER BY provider").all(),
      applied.has("0006_monetary_budget_ledger")
        ? (async () => {
            const current = new Date();
            const day = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), current.getUTCDate())).toISOString();
            const month = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), 1)).toISOString();
            return env.DB.prepare(`
              SELECT
                (SELECT COALESCE(SUM(actual_cost_nanos),0) FROM monetary_reservations WHERE status='settled' AND settled_at>=?) AS daily_spent_nanos,
                (SELECT COALESCE(SUM(actual_cost_nanos),0) FROM monetary_reservations WHERE status='settled' AND settled_at>=?) AS monthly_spent_nanos,
                (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM monetary_reservations WHERE status IN ('reserved','in_flight','ambiguous')) AS pending_reserved_nanos,
                (SELECT COUNT(*) FROM monetary_reservations WHERE status IN ('reserved','in_flight','ambiguous')) AS pending_count,
                (SELECT COUNT(*) FROM monetary_reservations WHERE status='ambiguous') AS ambiguous_count,
                (SELECT COUNT(*) FROM monetary_route_quarantines) AS quarantined_routes,
                (SELECT daily_budget_nanos FROM monetary_budget_policy WHERE policy_key='default') AS daily_budget_nanos,
                (SELECT monthly_budget_nanos FROM monetary_budget_policy WHERE policy_key='default') AS monthly_budget_nanos,
                (SELECT per_task_budget_nanos FROM monetary_budget_policy WHERE policy_key='default') AS per_task_budget_nanos,
                (SELECT enabled FROM monetary_budget_policy WHERE policy_key='default') AS budget_policy_enabled
            `).bind(day, month).first();
          })()
        : Promise.resolve(null),
    ]);
    return json({
      generated_at: new Date().toISOString(),
      tasks: (tasks.results || []).map(row => ({ status: row.status, count: Number(row.count) || 0 })),
      paper_events: (paper.results || []).map(row => ({ event_type: row.event_type, count: Number(row.count) || 0 })),
      latest_paper_event: latest ? {
        event_type: latest.event_type, candidate_id: latest.candidate_id, occurred_at: latest.occurred_at,
      } : null,
      latest_market_snapshot: snapshot ? {
        candidate_id: snapshot.candidate_id, occurred_at: snapshot.occurred_at,
        chain_id: snapshot.chain_id, block_number: snapshot.block_number,
      } : null,
      provider_usage: {
        requests: (usage.results || []).reduce((sum, row) => sum + (Number(row.requests) || 0), 0),
        tokens: (usage.results || []).reduce((sum, row) => sum + (Number(row.tokens) || 0), 0),
      },
      telegram_webhook_schema: {
        available: applied.has("0005_telegram_webhook_receipts"),
        status: applied.has("0005_telegram_webhook_receipts")
          ? "Migration 0005 applied; webhook activation remains separately gated"
          : "Migration 0005 is not applied to this database",
      },
      worker_cron: workerCronStatus.value,
      ci_health: ciHealth,
      kill_switch: killSwitch,
      last_scan: researchScan.value,
      source_freshness: { status: sourceFreshness.status, sources: sourceFreshness.value },
      task_runner_watchdog: {
        ready: watchdogReadiness.ready,
        missing: watchdogReadiness.missing,
        last_dispatch_requested: lastDispatchRequested,
        last_dispatch_intent_at: lastDispatchIntent?.created_at || null,
      },
      model_costs_usd: {
        total: (modelCosts.results || []).reduce((sum, row) => sum + (Number(row.cost_usd) || 0), 0),
        source: applied.has("0006_monetary_budget_ledger") ? "D1 monetary_reservations; legacy model_usage costs backfilled" : "model_usage D1 ledger; recorded provider costs only",
      },
      task_invocations: taskInvocations || { available: false },
      monetary_ledger: monetary ? {
        available: true,
        daily_spent_usd: Number(monetary.daily_spent_nanos || 0) / 1000000000,
        monthly_spent_usd: Number(monetary.monthly_spent_nanos || 0) / 1000000000,
        pending_reserved_usd: Number(monetary.pending_reserved_nanos || 0) / 1000000000,
        pending_count: Number(monetary.pending_count || 0),
        ambiguous_count: Number(monetary.ambiguous_count || 0),
        quarantined_routes: Number(monetary.quarantined_routes || 0),
        daily_budget_usd: Number(monetary.daily_budget_nanos || 0) / 1000000000,
        monthly_budget_usd: Number(monetary.monthly_budget_nanos || 0) / 1000000000,
        per_task_budget_usd: Number(monetary.per_task_budget_nanos || 0) / 1000000000,
        budget_policy_enabled: Number(monetary.budget_policy_enabled) === 1,
        unit: "USD",
        source: "D1 monetary_reservations; integer nano-USD",
      } : { available: false },
      unavailable_metrics: {
        ...(!researchScan.value ? { last_scan: researchScan.unavailable } : {}),
        ...(sourceFreshness.status === "unknown" ? { source_freshness: "No valid source observations have been recorded" } : {}),
        ...(!ciHealth ? { ci_health: "No fresh validated Engine CI snapshot has been ingested from GitHub Actions" } : {}),
        ...(!killSwitch ? { kill_switch: "No fresh validated default Task Runner inference-gate snapshot has been ingested" } : {}),
        ...(!workerCronStatus.value ? { worker_cron: workerCronStatus.unavailable } : {}),
        paper_ledger: applied.has("0004_paper_event_ledger") ? "Available" : "Migration 0004 is not applied to this database",
        provider_usage: applied.has("0003_durable_quota_ledger") ? "Available" : "Migration 0003 is not applied to this database",
        monetary_ledger: applied.has("0006_monetary_budget_ledger") ? "Available" : "Migration 0006 is not applied to this database",
        ...(!applied.has("0007_task_model_invocations") ? { task_invocations: "Migration 0007 is not applied to this database" } :
          !taskInvocations?.available ? { task_invocations: "D1 invocation lifecycle aggregate is unavailable" } : {}),
      },
    });
  } catch {
    return json({ error: "storage_unavailable" }, 503);
  }
}
