import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";

const TOKEN = "mobile-status-test-token";
function dbMock(migrationVersions=["0003_durable_quota_ledger","0004_paper_event_ledger","0005_telegram_webhook_receipts"], workerCron={created_at:"2026-11-03T00:00:00.000Z",details_json:JSON.stringify({cron:"*/15 * * * *",requeued:2,failed:1})}) {
  const calls=[];
  return {
    calls,
    prepare(sql) {
      return {
        sql, values: [],
        bind(...values){this.values=values;return this;},
        async first(){calls.push({kind:"first",sql,values:this.values});if(sql.includes("worker_cron_tick"))return workerCron;if(sql.includes("snapshot_captured"))return {candidate_id:"candidate-2",occurred_at:"2026-11-02T12:30:00.000Z",chain_id:1,block_number:123};return {event_type:"candidate_detected",candidate_id:"candidate-1",occurred_at:"2026-11-02T12:00:00.000Z"};},
        async all(){
          calls.push({kind:"all",sql,values:this.values});
          if(sql.includes("FROM tasks")) return {results:[{status:"pending",count:2}]};
          if(sql.includes("schema_migrations")) return {results:migrationVersions.map(version=>({version}))};
          if(sql.includes("FROM paper_events")) return {results:[{event_type:"candidate_detected",count:4}]};
          if(sql.includes("FROM quota_usage")) return {results:[{route_id:"groq:gpt-oss-20b",requests:3,tokens:1200}]};
          if(sql.includes("FROM model_usage")) return {results:[{provider:"groq",requests:2,input_tokens:900,output_tokens:300,cost_usd:0.0125}]};
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
 assert.match(body,/Telegram control readiness/);
 assert.match(body,/Worker Cron/);
 assert.match(body,/worker_cron/);
 assert.match(body,/model_costs_usd/);
 assert.match(body,/toFixed\(6\)/);
 assert.match(body,/sessionToken|accessToken/);
 assert.doesNotMatch(body,/mobile-status-test-token/);
 assert.equal(response.headers.get("cache-control"),"no-store");
 assert.match(response.headers.get("content-security-policy"),/frame-ancestors/);
 assert.ok(response.headers.get("content-security-policy").includes("https://api.github.com"));
 assert.ok(body.includes("api.github.com/repos/ghith93/raawaa-autonomous-engine/actions/runs"));
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
 assert.deepEqual(body.provider_usage,[{route_id:"groq:gpt-oss-20b",requests:3,tokens:1200}]);
 assert.deepEqual(body.model_costs_usd,{total:0.0125,by_provider:[{provider:"groq",requests:2,input_tokens:900,output_tokens:300,cost_usd:0.0125}],source:"model_usage D1 ledger; recorded provider costs only"});
 assert.deepEqual(body.telegram_webhook_schema,{available:true,status:"Migration 0005 applied; webhook activation remains separately gated"});
 assert.deepEqual(body.worker_cron,{last_seen_at:"2026-11-03T00:00:00.000Z",cron:"*/15 * * * *",requeued:2,failed:1});
 assert.equal(body.unavailable_metrics.worker_cron,undefined);
 assert.equal(body.unavailable_metrics.monetary_costs,undefined);
 assert.ok(DB.calls.every(call=>call.kind==="all"||call.kind==="first"));
});


test("mobile status reports Worker Cron history as unknown when no heartbeat exists",async()=>{
 const DB=dbMock(undefined,null);
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,200);
 const body=await response.json();
 assert.equal(body.worker_cron,null);
 assert.equal(body.unavailable_metrics.worker_cron,"No Worker Cron heartbeat has been recorded yet");
});

test("mobile status reports Telegram webhook schema as unavailable until migration 0005 is applied",async()=>{
 const DB=dbMock(["0003_durable_quota_ledger","0004_paper_event_ledger"]);
 const response=await worker.fetch(request("/v1/mobile/status"),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,200);
 const body=await response.json();
 assert.deepEqual(body.telegram_webhook_schema,{available:false,status:"Migration 0005 is not applied to this database"});
 assert.equal(DB.calls.some(call=>call.sql.includes("telegram_update_receipts")),false);
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
