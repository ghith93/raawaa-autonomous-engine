import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";

const TOKEN = "mobile-status-test-token";
function dbMock(migrationVersions=["0003_durable_quota_ledger","0004_paper_event_ledger","0009_task_execution_mode"], workerCron={created_at:"2026-11-03T00:00:00.000Z",details_json:JSON.stringify({cron:"*/15 * * * *",requeued:2,failed:1})}, watchdogEvents=[], ciHealthRow=null, killSwitchRow=null, invocationCounts=[], scanRow=null, sourceRows=[], executionModeStatusCounts=[
  {execution_mode:"not_started",status:"pending",count:3},
  {execution_mode:"dry_run",status:"succeeded",count:2},
  {execution_mode:"live_inference",status:"succeeded",count:1},
  {execution_mode:"unknown",status:"succeeded",count:4},
]) {
  const calls=[];
  return {
    calls,
    prepare(sql) {
      return {
        sql, values: [],
        bind(...values){this.values=values;return this;},
        async first(){calls.push({kind:"first",sql,values:this.values});if(sql.includes("worker_cron_tick"))return workerCron;if(sql.includes("ci_health_snapshot"))return ciHealthRow;if(sql.includes("kill_switch_snapshot"))return killSwitchRow;if(sql.includes("research_scan_completed"))return scanRow;if(sql.includes("snapshot_captured"))return {candidate_id:"candidate-2",occurred_at:"2026-11-02T12:30:00.000Z",chain_id:1,block_number:123};return {event_type:"candidate_detected",candidate_id:"candidate-1",occurred_at:"2026-11-02T12:00:00.000Z"};},
        async all(){
          calls.push({kind:"all",sql,values:this.values});
          if(sql.includes("GROUP BY execution_mode,status")) return {results:executionModeStatusCounts};
          if(sql.includes("GROUP BY execution_mode,status")) return {results:executionModeStatusCounts};
          if(sql.includes("FROM tasks")) return {results:[{status:"pending",count:2}]};
          if(sql.includes("FROM task_model_invocations")) return {results:invocationCounts};
          if(sql.includes("schema_migrations")) return {results:migrationVersions.map(version=>({version}))};
          if(sql.includes("FROM paper_events")) return {results:[{event_type:"candidate_detected",count:4}]};
          if(sql.includes("FROM quota_usage")) return {results:[{route_id:"groq:gpt-oss-20b",requests:3,tokens:1200}]};
          if(sql.includes("FROM model_usage")) return {results:[{provider:"groq",requests:2,input_tokens:900,output_tokens:300,cost_usd:0.0125}]};
          if(sql.includes("task_runner_dispatch_requested")) return {results:watchdogEvents};
          if(sql.includes("research_source_observation")) return {results:sourceRows};
          return {results:[]};
        },
      };
    },
  };
}
function request(path,token=TOKEN,method="GET"){
 const headers={};if(token)headers.authorization="Bearer "+token;
 return new Request("https://engine.example"+path,{method,headers});
}

test("mobile status page is public static HTML and never embeds an API token",async()=>{
 const response=await worker.fetch(request("/mobile",null),{DB:dbMock()});
 assert.equal(response.status,200);
 assert.match(response.headers.get("content-type"),/text\/html/);
 const body=await response.text();
 assert.match(body,/Read-only operational snapshot/);
 assert.match(body,/Recorded model inference costs/);
 assert.match(body,/Worker Cron/);
 assert.match(body,/Task Runner watchdog/);
 assert.match(body,/Latest explicit research scan/);
 assert.match(body,/Research source freshness/);
 assert.match(body,/worker_cron/);
 assert.match(body,/model_costs_usd/);
 assert.match(body,/toFixed\(6\)/);
 assert.match(body,/sessionToken|accessToken/);
 assert.doesNotMatch(body,/mobile-status-test-token/);
 assert.equal(response.headers.get("cache-control"),"no-store");
 assert.match(response.headers.get("content-security-policy"),/frame-ancestors/);
 assert.doesNotMatch(response.headers.get("content-security-policy"),/api\\.github\\.com/);
 assert.doesNotMatch(body,/api\\.github\\.com/); 
 assert.match(body,/Default Task Runner inference gate/);
 assert.match(body,/Durable task-step invocations/);
 assert.match(body,/unresolved_count/);
});

test("mobile status endpoint requires the existing Engine bearer token",async()=>{
 const response=await worker.fetch(request("/v1/mobile/status",null),{DB:dbMock(),ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,401);
});

test("mobile status endpoint returns read-only D1 metrics and explicit unknowns",async()=>{
 const DB=dbMock();
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,200);
 const body=await response.json();
 assert.deepEqual(body.tasks,[{status:"pending",count:2}]);
 assert.deepEqual(body.paper_events,[{event_type:"candidate_detected",count:4}]);
 assert.equal(body.latest_paper_event.candidate_id,"candidate-1");
 assert.deepEqual(body.latest_market_snapshot,{candidate_id:"candidate-2",occurred_at:"2026-11-02T12:30:00.000Z",chain_id:1,block_number:123});
 assert.deepEqual(body.provider_usage,{requests:3,tokens:1200});
 assert.deepEqual(body.model_costs_usd,{total:0.0125,source:"model_usage D1 ledger; recorded provider costs only"});
 assert.equal(JSON.stringify(body).includes("groq:gpt-oss-20b"),false);
 assert.equal(JSON.stringify(body).includes('"provider":"groq"'),false);
 assert.equal(JSON.stringify(body).includes("reservation_id"),false);
 assert.deepEqual(body.execution_modes,{
  available:true,
  counts:{not_started:3,dry_run:2,live_inference:1,unknown:4},
  status_counts:{
   not_started:{pending:3,running:0,succeeded:0,failed:0,blocked:0},
   dry_run:{pending:0,running:0,succeeded:2,failed:0,blocked:0},
   live_inference:{pending:0,running:0,succeeded:1,failed:0,blocked:0},
   unknown:{pending:0,running:0,succeeded:4,failed:0,blocked:0},
  },
 });
 assert.deepEqual(body.execution_modes,{
  available:true,
  counts:{not_started:3,dry_run:2,live_inference:1,unknown:4},
  status_counts:{
   not_started:{pending:3,running:0,succeeded:0,failed:0,blocked:0},
   dry_run:{pending:0,running:0,succeeded:2,failed:0,blocked:0},
   live_inference:{pending:0,running:0,succeeded:1,failed:0,blocked:0},
   unknown:{pending:0,running:0,succeeded:4,failed:0,blocked:0},
  },
 });
 assert.deepEqual(body.worker_cron,{last_seen_at:"2026-11-03T00:00:00.000Z",cron:"*/15 * * * *",requeued:2,failed:1});
 assert.deepEqual(body.task_runner_watchdog,{ready:false,missing:["GITHUB_APP_ID","GITHUB_APP_INSTALLATION_ID","GITHUB_APP_PRIVATE_KEY","GITHUB_APP_WATCHDOG_ENABLED"],last_dispatch_requested:null,last_dispatch_intent_at:null});
 assert.equal(body.unavailable_metrics.worker_cron,undefined);
 assert.equal(body.unavailable_metrics.monetary_costs,undefined);
 assert.ok(DB.calls.every(call=>call.kind==="all"||call.kind==="first"));
});



test("mobile status exposes only watchdog readiness and sanitized dispatch audit metadata",async()=>{
 const events=[
  {event:"task_runner_dispatch_requested",created_at:"2026-11-03T01:10:00.000Z",details_json:JSON.stringify({workflow:"task-runner.yml",ref:"main",http_status:204,scheduled_time:"2026-11-03T01:06:00.000Z"})},
  {event:"task_runner_dispatch_intent",created_at:"2026-11-03T01:09:59.000Z",details_json:JSON.stringify({workflow:"task-runner.yml",ref:"main",scheduled_time:"2026-11-03T01:06:00.000Z"})},
 ];
 const DB=dbMock(undefined,undefined,events);
 const env={DB,ENGINE_API_TOKEN:TOKEN,GITHUB_APP_ID:"12345",GITHUB_APP_INSTALLATION_ID:"67890",GITHUB_APP_PRIVATE_KEY:"PRIVATE_KEY_NEVER_RETURN",GITHUB_APP_WATCHDOG_ENABLED:"enabled"};
 const response=await worker.fetch(request("/v1/mobile/status"),env);
 assert.equal(response.status,200);
 const body=await response.json();
 assert.deepEqual(body.task_runner_watchdog,{
  ready:true,missing:[],
  last_dispatch_requested:{event:"task_runner_dispatch_requested",created_at:"2026-11-03T01:10:00.000Z",workflow:"task-runner.yml",ref:"main",http_status:204},
  last_dispatch_intent_at:"2026-11-03T01:09:59.000Z",
 });
 assert.equal(JSON.stringify(body).includes("PRIVATE_KEY_NEVER_RETURN"),false);
});

test("mobile status reports Worker Cron history as unknown when no heartbeat exists",async()=>{
 const DB=dbMock(undefined,null);
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,200);
 const body=await response.json();
 assert.equal(body.worker_cron,null);
 assert.equal(body.unavailable_metrics.worker_cron,"No Worker Cron heartbeat has been recorded yet");
});


test("mobile status returns fresh validated CI and default inference-gate snapshots",async()=>{
 const now=new Date().toISOString();
 const ci={created_at:now,details_json:JSON.stringify({
  workflow:"Engine CI",run_id:123456,run_number:78,event:"push",branch:"main",
  head_sha:"a".repeat(40),conclusion:"success",
  jobs:{test:"success",staging_migrate:"success",staging_worker_verify:"success"},
 })};
 const gate={created_at:now,details_json:JSON.stringify({
  scope:"scheduled_task_runner_default",status:"engaged",policy_mode:"safe_dry_run",
  live_inference_default:"disabled",allow_paid_model_calls:false,
  paid_fallback_enabled:false,allow_external_writes:false,source_sha:"a".repeat(40),
 })};
 const DB=dbMock(undefined,undefined,[],ci,gate);
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,200);
 const body=await response.json();
 assert.equal(body.ci_health.status,"success");
 assert.equal(body.ci_health.run_id,123456);
 assert.equal(body.ci_health.freshness,"fresh");
 assert.equal(body.kill_switch.status,"engaged");
 assert.equal(body.kill_switch.live_inference_default,"disabled");
 assert.equal(body.kill_switch.allow_paid_model_calls,false);
 assert.equal(body.kill_switch.freshness,"fresh");
 assert.equal(body.unavailable_metrics.ci_health,undefined);
 assert.equal(body.unavailable_metrics.kill_switch,undefined);
});

test("mobile status separates dry-run and legacy successes from live execution",async()=>{
 const DB=dbMock();
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,200);
 const body=await response.json();
 assert.equal(body.execution_modes.status_counts.dry_run.succeeded,2);
 assert.equal(body.execution_modes.status_counts.live_inference.succeeded,1);
 assert.equal(body.execution_modes.status_counts.unknown.succeeded,4);
});

test("mobile status separates dry-run and legacy successes from live execution",async()=>{
 const DB=dbMock();
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,200);
 const body=await response.json();
 assert.equal(body.execution_modes.status_counts.dry_run.succeeded,2);
 assert.equal(body.execution_modes.status_counts.live_inference.succeeded,1);
 assert.equal(body.execution_modes.status_counts.unknown.succeeded,4);
});

test("mobile status preserves stale CI and inference-gate snapshots as stale",async()=>{
 const stale=new Date(Date.now()-25*60*60*1000).toISOString();
 const ci={created_at:stale,details_json:JSON.stringify({
  workflow:"Engine CI",run_id:123456,run_number:78,event:"push",branch:"main",
  head_sha:"b".repeat(40),conclusion:"success",
  jobs:{test:"success",staging_migrate:"success",staging_worker_verify:"success"},
 })};
 const gate={created_at:stale,details_json:JSON.stringify({
  scope:"scheduled_task_runner_default",status:"engaged",policy_mode:"safe_dry_run",
  live_inference_default:"disabled",allow_paid_model_calls:false,
  paid_fallback_enabled:false,allow_external_writes:false,source_sha:"b".repeat(40),
 })};
 const DB=dbMock(undefined,undefined,[],ci,gate);
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 const body=await response.json();
 assert.equal(body.ci_health.status,"stale");
 assert.equal(body.ci_health.freshness,"stale");
 assert.equal(body.kill_switch.status,"stale");
 assert.equal(body.kill_switch.freshness,"stale");
});

test("mobile status exposes only aggregate durable task invocation lifecycle counts",async()=>{
 const versions=["0003_durable_quota_ledger","0004_paper_event_ledger","0006_monetary_budget_ledger","0007_task_model_invocations"];
 const counts=[
  {status:"prepared",count:2},
  {status:"in_flight",count:1},
  {status:"ambiguous",count:3},
  {status:"settled",count:9},
 ];
 const DB=dbMock(versions,undefined,[],null,null,counts);
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,200);
 const body=await response.json();
 assert.deepEqual(body.task_invocations,{
  available:true,
  counts:{prepared:2,in_flight:1,ambiguous:3,settled:9},
  unresolved_count:6,
 });
 const serialized=JSON.stringify(body);
 for(const secret of ["task-123","request-123","lease-secret","prompt text"]) assert.equal(serialized.includes(secret),false);
});

test("mobile status does not query invocation tables before migration 0007",async()=>{
 const DB=dbMock(["0003_durable_quota_ledger","0004_paper_event_ledger"]);
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,200);
 const body=await response.json();
 assert.deepEqual(body.task_invocations,{available:false});
 assert.equal(body.unavailable_metrics.task_invocations,"Migration 0007 is not applied to this database");
 assert.equal(DB.calls.some(call=>call.sql.includes("FROM task_model_invocations")),false);
 assert.deepEqual(body.execution_modes,{available:false});
 assert.equal(body.unavailable_metrics.execution_modes,"Migration 0009 is not applied to this database");
 assert.equal(DB.calls.some(call=>call.sql.includes("GROUP BY execution_mode,status")),false);
});


test("mobile status reports explicit scan and per-source freshness without disturbing task invocation aggregates",async()=>{
 const now=new Date().toISOString();
 const scan={scan_id:"123:1",scan_type:"github_crossref",workflow:"research-discovery.yml",
  run_id:123,run_number:7,run_attempt:1,head_sha:"a".repeat(40),
  started_at:now,completed_at:now,status:"partial",requests_made:6,academic_requests_made:1,
  candidate_count:4,academic_candidate_count:2,unavailable_query_count:1};
 const scanRow={created_at:now,details_json:JSON.stringify(scan)};
 const sourceRows=[
  {created_at:now,details_json:JSON.stringify({scan_id:"123:1",source:"github",status:"fresh",observed_at:now,freshness_hours:24,request_count:6,item_count:4,reason_code:null})},
  {created_at:now,details_json:JSON.stringify({scan_id:"123:1",source:"crossref",status:"unavailable",observed_at:now,freshness_hours:24,request_count:1,item_count:0,reason_code:"http_429"})},
 ];
 const invocations=[{status:"ambiguous",count:1},{status:"settled",count:2}];
 const DB=dbMock(["0003_durable_quota_ledger","0004_paper_event_ledger","0006_monetary_budget_ledger","0007_task_model_invocations"],undefined,[],null,null,invocations,scanRow,sourceRows);
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,200);
 const body=await response.json();
 assert.equal(body.last_scan.scan_id,"123:1");
 assert.equal(body.last_scan.status,"partial");
 assert.equal(body.last_scan.unavailable_query_count,1);
 assert.equal(body.source_freshness.status,"partial");
 assert.equal(body.source_freshness.sources.github.status,"fresh");
 assert.equal(body.source_freshness.sources.crossref.status,"unavailable");
 assert.equal(body.source_freshness.sources.crossref.reason_code,"http_429");
 assert.equal(body.task_invocations.counts.ambiguous,1);
 assert.equal(body.task_invocations.counts.settled,2);
 assert.equal(body.unavailable_metrics.last_scan,undefined);
});
test("mobile status keeps malformed scan and missing source observations explicitly unknown",async()=>{
 const scanRow={created_at:"not-a-date",details_json:"not-json"};
 const DB=dbMock(undefined,undefined,[],null,null,[],scanRow,[]);
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 const body=await response.json();
 assert.equal(body.last_scan,null);
 assert.match(body.unavailable_metrics.last_scan,/invalid/);
 assert.deepEqual(body.source_freshness,{status:"unknown",sources:null});
 assert.match(body.unavailable_metrics.source_freshness,/No valid source observations/);
});
test("mobile status endpoint rejects writes",async()=>{
 const response=await worker.fetch(request("/v1/mobile/status",TOKEN,"POST"),{DB:dbMock(),ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,405);
});

test("unauthenticated status requests do not query D1",async()=>{
 const DB=dbMock();
 const response=await worker.fetch(request("/v1/mobile/status",null),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,401);
 assert.equal(DB.calls.length,0);
});
