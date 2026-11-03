import test from "node:test";
import assert from "node:assert/strict";
import { createPrivateKey, generateKeyPairSync, verify as verifySignature } from "node:crypto";

const loadModule = () => import("../worker/github-app-auth.js").catch(() => null);
const keyPair = generateKeyPairSync("rsa", {
  modulusLength: 2048,
  publicKeyEncoding: { type: "spki", format: "pem" },
  privateKeyEncoding: { type: "pkcs1", format: "pem" },
});
const privateKeyPkcs1 = keyPair.privateKey;
const privateKeyPkcs8 = createPrivateKey(privateKeyPkcs1).export({ type: "pkcs8", format: "pem" }).toString();

function decodeBase64Url(value) {
  return Buffer.from(value.replace(/-/g, "+").replace(/_/g, "/"), "base64");
}

test("GitHub App JWT signs bounded RS256 claims from a PKCS#1 PEM", async () => {
  const module = await loadModule();
  assert.ok(module, "GitHub App auth module must exist");
  const nowSeconds = 1_800_000_000;
  const jwt = await module.createAppJwt({
    appId: "12345",
    privateKeyPem: privateKeyPkcs1,
    nowSeconds,
  });
  const [encodedHeader, encodedPayload, encodedSignature] = jwt.split(".");
  assert.equal(jwt.split(".").length, 3);
  const header = JSON.parse(decodeBase64Url(encodedHeader).toString("utf8"));
  const payload = JSON.parse(decodeBase64Url(encodedPayload).toString("utf8"));
  assert.deepEqual(header, { alg: "RS256", typ: "JWT" });
  assert.deepEqual(payload, { iat: nowSeconds - 60, exp: nowSeconds + 540, iss: "12345" });
  assert.equal(
    verifySignature("RSA-SHA256", Buffer.from(encodedHeader + "." + encodedPayload), keyPair.publicKey, decodeBase64Url(encodedSignature)),
    true,
  );
});

test("GitHub App JWT also accepts a PKCS#8 PEM", async () => {
  const module = await loadModule();
  assert.ok(module, "GitHub App auth module must exist");
  const jwt = await module.createAppJwt({
    appId: "12345",
    privateKeyPem: privateKeyPkcs8,
    nowSeconds: 1_800_000_000,
  });
  assert.equal(jwt.split(".").length, 3);
});

test("installation token request is scoped to this repository and Actions write", async () => {
  const module = await loadModule();
  assert.ok(module, "GitHub App auth module must exist");
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({
      token: "ghs_ephemeral_test_token",
      expires_at: "2026-11-03T02:00:00Z",
    }), { status: 201, headers: { "content-type": "application/json" } });
  };
  const token = await module.createInstallationToken({
    appId: "12345",
    installationId: "67890",
    privateKeyPem: privateKeyPkcs1,
    fetchImpl,
    nowSeconds: 1_800_000_000,
  });
  assert.equal(token, "ghs_ephemeral_test_token");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.github.com/app/installations/67890/access_tokens");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers.Authorization.startsWith("Bearer "), true);
  assert.equal(calls[0].options.headers["User-Agent"], "RaawaaAutonomousEngine/1.0");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    repository_ids: [1441352567],
    permissions: { actions: "write" },
  });
});

test("installation token errors never expose the GitHub response body", async () => {
  const module = await loadModule();
  assert.ok(module, "GitHub App auth module must exist");
  const fetchImpl = async () => new Response("PRIVATE_KEY_MUST_NOT_LEAK", { status: 403 });
  await assert.rejects(
    () => module.createInstallationToken({
      appId: "12345",
      installationId: "67890",
      privateKeyPem: privateKeyPkcs1,
      fetchImpl,
      nowSeconds: 1_800_000_000,
    }),
    error => {
      assert.match(error.message, /github_app_token_request_failed/);
      assert.equal(error.message.includes("PRIVATE_KEY_MUST_NOT_LEAK"), false);
      return true;
    },
  );
});

test("missing GitHub App credentials fail before network access", async () => {
  const module = await loadModule();
  assert.ok(module, "GitHub App auth module must exist");
  let calls = 0;
  await assert.rejects(
    () => module.createInstallationToken({
      appId: "",
      installationId: "67890",
      privateKeyPem: privateKeyPkcs1,
      fetchImpl: async () => { calls += 1; throw new Error("network must not run"); },
    }),
    /github_app_configuration_missing/,
  );
  assert.equal(calls, 0);
});
