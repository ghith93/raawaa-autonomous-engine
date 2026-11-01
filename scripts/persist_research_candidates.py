"""Persist public research discovery metadata into the authenticated D1 research store."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USER_AGENT = "RaawaaAutonomousEngine-Research/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)"


class ResearchPersistenceError(RuntimeError):
    pass


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
    return candidates


def _post(base_url: str, token: str, candidates: list[dict],
          access_client_id: str = "", access_client_secret: str = "",
          timeout: int = 20) -> dict:
    payload = json.dumps({"candidates": candidates}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read(16 * 1024 + 1).decode("utf-8"))
            if response.status not in (200, 201) or not isinstance(data, dict):
                raise ResearchPersistenceError("research persistence endpoint returned an invalid response")
            return data
    except HTTPError as exc:
        raise ResearchPersistenceError(f"research persistence endpoint returned HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise ResearchPersistenceError("research persistence endpoint unavailable or returned invalid JSON") from None


def persist_candidates(snapshot: dict, environ=None, request_fn=None, batch_size: int = 5) -> dict:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 10:
        raise ValueError("batch_size must be between 1 and 10")
    candidates = load_candidates(snapshot)
    sources = sorted({item["source"] for item in candidates})
    if not candidates:
        return {"discovered": 0, "persisted": 0, "batches": 0, "sources": []}
    env = os.environ if environ is None else environ
    base_url = env.get("RAAWAA_ENGINE_API_URL", "").strip()
    token = env.get("RAAWAA_ENGINE_API_TOKEN", "").strip()
    access_id = env.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID", "").strip()
    access_secret = env.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET", "").strip()
    if not base_url.startswith("https://") or not token:
        raise ValueError("an HTTPS engine API URL and application token are required")
    if bool(access_id) != bool(access_secret):
        raise ValueError("Cloudflare Access service-token credentials must be configured as a pair")
    post = request_fn or _post
    persisted = batches = 0
    for offset in range(0, len(candidates), batch_size):
        batch = candidates[offset:offset + batch_size]
        result = post(base_url, token, batch, access_id, access_secret)
        accepted = result.get("accepted_count") if isinstance(result, dict) else None
        if type(accepted) is not int or accepted != len(batch):
            raise ResearchPersistenceError("research persistence endpoint accepted an unexpected candidate count")
        persisted += accepted
        batches += 1
    return {"discovered": len(candidates), "persisted": persisted, "batches": batches, "sources": sources}


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
