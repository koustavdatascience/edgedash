from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, TypedDict

# Try to import psycopg2, provide helpful error if missing
try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    _POSTGRES_AVAILABLE = False
else:
    _POSTGRES_AVAILABLE = True


def _check_postgres_available() -> None:
    """Check if Postgres dependencies are available."""
    if not _POSTGRES_AVAILABLE:
        raise ImportError(
            "psycopg2-binary is required for Postgres storage. "
            "Install with: pip install psycopg2-binary"
        )

_db_url: str | None = None
_connection_params: dict[str, Any] | None = None


# DDL statements for Postgres
_LISTINGS_DDL = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    url TEXT NOT NULL,
    description TEXT NOT NULL,
    source TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    fit_score INTEGER NULL,
    fit_reason TEXT NULL,
    fit_components TEXT NULL,
    scored_at TEXT NULL
);
"""

_SKILL_GAPS_DDL = """
CREATE TABLE IF NOT EXISTS skill_gaps (
    skill TEXT PRIMARY KEY,
    frequency INTEGER NOT NULL,
    last_seen TEXT NOT NULL
);
"""

_CYCLE_LOG_DDL = """
CREATE TABLE IF NOT EXISTS cycle_log (
    id SERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    records_touched INTEGER NOT NULL,
    status TEXT NOT NULL,
    notes TEXT NOT NULL
);
"""

_EXTRACTION_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS extraction_cache (
    description_hash TEXT PRIMARY KEY,
    required_skills TEXT NOT NULL,
    nice_to_have TEXT NOT NULL,
    seniority TEXT NOT NULL,
    years_required INTEGER NULL,
    remote_ok INTEGER NULL,
    created_at TEXT NOT NULL
);
"""


class ListingInput(TypedDict):
    title: str
    company: str
    location: str
    url: str
    description: str
    source: str
    posted_at: str
    fetched_at: str


def init_db(db_url: str | None = None, **connection_params: Any) -> None:
    """Initialize Postgres database connection and create tables."""
    _check_postgres_available()
    global _db_url, _connection_params
    
    if db_url:
        _db_url = db_url
        _connection_params = None
    elif connection_params:
        _connection_params = connection_params
        _db_url = None
    else:
        # Try to get from environment
        _db_url = os.environ.get("DATABASE_URL")
        if not _db_url:
            raise ValueError(
                "Database URL not provided. Set DATABASE_URL environment variable "
                "or pass db_url parameter."
            )
    
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_LISTINGS_DDL)
            cur.execute(_SKILL_GAPS_DDL)
            cur.execute(_CYCLE_LOG_DDL)
            cur.execute(_EXTRACTION_CACHE_DDL)
            
            # Lightweight migrations for scorer columns
            for ddl in (
                "ALTER TABLE listings ADD COLUMN IF NOT EXISTS fit_components TEXT",
                "ALTER TABLE listings ADD COLUMN IF NOT EXISTS scored_at TEXT",
            ):
                cur.execute(ddl)
        
        conn.commit()


def upsert_listings(rows: list[ListingInput]) -> int:
    """Insert or ignore listings based on ID."""
    inserted = 0
    with _connect() as conn:
        with conn.cursor() as cur:
            for row in rows:
                row_id = listing_id(row["source"], row["url"])
                cur.execute(
                    """
                    INSERT INTO listings (
                        id, title, company, location, url, description,
                        source, posted_at, fetched_at, fit_score, fit_reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        row_id,
                        row["title"],
                        row["company"],
                        row["location"],
                        row["url"],
                        row["description"],
                        row["source"],
                        row["posted_at"],
                        row["fetched_at"],
                    ),
                )
                inserted += cur.rowcount
        conn.commit()
    return inserted


def count_unscored() -> int:
    """Count listings without a fit score."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NULL")
            return int(cur.fetchone()[0])


def last_fetch_time() -> str | None:
    """Get the most recent fetch timestamp."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(fetched_at) FROM listings")
            result = cur.fetchone()
            return result[0] if result and result[0] else None


def log_cycle(
    agent: str,
    started_at: str,
    finished_at: str,
    records_touched: int,
    status: str,
    notes: str,
) -> None:
    """Log a cycle execution."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cycle_log (
                    agent, started_at, finished_at, records_touched, status, notes
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (agent, started_at, finished_at, records_touched, status, notes),
            )
        conn.commit()


def get_listings(limit: int, min_score: int) -> list[dict[str, Any]]:
    """Get listings with scores above threshold."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, company, location, url, description, source,
                       posted_at, fetched_at, fit_score, fit_reason, fit_components
                FROM listings
                WHERE fit_score IS NOT NULL AND fit_score >= %s
                ORDER BY fit_score DESC, fetched_at DESC
                LIMIT %s
                """,
                (min_score, limit),
            )
            return [dict(row) for row in cur.fetchall()]


def get_cached_extraction(description_hash: str) -> dict[str, Any] | None:
    """Get cached extraction result."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT required_skills, nice_to_have, seniority, years_required, remote_ok
                FROM extraction_cache WHERE description_hash = %s
                """,
                (description_hash,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "required_skills": json.loads(row["required_skills"]),
                "nice_to_have": json.loads(row["nice_to_have"]),
                "seniority": row["seniority"],
                "years_required": row["years_required"],
                "remote_ok": bool(row["remote_ok"]) if row["remote_ok"] is not None else None,
            }


def put_cached_extraction(description_hash: str, data: dict[str, Any]) -> None:
    """Cache extraction result."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO extraction_cache
                    (description_hash, required_skills, nice_to_have, seniority, years_required, remote_ok, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (description_hash) DO UPDATE SET
                    required_skills = EXCLUDED.required_skills,
                    nice_to_have = EXCLUDED.nice_to_have,
                    seniority = EXCLUDED.seniority,
                    years_required = EXCLUDED.years_required,
                    remote_ok = EXCLUDED.remote_ok,
                    created_at = EXCLUDED.created_at
                """,
                (
                    description_hash,
                    json.dumps(data["required_skills"]),
                    json.dumps(data["nice_to_have"]),
                    data["seniority"],
                    data["years_required"],
                    (1 if data["remote_ok"] else 0) if data["remote_ok"] is not None else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        conn.commit()


def get_unscored_listings(limit: int) -> list[dict[str, Any]]:
    """Get unscored listings."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, company, location, url, description, source,
                       posted_at, fetched_at, fit_score, fit_reason
                FROM listings
                WHERE fit_score IS NULL
                ORDER BY fetched_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]


def update_listing_score(
    listing_id: str, score: int, reason: str, components: dict[str, Any]
) -> None:
    """Update listing with score and analysis."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE listings SET fit_score=%s, fit_reason=%s, fit_components=%s, scored_at=%s WHERE id=%s
                """,
                (int(score), reason, json.dumps(components), datetime.now(timezone.utc).isoformat(), listing_id),
            )
        conn.commit()


def get_skill_gaps() -> list[dict[str, Any]]:
    """Get all skill gaps ordered by frequency."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT skill, frequency, last_seen
                FROM skill_gaps
                ORDER BY frequency DESC, last_seen DESC
                """
            )
            return [dict(row) for row in cur.fetchall()]


def upsert_skill_gaps(skill_updates: dict[str, int]) -> int:
    """Update skill gap frequencies."""
    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        with conn.cursor() as cur:
            for skill, freq_delta in skill_updates.items():
                cur.execute(
                    """
                    INSERT INTO skill_gaps (skill, frequency, last_seen)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (skill) DO UPDATE SET
                        frequency = skill_gaps.frequency + EXCLUDED.frequency,
                        last_seen = EXCLUDED.last_seen
                    """,
                    (skill, freq_delta, now)
                )
                updated += cur.rowcount
        conn.commit()
    return updated


def get_scored_listings_with_components(limit: int) -> list[dict[str, Any]]:
    """Get scored listings with components."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, company, location, url, description, source,
                       posted_at, fetched_at, fit_score, fit_reason, fit_components
                FROM listings
                WHERE fit_score IS NOT NULL AND fit_components IS NOT NULL
                ORDER BY fetched_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            result = []
            for row in cur.fetchall():
                row_dict = dict(row)
                if row_dict.get("fit_components"):
                    try:
                        row_dict["fit_components"] = json.loads(row_dict["fit_components"])
                    except:
                        row_dict["fit_components"] = {}
                result.append(row_dict)
            return result


def get_cycle_log(limit: int = 50) -> list[dict[str, Any]]:
    """Get cycle history."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT agent, started_at, finished_at, records_touched, status, notes
                FROM cycle_log
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]


def get_stats() -> dict[str, Any]:
    """Get database statistics."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM listings")
            total_listings = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL")
            scored_listings = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NULL")
            unscored_listings = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM skill_gaps")
            total_gaps = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM cycle_log")
            total_cycles = cur.fetchone()[0]
            
            cur.execute("SELECT MAX(fetched_at) FROM listings")
            last_fetch = cur.fetchone()[0]
            
            cur.execute("SELECT MAX(finished_at) FROM cycle_log")
            last_cycle = cur.fetchone()[0]
    
    return {
        "total_listings": total_listings,
        "scored_listings": scored_listings,
        "unscored_listings": unscored_listings,
        "total_gaps": total_gaps,
        "total_cycles": total_cycles,
        "last_fetch": last_fetch,
        "last_cycle": last_cycle,
    }


def _connect():
    """Create and return a database connection."""
    _check_postgres_available()
    if _db_url:
        return psycopg2.connect(_db_url)
    elif _connection_params:
        return psycopg2.connect(**_connection_params)
    else:
        raise RuntimeError("Database not initialised. Call init_db() first.")


def listing_id(source: str, url: str) -> str:
    """Generate stable listing ID from source and URL."""
    payload = f"{source}\0{url}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def get_all_listings(limit: int = 5000) -> list[dict[str, Any]]:
    """Return all listings regardless of score, ordered by posted_at DESC."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, company, location, url, description, source,
                       posted_at, fetched_at, fit_score, fit_reason, fit_components
                FROM listings
                ORDER BY posted_at DESC, fetched_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]


def get_last_passing_cycle() -> dict[str, Any] | None:
    """Return the most recent cycle_log row where the verifier passed.

    The dashboard calls this to ensure it only reads verified data
    (rule 38 — stale verified data beats fresh unverified data).

    Returns a dict with keys: finished_at, notes, records_touched.
    Returns None if no passing verifier run exists yet.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT finished_at, notes, records_touched
                FROM cycle_log
                WHERE agent = 'verifier'
                  AND status = 'ok'
                ORDER BY id DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    return dict(row) if row else None
