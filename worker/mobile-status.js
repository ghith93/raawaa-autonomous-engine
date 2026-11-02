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
<section class="panel"><h2>Latest market snapshot</h2><div id="snapshot" class="muted">—</div></section><section class="panel"><h2>Latest paper event</h2><div id="latest" class="muted">—</div></section><section class="panel"><h2>Telegram control readiness</h2><div id="telegramSchema" class="rows"></div><p class="muted">Schema presence only; webhook activation still requires operator-managed secrets and Telegram configuration.</p></section><section class="panel"><h2>GitHub CI health</h2><div id="ci" class="rows"></div></section>
<section class="panel"><h2>Not yet connected to this status feed</h2><div id="gaps" class="rows"></div></section>
<button id="refresh">Refresh</button><button class="secondary" id="disconnect">Disconnect</button>
<p class="muted" id="updated"></p>
</section>
</main>
<script>
let accessToken = "";
const $ = id => document.getElementById(id);
const escapeText = value => String(value ?? "—");
async function loadCi() {
  try {
    const response=await fetch("https://api.github.com/repos/ghith93/raawaa-autonomous-engine/actions/runs?branch=main&per_page=10",{headers:{accept:"application/vnd.github+json"},cache:"no-store"});
    if(!response.ok) throw new Error("GitHub API unavailable");
    const data=await response.json();
    const runs=(data.workflow_runs||[]).filter(run=>run.name==="Engine CI"||run.name==="Scheduled Open-Source Research Discovery").slice(0,4);
    rows("ci",runs.map(run=>[run.name,run.status+(run.conclusion?" · "+run.conclusion:" · running")+" · "+run.updated_at]),"No recent CI runs");
  } catch { rows("ci",[],"GitHub API unavailable or rate-limited"); }
}
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
  rows("usage",(data.provider_usage||[]).map(x=>[x.route_id,(x.requests||0)+" requests · "+(x.tokens||0)+" tokens"]),"No D1 provider usage recorded");
  const costs=data.model_costs_usd||{};
  const byProvider=(costs.by_provider||[]).map(x=>[
    x.provider,
    "$"+(Number(x.cost_usd)||0).toFixed(6)+" · "+(x.requests||0)+" requests · "+((x.input_tokens||0)+(x.output_tokens||0))+" tokens"
  ]);
  rows("modelCosts",[
    ["Total recorded", "$"+(Number(costs.total)||0).toFixed(6)],
    ...byProvider
  ],"No recorded model inference costs");
  $("snapshot").textContent=data.latest_market_snapshot?data.latest_market_snapshot.candidate_id+" · chain "+(data.latest_market_snapshot.chain_id??"—")+" · block "+(data.latest_market_snapshot.block_number??"—")+" · "+data.latest_market_snapshot.occurred_at:"No market snapshot recorded";
  $("latest").textContent=data.latest_paper_event?data.latest_paper_event.event_type+" · "+data.latest_paper_event.candidate_id+" · "+data.latest_paper_event.occurred_at:"No paper event recorded";
  rows("telegramSchema",[[ "Webhook receipt schema", data.telegram_webhook_schema?.status || "Unknown" ]],"Schema status unavailable");
  await loadCi();
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
    "content-security-policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self' https://api.github.com; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  }});
}

export async function handleMobileStatus(request, env, url) {
  if (url.pathname !== "/v1/mobile/status") return null;
  if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
  try {
    const [tasks, migrations] = await Promise.all([
      env.DB.prepare("SELECT status,COUNT(*) AS count FROM tasks GROUP BY status").all(),
      env.DB.prepare("SELECT version FROM schema_migrations WHERE version IN ('0003_durable_quota_ledger','0004_paper_event_ledger','0005_telegram_webhook_receipts')").all(),
    ]);
    const applied = new Set((migrations.results || []).map(row => row.version));
    const [paper, latest, snapshot, usage, modelCosts] = await Promise.all([
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
      env.DB.prepare("SELECT provider,COUNT(*) AS requests,COALESCE(SUM(input_tokens),0) AS input_tokens,COALESCE(SUM(output_tokens),0) AS output_tokens,COALESCE(SUM(cost_usd),0) AS cost_usd FROM model_usage GROUP BY provider ORDER BY provider").all(),
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
      provider_usage: (usage.results || []).map(row => ({
        route_id: row.route_id, requests: Number(row.requests) || 0, tokens: Number(row.tokens) || 0,
      })),
      telegram_webhook_schema: {
        available: applied.has("0005_telegram_webhook_receipts"),
        status: applied.has("0005_telegram_webhook_receipts")
          ? "Migration 0005 applied; webhook activation remains separately gated"
          : "Migration 0005 is not applied to this database",
      },
      model_costs_usd: {
        total: (modelCosts.results || []).reduce((sum, row) => sum + (Number(row.cost_usd) || 0), 0),
        by_provider: (modelCosts.results || []).map(row => ({
          provider: row.provider,
          requests: Number(row.requests) || 0,
          input_tokens: Number(row.input_tokens) || 0,
          output_tokens: Number(row.output_tokens) || 0,
          cost_usd: Number(row.cost_usd) || 0,
        })),
        source: "model_usage D1 ledger; recorded provider costs only",
      },
      unavailable_metrics: {
        last_scan: "Scan-run heartbeat is not recorded; the latest captured market snapshot is shown separately",
        source_freshness: "Not yet aggregated from market-data adapters",
        ci_health: "Not yet ingested from GitHub Actions",
        kill_switch: "Engine policy state is not yet mirrored to the Worker",
        paper_ledger: applied.has("0004_paper_event_ledger") ? "Available" : "Migration 0004 is not applied to this database",
        provider_usage: applied.has("0003_durable_quota_ledger") ? "Available" : "Migration 0003 is not applied to this database",
      },
    });
  } catch {
    return json({ error: "storage_unavailable" }, 503);
  }
}
