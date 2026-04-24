"""
Extraction cache for the BERIL Atlas L2 layer.

DuckDB-backed cache keyed by (content_hash, prompt_version, vocab_version,
model_id). Ensures incremental scans are free when prompts + vocabs + model
are unchanged; deliberate version bumps force targeted re-extraction.

Design note §9.2 (cache key = sha256(content) + prompt_version +
vocab_version + model_id; implicit invalidation on any key change).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import duckdb


@dataclass
class CachedExtraction:
    """One cached extraction result."""

    cache_key: str
    content_hash: str
    prompt_version: str
    vocab_version: str
    model_id: str
    response_content: str  # raw model output (text)
    response_metadata: dict  # usage counts, finish_reason, etc.
    cached_at: dt.datetime


def _hash_content(content: str) -> str:
    """SHA256 of the content being extracted from."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _cache_key(content_hash: str, prompt_version: str,
                vocab_version: str, model_id: str) -> str:
    """Composite cache key — all four components compressed into a single hex."""
    joined = f"{content_hash}|{prompt_version}|{vocab_version}|{model_id}"
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class ExtractionCache:
    """DuckDB-backed extraction cache.

    Schema (single table):
        extraction_cache(cache_key, content_hash, prompt_version,
                          vocab_version, model_id, response_content,
                          response_metadata, cached_at)
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS extraction_cache (
            cache_key           VARCHAR PRIMARY KEY,
            content_hash        VARCHAR NOT NULL,
            prompt_version      VARCHAR NOT NULL,
            vocab_version       VARCHAR NOT NULL,
            model_id            VARCHAR NOT NULL,
            response_content    VARCHAR,
            response_metadata   VARCHAR,
            cached_at           TIMESTAMP NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cache_content
          ON extraction_cache(content_hash);
        CREATE INDEX IF NOT EXISTS idx_cache_versions
          ON extraction_cache(prompt_version, vocab_version, model_id);
    """

    def __init__(self, db_path: Path):
        """Open or create a cache at db_path."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.con = duckdb.connect(str(db_path))
        # Split DDL on blank-line boundaries to handle multi-statement block
        import re
        for stmt in re.split(r";\s*\n", self._DDL.strip()):
            stmt = stmt.strip().rstrip(";")
            if stmt:
                self.con.execute(stmt)

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ------ API ------

    def get(self, content: str, prompt_version: str, vocab_version: str,
            model_id: str) -> Optional[CachedExtraction]:
        """Return the cached extraction for this key, or None on miss."""
        h = _hash_content(content)
        key = _cache_key(h, prompt_version, vocab_version, model_id)
        row = self.con.execute("""
            SELECT cache_key, content_hash, prompt_version, vocab_version,
                   model_id, response_content, response_metadata, cached_at
            FROM extraction_cache
            WHERE cache_key = ?
        """, [key]).fetchone()
        if row is None:
            return None
        return CachedExtraction(
            cache_key=row[0],
            content_hash=row[1],
            prompt_version=row[2],
            vocab_version=row[3],
            model_id=row[4],
            response_content=row[5] or "",
            response_metadata=json.loads(row[6]) if row[6] else {},
            cached_at=row[7],
        )

    def put(self, content: str, prompt_version: str, vocab_version: str,
            model_id: str, response_content: str,
            response_metadata: Optional[dict] = None) -> CachedExtraction:
        """Insert (or replace) a cache entry. Returns the stored record."""
        h = _hash_content(content)
        key = _cache_key(h, prompt_version, vocab_version, model_id)
        now = dt.datetime.utcnow()
        meta_json = json.dumps(response_metadata or {})

        # DuckDB ON CONFLICT for upsert semantics
        self.con.execute("""
            INSERT INTO extraction_cache
              (cache_key, content_hash, prompt_version, vocab_version,
               model_id, response_content, response_metadata, cached_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (cache_key) DO UPDATE SET
              response_content = excluded.response_content,
              response_metadata = excluded.response_metadata,
              cached_at = excluded.cached_at
        """, [key, h, prompt_version, vocab_version, model_id,
              response_content, meta_json, now])
        return CachedExtraction(
            cache_key=key,
            content_hash=h,
            prompt_version=prompt_version,
            vocab_version=vocab_version,
            model_id=model_id,
            response_content=response_content,
            response_metadata=response_metadata or {},
            cached_at=now,
        )

    # ------ Diagnostics ------

    def stats(self) -> dict[str, Any]:
        """Summary stats for the cache."""
        row = self.con.execute("""
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT content_hash) AS distinct_contents,
                   COUNT(DISTINCT model_id) AS distinct_models,
                   COUNT(DISTINCT prompt_version) AS distinct_prompt_versions,
                   MIN(cached_at) AS earliest,
                   MAX(cached_at) AS latest
            FROM extraction_cache
        """).fetchone()
        return {
            "rows": row[0],
            "distinct_contents": row[1],
            "distinct_models": row[2],
            "distinct_prompt_versions": row[3],
            "earliest": row[4],
            "latest": row[5],
        }

    def hit_rate_for_key_combo(self, prompt_version: str,
                                vocab_version: str, model_id: str) -> int:
        """Count of cache entries matching a specific version combo."""
        row = self.con.execute("""
            SELECT COUNT(*) FROM extraction_cache
            WHERE prompt_version = ? AND vocab_version = ? AND model_id = ?
        """, [prompt_version, vocab_version, model_id]).fetchone()
        return row[0]
