"""Tests for build_plan() — four scenarios specified in the task.

All tests are pure: no I/O, no storage, no config file.
SystemState and a minimal config stub are constructed inline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from edgedash.planning import Plan, Task, build_plan
from edgedash.state import SystemState


# ---------------------------------------------------------------------------
# Minimal config stub — only the fields build_plan() reads
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Cfg:
    fetch_interval_hours: int = 6
    fetch_max_pages:      int = 5
    fetch_max_listings:   int = 200
    score_batch_size:     int = 25
    score_max_seconds:    int = 300
    analyse_max_seconds:  int = 60


_NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)


def _state(**kwargs) -> SystemState:
    """Build a SystemState with sensible defaults, overridden by kwargs."""
    defaults = dict(
        last_fetch_at      = "2026-09-07T06:00:00+00:00",  # 6 h ago exactly
        hours_since_fetch  = 6.0,
        unscored_count     = 0,
        last_scored_at     = None,
        gaps_computed_at   = "2026-09-07T06:05:00+00:00",
        gaps_stale         = False,
        last_cycle_verdict = "ok",
        last_cycle_at      = "2026-09-07T06:10:00+00:00",
    )
    defaults.update(kwargs)
    return SystemState(**defaults)


def _agent(plan: Plan, name: str) -> Task:
    for t in plan.tasks:
        if t.agent_name == name:
            return t
    raise KeyError(f"agent '{name}' not in plan")


# ---------------------------------------------------------------------------
# Scenario 1: everything stale — all three agents RUN
# ---------------------------------------------------------------------------

class TestEverythingStale:
    def setup_method(self):
        state = _state(
            hours_since_fetch = 8.0,   # > 6 → fetch needed
            unscored_count    = 41,    # > 0 → score needed
            gaps_computed_at  = "2026-09-07T04:00:00+00:00",
            gaps_stale        = True,  # score newer than snapshot
        )
        self.plan = build_plan(state, _Cfg())

    def test_all_three_run(self):
        assert all(t.run for t in self.plan.tasks)

    def test_fetch_runs(self):
        assert _agent(self.plan, "fetch").run is True

    def test_score_runs(self):
        assert _agent(self.plan, "scorer").run is True

    def test_analyse_runs(self):
        assert _agent(self.plan, "gap_analyzer").run is True

    def test_fetch_reason_contains_hours(self):
        assert "hours_since_fetch=8.0" in _agent(self.plan, "fetch").reason

    def test_score_reason_contains_count(self):
        assert "unscored_count=41" in _agent(self.plan, "scorer").reason

    def test_analyse_reason_is_stale(self):
        assert "gaps_stale=true" in _agent(self.plan, "gap_analyzer").reason

    def test_fetch_stop_conditions_set(self):
        sc = _agent(self.plan, "fetch").stop_conditions
        assert sc.max_pages   == 5
        assert sc.max_items   == 200

    def test_score_stop_conditions_set(self):
        sc = _agent(self.plan, "scorer").stop_conditions
        assert sc.max_items   == 25
        assert sc.max_seconds == 300

    def test_analyse_stop_conditions_set(self):
        sc = _agent(self.plan, "gap_analyzer").stop_conditions
        assert sc.max_seconds == 60


# ---------------------------------------------------------------------------
# Scenario 2: nothing to do — all three SKIP
# ---------------------------------------------------------------------------

class TestNothingToDo:
    def setup_method(self):
        state = _state(
            hours_since_fetch = 2.1,   # < 6 → skip fetch
            unscored_count    = 0,     # skip score
            gaps_stale        = False, # skip analyse
        )
        self.plan = build_plan(state, _Cfg())

    def test_all_three_skipped(self):
        assert all(not t.run for t in self.plan.tasks)

    def test_all_three_present(self):
        # Skipped agents must still appear — rule 31
        names = {t.agent_name for t in self.plan.tasks}
        assert names == {"fetch", "scorer", "gap_analyzer"}

    def test_fetch_reason_mentions_threshold(self):
        r = _agent(self.plan, "fetch").reason
        assert "skipped" in r
        assert "2.1" in r

    def test_score_reason_mentions_zero(self):
        assert "unscored_count=0" in _agent(self.plan, "scorer").reason

    def test_analyse_reason_up_to_date(self):
        assert "up to date" in _agent(self.plan, "gap_analyzer").reason

    def test_render_contains_skip_tags(self):
        rendered = self.plan.render()
        assert rendered.count("[SKIP]") == 3

    def test_stop_conditions_are_na(self):
        for task in self.plan.tasks:
            assert task.stop_conditions.render() == "n/a"


# ---------------------------------------------------------------------------
# Scenario 3: only unscored listings — fetch skips, score runs, analyse skips
# ---------------------------------------------------------------------------

class TestOnlyUnscored:
    def setup_method(self):
        state = _state(
            hours_since_fetch = 1.5,   # recently fetched → skip
            unscored_count    = 15,    # needs scoring
            gaps_stale        = False, # gaps still current (no new scores yet)
        )
        self.plan = build_plan(state, _Cfg())

    def test_fetch_skipped(self):
        assert _agent(self.plan, "fetch").run is False

    def test_score_runs(self):
        assert _agent(self.plan, "scorer").run is True

    def test_analyse_skipped(self):
        assert _agent(self.plan, "gap_analyzer").run is False

    def test_score_reason_contains_count(self):
        assert "unscored_count=15" in _agent(self.plan, "scorer").reason

    def test_render_has_one_run(self):
        rendered = self.plan.render()
        assert rendered.count("[RUN ]") == 1
        assert rendered.count("[SKIP]") == 2


# ---------------------------------------------------------------------------
# Scenario 4: gaps stale but nothing unscored
# ---------------------------------------------------------------------------

class TestGapsStaleNoUnscored:
    def setup_method(self):
        state = _state(
            hours_since_fetch = 0.5,   # just fetched → skip
            unscored_count    = 0,     # skip score
            gaps_stale        = True,  # but gaps are stale
        )
        self.plan = build_plan(state, _Cfg())

    def test_fetch_skipped(self):
        assert _agent(self.plan, "fetch").run is False

    def test_score_skipped(self):
        assert _agent(self.plan, "scorer").run is False

    def test_analyse_runs(self):
        assert _agent(self.plan, "gap_analyzer").run is True

    def test_analyse_reason_is_stale(self):
        assert "gaps_stale=true" in _agent(self.plan, "gap_analyzer").reason

    def test_render_has_one_run(self):
        rendered = self.plan.render()
        assert rendered.count("[RUN ]") == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_never_fetched_runs_fetch(self):
        state = _state(last_fetch_at=None, hours_since_fetch=None)
        plan  = build_plan(state, _Cfg())
        task  = _agent(plan, "fetch")
        assert task.run is True
        assert "never" in task.reason

    def test_never_analysed_runs_analyse(self):
        state = _state(gaps_computed_at=None, gaps_stale=False)
        plan  = build_plan(state, _Cfg())
        task  = _agent(plan, "gap_analyzer")
        assert task.run is True
        assert "null" in task.reason

    def test_exactly_at_threshold_runs_fetch(self):
        # hours_since_fetch == fetch_interval_hours → should fetch
        state = _state(hours_since_fetch=6.0)
        plan  = build_plan(state, _Cfg(fetch_interval_hours=6))
        assert _agent(plan, "fetch").run is True

    def test_just_below_threshold_skips_fetch(self):
        state = _state(hours_since_fetch=5.99)
        plan  = build_plan(state, _Cfg(fetch_interval_hours=6))
        assert _agent(plan, "fetch").run is False

    def test_plan_always_has_three_tasks(self):
        for unscored, stale, hours in [(0, False, 1), (5, True, 10), (0, True, 0.1)]:
            state = _state(unscored_count=unscored, gaps_stale=stale,
                           hours_since_fetch=hours)
            plan  = build_plan(state, _Cfg())
            assert len(plan.tasks) == 3

    def test_render_is_string(self):
        state = _state()
        plan  = build_plan(state, _Cfg())
        assert isinstance(plan.render(), str)
