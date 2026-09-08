from __future__ import annotations

import hashlib
import sqlite3
import time
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


def log_query(
    question: str,
    tool: str | None,
    params: dict[str, Any] | None,
    answerable: bool,
    duration: float,
) -> None:
    """Append one entry to the query_log table (rules 42-45)."""
    import json as _json

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO query_log (question, tool, params, answerable, duration, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                question,
                tool,
                _json.dumps(params) if params is not None else None,
                1 if answerable else 0,
                duration,
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )
        conn.commit()


def get_listings(limit: int, min_score: int) -> list[dict[str, Any]]:
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


# ---------------------------------------------------------------------------
# Cheap state queries — counts and MAX(timestamp) only, no full table loads
# ---------------------------------------------------------------------------

def last_scored_at() -> str | None:
    """ISO timestamp of the most recently scored listing, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(scored_at) FROM listings WHERE fit_score IS NOT NULL"
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def last_gap_snapshot_at() -> str | None:
    """ISO timestamp of the most recent gap snapshot row, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(computed_at) FROM gap_snapshots"
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def last_cycle_summary() -> dict[str, Any] | None:
    """Most recent cycle_log row (any agent), or None if no cycles yet."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT agent, finished_at, status, notes "
            "FROM cycle_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Gap snapshot functions (rule 25 — never overwrite a previous run's rows)
# ---------------------------------------------------------------------------

def write_gap_snapshot(run_id: str, computed_at: str, gaps: list[dict[str, Any]]) -> None:
    """Insert a full gap report snapshot. Each call appends new rows — never updates."""
    import json as _json

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
                    _json.dumps(gap["example_ids"]),
                ),
            )
        conn.commit()


def get_snapshot_by_run_id(run_id: str) -> list[dict[str, Any]]:
    """Return all rows for a specific run_id, ranked by opportunity_cost."""
    import json as _json

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
        d["example_ids"] = _json.loads(d["example_ids"])
        d["low_confidence"] = bool(d["low_confidence"])
        result.append(d)
    return result


def get_distinct_snapshot_runs() -> list[dict[str, Any]]:
    """Return one row per distinct run_id, ordered by computed_at ascending.

    Each row has: run_id, computed_at, skill_count.
    Read-only — used for trend comparison.
    """
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
    import json as _json

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
        d["example_ids"] = _json.loads(d["example_ids"])
        d["low_confidence"] = bool(d["low_confidence"])
        result.append(d)
    return result


def get_scored_listings_with_extractions(limit: int = 1000) -> list[dict[str, Any]]:
    """Return scored listings joined with their extraction cache entry.

    The join is done in Python (SHA-256 of description text) because
    SQLite does not have a built-in sha256() function.
    """
    import hashlib as _hashlib
    import json as _json

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
            "required_skills": _json.loads(r["required_skills"]),
            "nice_to_have":    _json.loads(r["nice_to_have"]),
            "created_at":      r["created_at"],
        }
        for r in cache_rows
    }

    result = []
    for listing in listings:
        desc = listing["description"] or ""
        h = _hashlib.sha256(desc.encode("utf-8")).hexdigest()
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


def get_all_listings(limit: int = 5000) -> list[dict[str, Any]]:
    """Return all listings regardless of score, ordered by posted_at DESC.

    Read-only helper for query tools (rule 46). No filtering by score.
    """
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


def get_last_passing_cycle() -> dict[str, Any] | None:
    """Return the most recent cycle_log row where the verifier passed.

    The dashboard calls this to ensure it only reads verified data
    (rule 38 — stale verified data beats fresh unverified data).

    Returns a dict with keys: finished_at, notes, records_touched.
    Returns None if no passing verifier run exists yet.
    """
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
