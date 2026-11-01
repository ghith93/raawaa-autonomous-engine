"""Deterministic first-pass triage for public research metadata; never auto-approves candidates."""
from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PERMISSIVE_LICENSES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD", "Unlicense"}
KNOWN_LICENSES = PERMISSIVE_LICENSES | {"MPL-2.0", "EPL-2.0", "CDDL-1.0", "GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1", "LGPL-3.0"}
PREFERRED_LANGUAGES = {"python", "typescript", "javascript", "go", "rust"}
RELEVANT_TOPICS = {"agent_frameworks", "coding_agents", "agent_memory", "mcp_ecosystem", "model_routing", "durable_execution"}
RELEVANT_PAPER_TERMS = {"agent", "agents", "orchestration", "workflow", "memory", "llm", "model", "mcp", "coding", "autonomous", "tool use"}


def _parse_datetime(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parts = value.split("-")
        if len(parts) not in (1, 2, 3):
            return None
        padded = [parts[0], parts[1].zfill(2) if len(parts) > 1 else "01",
                  parts[2].zfill(2) if len(parts) > 2 else "01"]
        return date(int(padded[0]), int(padded[1]), int(padded[2]))
    except (ValueError, TypeError):
        return None


def _priority(score: int, archived: bool = False) -> str:
    if archived or score < 45:
        return "low"
    if score >= 70:
        return "high"
    return "medium"


def score_github_candidate(item: dict, now: datetime) -> dict:
    score, reasons, risks = 0, [], []
    license_id = item.get("license_spdx")
    if license_id in PERMISSIVE_LICENSES:
        score += 20
        reasons.append("recognized_permissive_license_metadata")
    elif license_id in KNOWN_LICENSES:
        score += 10
        reasons.append("recognized_license_requires_terms_review")
        risks.append("license_terms_need_review")
    elif license_id:
        score += 5
        risks.append("unrecognized_license_identifier")
    else:
        risks.append("no_license_metadata")

    pushed = _parse_datetime(item.get("pushed_at"))
    age_days = (now - pushed).days if pushed else None
    if age_days is not None and age_days <= 30:
        score += 25
        reasons.append("updated_within_30_days")
    elif age_days is not None and age_days <= 180:
        score += 18
        reasons.append("updated_within_180_days")
    elif age_days is not None and age_days <= 365:
        score += 8
        reasons.append("updated_within_1_year")
    else:
        risks.append("stale_or_missing_activity_timestamp")

    stars = item.get("stars") if type(item.get("stars")) is int and item.get("stars") >= 0 else 0
    star_score = min(20, int(math.log10(stars + 1) * 5)) if stars else 0
    score += star_score
    if stars < 10:
        risks.append("low_public_adoption_signal")

    language = item.get("language")
    if isinstance(language, str) and language.lower() in PREFERRED_LANGUAGES:
        score += 10
        reasons.append("compatible_primary_language")
    elif language:
        score += 3
        risks.append("non_primary_engine_language")
    else:
        risks.append("language_unknown")

    discovered_via = set(item.get("discovered_via", [])) if isinstance(item.get("discovered_via"), list) else set()
    topics = {x.lower() for x in item.get("topics", []) if isinstance(x, str)} if isinstance(item.get("topics"), list) else set()
    if discovered_via & RELEVANT_TOPICS or topics & {"agents", "agent-framework", "mcp", "llm", "workflow", "memory"}:
        score += 20
        reasons.append("directly_relevant_to_engine_research")

    if isinstance(item.get("description"), str) and item["description"].strip():
        score += 5
    archived = item.get("archived") is True
    if archived:
        score = max(0, score - 50)
        risks.append("repository_archived")

    license_ready = license_id in PERMISSIVE_LICENSES
    return {
        "source": "github",
        "source_key": "github:" + str(item.get("full_name", "")).lower(),
        "title": item.get("full_name", ""),
        "url": item.get("url", ""),
        "score": min(100, max(0, score)),
        "priority": _priority(score, archived),
        "license_metadata": license_id,
        "license_review_required": not license_ready,
        "security_review_required": True,
        "compatibility_review_required": True,
        "archived": archived,
        "activity_age_days": age_days,
        "stars": stars,
        "language": language,
        "reasons": reasons,
        "risk_flags": risks,
        "review_status": "unreviewed",
        "automatic_adoption": False,
    }


def score_academic_candidate(item: dict, today: date) -> dict:
    score, reasons, risks = 0, [], []
    published = _parse_date(item.get("published"))
    age_days = (today - published).days if published else None
    if age_days is not None and age_days <= 730:
        score += 30
        reasons.append("published_within_2_years")
    elif age_days is not None and age_days <= 1825:
        score += 20
        reasons.append("published_within_5_years")
    elif age_days is not None:
        score += 8
        risks.append("older_research")
    else:
        risks.append("publication_date_missing")

    citations = item.get("citation_count") if type(item.get("citation_count")) is int and item.get("citation_count") >= 0 else 0
    citation_score = min(25, int(math.log10(citations + 1) * 8)) if citations else 0
    score += citation_score
    title = item.get("title", "").lower() if isinstance(item.get("title"), str) else ""
    matches = sorted(term for term in RELEVANT_PAPER_TERMS if term in title)
    if matches:
        score += min(35, 10 + len(matches) * 4)
        reasons.append("relevant_research_terms:" + ",".join(matches[:6]))
    else:
        risks.append("weak_topic_match")

    if item.get("license_urls"):
        risks.append("deposited_license_metadata_requires_verification")
    else:
        risks.append("no_open_license_metadata")

    return {
        "source": "crossref",
        "source_key": "crossref:" + str(item.get("doi", "")).lower(),
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "score": min(100, max(0, score)),
        "priority": _priority(score),
        "published": item.get("published"),
        "citation_count": citations,
        "license_review_required": True,
        "full_text_rights_review_required": True,
        "relevance_terms": matches,
        "reasons": reasons,
        "risk_flags": risks,
        "review_status": "unreviewed",
        "automatic_adoption": False,
    }


def triage_candidates(snapshot: dict, now: datetime | None = None) -> dict:
    if not isinstance(snapshot, dict):
        raise ValueError("research snapshot must be an object")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    today = current.date()
    github = [score_github_candidate(x, current) for x in snapshot.get("candidates", []) if isinstance(x, dict)]
    academic = [score_academic_candidate(x, today) for x in snapshot.get("academic_candidates", []) if isinstance(x, dict)]
    combined = sorted(github + academic, key=lambda x: (x["score"], x["title"]), reverse=True)
    return {
        "schema_version": 1,
        "triage_only": True,
        "review_required": True,
        "automatic_adoption": False,
        "model_calls": 0,
        "source_counts": {"github": len(github), "crossref": len(academic)},
        "github_candidates": sorted(github, key=lambda x: x["score"], reverse=True),
        "academic_candidates": sorted(academic, key=lambda x: x["score"], reverse=True),
        "recommended_for_manual_review": [x for x in combined if x["priority"] == "high"][:10],
        "all_candidates": combined,
    }


def main(argv=None) -> int:
    import sys
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else Path("research-candidates.json")
    try:
        with path.open(encoding="utf-8") as stream:
            result = triage_candidates(json.load(stream))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Research triage failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
