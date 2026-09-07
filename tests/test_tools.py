"""Tests for edgedash.query.tools — all 7 tools, clamping bounds, skill handling.

All tests are pure: no I/O, no LLM, no real config. Uses temp DB + mocked storage.
"""
from __future__ import annotations

import tempfile
import os
from datetime import datetime, timezone, timedelta
from collections import Counter

import pytest

from edgedash import storage as st
from edgedash.query.tools import (
    TOOLS,
    companies_hiring,
    best_matches,
    top_gaps,
    gap_detail,
    trend,
    listing_count,
    skill_demand,
    _clamp_int,
    _canonical_skill_or_empty,
)
from edgedash.skills import canonical


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db():
    """Temp DB with passing cycle, scored listings, extraction cache, gap snapshot."""
    tmp = tempfile.mktemp(suffix=".db")
    st.init_db(tmp)

    now = datetime.now(timezone.utc)
    # Passing verifier cycle (rule 46 gate)
    st.log_cycle("verifier", now.isoformat(), now.isoformat(), 0, "ok", "VERDICT: pass — all checks passed")

    # 4 listings: 3 recent, 1 old
    rows = [
        {
            "title": "DA1",
            "company": "Acme",
            "location": "Berlin",
            "url": "https://a.com/1",
            "description": "Python SQL",
            "source": "test",
            "posted_at": now.isoformat(),
            "fetched_at": now.isoformat(),
        },
        {
            "title": "DA2",
            "company": "Acme",
            "location": "Berlin",
            "url": "https://a.com/2",
            "description": "Python Tableau",
            "source": "test",
            "posted_at": now.isoformat(),
            "fetched_at": now.isoformat(),
        },
        {
            "title": "DA3",
            "company": "Globex",
            "location": "Berlin",
            "url": "https://a.com/3",
            "description": "SQL Excel",
            "source": "test",
            "posted_at": now.isoformat(),
            "fetched_at": now.isoformat(),
        },
        {
            "title": "Old",
            "company": "OldCo",
            "location": "Berlin",
            "url": "https://a.com/4",
            "description": "Python",
            "source": "test",
            "posted_at": (now - timedelta(days=20)).isoformat(),
            "fetched_at": now.isoformat(),
        },
    ]
    st.upsert_listings(rows)

    # Score all listings
    for r in st.get_unscored_listings(10):
        st.update_listing_score(
            r["id"], 85, "test reason", {"skill_match": 1.0, "seniority_fit": 1.0, "location_fit": 1.0, "recency": 1.0, "gaps": []}
        )

    # Extraction cache with known skills
    import json as _json
    import hashlib

    def desc_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    for r in st.get_listings(10, 0):
        desc = r.get("description") or ""
        h = desc_hash(desc)
        st.put_cached_extraction(
            h,
            {
                "required_skills": (
                    ["python", "sql"]
                    if "Python SQL" in desc
                    else (["python", "tableau"] if "Python Tableau" in desc else ["sql", "excel"])
                ),
                "nice_to_have": ["tableau"],
                "seniority": "mid",
                "years_required": 2,
                "remote_ok": False,
            },
        )

    # Gap analyzer (needs config with my_skills that DON'T cover all extracted skills)
    from edgedash.config import load_config

    cfg = load_config()
    # Override my_skills to create gaps (only python, not sql/tableau/excel)
    cfg = type("Cfg", (), {**cfg.__dict__, "my_skills": ["python"], "skills": ["python"]})()

    # First, write an OLD snapshot for trend (must be inserted BEFORE gap_analyzer
    # so gap_analyzer's snapshot is the latest by auto-increment ID)
    import uuid
    old_computed = (now - timedelta(weeks=2)).isoformat()
    old_run_id = str(uuid.uuid4())
    st.write_gap_snapshot(
        old_run_id,
        old_computed,
        [
            {
                "skill": "sql",
                "listings_blocked": 2,
                "opportunity_cost": 1.5,
                "mean_score": 80.0,
                "top_score": 85,
                "also_nice_to_have": 0,
                "low_confidence": False,
                # Use listing IDs (SHA-256 of source+url), not URLs
                "example_ids": [
                    "b997ab3ebc09a5cc0692c2d33a2292b2ac792fa003978782f74aba81e3716be0",  # DA1
                    "6479a860d4a5f10be6135bb21e17b82c36d21450337a99e919f76a211d2fea27",  # DA3
                ],
            }
        ],
    )

    # Now run gap_analyzer — its snapshot will be latest (higher auto-increment ID)
    from edgedash.agents.gap_analyzer import GapAnalyzer
    from edgedash.planning import StopConditions

    GapAnalyzer().run(cfg, st, StopConditions())

    yield tmp, st, cfg

    # Cleanup (Windows may lock — ignore)
    try:
        os.unlink(tmp)
    except Exception:
        pass


@pytest.fixture
def mock_storage(temp_db):
    """Returns (storage_module, config) for injection into tools._get_storage_and_config."""
    tmp, storage, cfg = temp_db
    return storage, cfg


# ---------------------------------------------------------------------------
# _clamp_int unit tests
# ---------------------------------------------------------------------------


class TestClampInt:
    def test_within_bounds(self):
        assert _clamp_int(5, 1, 10, 3) == 5

    def test_below_low(self):
        assert _clamp_int(0, 1, 10, 3) == 1

    def test_above_high(self):
        assert _clamp_int(999, 1, 10, 3) == 10

    def test_exact_low(self):
        assert _clamp_int(1, 1, 10, 3) == 1

    def test_exact_high(self):
        assert _clamp_int(10, 1, 10, 3) == 10

    def test_string_numeric(self):
        assert _clamp_int("7", 1, 10, 3) == 7

    def test_string_invalid(self):
        assert _clamp_int("oops", 1, 10, 3) == 3

    def test_bool_rejected(self):
        assert _clamp_int(True, 1, 10, 3) == 3
        assert _clamp_int(False, 1, 10, 3) == 3

    def test_none_rejected(self):
        assert _clamp_int(None, 1, 10, 3) == 3

    def test_negative(self):
        assert _clamp_int(-5, 1, 10, 3) == 1


# ---------------------------------------------------------------------------
# Canonical skill + DB presence
# ---------------------------------------------------------------------------


class TestCanonicalSkillOrEmpty:
    def test_known_skill_returns_canonical(self, mock_storage):
        storage, cfg = mock_storage
        # 'python' is in extraction_cache (required_skills)
        result = _canonical_skill_or_empty(storage, cfg, "python")
        assert result == "python"

    def test_alias_canonicalised(self, mock_storage):
        storage, cfg = mock_storage
        # alias map has 'py' -> 'python'
        result = _canonical_skill_or_empty(storage, cfg, "py")
        assert result == "python"

    def test_unknown_skill_returns_none(self, mock_storage):
        storage, cfg = mock_storage
        result = _canonical_skill_or_empty(storage, cfg, "kubernetes")
        assert result is None

    def test_parenthetical_stripped_then_canonical(self, mock_storage):
        storage, cfg = mock_storage
        # 'python (aws)' -> 'python' after canonical, which exists
        result = _canonical_skill_or_empty(storage, cfg, "python (aws)")
        assert result == "python"

    def test_empty_string_returns_none(self, mock_storage):
        storage, cfg = mock_storage
        result = _canonical_skill_or_empty(storage, cfg, "")
        assert result is None


# ---------------------------------------------------------------------------
# companies_hiring
# ---------------------------------------------------------------------------


class TestCompaniesHiring:
    def test_default_days(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = companies_hiring()
            assert r["summary"] == "3 listings from 2 companies in the last 7 days"
            assert len(r["rows"]) == 2
            assert r["rows"][0]["company"] == "Acme"
            assert r["rows"][0]["count"] == 2
        finally:
            tools._get_storage_and_config = orig

    def test_clamp_low_days(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = companies_hiring(days=0)  # clamped to 1
            assert "1 day" in r["summary"]
        finally:
            tools._get_storage_and_config = orig

    def test_clamp_high_days(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = companies_hiring(days=999)  # clamped to 90
            assert "90 days" in r["summary"]
            assert len(r["rows"]) == 3  # includes OldCo (20d old)
        finally:
            tools._get_storage_and_config = orig

    def test_string_days(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = companies_hiring(days="5")
            assert "5 days" in r["summary"]
        finally:
            tools._get_storage_and_config = orig

    def test_invalid_days_fallback(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = companies_hiring(days="oops")
            assert "7 days" in r["summary"]  # default
        finally:
            tools._get_storage_and_config = orig

    def test_no_passing_cycle_returns_empty(self, temp_db):
        tmp, storage, cfg = temp_db
        # Remove passing cycle
        with storage.get_connection_with_row_factory() as conn:
            conn.execute("DELETE FROM cycle_log WHERE agent='verifier' AND status='ok'")
            conn.commit()

        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = companies_hiring(7)
            assert r["rows"] == []
            assert "no passing cycle" in r["summary"]
        finally:
            tools._get_storage_and_config = orig


# ---------------------------------------------------------------------------
# best_matches
# ---------------------------------------------------------------------------


class TestBestMatches:
    def test_default_n(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = best_matches()
            assert len(r["rows"]) == 4  # all 4 scored
            assert r["rows"][0]["score"] == 85
            assert "top 4 matches" in r["summary"]
        finally:
            tools._get_storage_and_config = orig

    def test_clamp_low_n(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = best_matches(n=0)  # clamped to 1
            assert len(r["rows"]) == 1
            assert "top 1 match" in r["summary"]
        finally:
            tools._get_storage_and_config = orig

    def test_clamp_high_n(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = best_matches(n=999)  # clamped to 25
            assert len(r["rows"]) == 4  # only 4 exist
        finally:
            tools._get_storage_and_config = orig

    def test_shape(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = best_matches(2)
            row = r["rows"][0]
            assert set(row.keys()) == {"score", "title", "company", "reason", "url", "location", "source"}
        finally:
            tools._get_storage_and_config = orig


# ---------------------------------------------------------------------------
# top_gaps
# ---------------------------------------------------------------------------


class TestTopGaps:
    def test_default_n(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = top_gaps()
            # gaps exist for sql, tableau, excel (my_skills only has python)
            assert len(r["rows"]) >= 1
            assert "top" in r["summary"]
            row = r["rows"][0]
            assert set(row.keys()) == {
                "skill",
                "listings_blocked",
                "opportunity_cost",
                "mean_score",
                "top_score",
                "also_nice_to_have",
                "low_confidence",
                "example_ids",
            }
        finally:
            tools._get_storage_and_config = orig

    def test_clamp_bounds(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r1 = top_gaps(n=0)
            assert len(r1["rows"]) <= 1
            r2 = top_gaps(n=999)
            assert len(r2["rows"]) <= 25
        finally:
            tools._get_storage_and_config = orig


# ---------------------------------------------------------------------------
# gap_detail
# ---------------------------------------------------------------------------


class TestGapDetail:
    def test_known_skill_returns_listings(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = gap_detail("sql")
            # sql is a gap (my_skills only has python)
            assert len(r["rows"]) >= 1
            assert all(row["fit_score"] == 85 for row in r["rows"])
            assert r["rows"][0]["title"] in ("DA1", "DA3")
        finally:
            tools._get_storage_and_config = orig

    def test_unknown_skill_returns_empty(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = gap_detail("kubernetes")
            assert r["rows"] == []
            assert "not found in database" in r["summary"]
        finally:
            tools._get_storage_and_config = orig

    def test_canonicalisation_parenthetical(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = gap_detail("sql (postgres)")  # canonical -> sql
            assert len(r["rows"]) >= 1
        finally:
            tools._get_storage_and_config = orig


# ---------------------------------------------------------------------------
# trend
# ---------------------------------------------------------------------------


class TestTrend:
    def test_default_weeks(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = trend()
            # 2 snapshots in window (now + 2 weeks ago)
            assert len(r["rows"]) >= 1
            assert "trend over" in r["summary"]
            row = r["rows"][0]
            assert set(row.keys()) == {
                "skill",
                "earliest_cost",
                "latest_cost",
                "delta_abs",
                "delta_pct",
                "direction",
                "snapshot_count",
            }
        finally:
            tools._get_storage_and_config = orig

    def test_clamp_bounds(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r1 = trend(weeks=0)  # clamped to 1
            r2 = trend(weeks=999)  # clamped to 12
            assert isinstance(r1["rows"], list)
            assert isinstance(r2["rows"], list)
        finally:
            tools._get_storage_and_config = orig


# ---------------------------------------------------------------------------
# listing_count
# ---------------------------------------------------------------------------


class TestListingCount:
    def test_shape_and_summary(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = listing_count()
            assert len(r["rows"]) == 1
            row = r["rows"][0]
            assert set(row.keys()) == {"total_listings", "scored_listings", "unscored_listings", "newest_listing_date"}
            assert row["total_listings"] == 4
            assert row["scored_listings"] == 4
            assert row["unscored_listings"] == 0
            assert "100% scored" in r["summary"]
        finally:
            tools._get_storage_and_config = orig


# ---------------------------------------------------------------------------
# skill_demand
# ---------------------------------------------------------------------------


class TestSkillDemand:
    def test_known_skill_returns_counts(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = skill_demand("python")
            assert len(r["rows"]) == 1
            row = r["rows"][0]
            assert row["skill"] == "python"
            assert row["required_count"] == 2  # DA1, DA2
            assert row["nice_to_have_count"] == 0
            assert row["total_count"] == 2
            assert "2 required" in r["summary"]
        finally:
            tools._get_storage_and_config = orig

    def test_unknown_skill_returns_empty(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = skill_demand("kubernetes")
            assert r["rows"] == []
            assert "not found in database" in r["summary"]
        finally:
            tools._get_storage_and_config = orig

    def test_canonicalisation_parenthetical(self, mock_storage):
        storage, cfg = mock_storage
        import edgedash.query.tools as tools

        orig = tools._get_storage_and_config
        tools._get_storage_and_config = lambda: (storage, cfg)
        try:
            r = skill_demand("python (aws)")  # canonical -> python
            assert r["rows"][0]["skill"] == "python"
            assert r["rows"][0]["required_count"] == 2
        finally:
            tools._get_storage_and_config = orig


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_seven_tools_registered(self):
        expected = {
            "companies_hiring",
            "best_matches",
            "top_gaps",
            "gap_detail",
            "trend",
            "listing_count",
            "skill_demand",
        }
        assert set(TOOLS.keys()) == expected

    def test_each_has_description_and_schema(self):
        for name, spec in TOOLS.items():
            assert spec["description"], f"{name}: missing description"
            assert spec["parameters"], f"{name}: missing parameters"
            assert "type" in spec["parameters"], f"{name}: missing type in parameters"
            assert "properties" in spec["parameters"], f"{name}: missing properties"
            assert spec["func"] is not None, f"{name}: missing func"

    def test_skill_tools_have_required_skill_param(self):
        for name in ("gap_detail", "skill_demand"):
            spec = TOOLS[name]
            assert "skill" in spec["parameters"]["properties"], f"{name}: missing skill property"
            assert "skill" in spec["parameters"].get("required", []), f"{name}: skill not required"

    def test_int_tools_have_clamped_schema(self):
        # Companies_hiring: days 1-90
        props = TOOLS["companies_hiring"]["parameters"]["properties"]
        assert props["days"]["minimum"] == 1
        assert props["days"]["maximum"] == 90

        # Best_matches: n 1-25
        props = TOOLS["best_matches"]["parameters"]["properties"]
        assert props["n"]["minimum"] == 1
        assert props["n"]["maximum"] == 25

        # Top_gaps: n 1-25
        props = TOOLS["top_gaps"]["parameters"]["properties"]
        assert props["n"]["minimum"] == 1
        assert props["n"]["maximum"] == 25

        # Trend: weeks 1-12
        props = TOOLS["trend"]["parameters"]["properties"]
        assert props["weeks"]["minimum"] == 1
        assert props["weeks"]["maximum"] == 12