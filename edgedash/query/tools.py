"""Deterministic query registry — no LLM anywhere in this file.

Public API
----------
TOOLS : dict
    Populated by the @tool decorator. Each entry has {name, description,
    parameters (JSON-schema), func}.

Seven read-only tools (all gated on last passing cycle per rule 46):
  companies_hiring(days)  — companies with recent postings
  best_matches(n)         — highest-scoring listings
  top_gaps(n)             — skill gaps by opportunity cost
  gap_detail(skill)       — listings blocked by one skill (rule 26)
  trend(weeks)            — gap cost change over N weeks
  listing_count()         — totals and newest date
  skill_demand(skill)     — required vs nice_to_have counts

Rule 41: every parameter is treated as untrusted model output — validated
and CLAMPED before use. `skill` is canonicalised via edgedash.skills.canonical
and matched against skills actually present in the DB; never interpolated
into a query string.

Rule 2: all reads go through the storage module. No direct sqlite3.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from edgedash.skills import canonical

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TOOLS: dict[str, dict[str, Any]] = {}


def tool(
    *,
    name: str | None = None,
    description: str = "",
    parameters: dict[str, Any] | None = None,
) -> Callable:
    """Register a function as a query tool.

    Parameters
    ----------
    name:        Tool name exposed to the router model. Defaults to function name.
    description: What the router model sees — must be specific and unambiguous
                 about when the tool applies.
    parameters:  JSON-schema-style spec: {"type": "object", "properties": {...},
                 "required": [...]}. Used by the router to decide what to pass.

    Example
    -------
    @tool(
        description="Companies with listings posted in the last N days.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 90, "default": 7,
                         "description": "Lookback window in days."}
            },
            "required": []
        }
    )
    def companies_hiring(days: int = 7): ...
    """

    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        TOOLS[tool_name] = {
            "name": tool_name,
            "description": description,
            "parameters": parameters
            or {"type": "object", "properties": {}, "required": []},
            "func": func,
        }
        return func

    return decorator


# ---------------------------------------------------------------------------
# Internal helpers — all pure, all deterministic
# ---------------------------------------------------------------------------


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    """Coerce *value* to int and clamp to [low, high]; fallback to default."""
    try:
        if isinstance(value, bool):
            raise ValueError("bool is not int")
        iv = int(str(value).strip()) if isinstance(value, str) else int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, AttributeError):
        return default
    return max(low, min(high, iv))


def _get_storage_and_config():
    """Load config, resolve storage backend, init DB (safe to call repeatedly)."""
    from edgedash.config import load_config
    from edgedash.storage_factory import get_storage_module, init_db

    config = load_config()
    storage = get_storage_module(config)
    try:
        init_db(config)
    except Exception:
        pass  # DB already initialised or path missing — storage will raise on query
    return storage, config


def _last_passing_cycle(storage: Any) -> dict[str, Any] | None:
    """Return the verified-cycle boundary used by every query tool."""
    try:
        cycle = storage.get_last_passing_cycle()
        if not cycle or _parse_timestamp(cycle.get("finished_at")) is None:
            return None
        return cycle
    except Exception:
        return None


def _parse_timestamp(raw: Any) -> datetime | None:
    """Parse listing posted_at / fetched_at to tz-aware datetime, or None."""
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
        # date-only YYYY-MM-DD
        try:
            dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def _as_of(raw: Any, boundary: datetime) -> bool:
    """True when a timestamp is absent (legacy row) or not after boundary."""
    parsed = _parse_timestamp(raw)
    return parsed is None or parsed <= boundary


def _verified_listings(
    storage: Any, cycle: dict[str, Any], limit: int = 5000
) -> list[dict[str, Any]]:
    """Return listings exactly as they were known at the last passing cycle.

    Listings fetched after the boundary are excluded. A score written after the
    boundary is treated as not-yet-scored. Rows predating the ``scored_at``
    migration remain usable because their missing timestamp is legacy data.
    """
    boundary = _parse_timestamp(cycle.get("finished_at"))
    if boundary is None:
        return []

    verified: list[dict[str, Any]] = []
    for raw in _all_listings(storage, limit=limit):
        if not _as_of(raw.get("fetched_at"), boundary):
            continue
        row = dict(raw)
        if row.get("fit_score") is not None and not _as_of(row.get("scored_at"), boundary):
            row["fit_score"] = None
            row["fit_reason"] = None
            row["fit_components"] = None
        verified.append(row)
    return verified


def _verified_facts(
    storage: Any, cycle: dict[str, Any], listing_ids: set[str]
) -> list[dict[str, Any]]:
    """Return extracted facts belonging to verified, scored listings only."""
    boundary = _parse_timestamp(cycle.get("finished_at"))
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
        and _as_of(row.get("scored_at"), boundary)
        and _as_of(row.get("extracted_at"), boundary)
    ]


def _snapshot_runs_as_of(
    storage: Any, cycle: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return gap-snapshot runs up to the verified-cycle boundary."""
    boundary = _parse_timestamp(cycle.get("finished_at"))
    if boundary is None:
        return []
    try:
        runs = storage.get_distinct_snapshot_runs()
    except Exception:
        return []
    return [
        dict(run)
        for run in runs
        if (ts := _parse_timestamp(run.get("computed_at"))) is not None
        and ts <= boundary
    ]


def _latest_snapshot_as_of(
    storage: Any, cycle: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    runs = _snapshot_runs_as_of(storage, cycle)
    if not runs:
        return []
    latest = max(runs, key=lambda run: _parse_timestamp(run["computed_at"]))
    try:
        return storage.get_snapshot_by_run_id(latest["run_id"])[:limit]
    except Exception:
        return []


def _all_listings(storage: Any, limit: int = 5000) -> list[dict[str, Any]]:
    """Fetch all listings regardless of score, via storage module only.

    Uses get_all_listings if available (new helper), otherwise falls back to
    combining scored + unscored listings — still storage-only, no sqlite3.
    """
    if hasattr(storage, "get_all_listings"):
        try:
            return storage.get_all_listings(limit=limit)
        except Exception:
            pass
    # Fallback: combine scored (any score) + unscored
    rows: list[dict[str, Any]] = []
    try:
        rows.extend(storage.get_listings(limit=limit, min_score=0))
    except Exception:
        pass
    try:
        # get_unscored_listings returns up to limit as well
        rows.extend(storage.get_unscored_listings(limit=limit))
    except Exception:
        pass
    # Deduplicate by id in case of overlap (should not happen)
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


# ---------------------------------------------------------------------------
# 1. companies_hiring
# ---------------------------------------------------------------------------


@tool(
    description=(
        "Companies that have posted listings in the last N days, with per-company "
        "counts. Use this when the user asks which companies are hiring, who is "
        "actively posting, or for a breakdown of recent hiring activity by company. "
        "Do NOT use for fit scores, skill gaps, or trend questions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 90,
                "default": 7,
                "description": "Lookback window in days. Clamped to 1-90.",
            }
        },
        "required": [],
    },
)
def companies_hiring(days: int = 7) -> dict[str, Any]:
    """Return companies with listings posted in the last N days.

    Parameters
    ----------
    days: Lookback window. Clamped to [1, 90]; non-numeric -> 7.

    Returns
    -------
    {"rows": [{"company": str, "count": int}, ...], "summary": str}
    Rows sorted by count DESC then company ASC. Reads recent listings
    from storage; gated on last passing cycle per rule 46.
    """
    days = _clamp_int(days, 1, 90, 7)
    storage, _ = _get_storage_and_config()

    cycle = _last_passing_cycle(storage)
    if cycle is None:
        return {"rows": [], "summary": "no verified data yet — no passing cycle"}

    listings = _verified_listings(storage, cycle, limit=5000)
    if not listings:
        return {"rows": [], "summary": f"0 listings in the last {days} days"}

    boundary = _parse_timestamp(cycle["finished_at"])
    assert boundary is not None
    cutoff = boundary - timedelta(days=days)

    matched: list[dict[str, Any]] = []
    for lst in listings:
        # Prefer posted_at, fall back to fetched_at
        raw_ts = lst.get("posted_at") or lst.get("fetched_at")
        dt = _parse_timestamp(raw_ts)
        if dt is None:
            continue
        if dt >= cutoff:
            matched.append(lst)

    total_matched = len(matched)
    if total_matched == 0:
        return {"rows": [], "summary": f"0 listings in the last {days} days"}

    counter: Counter[str] = Counter()
    for lst in matched:
        company = (lst.get("company") or "").strip()
        if not company:
            company = "(unknown)"
        counter[company] += 1

    rows = [
        {"company": company, "count": count}
        for company, count in sorted(counter.items(), key=lambda x: (-x[1], x[0].lower()))
    ]

    summary = (
        f"{total_matched} listing{'s' if total_matched != 1 else ''} "
        f"from {len(rows)} compan{'ies' if len(rows) != 1 else 'y'} "
        f"in the last {days} day{'s' if days != 1 else ''}"
    )
    return {"rows": rows, "summary": summary}


# ---------------------------------------------------------------------------
# Helper: canonical skill + DB presence check
# ---------------------------------------------------------------------------


def _canonical_skill_or_empty(
    storage: Any,
    config: Any,
    skill: Any,
    facts: list[dict[str, Any]] | None = None,
) -> str | None:
    """Canonicalise *skill* via alias map; return None if not present in DB.

    The skill is canonicalised through edgedash.skills.canonical, then checked
    against skills that actually appear in required_skills or nice_to_have in
    the extraction_cache. If absent, returns None — caller returns empty rows
    rather than raising (rule 41: unknown skill returns empty, never error).
    """
    if not isinstance(skill, str):
        return None
    canon = canonical(skill, config.skill_aliases)
    if not canon:
        return None
    if facts is None:
        try:
            facts = storage.get_scored_listings_with_extractions(limit=5000)
        except Exception:
            facts = []
    present = {
        canonical(str(raw), config.skill_aliases)
        for row in facts
        for raw in (row.get("required_skills") or []) + (row.get("nice_to_have") or [])
    }
    return canon if canon in present else None


def _get_skill_stats(
    storage: Any,
    config: Any,
    facts: list[dict[str, Any]],
) -> tuple[set[str], dict[str, int], dict[str, int]]:
    """Return (all_skills_set, required_counts, nice_counts) from extraction_cache.

    Reads via storage module, no raw SQL in tools.py.
    """
    all_skills: set[str] = set()
    required_counts: dict[str, int] = Counter()
    nice_counts: dict[str, int] = Counter()

    for row in facts:
        required = {
            canonical(str(raw), config.skill_aliases)
            for raw in row.get("required_skills") or []
        }
        nice = {
            canonical(str(raw), config.skill_aliases)
            for raw in row.get("nice_to_have") or []
        }
        required.discard("")
        nice.discard("")
        all_skills.update(required)
        all_skills.update(nice)
        for skill_name in required:
            required_counts[skill_name] += 1
        for skill_name in nice:
            nice_counts[skill_name] += 1

    return all_skills, required_counts, nice_counts


# ---------------------------------------------------------------------------
# 2. best_matches
# ---------------------------------------------------------------------------


@tool(
    description=(
        "Highest-scoring job listings with fit score, title, company, and reason. "
        "Use this when the user asks for the best matches, top jobs, or wants to "
        "see which listings score highest for their profile. Do NOT use for skill "
        "gaps, company hiring activity, or trend analysis."
    ),
    parameters={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
                "default": 10,
                "description": "Number of top matches to return. Clamped to 1-25.",
            }
        },
        "required": [],
    },
)
def best_matches(n: int = 10) -> dict[str, Any]:
    """Return top-N scored listings with score, title, company, reason.

    Parameters
    ----------
    n: Number of results. Clamped to [1, 25]; non-numeric -> 10.

    Returns
    -------
    {"rows": [{"score": int, "title": str, "company": str, "reason": str,
              "url": str, "location": str, "source": str}, ...], "summary": str}
    Sorted by score DESC. Gated on last passing cycle per rule 46.
    """
    n = _clamp_int(n, 1, 25, 10)
    storage, _ = _get_storage_and_config()

    cycle = _last_passing_cycle(storage)
    if cycle is None:
        return {"rows": [], "summary": "no verified data yet — no passing cycle"}

    scored = [
        row for row in _verified_listings(storage, cycle)
        if row.get("fit_score") is not None
    ]
    scored.sort(
        key=lambda row: (
            -int(row.get("fit_score") or 0),
            str(row.get("title") or "").lower(),
            str(row.get("id") or ""),
        )
    )
    rows = scored[:n]

    out_rows = [
        {
            "score": int(r.get("fit_score") or 0),
            "title": r.get("title") or "",
            "company": r.get("company") or "",
            "reason": r.get("fit_reason") or "",
            "url": r.get("url") or "",
            "location": r.get("location") or "",
            "source": r.get("source") or "",
        }
        for r in rows
    ]

    summary = (
        f"top {len(out_rows)} match{'es' if len(out_rows) != 1 else ''} "
        f"from {len(scored)} scored listing{'s' if len(scored) != 1 else ''}"
    )
    return {"rows": out_rows, "summary": summary}


# ---------------------------------------------------------------------------
# 3. top_gaps
# ---------------------------------------------------------------------------


@tool(
    description=(
        "Top skill gaps by opportunity cost — skills you don't have that block "
        "high-scoring listings. Each gap shows listings_blocked and opportunity_cost "
        "(sum of fit_score/100). Use this when the user asks what skills they're "
        "missing, which gaps hurt their profile most, or where to upskill. "
        "Do NOT use for company hiring, individual listing details, or trend over time."
    ),
    parameters={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
                "default": 5,
                "description": "Number of top gaps to return. Clamped to 1-25.",
            }
        },
        "required": [],
    },
)
def top_gaps(n: int = 5) -> dict[str, Any]:
    """Return top-N skill gaps by opportunity cost.

    Parameters
    ----------
    n: Number of results. Clamped to [1, 25]; non-numeric -> 5.

    Returns
    -------
    {"rows": [{"skill": str, "listings_blocked": int, "opportunity_cost": float,
              "mean_score": float, "top_score": int, "also_nice_to_have": int,
              "low_confidence": bool, "example_ids": list[str]}, ...], "summary": str}
    Sorted by opportunity_cost DESC. Gated on last passing cycle per rule 46.
    """
    n = _clamp_int(n, 1, 25, 5)
    storage, _ = _get_storage_and_config()

    cycle = _last_passing_cycle(storage)
    if cycle is None:
        return {"rows": [], "summary": "no verified data yet — no passing cycle"}

    gaps = _latest_snapshot_as_of(storage, cycle, limit=n)

    out_rows = [
        {
            "skill": g.get("skill", ""),
            "listings_blocked": int(g.get("listings_blocked") or 0),
            "opportunity_cost": float(g.get("opportunity_cost") or 0.0),
            "mean_score": float(g.get("mean_score") or 0.0),
            "top_score": int(g.get("top_score") or 0),
            "also_nice_to_have": int(g.get("also_nice_to_have") or 0),
            "low_confidence": bool(g.get("low_confidence") or False),
            "example_ids": g.get("example_ids") or [],
        }
        for g in gaps
    ]

    summary = (
        f"top {len(out_rows)} gap{'s' if len(out_rows) != 1 else ''} "
        f"from {len(out_rows)} skill{'s' if len(out_rows) != 1 else ''} "
        f"in latest snapshot"
    )
    return {"rows": out_rows, "summary": summary}


# ---------------------------------------------------------------------------
# 4. gap_detail
# ---------------------------------------------------------------------------


@tool(
    description=(
        "Drill-down into one named skill gap — returns the specific listings blocked "
        "by that skill, with their scores, titles, companies, and reasons. This is "
        "rule 26's traceability: every reported gap must be able to list the specific "
        "listing IDs it was computed from. Use this when the user asks 'which jobs "
        "am I blocked on for <skill>?' or wants to see the evidence for a gap. "
        "The skill parameter is canonicalised and matched against skills actually "
        "present in the database. Unknown or non-existent skills return empty rows."
    ),
    parameters={
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Skill name (will be canonicalised via alias map). Must exist in the database.",
            }
        },
        "required": ["skill"],
    },
)
def gap_detail(skill: str) -> dict[str, Any]:
    """Return listings blocked by one named skill (rule 26 drill-down).

    Parameters
    ----------
    skill: Skill name. Canonicalised via alias map; matched against DB skills.
           Unknown/non-existent skills return empty rows, never raise.

    Returns
    -------
    {"rows": [{"id": str, "title": str, "company": str, "fit_score": int,
              "reason": str, "url": str, "location": str}, ...], "summary": str}
    Sorted by fit_score DESC. Gated on last passing cycle per rule 46.
    """
    storage, config = _get_storage_and_config()

    cycle = _last_passing_cycle(storage)
    if cycle is None:
        return {"rows": [], "summary": "no verified data yet — no passing cycle"}

    listings = _verified_listings(storage, cycle)
    scored_by_id = {
        row["id"]: row
        for row in listings
        if row.get("id") and row.get("fit_score") is not None
    }
    facts = _verified_facts(storage, cycle, set(scored_by_id))
    canon = _canonical_skill_or_empty(storage, config, skill, facts)
    if canon is None:
        return {"rows": [], "summary": f"skill '{skill}' not found in database (after canonicalisation)"}

    my_skills = {canonical(raw, config.skill_aliases) for raw in config.my_skills}
    if canon in my_skills:
        return {"rows": [], "summary": f"'{canon}' is already in the configured skill profile"}

    blocked_ids = {
        row.get("id")
        for row in facts
        if canon in {
            canonical(str(raw), config.skill_aliases)
            for raw in row.get("required_skills") or []
        }
    }
    matched = [scored_by_id[lid] for lid in blocked_ids if lid in scored_by_id]

    out_rows = [
        {
            "id": r.get("id", ""),
            "title": r.get("title") or "",
            "company": r.get("company") or "",
            "fit_score": int(r.get("fit_score") or 0),
            "reason": r.get("fit_reason") or "",
            "url": r.get("url") or "",
            "location": r.get("location") or "",
        }
        for r in sorted(
            matched,
            key=lambda x: (
                -int(x.get("fit_score") or 0),
                str(x.get("title") or "").lower(),
                str(x.get("id") or ""),
            ),
        )
    ]

    summary = (
        f"{len(out_rows)} listing{'s' if len(out_rows) != 1 else ''} blocked by "
        f"'{canon}' (from latest gap snapshot)"
    )
    return {"rows": out_rows, "summary": summary}


# ---------------------------------------------------------------------------
# 5. trend
# ---------------------------------------------------------------------------


@tool(
    description=(
        "Skill gap trend over N weeks — compares opportunity_cost of top gaps across "
        "historical snapshots. Shows which gaps are growing/shrinking. Use this when "
        "the user asks how the market is changing, whether a skill is becoming more "
        "or less important, or for trajectory analysis. Do NOT use for current top "
        "gaps (use top_gaps), company hiring, or individual listing details."
    ),
    parameters={
        "type": "object",
        "properties": {
            "weeks": {
                "type": "integer",
                "minimum": 1,
                "maximum": 12,
                "default": 3,
                "description": "Number of weeks of snapshots to compare. Clamped to 1-12.",
            }
        },
        "required": [],
    },
)
def trend(weeks: int = 3) -> dict[str, Any]:
    """Return gap opportunity_cost change over N weeks from snapshots.

    Parameters
    ----------
    weeks: Lookback window in weeks. Clamped to [1, 12]; non-numeric -> 3.

    Returns
    -------
    {"rows": [{"skill": str, "earliest_cost": float, "latest_cost": float,
              "delta_abs": float, "delta_pct": float, "direction": str,
              "snapshot_count": int}, ...], "summary": str}
    Rows sorted by latest_cost DESC. Only includes skills present in both
    earliest and latest snapshots within the window. Gated on last passing cycle.
    """
    weeks = _clamp_int(weeks, 1, 12, 3)
    storage, _ = _get_storage_and_config()

    cycle = _last_passing_cycle(storage)
    if cycle is None:
        return {"rows": [], "summary": "no verified data yet — no passing cycle"}

    runs = _snapshot_runs_as_of(storage, cycle)

    if not runs:
        return {"rows": [], "summary": "no gap snapshots available"}

    # Filter runs within the last N weeks
    boundary = _parse_timestamp(cycle["finished_at"])
    assert boundary is not None
    cutoff = boundary - timedelta(weeks=weeks)
    recent_runs = [
        r for r in runs
        if r.get("computed_at")
        and _parse_timestamp(r["computed_at"])
        and _parse_timestamp(r["computed_at"]) >= cutoff
    ]

    if len(recent_runs) < 2:
        return {"rows": [], "summary": f"need at least 2 snapshots in the last {weeks} week(s) — found {len(recent_runs)}"}

    earliest_run = recent_runs[0]
    latest_run = recent_runs[-1]

    try:
        earliest_rows = storage.get_snapshot_by_run_id(earliest_run["run_id"])
        latest_rows = storage.get_snapshot_by_run_id(latest_run["run_id"])
    except Exception:
        return {"rows": [], "summary": "failed to load snapshots"}

    earliest_map = {r["skill"]: float(r.get("opportunity_cost") or 0.0) for r in earliest_rows}
    latest_map = {r["skill"]: float(r.get("opportunity_cost") or 0.0) for r in latest_rows}

    # Only skills in both snapshots
    common_skills = set(earliest_map.keys()) & set(latest_map.keys())
    if not common_skills:
        return {"rows": [], "summary": "no common skills between earliest and latest snapshots"}

    out_rows = []
    for skill in common_skills:
        e = earliest_map[skill]
        l = latest_map[skill]
        delta = l - e
        pct = (delta / e * 100) if e > 0 else 0.0
        direction = "up" if delta > 0.05 else ("down" if delta < -0.05 else "flat")
        out_rows.append({
            "skill": skill,
            "earliest_cost": round(e, 2),
            "latest_cost": round(l, 2),
            "delta_abs": round(delta, 2),
            "delta_pct": round(pct, 1),
            "direction": direction,
            "snapshot_count": len(recent_runs),
        })

    out_rows.sort(key=lambda x: x["latest_cost"], reverse=True)

    summary = (
        f"trend over {len(recent_runs)} snapshot(s) "
        f"({earliest_run['computed_at'][:10]} → {latest_run['computed_at'][:10]}), "
        f"{len(out_rows)} common skill(s)"
    )
    return {"rows": out_rows, "summary": summary}


# ---------------------------------------------------------------------------
# 6. listing_count
# ---------------------------------------------------------------------------


@tool(
    description=(
        "Overall database totals: total listings, scored, unscored, and the newest "
        "listing date. Use this when the user asks for a summary, how many jobs are "
        "in the system, what percentage are scored, or when the last listing was "
        "added. Do NOT use for skill gaps, company breakdowns, or fit score details."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
def listing_count() -> dict[str, Any]:
    """Return totals: listings, scored, unscored, newest listing date.

    Returns
    -------
    {"rows": [{"total_listings": int, "scored_listings": int,
              "unscored_listings": int, "newest_listing_date": str}], "summary": str}
    Gated on last passing cycle per rule 46.
    """
    storage, _ = _get_storage_and_config()

    cycle = _last_passing_cycle(storage)
    if cycle is None:
        return {"rows": [], "summary": "no verified data yet — no passing cycle"}

    listings = _verified_listings(storage, cycle)
    scored_rows = [row for row in listings if row.get("fit_score") is not None]
    fetch_times = [
        ts for row in listings
        if (ts := _parse_timestamp(row.get("fetched_at"))) is not None
    ]

    row = {
        "total_listings": len(listings),
        "scored_listings": len(scored_rows),
        "unscored_listings": len(listings) - len(scored_rows),
        "newest_listing_date": max(fetch_times).isoformat() if fetch_times else "never",
    }

    total = row["total_listings"]
    scored = row["scored_listings"]
    pct = (scored / total * 100) if total else 0

    summary = (
        f"{total} total listing{'s' if total != 1 else ''} "
        f"({scored} scored, {row['unscored_listings']} unscored, "
        f"{pct:.0f}% scored), newest: {row['newest_listing_date'][:10] if row['newest_listing_date'] != 'never' else 'never'}"
    )
    return {"rows": [row], "summary": summary}


# ---------------------------------------------------------------------------
# 7. skill_demand
# ---------------------------------------------------------------------------


@tool(
    description=(
        "How often one skill appears in required_skills vs nice_to_have across all "
        "extracted listings. Returns required_count, nice_to_have_count, and total. "
        "The skill is canonicalised and matched against skills actually present in "
        "the database. Unknown or non-existent skills return empty rows. "
        "Use this when the user asks 'how many jobs require <skill>?' or wants to "
        "know demand split for a specific skill. Do NOT use for gap rankings "
        "(use top_gaps) or trend over time (use trend)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Skill name (will be canonicalised via alias map). Must exist in the database.",
            }
        },
        "required": ["skill"],
    },
)
def skill_demand(skill: str) -> dict[str, Any]:
    """Return required vs nice_to_have counts for one skill.

    Parameters
    ----------
    skill: Skill name. Canonicalised via alias map; matched against DB skills.
           Unknown/non-existent skills return empty rows, never raise.

    Returns
    -------
    {"rows": [{"skill": str, "required_count": int, "nice_to_have_count": int,
              "total_count": int}], "summary": str}
    Gated on last passing cycle per rule 46.
    """
    storage, config = _get_storage_and_config()

    cycle = _last_passing_cycle(storage)
    if cycle is None:
        return {"rows": [], "summary": "no verified data yet — no passing cycle"}

    listings = _verified_listings(storage, cycle)
    verified_ids = {
        row["id"] for row in listings
        if row.get("id") and row.get("fit_score") is not None
    }
    facts = _verified_facts(storage, cycle, verified_ids)
    canon = _canonical_skill_or_empty(storage, config, skill, facts)
    if canon is None:
        return {"rows": [], "summary": f"skill '{skill}' not found in database (after canonicalisation)"}

    _, required_counts, nice_counts = _get_skill_stats(storage, config, facts)
    required_count = required_counts.get(canon, 0)
    nice_count = nice_counts.get(canon, 0)

    row = {
        "skill": canon,
        "required_count": required_count,
        "nice_to_have_count": nice_count,
        "total_count": required_count + nice_count,
    }

    summary = (
        f"'{canon}': {required_count} required, {nice_count} nice-to-have, "
        f"{required_count + nice_count} total listing{'s' if (required_count + nice_count) != 1 else ''}"
    )
    return {"rows": [row], "summary": summary}
