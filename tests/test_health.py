"""Unit tests for edgedash.health system health monitoring module."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from edgedash.health import (
    HealthCheckResult,
    SystemHealthReport,
    check_db_reachability,
    check_last_successful_cycle_age,
    check_newest_listing_age,
    check_recent_verification_failures,
    evaluate_health,
    run_cli,
)


class DummyStorage:
    def __init__(
        self,
        last_fetch: str | None = None,
        last_passing: dict | None = None,
        cycle_logs: list | None = None,
        raise_db_err: bool = False,
    ):
        self._last_fetch = last_fetch
        self._last_passing = last_passing
        self._cycle_logs = cycle_logs or []
        self._raise_db_err = raise_db_err
        self._backend = "sqlite"

    def last_cycle_summary(self):
        if self._raise_db_err:
            raise RuntimeError("Database connection failed")
        return {"status": "complete"}

    def last_fetch_time(self):
        if self._raise_db_err:
            raise RuntimeError("Database connection failed")
        return self._last_fetch

    def get_last_passing_cycle(self):
        if self._raise_db_err:
            raise RuntimeError("Database connection failed")
        return self._last_passing

    def get_cycle_log(self, limit: int = 50):
        if self._raise_db_err:
            raise RuntimeError("Database connection failed")
        return self._cycle_logs[:limit]


def test_db_reachability_pass():
    storage = DummyStorage()
    res = check_db_reachability(storage)
    assert res.passed is True
    assert "connected" in res.observed


def test_db_reachability_fail():
    storage = DummyStorage(raise_db_err=True)
    res = check_db_reachability(storage)
    assert res.passed is False
    assert "unreachable" in res.observed


def test_newest_listing_age_pass():
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(hours=10)).isoformat()
    storage = DummyStorage(last_fetch=recent_ts)

    res = check_newest_listing_age(storage, now)
    assert res.passed is True
    assert "0.4d ago" in res.observed or "0.4 days" in res.observed or "0.4" in res.observed


def test_newest_listing_age_fail_stale():
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=5)).isoformat()
    storage = DummyStorage(last_fetch=old_ts)

    res = check_newest_listing_age(storage, now)
    assert res.passed is False
    assert "exceeds 3-day limit" in res.message


def test_newest_listing_age_fail_empty():
    now = datetime.now(timezone.utc)
    storage = DummyStorage(last_fetch=None)

    res = check_newest_listing_age(storage, now)
    assert res.passed is False
    assert "no listings in database" in res.observed


def test_last_successful_cycle_pass():
    now = datetime.now(timezone.utc)
    cycle_ts = (now - timedelta(hours=12)).isoformat()
    storage = DummyStorage(last_passing={"finished_at": cycle_ts})

    res = check_last_successful_cycle_age(storage, now)
    assert res.passed is True


def test_last_successful_cycle_fail_stale():
    now = datetime.now(timezone.utc)
    old_cycle_ts = (now - timedelta(hours=60)).isoformat()
    storage = DummyStorage(last_passing={"finished_at": old_cycle_ts})

    res = check_last_successful_cycle_age(storage, now)
    assert res.passed is False
    assert "exceeds 48h limit" in res.message


def test_last_successful_cycle_fail_empty():
    now = datetime.now(timezone.utc)
    storage = DummyStorage(last_passing=None)

    res = check_last_successful_cycle_age(storage, now)
    assert res.passed is False
    assert "no passing cycle recorded" in res.observed


def test_recent_verification_failures_pass():
    logs = [
        {"agent": "verifier", "status": "ok", "notes": "VERDICT: pass"},
        {"agent": "verifier", "status": "ok", "notes": "VERDICT: pass"},
        {"agent": "verifier", "status": "failed", "notes": "VERDICT: fail"},
    ]
    storage = DummyStorage(cycle_logs=logs)
    res = check_recent_verification_failures(storage)
    assert res.passed is True
    assert "1/3 recent verifications failed" in res.observed


def test_recent_verification_failures_fail_all_three():
    logs = [
        {"agent": "verifier", "status": "failed", "notes": "VERDICT: fail"},
        {"agent": "verifier", "status": "failed", "notes": "VERDICT: fail"},
        {"agent": "verifier", "status": "failed", "notes": "VERDICT: fail"},
    ]
    storage = DummyStorage(cycle_logs=logs)
    res = check_recent_verification_failures(storage)
    assert res.passed is False
    assert "3/3 recent verifications failed" in res.observed


def test_evaluate_health_green():
    now = datetime.now(timezone.utc)
    recent_fetch = (now - timedelta(hours=2)).isoformat()
    recent_cycle = (now - timedelta(hours=5)).isoformat()
    logs = [
        {"agent": "verifier", "status": "ok", "notes": "VERDICT: pass"},
    ]
    storage = DummyStorage(
        last_fetch=recent_fetch,
        last_passing={"finished_at": recent_cycle},
        cycle_logs=logs,
    )

    report = evaluate_health(storage, now)
    assert report.is_healthy is True
    assert report.dashboard_status == "green"
    assert "🟢 Live" in report.dashboard_message


def test_evaluate_health_amber_stale():
    now = datetime.now(timezone.utc)
    recent_fetch = (now - timedelta(hours=2)).isoformat()
    stale_cycle = (now - timedelta(hours=30)).isoformat()
    logs = [
        {"agent": "verifier", "status": "ok", "notes": "VERDICT: pass"},
    ]
    storage = DummyStorage(
        last_fetch=recent_fetch,
        last_passing={"finished_at": stale_cycle},
        cycle_logs=logs,
    )

    report = evaluate_health(storage, now)
    # Stale cycle age > 24h triggers amber status on dashboard, and > 48h triggers unhealthy
    assert report.dashboard_status == "amber"
    assert "🟡 Stale" in report.dashboard_message


def test_evaluate_health_red_failures():
    now = datetime.now(timezone.utc)
    recent_fetch = (now - timedelta(hours=2)).isoformat()
    recent_cycle = (now - timedelta(hours=2)).isoformat()
    failed_logs = [
        {"agent": "verifier", "status": "failed", "notes": "VERDICT: fail"},
        {"agent": "verifier", "status": "failed", "notes": "VERDICT: fail"},
        {"agent": "verifier", "status": "failed", "notes": "VERDICT: fail"},
    ]
    storage = DummyStorage(
        last_fetch=recent_fetch,
        last_passing={"finished_at": recent_cycle},
        cycle_logs=failed_logs,
    )

    report = evaluate_health(storage, now)
    assert report.dashboard_status == "red"
    assert "🔴 Critical" in report.dashboard_message


def _raise_system_exit(code: int = 0) -> None:
    raise SystemExit(code)


def test_cli_exit_code_zero():
    now = datetime.now(timezone.utc)
    recent_fetch = (now - timedelta(hours=2)).isoformat()
    recent_cycle = (now - timedelta(hours=5)).isoformat()
    storage = DummyStorage(
        last_fetch=recent_fetch,
        last_passing={"finished_at": recent_cycle},
        cycle_logs=[{"agent": "verifier", "status": "ok", "notes": "VERDICT: pass"}],
    )

    with patch("edgedash.health.load_config"), \
         patch("edgedash.health.get_storage_module", return_value=storage), \
         patch("edgedash.health.init_db"), \
         patch("sys.exit", side_effect=_raise_system_exit):
        with pytest.raises(SystemExit) as exc_info:
            run_cli()
        assert exc_info.value.code == 0


def test_cli_exit_code_one_unhealthy():
    storage = DummyStorage(last_fetch=None)

    with patch("edgedash.health.load_config"), \
         patch("edgedash.health.get_storage_module", return_value=storage), \
         patch("edgedash.health.init_db"), \
         patch("sys.exit", side_effect=_raise_system_exit):
        with pytest.raises(SystemExit) as exc_info:
            run_cli()
        assert exc_info.value.code == 1


