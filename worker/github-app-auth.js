const GITHUB_API = "https://api.github.com";
const GITHUB_API_VERSION = "2026-03-10";
const TARGET_REPOSITORY_ID = 1441352567;
const APP_PERMISSION_SCOPE = { actions: "write" };

function concatBytes(...parts) {
  const length = parts.reduce((total, part) => total + part.length, 0);
  const output = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) {
    output.set(part, offset);
    offset += part.length;
  }
  return output;
}

function derLength(length) {
  if (!Number.isSafeInteger(length) || length < 0) {
    throw new Error("github_app_private_key_invalid");
  }
  if (length < 128) return Uint8Array.of(length);
  const bytes = [];
  let remaining = length;
  while (remaining > 0) {
    bytes.unshift(remaining & 0xff);
    remaining = Math.floor(remaining / 256);
  }
  return Uint8Array.of(0x80 | bytes.length, ...bytes);
}

function wrapPkcs1AsPkcs8(pkcs1Der) {
  const version = Uint8Array.of(0x02, 0x01, 0x00);
  const rsaAlgorithmIdentifier = Uint8Array.of(
    0x30, 0x0d,
    0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01,
    0x05, 0x00,
  );
  const privateKeyOctets = concatBytes(
    Uint8Array.of(0x04),
    derLength(pkcs1Der.length),
    pkcs1Der,
  );
  const body = concatBytes(version, rsaAlgorithmIdentifier, privateKeyOctets);
  return concatBytes(Uint8Array.of(0x30), derLength(body.length), body);
}

function decodePem(privateKeyPem) {
  if (typeof privateKeyPem !== "string" || !privateKeyPem.trim()) {
    throw new Error("github_app_configuration_missing");
  }
  const match = privateKeyPem.trim().match(
    /^-----BEGIN (RSA PRIVATE KEY|PRIVATE KEY)-----([\s\S]+?)-----END \1-----$/,
  );
  if (!match) throw new Error("github_app_private_key_invalid");
  const base64 = match[2].replace(/\s/g, "");
  if (!base64 || !/^[A-Za-z0-9+/]+={0,2}$/.test(base64)) {
    throw new Error("github_app_private_key_invalid");
  }
  try {
    const binary = atob(base64);
    const der = Uint8Array.from(binary, character => character.charCodeAt(0));
    return {
      format: match[1] === "RSA PRIVATE KEY" ? "pkcs1" : "pkcs8",
      der,
    };
  } catch {
    throw new Error("github_app_private_key_invalid");
  }
}

function base64Url(bytes) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
}

async function importSigningKey(privateKeyPem, cryptoImpl) {
  const decoded = decodePem(privateKeyPem);
  const pkcs8 = decoded.format === "pkcs1"
    ? wrapPkcs1AsPkcs8(decoded.der)
    : decoded.der;
  try {
    return await cryptoImpl.subtle.importKey(
      "pkcs8",
      pkcs8,
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      false,
      ["sign"],
    );
  } catch {
    throw new Error("github_app_private_key_invalid");
  }
}

export async function createAppJwt({
  appId,
  privateKeyPem,
  nowSeconds = Math.floor(Date.now() / 1000),
  cryptoImpl = globalThis.crypto,
}) {
  if (typeof appId !== "string" || !appId.trim() ||
      typeof privateKeyPem !== "string" || !privateKeyPem.trim()) {
    throw new Error("github_app_configuration_missing");
  }
  if (!Number.isSafeInteger(nowSeconds) || nowSeconds < 1 ||
      !cryptoImpl?.subtle || typeof cryptoImpl.subtle.sign !== "function") {
    throw new Error("github_app_crypto_unavailable");
  }

  const key = await importSigningKey(privateKeyPem, cryptoImpl);
  const header = base64Url(new TextEncoder().encode(JSON.stringify({
    alg: "RS256",
    typ: "JWT",
  })));
  const payload = base64Url(new TextEncoder().encode(JSON.stringify({
    iat: nowSeconds - 60,
    exp: nowSeconds + 540,
    iss: appId,
  })));
  const signingInput = header + "." + payload;
  const signature = await cryptoImpl.subtle.sign(
    { name: "RSASSA-PKCS1-v1_5" },
    key,
    new TextEncoder().encode(signingInput),
  );
  return signingInput + "." + base64Url(new Uint8Array(signature));
}

export async function createInstallationToken({
  appId,
  installationId,
  privateKeyPem,
  fetchImpl = globalThis.fetch,
  cryptoImpl = globalThis.crypto,
  nowSeconds,
}) {
  if (typeof appId !== "string" || !appId.trim() ||
      typeof installationId !== "string" || !installationId.trim() ||
      typeof privateKeyPem !== "string" || !privateKeyPem.trim()) {
    throw new Error("github_app_configuration_missing");
  }
  if (typeof fetchImpl !== "function") {
    throw new Error("github_app_fetch_unavailable");
  }

  const jwt = await createAppJwt({
    appId,
    privateKeyPem,
    ...(nowSeconds === undefined ? {} : { nowSeconds }),
    cryptoImpl,
  });
  let response;
  try {
    response = await fetchImpl(
      GITHUB_API + "/app/installations/" + encodeURIComponent(installationId) + "/access_tokens",
      {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: "Bearer " + jwt,
          "Content-Type": "application/json",
          "X-GitHub-Api-Version": GITHUB_API_VERSION,
          "User-Agent": "RaawaaAutonomousEngine/1.0",
        },
        body: JSON.stringify({
          repository_ids: [TARGET_REPOSITORY_ID],
          permissions: APP_PERMISSION_SCOPE,
        }),
      },
    );
  } catch {
    throw new Error("github_app_token_request_failed");
  }
  if (!response?.ok) {
    const status = Number.isInteger(response?.status) ? response.status : 0;
    throw new Error("github_app_token_request_failed:" + status);
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("github_app_token_response_invalid");
  }
  if (!payload || typeof payload.token !== "string" ||
      !payload.token.trim() || payload.token.length > 500) {
    throw new Error("github_app_token_response_invalid");
  }
  return payload.token;
}
