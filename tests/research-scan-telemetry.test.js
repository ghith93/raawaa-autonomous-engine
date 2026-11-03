import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";

const TOKEN = "research-scan-test-token";
const scan = {
  scan_id:"123:1",scan_type:"github_crossref",workflow:"research-discovery.yml",
  run_id:123,run_number:7,run_attempt:1,head_sha:"a".repeat(40),
  started_at:"2026-11-03T12:00:00.000Z",completed_at:"2026-11-03T12:05:00.000Z",
  status:"success",requests_made:6,academic_requests_made:1,
  candidate_count:5,academic_candidate_count:4,unavailable_query_count:0,
};
const observations = ["github","crossref"].map((source,index)=>({
  scan_id:"123:1",source,status:"fresh",observed_at:"2026-11-03T12:05:00.000Z",
  freshness_hours:24,request_count:index?1:6,item_count:index?4:5,reason_code:null,
}));
function dbMock(){
 const calls=[];
 return {calls,prepare(sql){return {sql,values:[],bind(...values){this.values=values;return this;},
 async run(){calls.push({kind:"run",sql,values:this.values});return {success:true,meta:{changes:1}};},
 async all(){calls.push({kind:"all",sql,values:this.values});return {results:[]};},
 async first(){calls.push({kind:"first",sql,values:this.values});return {ok:1};}
 };},async batch(statements){calls.push({kind:"batch",statements});return statements.map(()=>({success:true,meta:{changes:1}}));}};}
function req(body,token=TOKEN,method="POST"){
 return new Request("https://engine.example/v1/research/scan-runs",{method,headers:{"content-type":"application/json",...(token?{authorization:"Bearer "+token}:{})},body:method==="GET"?undefined:JSON.stringify(body)});
}
test("scan telemetry requires the Engine bearer token before touching D1",async()=>{
 const DB=dbMock();
 const response=await worker.fetch(req({scan,source_observations:observations},null),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,401);assert.equal(DB.calls.length,0);
});
test("scan telemetry accepts a validated scan and both source observations",async()=>{
 const DB=dbMock();
 const response=await worker.fetch(req({scan,source_observations:observations}),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,201);
 assert.deepEqual(await response.json(),{ok:true,scan_id:"123:1",recorded_events:3});
 assert.equal(DB.calls[0].kind,"batch");
 assert.equal(DB.calls[0].statements.length,3);
});
test("scan telemetry rejects missing source or mismatched scan identity",async()=>{
 const DB=dbMock();
 const response=await worker.fetch(req({scan,source_observations:[observations[0]]}),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,400);assert.equal(DB.calls.length,0);
 const mismatch={...observations[1],scan_id:"other"};
 const response2=await worker.fetch(req({scan,source_observations:[observations[0],mismatch]}),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response2.status,400);assert.equal(DB.calls.length,0);
});
test("scan telemetry rejects untrusted workflow, malformed timestamps, and unknown reason codes",async()=>{
 for(const invalid of [
  {...scan,workflow:"attacker.yml"},
  {...scan,completed_at:"not-a-date"},
  {...scan,status:"invented"},
 ]){
  const DB=dbMock();
  const response=await worker.fetch(req({scan:invalid,source_observations:observations}),{DB,ENGINE_API_TOKEN:TOKEN});
  assert.equal(response.status,400);assert.equal(DB.calls.length,0);
 }
 const badObs=[observations[0],{...observations[1],status:"unavailable",reason_code:"raw-provider-error"}];
 const DB=dbMock();
 const response=await worker.fetch(req({scan,source_observations:badObs}),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,400);assert.equal(DB.calls.length,0);
});
test("scan telemetry endpoint rejects GET and is authenticated",async()=>{
 const DB=dbMock();
 const response=await worker.fetch(req({},TOKEN,"GET"),{DB,ENGINE_API_TOKEN:TOKEN});
 assert.equal(response.status,405);assert.equal(DB.calls.length,0);
});
