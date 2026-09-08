from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

# Expose sqlite3 for modules that need row_factory
_sqlite3 = sqlite3
_db_path: str | None = None

# Backend selection: "sqlite" or "postgres". Decided at init_db() time by the
# presence of DATABASE_URL (rule 47 — hosted state lives in the hosted DB).
_backend: str = "sqlite"
_pg_url: str | None = None

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# SQLite DDL
# ---------------------------------------------------------------------------

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

_GAP_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS gap_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT    NOT NULL,
    computed_at TEXT    NOT NULL,
    skill       TEXT    NOT NULL,
    listings_blocked  INTEGER NOT NULL,
    opportunity_cost  REAL    NOT NULL,
    mean_score        REAL    NOT NULL,
    top_score         INTEGER NOT NULL,
    also_nice_to_have INTEGER NOT NULL,
    low_confidence    INTEGER NOT NULL,
    example_ids       TEXT    NOT NULL
);
"""

_QUERY_LOG_DDL = """
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    tool TEXT NULL,
    params TEXT NULL,
    answerable INTEGER NOT NULL,
    duration REAL NOT NULL,
    created_at TEXT NOT NULL
);
"""

# ---------------------------------------------------------------------------
# Postgres DDL (dialect differences: SERIAL ids, DOUBLE PRECISION)
# ---------------------------------------------------------------------------

_PG_LISTINGS_DDL = """
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

_PG_SKILL_GAPS_DDL = """
CREATE TABLE IF NOT EXISTS skill_gaps (
    skill TEXT PRIMARY KEY,
    frequency INTEGER NOT NULL,
    last_seen TEXT NOT NULL
);
"""

_PG_CYCLE_LOG_DDL = """
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

_PG_EXTRACTION_CACHE_DDL = """
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

_PG_GAP_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS gap_snapshots (
    id SERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    skill TEXT NOT NULL,
    listings_blocked INTEGER NOT NULL,
    opportunity_cost DOUBLE PRECISION NOT NULL,
    mean_score DOUBLE PRECISION NOT NULL,
    top_score INTEGER NOT NULL,
    also_nice_to_have INTEGER NOT NULL,
    low_confidence INTEGER NOT NULL,
    example_ids TEXT NOT NULL
);
"""

_PG_QUERY_LOG_DDL = """
CREATE TABLE IF NOT EXISTS query_log (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    tool TEXT NULL,
    params TEXT NULL,
    answerable INTEGER NOT NULL,
    duration DOUBLE PRECISION NOT NULL,
    created_at TEXT NOT NULL
);
"""

_PG_DDLS = (
    _PG_LISTINGS_DDL,
    _PG_SKILL_GAPS_DDL,
    _PG_CYCLE_LOG_DDL,
    _PG_EXTRACTION_CACHE_DDL,
    _PG_GAP_SNAPSHOTS_DDL,
    _PG_QUERY_LOG_DDL,
)

_PG_SQLITE_MIGRATIONS = _PG_LISTINGS_DDL  # not used; kept for clarity


class ListingInput(TypedDict):
    title: str
    company: str
    location: str
    url: str
    description: str
    source: str
    posted_at: str
    fetched_at: str


# ---------------------------------------------------------------------------
# Backend selection + connection helpers
# ---------------------------------------------------------------------------

def _check_postgres_available() -> None:
    if psycopg2 is None:
        raise RuntimeError(
            "psycopg2-binary is required for Postgres storage. "
            "pip install psycopg2-binary"
        )


def _activate_backend() -> None:
    """Choose backend from DATABASE_URL (postgres) or fall back to sqlite."""
    global _backend, _pg_url
    url = os.environ.get("DATABASE_URL")
    if url:
        _backend = "postgres"
        _pg_url = url
    else:
        _backend = "sqlite"
        _pg_url = None


def _backend_is_postgres() -> bool:
    return _backend == "postgres"


def _pg_connect():
    """Open a raw Postgres connection. No secrets are logged on failure (rule 48)."""
    _check_postgres_available()
    if _pg_url is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return psycopg2.connect(_pg_url)


def init_db(path: str) -> None:
    """Initialise the active backend and ensure every table exists.

    Backend is chosen from DATABASE_URL every call: set -> Postgres,
    absent -> local SQLite (offline dev). Safe to run repeatedly.
    """
    global _db_path
    _activate_backend()

    if _backend_is_postgres():
        print("[storage] active backend: postgres (DATABASE_URL set) — host state lives in the hosted DB")
        with _pg_connect() as conn:
            with conn.cursor() as cur:
                for ddl in _PG_DDLS:
                    cur.execute(ddl)
                for ddl in (
                    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS fit_components TEXT",
                    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS scored_at TEXT",
                ):
                    cur.execute(ddl)
            conn.commit()
        return

    _db_path = path
    print(f"[storage] active backend: sqlite (DATABASE_URL absent) -> {path}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_LISTINGS_DDL + _SKILL_GAPS_DDL + _CYCLE_LOG_DDL + _EXTRACTION_CACHE_DDL + _GAP_SNAPSHOTS_DDL + _QUERY_LOG_DDL)
        # lightweight migrations — safe to run on existing databases
        for ddl in (
            "ALTER TABLE listings ADD COLUMN fit_components TEXT",
            "ALTER TABLE listings ADD COLUMN scored_at TEXT",
        ):
            try:
                conn.execute(ddl)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# listings
# ---------------------------------------------------------------------------

def upsert_listings(rows: list[ListingInput]) -> int:
    if _backend_is_postgres():
        return _pg_upsert_listings(rows)
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
    if _backend_is_postgres():
        return _pg_count_unscored()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE fit_score IS NULL"
        ).fetchone()
    return int(row[0])


def last_fetch_time() -> str | None:
    if _backend_is_postgres():
        return _pg_last_fetch_time()
    with _connect() as conn:
        row = conn.execute("SELECT MAX(fetched_at) FROM listings").fetchone()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def get_listings(limit: int, min_score: int) -> list[dict[str, Any]]:
    if _backend_is_postgres():
        return _pg_get_listings(limit, min_score)
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, title, company, location, url, description, source,
                   posted_at, fetched_at, fit_score, fit_reason, fit_components,
                   scored_at
            FROM listings
            WHERE fit_score IS NOT NULL AND fit_score >= ?
            ORDER BY fit_score DESC, fetched_at DESC
            LIMIT ?
            """,
            (min_score, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_unscored_listings(limit: int) -> list[dict[str, Any]]:
    if _backend_is_postgres():
        return _pg_get_unscored_listings(limit)
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
    if _backend_is_postgres():
        _pg_update_listing_score(listing_id, score, reason, components)
        return

    with _connect() as conn:
        conn.execute(
            "UPDATE listings SET fit_score=?, fit_reason=?, fit_components=?, scored_at=? WHERE id=?",
            (int(score), reason, json.dumps(components), datetime.now(timezone.utc).isoformat(), listing_id),
        )
        conn.commit()


def get_scored_listings_with_components(limit: int) -> list[dict[str, Any]]:
    if _backend_is_postgres():
        return _pg_get_scored_listings_with_components(limit)

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
                row_dict["fit_components"] = json.loads(row_dict["fit_components"])
            except Exception:
                row_dict["fit_components"] = {}
        result.append(row_dict)

    return result


def get_all_listings(limit: int = 5000) -> list[dict[str, Any]]:
    """Return all listings regardless of score, ordered by posted_at DESC.

    Read-only helper for query tools (rule 46). No filtering by score.
    """
    if _backend_is_postgres():
        return _pg_get_all_listings(limit)
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, title, company, location, url, description, source,
                   posted_at, fetched_at, fit_score, fit_reason, fit_components,
                   scored_at
            FROM listings
            ORDER BY posted_at DESC, fetched_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# extraction cache
# ---------------------------------------------------------------------------

def get_cached_extraction(description_hash: str) -> dict[str, Any] | None:
    if _backend_is_postgres():
        return _pg_get_cached_extraction(description_hash)

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
        "required_skills": json.loads(row["required_skills"]),
        "nice_to_have": json.loads(row["nice_to_have"]),
        "seniority": row["seniority"],
        "years_required": row["years_required"],
        "remote_ok": bool(row["remote_ok"]) if row["remote_ok"] is not None else None,
    }


def put_cached_extraction(description_hash: str, data: dict[str, Any]) -> None:
    if _backend_is_postgres():
        _pg_put_cached_extraction(description_hash, data)
        return

    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO extraction_cache
                (description_hash, required_skills, nice_to_have, seniority, years_required, remote_ok, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
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


def get_scored_listings_with_extractions(limit: int = 1000) -> list[dict[str, Any]]:
    """Return scored listings joined with their extraction cache entry.

    The join is done in Python (SHA-256 of description text) because
    SQLite does not have a built-in sha256() function.
    """
    if _backend_is_postgres():
        return _pg_get_scored_listings_with_extractions(limit)

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        listings = conn.execute(
            "SELECT id, title, company, fit_score, description, scored_at "
            "FROM listings WHERE fit_score IS NOT NULL "
            "ORDER BY fit_score DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cache_rows = conn.execute(
            "SELECT description_hash, required_skills, nice_to_have, created_at "
            "FROM extraction_cache"
        ).fetchall()

    cache = {
        r["description_hash"]: {
            "required_skills": json.loads(r["required_skills"]),
            "nice_to_have":    json.loads(r["nice_to_have"]),
            "created_at":      r["created_at"],
        }
        for r in cache_rows
    }

    result = []
    for listing in listings:
        desc = listing["description"] or ""
        h = hashlib.sha256(desc.encode("utf-8")).hexdigest()
        if h not in cache:
            continue  # not yet extracted — skip
        result.append({
            "id":              listing["id"],
            "title":           listing["title"],
            "company":         listing["company"],
            "fit_score":       listing["fit_score"],
            "scored_at":       listing["scored_at"],
            "required_skills": cache[h]["required_skills"],
            "nice_to_have":    cache[h]["nice_to_have"],
            "extracted_at":    cache[h]["created_at"],
        })
    return result


# ---------------------------------------------------------------------------
# cycle log
# ---------------------------------------------------------------------------

def log_cycle(
    agent: str,
    started_at: str,
    finished_at: str,
    records_touched: int,
    status: str,
    notes: str,
) -> None:
    if _backend_is_postgres():
        _pg_log_cycle(agent, started_at, finished_at, records_touched, status, notes)
        return
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


def get_cycle_log(limit: int = 50) -> list[dict[str, Any]]:
    if _backend_is_postgres():
        return _pg_get_cycle_log(limit)
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


def last_cycle_summary() -> dict[str, Any] | None:
    """Most recent cycle_log row (any agent), or None if no cycles yet."""
    if _backend_is_postgres():
        return _pg_last_cycle_summary()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT agent, finished_at, status, notes "
            "FROM cycle_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_last_passing_cycle() -> dict[str, Any] | None:
    """Return the most recent cycle_log row where the verifier passed.

    The dashboard calls this to ensure it only reads verified data
    (rule 38 — stale verified data beats fresh unverified data).

    Returns a dict with keys: finished_at, notes, records_touched.
    Returns None if no passing verifier run exists yet.
    """
    if _backend_is_postgres():
        return _pg_get_last_passing_cycle()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT finished_at, notes, records_touched
            FROM cycle_log
            WHERE agent = 'verifier'
              AND status = 'ok'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# query log
# ---------------------------------------------------------------------------

def log_query(
    question: str,
    tool: str | None,
    params: dict[str, Any] | None,
    answerable: bool,
    duration: float,
) -> None:
    """Append one entry to the query_log table (rules 42-45)."""
    if _backend_is_postgres():
        _pg_log_query(question, tool, params, answerable, duration)
        return
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO query_log (question, tool, params, answerable, duration, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                question,
                tool,
                json.dumps(params) if params is not None else None,
                1 if answerable else 0,
                duration,
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# skill gaps
# ---------------------------------------------------------------------------

def get_skill_gaps() -> list[dict[str, Any]]:
    if _backend_is_postgres():
        return _pg_get_skill_gaps()
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
    if _backend_is_postgres():
        return _pg_upsert_skill_gaps(skill_updates)

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


# ---------------------------------------------------------------------------
# gap snapshots (rule 25 — never overwrite a previous run's rows)
# ---------------------------------------------------------------------------

def write_gap_snapshot(run_id: str, computed_at: str, gaps: list[dict[str, Any]]) -> None:
    """Insert a full gap report snapshot. Each call appends new rows — never updates."""
    if _backend_is_postgres():
        _pg_write_gap_snapshot(run_id, computed_at, gaps)
        return

    with _connect() as conn:
        for gap in gaps:
            conn.execute(
                """
                INSERT INTO gap_snapshots (
                    run_id, computed_at, skill, listings_blocked,
                    opportunity_cost, mean_score, top_score,
                    also_nice_to_have, low_confidence, example_ids
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    computed_at,
                    gap["skill"],
                    gap["listings_blocked"],
                    gap["opportunity_cost"],
                    gap["mean_score"],
                    gap["top_score"],
                    gap["also_nice_to_have"],
                    1 if gap.get("low_confidence") else 0,
                    json.dumps(gap["example_ids"]),
                ),
            )
        conn.commit()


def get_snapshot_by_run_id(run_id: str) -> list[dict[str, Any]]:
    """Return all rows for a specific run_id, ranked by opportunity_cost."""
    if _backend_is_postgres():
        return _pg_get_snapshot_by_run_id(run_id)

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT skill, listings_blocked, opportunity_cost, mean_score,
                   top_score, also_nice_to_have, low_confidence, example_ids,
                   computed_at, run_id
            FROM gap_snapshots
            WHERE run_id = ?
            ORDER BY opportunity_cost DESC
            """,
            (run_id,),
        ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        d["example_ids"] = json.loads(d["example_ids"])
        d["low_confidence"] = bool(d["low_confidence"])
        result.append(d)
    return result


def get_distinct_snapshot_runs() -> list[dict[str, Any]]:
    """Return one row per distinct run_id, ordered by computed_at ascending.

    Each row has: run_id, computed_at, skill_count.
    Read-only — used for trend comparison.
    """
    if _backend_is_postgres():
        return _pg_get_distinct_snapshot_runs()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT run_id,
                   MIN(computed_at) AS computed_at,
                   COUNT(*)         AS skill_count
            FROM gap_snapshots
            GROUP BY run_id
            ORDER BY computed_at ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_snapshot(limit: int = 10) -> list[dict[str, Any]]:
    """Return all rows from the most recent gap snapshot run, ranked by opportunity_cost."""
    if _backend_is_postgres():
        return _pg_get_latest_snapshot(limit)

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        # Find the latest run_id
        latest = conn.execute(
            "SELECT run_id FROM gap_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            return []
        run_id = latest["run_id"]
        rows = conn.execute(
            """
            SELECT skill, listings_blocked, opportunity_cost, mean_score,
                   top_score, also_nice_to_have, low_confidence, example_ids,
                   computed_at, run_id
            FROM gap_snapshots
            WHERE run_id = ?
            ORDER BY opportunity_cost DESC
            LIMIT ?
            """,
            (run_id, limit),
        ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        d["example_ids"] = json.loads(d["example_ids"])
        d["low_confidence"] = bool(d["low_confidence"])
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# cheap state queries — counts and MAX(timestamp) only, no full table loads
# ---------------------------------------------------------------------------

def last_scored_at() -> str | None:
    """ISO timestamp of the most recently scored listing, or None."""
    if _backend_is_postgres():
        return _pg_last_scored_at()
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(scored_at) FROM listings WHERE fit_score IS NOT NULL"
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def last_gap_snapshot_at() -> str | None:
    """ISO timestamp of the most recent gap snapshot row, or None."""
    if _backend_is_postgres():
        return _pg_last_gap_snapshot_at()
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(computed_at) FROM gap_snapshots"
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def get_stats() -> dict[str, Any]:
    """Get overall statistics about the database."""
    if _backend_is_postgres():
        return _pg_get_stats()
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


# ---------------------------------------------------------------------------
# low-level connection helpers
# ---------------------------------------------------------------------------

def get_connection_with_row_factory():
    """Get a connection with row_factory set for dict-like access."""
    if _backend_is_postgres():
        raw = _pg_connect()

        class _PgConn:
            """Minimal psycopg2 shim exposing execute/commit/close like sqlite."""
            def __init__(self) -> None:
                self._cursor = raw.cursor(cursor_factory=RealDictCursor)

            def execute(self, sql: str, params: Any = None):
                self._cursor.execute(sql, params or ())
                return self._cursor

            def commit(self) -> None:
                raw.commit()

            def close(self) -> None:
                raw.close()

            def __enter__(self):
                return self

            def __exit__(self, *exc_info: Any) -> bool:
                self.close()
                return False

        return _PgConn()

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


# ===========================================================================
# Postgres implementations — dialect fully contained in this module (rule 2)
# ===========================================================================

def _pg_upsert_listings(rows: list[ListingInput]) -> int:
    inserted = 0
    with _pg_connect() as conn:
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


def _pg_count_unscored() -> int:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NULL")
            return int(cur.fetchone()[0])


def _pg_last_fetch_time() -> str | None:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(fetched_at) FROM listings")
            result = cur.fetchone()
            return result[0] if result and result[0] else None


def _pg_get_listings(limit: int, min_score: int) -> list[dict[str, Any]]:
    with _pg_connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, company, location, url, description, source,
                       posted_at, fetched_at, fit_score, fit_reason, fit_components,
                       scored_at
                FROM listings
                WHERE fit_score IS NOT NULL AND fit_score >= %s
                ORDER BY fit_score DESC, fetched_at DESC
                LIMIT %s
                """,
                (min_score, limit),
            )
            return [dict(row) for row in cur.fetchall()]


def _pg_get_unscored_listings(limit: int) -> list[dict[str, Any]]:
    with _pg_connect() as conn:
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


def _pg_update_listing_score(
    listing_id: str, score: int, reason: str, components: dict[str, Any]
) -> None:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE listings SET fit_score=%s, fit_reason=%s, fit_components=%s, scored_at=%s WHERE id=%s",
                (int(score), reason, json.dumps(components), datetime.now(timezone.utc).isoformat(), listing_id),
            )
        conn.commit()


def _pg_get_scored_listings_with_components(limit: int) -> list[dict[str, Any]]:
    with _pg_connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, company, location, url, description, source,
                       posted_at, fetched_at, fit_score, fit_reason, fit_components,
                       scored_at
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
                    except Exception:
                        row_dict["fit_components"] = {}
                result.append(row_dict)
            return result


def _pg_get_all_listings(limit: int = 5000) -> list[dict[str, Any]]:
    with _pg_connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, company, location, url, description, source,
                       posted_at, fetched_at, fit_score, fit_reason, fit_components,
                       scored_at
                FROM listings
                ORDER BY posted_at DESC, fetched_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]


def _pg_get_cached_extraction(description_hash: str) -> dict[str, Any] | None:
    with _pg_connect() as conn:
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


def _pg_put_cached_extraction(description_hash: str, data: dict[str, Any]) -> None:
    with _pg_connect() as conn:
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


def _pg_get_scored_listings_with_extractions(limit: int = 1000) -> list[dict[str, Any]]:
    with _pg_connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, company, fit_score, description, scored_at
                FROM listings
                WHERE fit_score IS NOT NULL
                ORDER BY fit_score DESC
                LIMIT %s
                """,
                (limit,),
            )
            listings = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT description_hash, required_skills, nice_to_have, created_at
                FROM extraction_cache
                """
            )
            cache_rows = [dict(row) for row in cur.fetchall()]

    cache = {
        row["description_hash"]: {
            "required_skills": json.loads(row["required_skills"]),
            "nice_to_have": json.loads(row["nice_to_have"]),
            "created_at": row["created_at"],
        }
        for row in cache_rows
    }
    result = []
    for listing in listings:
        desc_hash = hashlib.sha256((listing.get("description") or "").encode("utf-8")).hexdigest()
        facts = cache.get(desc_hash)
        if facts is None:
            continue
        result.append({
            "id": listing["id"],
            "title": listing["title"],
            "company": listing["company"],
            "fit_score": listing["fit_score"],
            "scored_at": listing["scored_at"],
            "required_skills": facts["required_skills"],
            "nice_to_have": facts["nice_to_have"],
            "extracted_at": facts["created_at"],
        })
    return result


def _pg_log_cycle(
    agent: str,
    started_at: str,
    finished_at: str,
    records_touched: int,
    status: str,
    notes: str,
) -> None:
    with _pg_connect() as conn:
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


def _pg_get_cycle_log(limit: int = 50) -> list[dict[str, Any]]:
    with _pg_connect() as conn:
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


def _pg_last_cycle_summary() -> dict[str, Any] | None:
    with _pg_connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT agent, finished_at, status, notes "
                "FROM cycle_log ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
    return dict(row) if row else None


def _pg_get_last_passing_cycle() -> dict[str, Any] | None:
    with _pg_connect() as conn:
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


def _pg_log_query(
    question: str,
    tool: str | None,
    params: dict[str, Any] | None,
    answerable: bool,
    duration: float,
) -> None:
    _check_postgres_available()
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO query_log (question, tool, params, answerable, duration, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    question,
                    tool,
                    json.dumps(params) if params is not None else None,
                    1 if answerable else 0,
                    duration,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        conn.commit()


def _pg_get_skill_gaps() -> list[dict[str, Any]]:
    with _pg_connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT skill, frequency, last_seen
                FROM skill_gaps
                ORDER BY frequency DESC, last_seen DESC
                """
            )
            return [dict(row) for row in cur.fetchall()]


def _pg_upsert_skill_gaps(skill_updates: dict[str, int]) -> int:
    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    with _pg_connect() as conn:
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


def _pg_write_gap_snapshot(run_id: str, computed_at: str, gaps: list[dict[str, Any]]) -> None:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            for gap in gaps:
                cur.execute(
                    """
                    INSERT INTO gap_snapshots (
                        run_id, computed_at, skill, listings_blocked,
                        opportunity_cost, mean_score, top_score,
                        also_nice_to_have, low_confidence, example_ids
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id, computed_at, gap["skill"], gap["listings_blocked"],
                        gap["opportunity_cost"], gap["mean_score"], gap["top_score"],
                        gap["also_nice_to_have"], 1 if gap.get("low_confidence") else 0,
                        json.dumps(gap["example_ids"]),
                    ),
                )
        conn.commit()


def _pg_get_snapshot_by_run_id(run_id: str) -> list[dict[str, Any]]:
    with _pg_connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT skill, listings_blocked, opportunity_cost, mean_score,
                       top_score, also_nice_to_have, low_confidence, example_ids,
                       computed_at, run_id
                FROM gap_snapshots
                WHERE run_id = %s
                ORDER BY opportunity_cost DESC
                """,
                (run_id,),
            )
            rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        row["example_ids"] = json.loads(row["example_ids"])
        row["low_confidence"] = bool(row["low_confidence"])
    return rows


def _pg_get_distinct_snapshot_runs() -> list[dict[str, Any]]:
    with _pg_connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT run_id, MIN(computed_at) AS computed_at,
                       COUNT(*) AS skill_count
                FROM gap_snapshots
                GROUP BY run_id
                ORDER BY MIN(computed_at) ASC
                """
            )
            return [dict(row) for row in cur.fetchall()]


def _pg_get_latest_snapshot(limit: int = 10) -> list[dict[str, Any]]:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT run_id FROM gap_snapshots ORDER BY id DESC LIMIT 1")
            latest = cur.fetchone()
    return _pg_get_snapshot_by_run_id(latest[0])[:limit] if latest else []


def _pg_last_scored_at() -> str | None:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(scored_at) FROM listings WHERE fit_score IS NOT NULL"
            )
            row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def _pg_last_gap_snapshot_at() -> str | None:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(computed_at) FROM gap_snapshots")
            row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def _pg_get_stats() -> dict[str, Any]:
    with _pg_connect() as conn:
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


# ---------------------------------------------------------------------------
# CLI — migrate + check (run: python -m edgedash.storage)
# ---------------------------------------------------------------------------

_TABLES = (
    "listings",
    "skill_gaps",
    "cycle_log",
    "extraction_cache",
    "gap_snapshots",
    "query_log",
)


def _cmd_migrate(path: str) -> None:
    """Create every table on the active backend; idempotent (rule 51)."""
    init_db(path)
    print("migrate: all tables ensured — safe to run repeatedly")


def _cmd_check(path: str) -> None:
    """Print active backend, connectivity, and per-table row counts (rule 50)."""
    _activate_backend()
    backend = _backend
    print(f"backend    : {backend}")

    try:
        if _backend_is_postgres():
            conn = _pg_connect()
            cur = conn.cursor()
        else:
            if os.environ.get("EDGEDASH_DB_PATH"):
                db_file = os.environ["EDGEDASH_DB_PATH"]
            else:
                db_file = path
            import sqlite3 as _sq
            conn = _sq.connect(db_file)
            cur = conn.cursor()
    except Exception:
        # Rule 48: never surface connection details/secrets in an error.
        print("connection : FAILED (check DATABASE_URL / database availability)")
        print("row counts : unavailable")
        return

    print("connection : ok")
    try:
        for table in _TABLES:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                print(f"  {table:20s} {count}")
            except Exception:
                # Mid-migration or not yet created — status, not a traceback.
                print(f"  {table:20s} (missing — run --migrate)")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m edgedash.storage")
    parser.add_argument(
        "--migrate", action="store_true",
        help="Create every table on the active backend (idempotent)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Print active backend, connectivity, and row counts per table",
    )
    parser.add_argument(
        "--db-path", default="data/edgedash.db",
        help="SQLite fallback path when DATABASE_URL is absent (default: data/edgedash.db)",
    )
    args = parser.parse_args(argv)

    if args.migrate:
        _cmd_migrate(args.db_path)
    elif args.check:
        _cmd_check(args.db_path)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()