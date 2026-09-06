"""Tests for edgedash/verification.py.

One passing case, one failing case, and the trivial/edge case for each
check. All tests are pure — no I/O, no storage, no config file.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from edgedash.verification import (
    CheckResult,
    Verdict,
    check_extraction_sanity,
    check_freshness,
    check_gap_sample_size,
    check_score_spread,
    run_all_checks,
)


# ---------------------------------------------------------------------------
# Minimal config stub — only the threshold fields each check reads
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Cfg:
    min_score_spread:          int   = 10
    min_score_stdev:           int   = 5
    max_empty_extraction_pct:  int   = 20
    max_skills_per_listing:    int   = 20
    min_gap_sample:            int   = 3
    max_data_age_days:         int   = 3


_NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)
_CFG = _Cfg()


# ---------------------------------------------------------------------------
# check_score_spread
# ---------------------------------------------------------------------------

class TestCheckScoreSpread:

    def test_healthy_distribution_passes(self):
        # spread=50, stdev well above 5
        scores = [30, 45, 55, 65, 80]
        r = check_score_spread(scores, _CFG)
        assert r.passed is True
        assert r.name == "score_spread"
        assert "spread=50" in r.observed

    def test_tight_spread_fails(self):
        # spread=5 < min_score_spread=10
        scores = [40, 41, 43, 44, 45]
        r = check_score_spread(scores, _CFG)
        assert r.passed is False
        assert "spread=5" in r.observed
        assert "min_score_spread=10" in r.threshold
        assert "inflating" in r.message

    def test_tight_stdev_fails_even_if_spread_ok(self):
        # spread=10 (exactly at threshold), but stdev is tiny
        # Use scores that give spread=10 but very tight stdev
        scores = [45, 45, 45, 45, 55]   # spread=10, stdev≈4.47
        r = check_score_spread(scores, _CFG)
        assert r.passed is False
        assert "stdev" in r.observed
        assert "min_score_stdev=5" in r.threshold

    def test_fewer_than_five_scores_passes_trivially(self):
        scores = [40, 41, 42, 43]   # 4 scores
        r = check_score_spread(scores, _CFG)
        assert r.passed is True
        assert "too few" in r.message
        assert "n=4" in r.observed

    def test_empty_scores_passes_trivially(self):
        r = check_score_spread([], _CFG)
        assert r.passed is True
        assert "n=0" in r.observed

    def test_exactly_five_scores_evaluated(self):
        # Exactly 5 scores — must be evaluated, not trivially passed
        scores = [50, 51, 52, 53, 54]   # spread=4 < 10
        r = check_score_spread(scores, _CFG)
        assert r.passed is False

    def test_result_is_check_result(self):
        r = check_score_spread([10, 30, 50, 70, 90], _CFG)
        assert isinstance(r, CheckResult)


# ---------------------------------------------------------------------------
# check_extraction_sanity
# ---------------------------------------------------------------------------

class TestCheckExtractionSanity:

    def _facts(self, skills_per: list[list[str]]) -> list[dict]:
        return [{"required_skills": s} for s in skills_per]

    def test_healthy_extractions_pass(self):
        facts = self._facts([
            ["python", "sql"],
            ["tableau", "excel", "power bi"],
            ["pandas", "data cleaning"],
        ])
        r = check_extraction_sanity(facts, _CFG)
        assert r.passed is True
        assert "empty_pct=0.0%" in r.observed

    def test_too_many_empty_fails(self):
        # 3 of 4 empty = 75% > 20%
        facts = self._facts([[], [], [], ["python"]])
        r = check_extraction_sanity(facts, _CFG)
        assert r.passed is False
        assert "empty_pct=75.0%" in r.observed
        assert "max_empty_extraction_pct=20%" in r.threshold
        assert "broken" in r.message

    def test_oversized_skills_list_fails(self):
        # one listing with 21 skills > max_skills_per_listing=20
        fat_skills = [f"skill_{i}" for i in range(21)]
        facts = self._facts([["python"], fat_skills, ["sql"]])
        r = check_extraction_sanity(facts, _CFG)
        assert r.passed is False
        assert "max_skills_per_listing=21" in r.observed
        assert "sentence" in r.message

    def test_empty_facts_list_passes_trivially(self):
        r = check_extraction_sanity([], _CFG)
        assert r.passed is True
        assert "trivially" in r.message

    def test_exactly_at_empty_threshold_passes(self):
        # Exactly 20% empty — should pass (threshold is strictly greater than)
        facts = self._facts([[], ["sql"], ["python"], ["pandas"], ["tableau"]])
        r = check_extraction_sanity(facts, _CFG)
        assert r.passed is True

    def test_exactly_at_skills_threshold_passes(self):
        # Exactly 20 skills — should pass (threshold is strictly greater than)
        facts = self._facts([[f"s{i}" for i in range(20)]])
        r = check_extraction_sanity(facts, _CFG)
        assert r.passed is True

    def test_none_required_skills_counts_as_empty(self):
        facts = [{"required_skills": None}, {"required_skills": ["sql"]}]
        r = check_extraction_sanity(facts, _CFG)
        # 1 of 2 = 50% > 20% → should fail
        assert r.passed is False


# ---------------------------------------------------------------------------
# check_gap_sample_size
# ---------------------------------------------------------------------------

class TestCheckGapSampleSize:

    def _gap(self, skill: str, n: int) -> dict:
        return {"skill": skill, "listings_blocked": n}

    def test_adequate_sample_passes(self):
        gaps = [self._gap("kubernetes", 5), self._gap("typescript", 3)]
        r = check_gap_sample_size(gaps, _CFG)
        assert r.passed is True
        assert "kubernetes" in r.observed
        assert "5" in r.observed

    def test_top_gap_too_small_fails(self):
        # top gap only 2 listings < min_gap_sample=3
        gaps = [self._gap("kubernetes", 2), self._gap("typescript", 8)]
        r = check_gap_sample_size(gaps, _CFG)
        assert r.passed is False
        assert "top_gap_sample=2" in r.observed
        assert "min_gap_sample=3" in r.threshold
        assert "confidence" in r.message

    def test_exactly_at_threshold_passes(self):
        # exactly 3 == min_gap_sample=3 → should pass
        gaps = [self._gap("python", 3)]
        r = check_gap_sample_size(gaps, _CFG)
        assert r.passed is True

    def test_empty_gaps_passes_trivially(self):
        r = check_gap_sample_size([], _CFG)
        assert r.passed is True
        assert "trivially" in r.message

    def test_only_top_gap_evaluated(self):
        # second gap is small but top is fine — should pass
        gaps = [self._gap("kubernetes", 10), self._gap("typescript", 1)]
        r = check_gap_sample_size(gaps, _CFG)
        assert r.passed is True


# ---------------------------------------------------------------------------
# check_freshness
# ---------------------------------------------------------------------------

class TestCheckFreshness:

    def test_fresh_data_passes(self):
        # 1 day old, max is 3
        ts = "2026-09-06T12:00:00+00:00"
        r = check_freshness(ts, _CFG, _NOW)
        assert r.passed is True
        assert "1.0" in r.observed or "1." in r.observed

    def test_stale_data_fails(self):
        # 4 days old > max_data_age_days=3
        ts = "2026-09-03T12:00:00+00:00"
        r = check_freshness(ts, _CFG, _NOW)
        assert r.passed is False
        assert "4.0d" in r.observed
        assert "max_data_age_days=3" in r.threshold
        assert "silently failed" in r.message

    def test_null_fetch_at_fails(self):
        r = check_freshness(None, _CFG, _NOW)
        assert r.passed is False
        assert "null" in r.observed
        assert "never succeeded" in r.message

    def test_exactly_at_threshold_passes(self):
        # exactly 3 days old — should pass (strictly greater than)
        ts = "2026-09-04T12:00:00+00:00"
        r = check_freshness(ts, _CFG, _NOW)
        assert r.passed is True

    def test_naive_timestamp_treated_as_utc(self):
        # timestamps without tz info should not crash
        ts = "2026-09-06T12:00:00"   # no tz
        r = check_freshness(ts, _CFG, _NOW)
        assert r.passed is True

    def test_unparseable_timestamp_fails(self):
        r = check_freshness("not-a-date", _CFG, _NOW)
        assert r.passed is False
        assert "parse" in r.message

    def test_now_is_parameter_not_internal(self):
        # Confirm the function uses the supplied now, not the real clock
        ts = "2020-01-01T00:00:00+00:00"
        custom_now = datetime(2020, 1, 2, tzinfo=timezone.utc)   # 1 day later
        r = check_freshness(ts, _CFG, custom_now)
        assert r.passed is True   # 1 day < 3 day threshold


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------

class TestRunAllChecks:

    def _all_good(self):
        scores          = [30, 45, 55, 65, 80]
        facts_list      = [{"required_skills": ["python", "sql"]}] * 5
        gaps            = [{"skill": "kubernetes", "listings_blocked": 5}]
        latest_fetch_at = "2026-09-07T06:00:00+00:00"   # 6h ago
        return scores, facts_list, gaps, latest_fetch_at

    def test_all_passing_returns_passed_verdict(self):
        scores, facts, gaps, ts = self._all_good()
        verdict, results = run_all_checks(scores, facts, gaps, ts, _CFG, _NOW)
        assert verdict.passed is True
        assert verdict.failed_checks == []
        assert "all checks passed" in verdict.summary

    def test_one_failure_returns_failed_verdict(self):
        scores, facts, gaps, ts = self._all_good()
        # Corrupt scores to trigger spread failure
        bad_scores = [44, 44, 44, 44, 44, 45]
        verdict, results = run_all_checks(bad_scores, facts, gaps, ts, _CFG, _NOW)
        assert verdict.passed is False
        assert len(verdict.failed_checks) >= 1
        assert "score_spread" in verdict.failed_checks[0].name

    def test_all_four_checks_always_run(self):
        scores, facts, gaps, ts = self._all_good()
        # Even if first check fails, all four must run
        bad_scores = [44, 44, 44, 44, 44, 45]
        _, results = run_all_checks(bad_scores, facts, gaps, ts, _CFG, _NOW)
        names = {r.name for r in results}
        assert names == {"score_spread", "extraction_sanity",
                         "gap_sample_size", "freshness"}

    def test_summary_names_failing_checks(self):
        scores, facts, gaps, _ = self._all_good()
        stale_ts = "2026-09-01T00:00:00+00:00"   # 6 days old
        verdict, _ = run_all_checks(scores, facts, gaps, stale_ts, _CFG, _NOW)
        assert "freshness" in verdict.summary
        assert verdict.passed is False

    def test_returns_tuple_of_verdict_and_list(self):
        scores, facts, gaps, ts = self._all_good()
        result = run_all_checks(scores, facts, gaps, ts, _CFG, _NOW)
        assert isinstance(result, tuple)
        assert len(result) == 2
        verdict, all_results = result
        assert isinstance(verdict, Verdict)
        assert isinstance(all_results, list)
        assert all(isinstance(r, CheckResult) for r in all_results)
