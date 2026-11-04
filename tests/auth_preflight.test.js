// The bearer preflight used by the production deploy workflow is
// `POST /v1/diagnostics/auth`. That workflow now sends the repository
// ENGINE_API_TOKEN to the deployed Worker and treats HTTP 200 as proof the two
// stores agree. That is only safe while the route stays a pure authentication
// probe: if it ever became reachable without auth, or touched D1, the preflight
// would turn into a token oracle or a side effect inside a deployment gate.
//
// These tests pin the route's contract against the real Worker handler:
//   * 401 vs 200 depends only on the bearer value, never on D1 or method side
//     effects, so a 200 cannot be produced by an unauthenticated caller.
//   * The route performs no D1 access at all (the mock throws if it is used).
//   * A wrong token is answered identically for any wrong value, so the
//     response carries no information about how close a guess was.
import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";

const GOOD = "a".repeat(48);
const ENV = { ENGINE_API_TOKEN: GOOD };

function explodingDb() {
  const boom = () => {
    throw new Error("D1 must not be touched by the auth preflight");
  };
  return { prepare: boom, batch: boom, exec: boom };
}

function authRequest(token, { method = "POST" } = {}) {
  const headers = { accept: "application/json" };
  if (token !== null) headers.authorization = "Bearer " + token;
  return new Request("https://engine.example/v1/diagnostics/auth", {
    method,
    headers,
    body: method === "POST" ? "" : undefined,
  });
}

test("auth preflight accepts the configured token and never reads D1", async () => {
  const response = await worker.fetch(authRequest(GOOD), { ...ENV, DB: explodingDb() });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.deepEqual(body, { ok: true, authenticated: true });
});

test("auth preflight rejects a wrong token with 401 and no detail", async () => {
  for (const wrong of ["b".repeat(48), "", GOOD.slice(0, -1), GOOD + "x", GOOD.toUpperCase()]) {
    const response = await worker.fetch(authRequest(wrong), { ...ENV, DB: explodingDb() });
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { error: "unauthorized" });
  }
});

test("auth preflight rejects a missing header without revealing the token's shape", async () => {
  const response = await worker.fetch(authRequest(null), { ...ENV, DB: explodingDb() });
  assert.equal(response.status, 401);
  assert.deepEqual(await response.json(), { error: "unauthorized" });
});

test("auth preflight fails closed when the Worker secret is not configured", async () => {
  // An unset secret must never make every presented token look correct.
  for (const env of [{}, { ENGINE_API_TOKEN: "" }, { ENGINE_API_TOKEN: null }]) {
    const response = await worker.fetch(authRequest(GOOD), { ...env, DB: explodingDb() });
    assert.equal(response.status, 401);
  }
});

test("auth preflight is POST-only and documents the method rule", async () => {
  const response = await worker.fetch(authRequest(GOOD, { method: "GET" }), {
    ...ENV,
    DB: explodingDb(),
  });
  assert.equal(response.status, 405);
});

test("the bare unauthorized body is identical for every wrong token", async () => {
  // Constant-time comparison means the status must be the only observable
  // difference; the body must not grow with the length of the guess.
  const bodies = [];
  for (const wrong of ["z", "z".repeat(64), "z".repeat(256)]) {
    const response = await worker.fetch(authRequest(wrong), { ...ENV, DB: explodingDb() });
    bodies.push(await response.text());
  }
  assert.equal(new Set(bodies).size, 1, "unauthorized responses must be indistinguishable");
});
