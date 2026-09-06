"""System state inspection — cheap reads only, no full table loads.

Public API
----------
read_state(config, storage, now) -> SystemState
    Snapshot of the system at `now`.  `now` is a parameter so callers
    (and tests) control the clock — datetime.now() is never called here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class SystemState:
    # Fetch
    last_fetch_at:      str | None   # ISO timestamp of last fetched_at
    hours_since_fetch:  float | None # None means never fetched

    # Scoring
    unscored_count:     int
    last_scored_at:     str | None   # ISO timestamp of last score written

    # Gap analysis
    gaps_computed_at:   str | None   # ISO timestamp of latest gap snapshot
    gaps_stale:         bool         # True if any score is newer than gap snapshot

    # Last cycle
    last_cycle_verdict: str | None   # status field from most recent cycle_log row
    last_cycle_at:      str | None   # finished_at from most recent cycle_log row


def _parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO timestamp string to a timezone-aware datetime, or None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _hours_between(earlier: datetime | None, later: datetime) -> float | None:
    """Return decimal hours from earlier to later, or None if earlier is None."""
    if earlier is None:
        return None
    delta = later - earlier
    return delta.total_seconds() / 3600


def read_state(config: Any, storage: Any, now: datetime) -> SystemState:
    """Read current system state.

    Parameters
    ----------
    config:   Config object (unused for queries, kept for interface consistency
              and future threshold reads).
    storage:  The storage module — rule 2, no direct sqlite3 here.
    now:      The reference timestamp.  Never call datetime.now() inside
              this function — pass it in so the function is testable.

    All queries are cheap: COUNT(*) with a WHERE clause, or MAX(col).
    """
    # --- Fetch ---
    last_fetch_str   = storage.last_fetch_time()
    last_fetch_dt    = _parse_iso(last_fetch_str)
    hours_since      = _hours_between(last_fetch_dt, now)

    # --- Scoring ---
    unscored         = storage.count_unscored()
    last_scored_str  = storage.last_scored_at()

    # --- Gap analysis ---
    gaps_str         = storage.last_gap_snapshot_at()
    gaps_dt          = _parse_iso(gaps_str)
    last_scored_dt   = _parse_iso(last_scored_str)

    # Gaps are stale when a score exists that is newer than the latest snapshot
    if gaps_dt is None:
        gaps_stale = False          # no snapshot yet — handled separately by planner
    elif last_scored_dt is None:
        gaps_stale = False          # nothing scored → nothing can be stale
    else:
        gaps_stale = last_scored_dt > gaps_dt

    # --- Last cycle ---
    last_cycle = storage.last_cycle_summary()
    if last_cycle:
        last_cycle_verdict = last_cycle.get("status")
        last_cycle_at      = last_cycle.get("finished_at")
    else:
        last_cycle_verdict = None
        last_cycle_at      = None

    return SystemState(
        last_fetch_at      = last_fetch_str,
        hours_since_fetch  = round(hours_since, 2) if hours_since is not None else None,
        unscored_count     = unscored,
        last_scored_at     = last_scored_str,
        gaps_computed_at   = gaps_str,
        gaps_stale         = gaps_stale,
        last_cycle_verdict = last_cycle_verdict,
        last_cycle_at      = last_cycle_at,
    )
