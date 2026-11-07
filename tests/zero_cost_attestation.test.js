import test from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { verifyZeroCostAttestation } from "../worker/zero-cost-attestation.js";

const cryptoImpl = webcrypto;
const NOW = Date.parse("2026-11-07T00:00:00Z");
const expected = { account_id: "a".repeat(32), worker: "control-worker", database_id: "12345678-1234-1234-1234-123456789abc" };
const encode = bytes => Buffer.from(bytes).toString("base64url");
const keys = await cryptoImpl.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
const publicKeySpki = encode(await cryptoImpl.subtle.exportKey("spki", keys.publicKey));
const valid = () => ({ v: 1, scope: "watchdog-d1-dispatch", ...expected, evidence_sha256: "b".repeat(64), issued_at_ms: NOW - 1000, expires_at_ms: NOW + 60_000, eligible: true });
async function sign(payload) {
  const encoded = encode(Buffer.from(JSON.stringify(payload)));
  const signature = await cryptoImpl.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, keys.privateKey, Buffer.from(encoded));
  return `${encoded}.${encode(signature)}`;
}
async function verify(token, overrides = {}) {
  return verifyZeroCostAttestation({ token, publicKeySpki, expected, nowMs: NOW, cryptoImpl, ...overrides });
}

test("valid signed fixture is accepted only by the cryptographic building block", async () => {
  assert.equal(await verify(await sign(valid())), true);
});

test("invalid expiry, future issuance, lifetime, identity and claims fail closed", async () => {
  for (const change of [
    { expires_at_ms: NOW }, { issued_at_ms: NOW + 1 }, { expires_at_ms: NOW + 120_000 },
    { expires_at_ms: NOW - 2000 }, { account_id: "c".repeat(32) }, { worker: "other-worker" },
    { database_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" }, { evidence_sha256: "unknown" },
    { eligible: false }, { eligible: "true" }, { scope: "all" }, { v: 2 }, { extra: "ignored?" },
    { issued_at_ms: -1 }, { evidence_sha256: null }, { worker: ["control-worker"] },
  ]) {
    assert.equal(await verify(await sign({ ...valid(), ...change })), false, JSON.stringify(change));
  }
});

test("tampering, wrong key, malformed tokens and invalid verifier configuration fail closed", async () => {
  const token = await sign(valid());
  const wrong = await cryptoImpl.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const wrongKey = encode(await cryptoImpl.subtle.exportKey("spki", wrong.publicKey));
  assert.equal(await verify(token, { publicKeySpki: wrongKey }), false);
  // Even a correctly signed ambiguous JSON document is not accepted.
  const duplicate = JSON.stringify(valid()).replace('"eligible":true', '"eligible":true,"eligible":true');
  const ambiguousPayload = encode(Buffer.from(duplicate));
  const ambiguousSignature = await cryptoImpl.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, keys.privateKey, Buffer.from(ambiguousPayload));
  assert.equal(await verify(`${ambiguousPayload}.${encode(ambiguousSignature)}`), false);
  assert.equal(await verify(`${encode(Buffer.from(JSON.stringify({ ...valid(), evidence_sha256: "c".repeat(64) })))}.${token.split(".")[1]}`), false);
  for (const bad of ["", "bad", "a.b.c", "a.b", "x".repeat(5000), `${token}=`, null]) {
    assert.equal(await verify(bad), false);
  }
  assert.equal(await verify(token, { nowMs: NaN }), false);
  assert.equal(await verify(token, { expected: null }), false);
  assert.equal(await verify(token, { publicKeySpki: "invalid" }), false);
});
