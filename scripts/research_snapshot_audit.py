"""Deterministic offline audit helpers for research discovery snapshots.

No network access, source adoption, lifecycle mutation, or persistence.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = {"fbclid", "gclid"}
_STABLE_METADATA_FIELDS = (
    "source", "full_name", "doi", "arxiv_id", "url", "title", "description",
    "language", "license_spdx", "license_urls", "topics", "authors",
)


def canonical_github_identity(value: str) -> str | None:
    """Return owner/repository identity for a GitHub URL or owner/repo string."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if "://" in candidate:
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in {"http", "https"} or (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}:
            return None
        path = parsed.path
    else:
        path = candidate
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner, repository = parts[0].casefold(), parts[1].casefold()
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not owner or not repository or owner in {".", ".."} or repository in {".", ".."}:
        return None
    return owner + "/" + repository


def canonicalize_url(value: str) -> str | None:
    """Normalize URL identity while preserving all non-tracking query parameters."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return None
    host = host.casefold().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None
    netloc = host + ((":" + str(port)) if port is not None else "")
    path = parsed.path or "/"
    path = re.sub(r"/{2,}", "/", path)
    if path != "/":
        path = path.rstrip("/")
    if host in {"doi.org", "dx.doi.org"}:
        host = "doi.org"
        netloc = host
        path = path.casefold()
    query = [
        (key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not (key.casefold().startswith("utm_") or key.casefold() in _TRACKING_KEYS)
    ]
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def stable_metadata_fingerprint(record: dict) -> str:
    """Hash stable metadata only; discovery timestamps/provenance are excluded."""
    stable = {field: record[field] for field in _STABLE_METADATA_FIELDS if field in record}
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def classify_expected_ids(
    expected_ids: list[str],
    snapshot_ids: set[str],
    retrieved_ids: set[str],
) -> list[dict[str, str]]:
    """Classify each expected ID without guessing whether an editorial label is wrong."""
    output = []
    for doc_id in dict.fromkeys(item for item in expected_ids if isinstance(item, str)):
        if doc_id not in snapshot_ids:
            status = "source_snapshot_missing"
        elif doc_id in retrieved_ids:
            status = "retrieved"
        else:
            status = "ranking_miss"
        output.append({
            "document_id": doc_id,
            "status": status,
            "label_review": "human_review_required",
        })
    return output
