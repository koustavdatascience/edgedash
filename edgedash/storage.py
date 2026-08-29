from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any, TypedDict

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
        conn.executescript(_LISTINGS_DDL + _SKILL_GAPS_DDL + _CYCLE_LOG_DDL)


def upsert_listings(rows: list[ListingInput]) -> int:
    inserted = 0
    with _connect() as conn:
        for row in rows:
            listing_id = _listing_id(row["source"], row["url"])
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO listings (
                    id, title, company, location, url, description,
                    source, posted_at, fetched_at, fit_score, fit_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    listing_id,
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
                   posted_at, fetched_at, fit_score, fit_reason
            FROM listings
            WHERE fit_score IS NOT NULL AND fit_score >= ?
            ORDER BY fit_score DESC, fetched_at DESC
            LIMIT ?
            """,
            (min_score, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _connect() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("Database not initialised. Call init_db(path) first.")
    return sqlite3.connect(_db_path)


def _listing_id(source: str, url: str) -> str:
    payload = f"{source}\0{url}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
