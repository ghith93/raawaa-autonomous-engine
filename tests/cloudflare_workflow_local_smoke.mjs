import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";

const PORT = 8791;
const BASE = "http://127.0.0.1:" + PORT;
const CONFIG = "spikes/cloudflare-workflows/wrangler.toml";
const INSTANCE = /^task-[a-f0-9]{32}$/;

function safeWranglerEnvironment() {
  return {
    PATH: process.env.PATH || "",
    HOME: process.env.HOME || "/tmp",
    CI: "1",
    NO_UPDATE_NOTIFIER: "1",
    WRANGLER_SEND_METRICS: "false",
  };
}

async function request(path, options = {}) {
  const response = await fetch(BASE + path, {
    ...options,
    headers: { "content-type": "application/json", ...(options.headers || {}) },
  });
  const body = await response.json();
  if (!response.ok) throw new Error("HTTP " + response.status + ": " + JSON.stringify(body));
  return body;
}

async function waitForStatus(id, predicate, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  while (Date.now() < deadline) {
    latest = await request("/status/" + id);
    if (predicate(latest)) return latest;
    await delay(200);
  }
  throw new Error("workflow status timeout: " + JSON.stringify(latest));
}

const child = spawn("npx", [
  "--yes", "wrangler@4.79.0", "dev",
  "--config", CONFIG,
  "--ip", "127.0.0.1",
  "--port", String(PORT),
  "--log-level", "error",
], {
  cwd: process.cwd(),
  env: safeWranglerEnvironment(),
  stdio: ["ignore", "pipe", "pipe"],
});

let logs = "";
child.stdout.setEncoding("utf8");
child.stderr.setEncoding("utf8");
child.stdout.on("data", chunk => { logs = (logs + chunk).slice(-8000); });
child.stderr.on("data", chunk => { logs = (logs + chunk).slice(-8000); });

try {
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (child.exitCode !== null) throw new Error("Wrangler exited early: " + logs);
    try {
      const health = await request("/health");
      if (health.ok === true && health.mode === "local-workflow-spike") {
        ready = true;
        break;
      }
    } catch {
      // Wrangler is still compiling or starting.
    }
    await delay(250);
  }
  assert.equal(ready, true, "Wrangler local Workflow runtime did not become ready: " + logs);

  const primaryPayload = {
    task_id: "task-approval",
    idempotency_key: "idem-approval",
    await_approval: true,
    approval_timeout_seconds: 30,
  };
  const started = await request("/start", { method: "POST", body: JSON.stringify(primaryPayload) });
  assert.match(started.id, INSTANCE);
  const duplicate = await request("/start", { method: "POST", body: JSON.stringify(primaryPayload) });
  assert.equal(duplicate.id, started.id);
  assert.equal(duplicate.reused, true);

  const waiting = await waitForStatus(started.id, x => x.status === "waiting");
  const beforeRestart = waiting.local_step_outputs || [];
  assert.equal(beforeRestart.filter(x => x?.status === "claimed").length, 1);
  assert.equal(beforeRestart.filter(x => x?.result === "research-stub").length, 1);

  const restarted = await request("/restart/" + started.id, {
    method: "POST", body: JSON.stringify({ from_step: "review-stub" }),
  });
  assert.equal(restarted.restarted_from, "review-stub");
  const waitingAgain = await waitForStatus(started.id, x => x.status === "waiting");
  const afterRestart = waitingAgain.local_step_outputs || [];
  assert.equal(afterRestart.filter(x => x?.status === "claimed").length, 1,
    "restart from review must reuse the completed claim step");
  assert.equal(afterRestart.filter(x => x?.result === "research-stub").length, 1,
    "restart from review must reuse the completed research step");

  await request("/approve/" + started.id, {
    method: "POST",
    body: JSON.stringify({ approved: true, approval_id: "local-approval-1" }),
  });
  const completed = await waitForStatus(started.id, x => x.status === "complete");
  assert.equal(completed.output?.status, "completed");
  assert.equal(completed.output?.approval_id, "local-approval-1");

  const cancelPayload = {
    task_id: "task-cancel",
    idempotency_key: "idem-cancel",
    await_approval: true,
    approval_timeout_seconds: 30,
  };
  const cancelStarted = await request("/start", { method: "POST", body: JSON.stringify(cancelPayload) });
  await waitForStatus(cancelStarted.id, x => x.status === "waiting");
  const cancelResult = await request("/cancel/" + cancelStarted.id, { method: "POST", body: "{}" });
  assert.equal(cancelResult.terminated, true);
  const terminated = await waitForStatus(cancelStarted.id, x => x.status === "terminated");
  assert.equal(terminated.status, "terminated");

  const expiredPayload = {
    task_id: "task-expired",
    idempotency_key: "idem-expired",
    lease_expires_at: "2020-01-01T00:00:00.000Z",
  };
  const expiredStart = await request("/start", { method: "POST", body: JSON.stringify(expiredPayload) });
  const expired = await waitForStatus(expiredStart.id, x => x.status === "complete");
  assert.equal(expired.output?.status, "lease_expired");

  const timeoutPayload = {
    task_id: "task-timeout",
    idempotency_key: "idem-timeout",
    await_approval: true,
    approval_timeout_seconds: 2,
  };
  const timeoutStart = await request("/start", { method: "POST", body: JSON.stringify(timeoutPayload) });
  const timedOut = await waitForStatus(timeoutStart.id, x => x.status === "complete" || x.status === "errored", 15_000);
  assert.equal(timedOut.status, "complete");
  assert.equal(timedOut.output?.status, "approval_timed_out");

  const invalid = await fetch(BASE + "/start", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...primaryPayload, command: "arbitrary shell" }),
  });
  assert.equal(invalid.status, 400);

  console.log("Cloudflare Workflows local smoke passed: duplicate create, step replay, approval event, terminate, lease expiry and timeout.");
} catch (error) {
  throw new Error(String(error?.stack || error) + "\nWrangler logs:\n" + logs);
} finally {
  child.kill("SIGTERM");
  await Promise.race([
    new Promise(resolve => child.once("exit", resolve)),
    delay(5000),
  ]);
}
