# Long-term memory v1

## Delivered

- SQLite-backed structured memories with category, content, source/provenance, confidence, tags, creation time, optional expiry, and archive state.
- Search by content/category/tags, optional category filter, bounded results, and recent-memory listing.
- Archive operation for retiring obsolete entries without silently rewriting history.
- CLI commands: `memory-add`, `memory-search`, `memory-list`, and `memory-archive`.
- Tests for provenance, filtering, archive behavior, and validation.

## Safety and limits

- The memory store is local SQLite. It is not synchronized across ephemeral GitHub Actions runners and is not yet a cloud-persistent memory service.
- Do not store passwords, API keys, access tokens, payment details, customer personal data, or other sensitive information.
- Confidence is an explicit metadata value supplied by the caller, not an automatically verified truth score.
- Expiry is enforced during search/list; expired rows remain in the database until separately archived or purged.
- No automatic memory extraction, embedding model, vector database, external provider, or agent is enabled in this phase.

## CLI

```bash
python -m engine.cli memory-add --category decision --content "Prefer free-first model routing" --source operator_note --confidence 0.95 --tags models,policy
python -m engine.cli memory-search --query "free-first"
python -m engine.cli memory-list --limit 30
python -m engine.cli memory-archive --id MEMORY_ID
```

All commands use the same `RAAWAA_ENGINE_DB` path as the task queue.
