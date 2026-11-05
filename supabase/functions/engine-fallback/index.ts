// Staging-only, exact-task fallback for the scheduled process probe.
// The function gateway's built-in JWT check is disabled because GitHub sends an
// OIDC token, not a Supabase JWT. Every request is therefore authenticated and
// restricted here before the service-role RPC key is used.
const EXPECTED_ISSUER = "https://token.actions.githubusercontent.com";
const EXPECTED_AUDIENCE = "raawaa-engine-staging-fallback";
const EXPECTED_REPOSITORY = "ghith93/raawaa-autonomous-engine";
const EXPECTED_REPOSITORY_ID = "1441352567";
const EXPECTED_REF = "refs/heads/main";
const EXPECTED_SUBJECT = `repo:${EXPECTED_REPOSITORY}:ref:${EXPECTED_REF}`;
const EXPECTED_WORKFLOW_REF = `${EXPECTED_REPOSITORY}/.github/workflows/staging-supervised-process-probe.yml@${EXPECTED_REF}`;
const MAX_REQUEST_BYTES = 16 * 1024;
const MAX_RESPONSE_BYTES = 64 * 1024;
const encoder = new TextEncoder();

type GithubJwk = JsonWebKey & { kid?: string; kty?: string };
let jwksCache: { keys: GithubJwk[]; expiresAt: number } | null = null;

class AuthError extends Error {}
class SizeLimitError extends Error {}

async function readBoundedText(stream: ReadableStream<Uint8Array> | null, limit: number): Promise<string> {
  if (!stream) return "";
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limit) {
        await reader.cancel();
        throw new SizeLimitError("body_limit");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(bytes);
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

function decodeBase64Url(value: string): Uint8Array<ArrayBuffer> {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
  const raw = atob(padded);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index++) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

function decodeJwtPart(value: string): Record<string, unknown> {
  const decoded = new TextDecoder().decode(decodeBase64Url(value));
  const parsed: unknown = JSON.parse(decoded);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new AuthError("invalid_token");
  }
  return parsed as Record<string, unknown>;
}

async function getJwks(): Promise<GithubJwk[]> {
  if (jwksCache && jwksCache.expiresAt > Date.now()) return jwksCache.keys;
  const response = await fetch(`${EXPECTED_ISSUER}/.well-known/jwks`, {
    method: "GET",
    redirect: "error",
    signal: AbortSignal.timeout(5_000),
    headers: { accept: "application/json" },
  });
  if (!response.ok) throw new Error("oidc_keys_unavailable");
  const text = await readBoundedText(response.body, 32 * 1024);
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || !Array.isArray((value as { keys?: unknown }).keys)) {
    throw new Error("oidc_keys_invalid");
  }
  const keys = (value as { keys: GithubJwk[] }).keys;
  if (keys.length < 1 || keys.length > 20) throw new Error("oidc_keys_invalid");
  jwksCache = { keys, expiresAt: Date.now() + 5 * 60_000 };
  return keys;
}

async function verifyGithubOidc(token: string): Promise<Record<string, unknown>> {
  const parts = token.split(".");
  if (parts.length !== 3 || token.length > 16 * 1024) throw new AuthError("invalid_token");
  const header = decodeJwtPart(parts[0]);
  const claims = decodeJwtPart(parts[1]);
  if (header.alg !== "RS256" || typeof header.kid !== "string") throw new AuthError("invalid_token");
  const now = Math.floor(Date.now() / 1000);
  const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (claims.iss !== EXPECTED_ISSUER || !audiences.includes(EXPECTED_AUDIENCE)
      || claims.sub !== EXPECTED_SUBJECT || claims.repository !== EXPECTED_REPOSITORY
      || String(claims.repository_id ?? "") !== EXPECTED_REPOSITORY_ID
      || claims.ref !== EXPECTED_REF || claims.event_name !== "schedule"
      || claims.workflow_ref !== EXPECTED_WORKFLOW_REF
      || typeof claims.iat !== "number" || typeof claims.exp !== "number"
      || claims.exp <= now || claims.iat > now + 60 || claims.exp - claims.iat > 600
      || (typeof claims.nbf === "number" && claims.nbf > now + 60)
      || typeof claims.run_id !== "string" || !/^\d{1,20}$/.test(claims.run_id)) {
    throw new AuthError("token_scope_rejected");
  }
  const jwk = (await getJwks()).find((candidate) => candidate.kid === header.kid && candidate.kty === "RSA");
  if (!jwk || (jwk.alg && jwk.alg !== "RS256") || (jwk.use && jwk.use !== "sig")) {
    throw new AuthError("unknown_signing_key");
  }
  const key = await crypto.subtle.importKey(
    "jwk",
    jwk,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const signatureValid = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    decodeBase64Url(parts[2]),
    encoder.encode(`${parts[0]}.${parts[1]}`),
  );
  if (!signatureValid) throw new AuthError("invalid_signature");
  return claims;
}

function getServiceKey(): string {
  const configured = Deno.env.get("SUPABASE_SECRET_KEYS");
  if (!configured) throw new Error("service_key_unavailable");
  const keys: unknown = JSON.parse(configured);
  if (!keys || typeof keys !== "object" || typeof (keys as Record<string, unknown>).default !== "string") {
    throw new Error("service_key_unavailable");
  }
  return (keys as Record<string, string>).default;
}

async function callRpc(name: string, args: Record<string, unknown>): Promise<Response> {
  const baseUrl = Deno.env.get("SUPABASE_URL");
  if (!baseUrl || !/^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(baseUrl)) {
    throw new Error("database_endpoint_unavailable");
  }
  const serviceKey = getServiceKey();
  const response = await fetch(`${baseUrl}/rest/v1/rpc/${name}`, {
    method: "POST",
    redirect: "error",
    signal: AbortSignal.timeout(10_000),
    headers: {
      apikey: serviceKey,
      authorization: `Bearer ${serviceKey}`,
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify(args),
  });
  let text: string;
  try {
    text = await readBoundedText(response.body, MAX_RESPONSE_BYTES);
  } catch {
    return jsonResponse({ error: "fallback_database_unavailable" }, 503);
  }
  if (!response.ok) return jsonResponse({ error: "fallback_database_unavailable" }, 503);
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return jsonResponse({ error: "fallback_database_invalid_response" }, 503);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return jsonResponse({ error: "fallback_database_invalid_response" }, 503);
  }
  const record = value as Record<string, unknown>;
  const requestedStatus = record.http_status;
  if (typeof requestedStatus === "number") {
    const { http_status: _ignored, ...body } = record;
    const status = requestedStatus >= 400 && requestedStatus <= 599 ? requestedStatus : 500;
    return jsonResponse(body, status);
  }
  return jsonResponse(record);
}

async function routeRequest(body: Record<string, unknown>): Promise<Response> {
  if (body.method !== "POST" || typeof body.path !== "string") {
    return jsonResponse({ error: "unsupported_request" }, 400);
  }
  const path = body.path;
  const payload = body.payload && typeof body.payload === "object" && !Array.isArray(body.payload)
    ? body.payload as Record<string, unknown>
    : {};

  if (path === "/health") {
    return await callRpc("engine_fallback_ping", {});
  }
  if (path === "/v1/tasks/enqueue") {
    return await callRpc("engine_fallback_enqueue", {
      p_task_type: payload.task_type,
      p_title: payload.title,
      p_payload: payload.payload,
      p_idempotency_key: payload.idempotency_key,
      p_max_attempts: payload.max_attempts,
      p_priority: payload.priority,
    });
  }
  const claim = path.match(/^\/v1\/tasks\/([A-Za-z0-9_-]{1,128})\/claim-supervised-probe$/);
  if (claim) return await callRpc("engine_fallback_claim", { p_task_id: claim[1] });

  const heartbeat = path.match(/^\/v1\/tasks\/([A-Za-z0-9_-]{1,128})\/heartbeat$/);
  if (heartbeat) {
    return await callRpc("engine_fallback_heartbeat", {
      p_task_id: heartbeat[1], p_lease_token: payload.lease_token,
    });
  }
  const finish = path.match(/^\/v1\/tasks\/([A-Za-z0-9_-]{1,128})\/finish$/);
  if (finish) {
    return await callRpc("engine_fallback_finish", { p_task_id: finish[1], p_body: payload });
  }
  const dispatch = path.match(/^\/v1\/tasks\/([A-Za-z0-9_-]{1,128})\/process-dispatches$/);
  if (dispatch) {
    return await callRpc("engine_fallback_process_dispatch", { p_task_id: dispatch[1], p_body: payload });
  }
  return jsonResponse({ error: "route_not_found" }, 404);
}

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST") return jsonResponse({ error: "method_not_allowed" }, 405);
  const authorization = request.headers.get("authorization") ?? "";
  const tokenMatch = authorization.match(/^Bearer ([A-Za-z0-9._-]+)$/);
  if (!tokenMatch) return jsonResponse({ error: "unauthorized" }, 401);
  try {
    await verifyGithubOidc(tokenMatch[1]);
    const contentLength = Number(request.headers.get("content-length") ?? "0");
    if (Number.isFinite(contentLength) && contentLength > MAX_REQUEST_BYTES) {
      return jsonResponse({ error: "request_too_large" }, 413);
    }
    let raw: string;
    try {
      raw = await readBoundedText(request.body, MAX_REQUEST_BYTES);
    } catch (error) {
      if (error instanceof SizeLimitError) return jsonResponse({ error: "request_too_large" }, 413);
      throw error;
    }
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return jsonResponse({ error: "invalid_request" }, 400);
    }
    return await routeRequest(parsed as Record<string, unknown>);
  } catch (error) {
    if (error instanceof AuthError) return jsonResponse({ error: "unauthorized" }, 401);
    // Do not include exception text: it can contain provider or runtime details.
    return jsonResponse({ error: "fallback_unavailable" }, 503);
  }
});
