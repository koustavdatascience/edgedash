"""Verified-cycle boundary helpers — rule 38 compliance shared by consumers.

Every read that reaches the dashboard or a query tool must be scoped to the
last PASSING cycle. A listing fetched after the boundary, or scored after the
boundary, is not yet verified and must not be shown. A gap snapshot computed
after the boundary is not yet verified either.

This module centralises that boundary logic so dashboard.py and
edgedash/query/tools.py behave identically (single source of truth).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def parse_timestamp(raw: Any) -> datetime | None:
    """Parse listing posted_at / fetched_at / scored_at to tz-aware datetime.

    Returns None when the value is absent or unparseable (legacy rows).
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except Exception:
            return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
        try:
            dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def as_of(raw: Any, boundary: datetime) -> bool:
    """True when a timestamp is absent (legacy row) or not after boundary."""
    parsed = parse_timestamp(raw)
    return parsed is None or parsed <= boundary


def last_passing_cycle(storage: Any) -> dict[str, Any] | None:
    """Return the verified-cycle boundary, or None if no passing cycle."""
    try:
        cycle = storage.get_last_passing_cycle()
        if not cycle or parse_timestamp(cycle.get("finished_at")) is None:
            return None
        return cycle
    except Exception:
        return None


def all_listings(storage: Any, limit: int = 5000) -> list[dict[str, Any]]:
    """Fetch all listings regardless of score, via storage module only.

    Uses get_all_listings if available (both backends), otherwise falls back to
    combining scored + unscored listings — still storage-only, no raw sqlite3.
    """
    if hasattr(storage, "get_all_listings"):
        try:
            return storage.get_all_listings(limit=limit)
        except Exception:
            pass
    rows: list[dict[str, Any]] = []
    try:
        rows.extend(storage.get_listings(limit=limit, min_score=0))
    except Exception:
        pass
    try:
        rows.extend(storage.get_unscored_listings(limit=limit))
    except Exception:
        pass
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in rows:
        lid = r.get("id")
        if lid and lid in seen:
            continue
        if lid:
            seen.add(lid)
        deduped.append(r)
    return deduped


def verified_listings(
    storage: Any, cycle: dict[str, Any], limit: int = 5000
) -> list[dict[str, Any]]:
    """Return listings exactly as they were known at the last passing cycle.

    Listings fetched after the boundary are excluded. A score written after the
    boundary is treated as not-yet-scored. Rows predating the ``scored_at``
    migration remain usable because their missing timestamp is legacy data.
    """
    boundary = parse_timestamp(cycle.get("finished_at"))
    if boundary is None:
        return []

    verified: list[dict[str, Any]] = []
    for raw in all_listings(storage, limit=limit):
        if not as_of(raw.get("fetched_at"), boundary):
            continue
        row = dict(raw)
        if row.get("fit_score") is not None and not as_of(row.get("scored_at"), boundary):
            row["fit_score"] = None
            row["fit_reason"] = None
            row["fit_components"] = None
        verified.append(row)
    return verified


def verified_facts(
    storage: Any, cycle: dict[str, Any], listing_ids: set[str]
) -> list[dict[str, Any]]:
    """Return extracted facts belonging to verified, scored listings only."""
    boundary = parse_timestamp(cycle.get("finished_at"))
    if boundary is None:
        return []
    try:
        facts = storage.get_scored_listings_with_extractions(limit=5000)
    except Exception:
        return []
    return [
        dict(row)
        for row in facts
        if row.get("id") in listing_ids
        and as_of(row.get("scored_at"), boundary)
        and as_of(row.get("extracted_at"), boundary)
    ]


def snapshot_runs_as_of(
    storage: Any, cycle: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return gap-snapshot runs up to the verified-cycle boundary."""
    boundary = parse_timestamp(cycle.get("finished_at"))
    if boundary is None:
        return []
    try:
        runs = storage.get_distinct_snapshot_runs()
    except Exception:
        return []
    return [
        dict(run)
        for run in runs
        if (ts := parse_timestamp(run.get("computed_at"))) is not None
        and ts <= boundary
    ]


def latest_snapshot_as_of(
    storage: Any, cycle: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    """Return rows from the latest snapshot that predates the verified boundary."""
    runs = snapshot_runs_as_of(storage, cycle)
    if not runs:
        return []
    latest = max(runs, key=lambda run: parse_timestamp(run["computed_at"]))
    try:
        return storage.get_snapshot_by_run_id(latest["run_id"])[:limit]
    except Exception:
        return []


def strip_ts(raw: Any) -> datetime | None:
    """Public alias of parse_timestamp — used before this module existed.

    Kept so tools.py can rely on a single definition without guessing a name.
    """
    return parse_timestamp(raw)


# Re-exported under the underscore-prefixed names the query tools previously
# defined locally. Keeping the private mirrors means call sites read the same
# as before while the source of truth lives in one place.
_parse_timestamp = parse_timestamp  # noqa: N816 (kept for tool parity)
_last_passing_cycle = last_passing_cycle  # noqa: N816
_verified_listings = verified_listings  # noqa: N816
_verified_facts = verified_facts  # noqa: N816
_snapshot_runs_as_of = snapshot_runs_as_of  # noqa: N816
_latest_snapshot_as_of = latest_snapshot_as_of  # noqa: N816
_as_of = as_of  # noqa: N816
_all_listings = all_listings  # noqa: N816