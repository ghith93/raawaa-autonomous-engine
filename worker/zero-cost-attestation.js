/**
 * Cryptographic building block only. It is NOT connected to the watchdog and
 * no issuer exists here. A valid signature is not evidence of a zero-cost
 * account unless an independently trusted issuer verifies the actual billing
 * controls and resources. See docs/operations/watchdog-attestation.md.
 */
const MAX_TOKEN_LENGTH = 4096;
const MAX_LIFETIME_MS = 120_000;
const FIELDS = ["v", "scope", "account_id", "worker", "database_id", "evidence_sha256", "issued_at_ms", "expires_at_ms", "eligible"];
const ACCOUNT = /^[0-9a-f]{32}$/;
const DB_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const WORKER = /^[a-z0-9][a-z0-9-]{0,62}$/;
const HASH = /^[0-9a-f]{64}$/;

function decodeBase64url(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/.test(value) || value.length % 4 === 1) throw new Error("invalid");
  const bytes = atob(value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4));
  const out = Uint8Array.from(bytes, c => c.charCodeAt(0));
  // Reject noncanonical encodings rather than allowing multiple token identities.
  let canonical = btoa(String.fromCharCode(...out)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  if (canonical !== value) throw new Error("invalid");
  return out;
}

/** Verify an ES256-signed, short-lived resource-bound claim; returns only boolean. */
export async function verifyZeroCostAttestation({ token, publicKeySpki, expected, nowMs, cryptoImpl = globalThis.crypto }) {
  try {
    if (typeof token !== "string" || token.length > MAX_TOKEN_LENGTH ||
        typeof publicKeySpki !== "string" || publicKeySpki.length > 512 ||
        !Number.isSafeInteger(nowMs) || !expected || !cryptoImpl?.subtle) return false;
    const pieces = token.split(".");
    if (pieces.length !== 2) return false;
    const payloadBytes = decodeBase64url(pieces[0]);
    const signature = decodeBase64url(pieces[1]);
    if (signature.length !== 64) return false;
    const json = new TextDecoder("utf-8", { fatal: true }).decode(payloadBytes);
    const payload = JSON.parse(json);
    if (!payload || typeof payload !== "object" || Array.isArray(payload) ||
        Object.keys(payload).length !== FIELDS.length || !FIELDS.every(key => Object.hasOwn(payload, key)) ||
        JSON.stringify(payload) !== json) return false;
    if (payload.v !== 1 || payload.scope !== "watchdog-d1-dispatch" || payload.eligible !== true ||
        typeof payload.account_id !== "string" || typeof payload.worker !== "string" ||
        typeof payload.database_id !== "string" || typeof payload.evidence_sha256 !== "string" ||
        !ACCOUNT.test(payload.account_id) || !WORKER.test(payload.worker) || !DB_ID.test(payload.database_id) ||
        !HASH.test(payload.evidence_sha256) ||
        payload.account_id !== expected.account_id || payload.worker !== expected.worker ||
        payload.database_id !== expected.database_id ||
        !Number.isSafeInteger(payload.issued_at_ms) || !Number.isSafeInteger(payload.expires_at_ms) ||
        payload.issued_at_ms > nowMs || payload.expires_at_ms <= nowMs ||
        payload.expires_at_ms <= payload.issued_at_ms ||
        payload.expires_at_ms - payload.issued_at_ms > MAX_LIFETIME_MS) return false;
    const key = await cryptoImpl.subtle.importKey("spki", decodeBase64url(publicKeySpki),
      { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    return await cryptoImpl.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, signature,
      new TextEncoder().encode(pieces[0]));
  } catch {
    return false;
  }
}
