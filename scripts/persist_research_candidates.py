"""Persist public research discovery metadata into the authenticated D1 research store."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


USER_AGENT = "RaawaaAutonomousEngine-Research/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)"


class ResearchPersistenceError(RuntimeError):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    """Never forward engine bearer or Cloudflare Access credentials across redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "redirects are disabled for authenticated D1 requests", headers, fp)


def _open_without_redirects(request: Request, timeout: int):
    return build_opener(_RejectRedirects).open(request, timeout=timeout)


def load_candidates(snapshot: dict) -> list[dict]:
    if not isinstance(snapshot, dict):
        raise ValueError("research snapshot must be an object")
    candidates = []
    seen = set()
    for item in snapshot.get("candidates", []):
        if not isinstance(item, dict) or not isinstance(item.get("full_name"), str):
            continue
        key = "github:" + item["full_name"].lower()
        if key not in seen:
            seen.add(key)
            candidates.append({**item, "source": "github"})
    for item in snapshot.get("academic_candidates", []):
        if not isinstance(item, dict) or not isinstance(item.get("doi"), str):
            continue
        key = "crossref:" + item["doi"].lower()
        if key not in seen:
            seen.add(key)
            candidates.append({**item, "source": "crossref"})
    for item in snapshot.get("arxiv_candidates", []):
        if not isinstance(item, dict) or not isinstance(item.get("arxiv_id"), str):
            continue
        key = "arxiv:" + item["arxiv_id"].lower()
        if key not in seen:
            seen.add(key)
            candidates.append({**item, "source": "arxiv"})
    for item in snapshot.get("searxng_candidates", []):
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        key = "searxng:" + item["url"][:900]
        if key not in seen:
            seen.add(key)
            candidates.append({**item, "source": "searxng"})
    return candidates


def _post(base_url: str, token: str, candidates: list[dict], batch_id: str,
          access_client_id: str = "", access_client_secret: str = "",
          timeout: int = 20) -> dict:
    payload = json.dumps({"batch_id": batch_id, "candidates": candidates},
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if access_client_id and access_client_secret:
        headers["CF-Access-Client-Id"] = access_client_id
        headers["CF-Access-Client-Secret"] = access_client_secret
    request = Request(base_url.rstrip("/") + "/v1/research/candidates",
                      data=payload, headers=headers, method="POST")
    try:
        with _open_without_redirects(request, timeout) as response:
            raw = response.read(16 * 1024 + 1)
            if len(raw) > 16 * 1024:
                raise ResearchPersistenceError("research persistence response exceeded limit")
            data = json.loads(raw.decode("utf-8"))
            if response.status not in (200, 201) or not isinstance(data, dict):
                raise ResearchPersistenceError("research persistence endpoint returned an invalid response")
            return data
    except HTTPError as exc:
        raise ResearchPersistenceError(f"research persistence endpoint returned HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise ResearchPersistenceError("research persistence endpoint unavailable or returned invalid JSON") from None



def _get(base_url: str, token: str, source_keys: list[str],
         access_client_id: str = "", access_client_secret: str = "",
         timeout: int = 20) -> dict:
    if not isinstance(source_keys, list) or not 1 <= len(source_keys) <= 25:
        raise ValueError("source_keys must contain between 1 and 25 keys")
    payload = json.dumps({"source_keys": source_keys}, separators=(",", ":")).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if access_client_id and access_client_secret:
        headers["CF-Access-Client-Id"] = access_client_id
        headers["CF-Access-Client-Secret"] = access_client_secret
    request = Request(base_url.rstrip("/") + "/v1/research/candidates/verify",
                      data=payload, headers=headers, method="POST")
    try:
        with _open_without_redirects(request, timeout=timeout) as response:
            raw = response.read(16 * 1024 + 1)
            if len(raw) > 16 * 1024:
                raise ResearchPersistenceError("research readback response exceeded limit")
            data = json.loads(raw.decode("utf-8"))
            if response.status != 200 or not isinstance(data, dict):
                raise ResearchPersistenceError("research readback endpoint returned an invalid response")
            return data
    except HTTPError as exc:
        raise ResearchPersistenceError(f"research readback endpoint returned HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise ResearchPersistenceError("research readback endpoint unavailable or returned invalid JSON") from None


def _candidate_key(item: dict) -> str:
    if item["source"] == "github":
        return "github:" + item["full_name"].lower()
    if item["source"] == "crossref":
        return "crossref:" + item["doi"].lower()
    if item["source"] == "arxiv":
        return "arxiv:" + item["arxiv_id"].lower()
    return "searxng:" + item["url"][:900]


def persist_candidates(snapshot: dict, environ=None, request_fn=None, batch_size: int = 5,
                       sleep_fn=None, verify_fn=None) -> dict:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 10:
        raise ValueError("batch_size must be between 1 and 10")
    candidates = load_candidates(snapshot)
    sources = sorted({item["source"] for item in candidates})
    if not candidates:
        return {"discovered": 0, "persisted": 0, "verified": 0, "batches": 0,
                "replayed_batches": 0, "sources": []}
    env = os.environ if environ is None else environ
    base_url = env.get("RAAWAA_ENGINE_API_URL", "").strip()
    token = env.get("RAAWAA_ENGINE_API_TOKEN", "").strip()
    access_id = env.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID", "").strip()
    access_secret = env.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET", "").strip()
    scan_id = (env.get("GITHUB_RUN_ID") or env.get("RAAWAA_RESEARCH_SCAN_ID") or "").strip()
    if not base_url.startswith("https://") or not token:
        raise ValueError("an HTTPS engine API URL and application token are required")
    if not scan_id or not all(ch.isalnum() or ch in "-_" for ch in scan_id) or len(scan_id) > 100:
        raise ValueError("a stable GitHub run ID or explicit research scan ID is required")
    if bool(access_id) != bool(access_secret):
        raise ValueError("Cloudflare Access service-token credentials must be configured as a pair")
    post = request_fn or _post
    verify = verify_fn or _get
    sleeper = sleep_fn or time.sleep
    retry_delays = (5, 10, 20)
    retryable_markers = ("HTTP 404", "HTTP 408", "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "unavailable")
    persisted = verified = batches = replayed_batches = 0
    for batch_number, offset in enumerate(range(0, len(candidates), batch_size), start=1):
        batch = candidates[offset:offset + batch_size]
        batch_id = f"github:{scan_id}:{batch_number}"
        result = None
        for attempt in range(len(retry_delays) + 1):
            try:
                result = post(base_url, token, batch, batch_id, access_id, access_secret)
                break
            except ResearchPersistenceError as exc:
                retryable = any(marker in str(exc) for marker in retryable_markers)
                if not retryable or attempt >= len(retry_delays):
                    raise
                sleeper(retry_delays[attempt])
        accepted = result.get("accepted_count") if isinstance(result, dict) else None
        returned_batch = result.get("batch_id") if isinstance(result, dict) else None
        replayed = result.get("replayed") if isinstance(result, dict) else None
        if type(accepted) is not int or accepted != len(batch) or returned_batch != batch_id or type(replayed) is not bool:
            raise ResearchPersistenceError("research persistence endpoint returned an unexpected batch receipt")
        expected = [_candidate_key(item) for item in batch]
        readback = verify(base_url, token, expected, access_id, access_secret)
        found = readback.get("matched_keys") if isinstance(readback, dict) else None
        if not isinstance(found, list) or set(found) != set(expected):
            raise ResearchPersistenceError("research D1 exact-key readback did not confirm every submitted candidate")
        persisted += accepted
        verified += len(expected)
        batches += 1
        replayed_batches += int(replayed)
    return {"discovered": len(candidates), "persisted": persisted, "verified": verified,
            "batches": batches, "replayed_batches": replayed_batches, "sources": sources}

def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else Path("research-candidates.json")
    try:
        with path.open(encoding="utf-8") as stream:
            snapshot = json.load(stream)
        result = persist_candidates(snapshot)
    except (OSError, ValueError, ResearchPersistenceError) as exc:
        print(f"Research persistence failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
