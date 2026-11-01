"""Structured, provenance-aware long-term memory backed by SQLite."""
from __future__ import annotations
import json, sqlite3, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("data/engine.db")
def now_utc(): return datetime.now(timezone.utc)
def iso(value): return value.isoformat()

class MemoryStore:
    """Local searchable memory. Never store credentials or sensitive data here."""
    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, category TEXT NOT NULL, content TEXT NOT NULL,
                source TEXT NOT NULL, confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                tags_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT, archived_at TEXT)''')
            db.execute("CREATE INDEX IF NOT EXISTS idx_memories_category_created ON memories(category, created_at)")
    def _connect(self):
        conn=sqlite3.connect(self.db_path, timeout=10); conn.row_factory=sqlite3.Row; return conn
    def add(self, category: str, content: str, source: str, confidence: float=0.8,
            tags: list[str] | None=None, ttl_days: int | None=None) -> dict[str, Any]:
        category,content,source=category.strip(),content.strip(),source.strip()
        if not category or not content or not source: raise ValueError("category, content, and source are required")
        if not 0 <= confidence <= 1: raise ValueError("confidence must be between 0 and 1")
        if ttl_days is not None and not 1 <= ttl_days <= 3650: raise ValueError("ttl_days must be between 1 and 3650")
        tags=tags or []
        if any(not isinstance(tag,str) for tag in tags): raise ValueError("tags must be strings")
        created=now_utc(); memory_id=str(uuid.uuid4())
        expires=iso(created+timedelta(days=ttl_days)) if ttl_days is not None else None
        with self._connect() as db:
            db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,NULL)",
                (memory_id,category,content,source,float(confidence),json.dumps(tags,ensure_ascii=False),iso(created),expires))
            return dict(db.execute("SELECT * FROM memories WHERE id=?",(memory_id,)).fetchone())
    def search(self, query: str, category: str | None=None, limit: int=20) -> list[dict[str,Any]]:
        query=query.strip()
        if not query: raise ValueError("query is required")
        sql="""SELECT * FROM memories WHERE archived_at IS NULL AND (expires_at IS NULL OR expires_at > ?)
               AND (content LIKE ? OR category LIKE ? OR tags_json LIKE ?)"""
        params=[iso(now_utc()),f"%{query}%",f"%{query}%",f"%{query}%"]
        if category: sql += " AND category=?"; params.append(category)
        sql += " ORDER BY confidence DESC, created_at DESC LIMIT ?"; params.append(max(1,min(int(limit),100)))
        with self._connect() as db: return [dict(x) for x in db.execute(sql,params).fetchall()]
    def list_recent(self, limit: int=50) -> list[dict[str,Any]]:
        with self._connect() as db:
            rows=db.execute("SELECT * FROM memories WHERE archived_at IS NULL AND (expires_at IS NULL OR expires_at > ?) ORDER BY created_at DESC LIMIT ?",(iso(now_utc()),max(1,min(int(limit),100)))).fetchall()
            return [dict(x) for x in rows]
    def archive(self, memory_id: str) -> bool:
        with self._connect() as db:
            cur=db.execute("UPDATE memories SET archived_at=? WHERE id=? AND archived_at IS NULL",(iso(now_utc()),memory_id))
            return cur.rowcount==1
