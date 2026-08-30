from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any, TypedDict

# Expose sqlite3 for modules that need row_factory
_sqlite3 = sqlite3
_db_path: str | None = None

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
    fit_reason TEXT NULL
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    years_required INTEGER,
    remote_ok INTEGER,
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


def init_db(path: str) -> None:
    global _db_path
    _db_path = path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_LISTINGS_DDL + _SKILL_GAPS_DDL + _CYCLE_LOG_DDL + _EXTRACTION_CACHE_DDL)
        # lightweight migrations for scorer columns — safe on existing DBs
        for ddl in (
            "ALTER TABLE listings ADD COLUMN fit_components TEXT",
            "ALTER TABLE listings ADD COLUMN scored_at TEXT",
        ):
            try:
                conn.execute(ddl)
            except Exception:
                pass


def upsert_listings(rows: list[ListingInput]) -> int:
    inserted = 0
    with _connect() as conn:
        for row in rows:
            row_id = listing_id(row["source"], row["url"])
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO listings (
                    id, title, company, location, url, description,
                    source, posted_at, fetched_at, fit_score, fit_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
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
            inserted += cursor.rowcount
        conn.commit()
    return inserted


def count_unscored() -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE fit_score IS NULL"
        ).fetchone()
    return int(row[0])


def last_fetch_time() -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT MAX(fetched_at) FROM listings").fetchone()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def log_cycle(
    agent: str,
    started_at: str,
    finished_at: str,
    records_touched: int,
    status: str,
    notes: str,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO cycle_log (
                agent, started_at, finished_at, records_touched, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (agent, started_at, finished_at, records_touched, status, notes),
        )
        conn.commit()


def get_listings(limit: int, min_score: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, title, company, location, url, description, source,
                   posted_at, fetched_at, fit_score, fit_reason, fit_components
            FROM listings
            WHERE fit_score IS NOT NULL AND fit_score >= ?
            ORDER BY fit_score DESC, fetched_at DESC
            LIMIT ?
            """,
            (min_score, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_cached_extraction(description_hash: str) -> dict[str, Any] | None:
    import json as _json

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT required_skills, nice_to_have, seniority, years_required, remote_ok "
            "FROM extraction_cache WHERE description_hash = ?",
            (description_hash,),
        ).fetchone()
    if row is None:
        return None
    return {
        "required_skills": _json.loads(row["required_skills"]),
        "nice_to_have": _json.loads(row["nice_to_have"]),
        "seniority": row["seniority"],
        "years_required": row["years_required"],
        "remote_ok": bool(row["remote_ok"]) if row["remote_ok"] is not None else None,
    }


def put_cached_extraction(description_hash: str, data: dict[str, Any]) -> None:
    import json as _json
    from datetime import datetime, timezone

    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO extraction_cache
                (description_hash, required_skills, nice_to_have, seniority, years_required, remote_ok, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                description_hash,
                _json.dumps(data["required_skills"]),
                _json.dumps(data["nice_to_have"]),
                data["seniority"],
                data["years_required"],
                (1 if data["remote_ok"] else 0) if data["remote_ok"] is not None else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def get_unscored_listings(limit: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, title, company, location, url, description, source,
                   posted_at, fetched_at, fit_score, fit_reason
            FROM listings
            WHERE fit_score IS NULL
            ORDER BY fetched_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_listing_score(
    listing_id: str, score: int, reason: str, components: dict[str, Any]
) -> None:
    import json as _json
    from datetime import datetime, timezone

    with _connect() as conn:
        conn.execute(
            "UPDATE listings SET fit_score=?, fit_reason=?, fit_components=?, scored_at=? WHERE id=?",
            (int(score), reason, _json.dumps(components), datetime.now(timezone.utc).isoformat(), listing_id),
        )
        conn.commit()


def get_skill_gaps() -> list[dict[str, Any]]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT skill, frequency, last_seen
            FROM skill_gaps
            ORDER BY frequency DESC, last_seen DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_skill_gaps(skill_updates: dict[str, int]) -> int:
    from datetime import datetime, timezone

    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        for skill, freq_delta in skill_updates.items():
            # Check if skill exists
            existing = conn.execute(
                "SELECT frequency FROM skill_gaps WHERE skill = ?", (skill,)
            ).fetchone()
            
            if existing:
                # Update existing
                new_freq = existing[0] + freq_delta
                conn.execute(
                    "UPDATE skill_gaps SET frequency = ?, last_seen = ? WHERE skill = ?",
                    (new_freq, now, skill)
                )
            else:
                # Insert new
                conn.execute(
                    "INSERT INTO skill_gaps (skill, frequency, last_seen) VALUES (?, ?, ?)",
                    (skill, freq_delta, now)
                )
            updated += 1
        conn.commit()
    return updated


def get_scored_listings_with_components(limit: int) -> list[dict[str, Any]]:
    import json as _json
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, title, company, location, url, description, source,
                   posted_at, fetched_at, fit_score, fit_reason, fit_components
            FROM listings
            WHERE fit_score IS NOT NULL AND fit_components IS NOT NULL
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    
    result = []
    for row in rows:
        row_dict = dict(row)
        # Parse fit_components if it exists
        if row_dict.get("fit_components"):
            try:
                row_dict["fit_components"] = _json.loads(row_dict["fit_components"])
            except:
                row_dict["fit_components"] = {}
        result.append(row_dict)
    
    return result


def get_cycle_log(limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT agent, started_at, finished_at, records_touched, status, notes
            FROM cycle_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_stats() -> dict[str, Any]:
    """Get overall statistics about the database."""
    with _connect() as conn:
        # Total listings
        total_listings = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        
        # Scored listings
        scored_listings = conn.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL").fetchone()[0]
        
        # Unscored listings
        unscored_listings = conn.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NULL").fetchone()[0]
        
        # Total skill gaps
        total_gaps = conn.execute("SELECT COUNT(*) FROM skill_gaps").fetchone()[0]
        
        # Total cycles
        total_cycles = conn.execute("SELECT COUNT(*) FROM cycle_log").fetchone()[0]
        
        # Last fetch time
        last_fetch = conn.execute("SELECT MAX(fetched_at) FROM listings").fetchone()[0]
        
        # Last cycle time
        last_cycle = conn.execute("SELECT MAX(finished_at) FROM cycle_log").fetchone()[0]
    
    return {
        "total_listings": total_listings,
        "scored_listings": scored_listings,
        "unscored_listings": unscored_listings,
        "total_gaps": total_gaps,
        "total_cycles": total_cycles,
        "last_fetch": last_fetch,
        "last_cycle": last_cycle,
    }


def get_connection_with_row_factory():
    """Get a connection with row_factory set for dict-like access."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    return conn


def _connect() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("Database not initialised. Call init_db(path) first.")
    return sqlite3.connect(_db_path)


def listing_id(source: str, url: str) -> str:
    payload = f"{source}\0{url}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
