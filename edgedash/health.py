"""Lightweight System Health Check — Rule 50 Compliant.

CLI Usage:
    python -m edgedash.health

Performs 4 read-only checks:
  1. Database reachability
  2. Newest listing age (fails if > 3 days)
  3. Last successful cycle age (fails if > 48 hours)
  4. Recent cycle verification trend (fails if last 3 cycles all failed)

Exits 0 if healthy, non-zero (1) if unhealthy.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from edgedash.config import Config, load_config
from edgedash.storage_factory import get_storage_module, init_db


@dataclass(frozen=True)
class HealthCheckResult:
    name: str
    passed: bool
    observed: str
    message: str


@dataclass(frozen=True)
class SystemHealthReport:
    is_healthy: bool
    checks: list[HealthCheckResult]
    dashboard_status: str  # "green" | "amber" | "red"
    dashboard_message: str


def _parse_iso_utc(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def check_db_reachability(storage: Any) -> HealthCheckResult:
    """Check 1: Database reachable."""
    try:
        _ = storage.last_cycle_summary()
        backend = getattr(storage, "_backend", "db")
        return HealthCheckResult(
            name="Database reachability",
            passed=True,
            observed=f"connected ({backend})",
            message="Database is reachable",
        )
    except Exception as exc:
        return HealthCheckResult(
            name="Database reachability",
            passed=False,
            observed=f"unreachable: {exc}",
            message="Database connection or query failed",
        )


def check_newest_listing_age(storage: Any, now: datetime) -> HealthCheckResult:
    """Check 2: Newest listing older than 3 days (72h)."""
    try:
        last_fetch = storage.last_fetch_time()
        if not last_fetch:
            return HealthCheckResult(
                name="Newest listing age",
                passed=False,
                observed="no listings in database",
                message="No listings found in database",
            )

        dt = _parse_iso_utc(last_fetch)
        if dt is None:
            return HealthCheckResult(
                name="Newest listing age",
                passed=False,
                observed=f"unparseable timestamp: {last_fetch}",
                message="Invalid listing timestamp format",
            )

        age_seconds = (now - dt).total_seconds()
        age_days = age_seconds / 86400.0

        if age_days <= 3.0:
            return HealthCheckResult(
                name="Newest listing age",
                passed=True,
                observed=f"{last_fetch} ({age_days:.1f}d ago)",
                message="Newest listing is within 3 days limit",
            )
        else:
            return HealthCheckResult(
                name="Newest listing age",
                passed=False,
                observed=f"{last_fetch} ({age_days:.1f}d ago)",
                message=f"Newest listing is {age_days:.1f} days old (exceeds 3-day limit)",
            )
    except Exception as exc:
        return HealthCheckResult(
            name="Newest listing age",
            passed=False,
            observed=f"query error: {exc}",
            message="Failed to query listing timestamp",
        )


def check_last_successful_cycle_age(storage: Any, now: datetime) -> HealthCheckResult:
    """Check 3: No successful cycle in 48 hours."""
    try:
        passing = storage.get_last_passing_cycle()
        if not passing or not passing.get("finished_at"):
            return HealthCheckResult(
                name="Last successful cycle",
                passed=False,
                observed="no passing cycle recorded",
                message="No successful cycle found in database",
            )

        ts_str = passing["finished_at"]
        dt = _parse_iso_utc(ts_str)
        if dt is None:
            return HealthCheckResult(
                name="Last successful cycle",
                passed=False,
                observed=f"unparseable timestamp: {ts_str}",
                message="Invalid cycle timestamp format",
            )

        age_seconds = (now - dt).total_seconds()
        age_hours = age_seconds / 3600.0

        if age_hours <= 48.0:
            return HealthCheckResult(
                name="Last successful cycle",
                passed=True,
                observed=f"{ts_str} ({age_hours:.1f}h ago)",
                message="Successful cycle within 48 hours limit",
            )
        else:
            return HealthCheckResult(
                name="Last successful cycle",
                passed=False,
                observed=f"{ts_str} ({age_hours:.1f}h ago)",
                message=f"Last successful cycle was {age_hours:.1f}h ago (exceeds 48h limit)",
            )
    except Exception as exc:
        return HealthCheckResult(
            name="Last successful cycle",
            passed=False,
            observed=f"query error: {exc}",
            message="Failed to query cycle logs",
        )


def check_recent_verification_failures(storage: Any) -> HealthCheckResult:
    """Check 4: Last 3 cycles all failed verification."""
    try:
        cycle_log = storage.get_cycle_log(limit=50)
        verifier_rows = [r for r in cycle_log if r.get("agent") == "verifier"]

        if verifier_rows:
            recent = verifier_rows[:3]
            failed_count = sum(
                1 for r in recent
                if r.get("status") not in ("ok", "pass") or "VERDICT: fail" in r.get("notes", "")
            )
            all_failed = (len(recent) >= 3 and failed_count == 3)
            obs = f"{failed_count}/{len(recent)} recent verifications failed"
        else:
            orch_rows = [r for r in cycle_log if r.get("agent") == "orchestrator"]
            recent = orch_rows[:3]
            failed_count = sum(
                1 for r in recent
                if r.get("status") in ("degraded", "failed", "fail") or "VERDICT: fail" in r.get("notes", "")
            )
            all_failed = (len(recent) >= 3 and failed_count == 3)
            obs = f"{failed_count}/{len(recent)} recent cycles failed"

        if all_failed:
            return HealthCheckResult(
                name="Last 3 cycles verification",
                passed=False,
                observed=obs,
                message="Last 3 consecutive cycles failed verification",
            )
        else:
            return HealthCheckResult(
                name="Last 3 cycles verification",
                passed=True,
                observed=obs,
                message="Verification trend healthy",
            )
    except Exception as exc:
        return HealthCheckResult(
            name="Last 3 cycles verification",
            passed=False,
            observed=f"query error: {exc}",
            message="Failed to evaluate verification history",
        )


def evaluate_health(storage: Any, now: datetime | None = None) -> SystemHealthReport:
    """Evaluate system health and return a SystemHealthReport."""
    if now is None:
        now = datetime.now(timezone.utc)

    db_res = check_db_reachability(storage)

    if not db_res.passed:
        # DB unreachable: subsequent queries cannot execute
        checks = [
            db_res,
            HealthCheckResult("Newest listing age", False, "skipped (DB unreachable)", "DB unreachable"),
            HealthCheckResult("Last successful cycle", False, "skipped (DB unreachable)", "DB unreachable"),
            HealthCheckResult("Last 3 cycles verification", False, "skipped (DB unreachable)", "DB unreachable"),
        ]
        return SystemHealthReport(
            is_healthy=False,
            checks=checks,
            dashboard_status="red",
            dashboard_message="🔴 Critical: Database unreachable",
        )

    listing_res = check_newest_listing_age(storage, now)
    cycle_res = check_last_successful_cycle_age(storage, now)
    verif_res = check_recent_verification_failures(storage)

    checks = [db_res, listing_res, cycle_res, verif_res]
    is_healthy = all(c.passed for c in checks)

    # Dashboard status indicator calculation:
    # - RED: if last 3 cycles failed verification
    # - GREEN: if last cycle passed within 24h (and not red)
    # - AMBER: if last cycle passed > 24h ago (or no passing cycle in 24h)
    if not verif_res.passed:
        dash_status = "red"
        dash_msg = "🔴 Critical: Last 3 cycle runs failed verification"
    else:
        passing = storage.get_last_passing_cycle()
        data_ts = passing.get("finished_at") if passing else None
        dt_pass = _parse_iso_utc(data_ts)

        if dt_pass is not None:
            age_hours = (now - dt_pass).total_seconds() / 3600.0
            if age_hours <= 24.0:
                dash_status = "green"
                dash_msg = f"🟢 Live: Last cycle passed {age_hours:.1f}h ago ({data_ts})"
            else:
                dash_status = "amber"
                dash_msg = f"🟡 Stale: Last passing cycle was {age_hours:.1f}h ago ({data_ts})"
        else:
            dash_status = "amber"
            dash_msg = "🟡 Stale: No passing cycle recorded yet"

    return SystemHealthReport(
        is_healthy=is_healthy,
        checks=checks,
        dashboard_status=dash_status,
        dashboard_message=dash_msg,
    )


def run_cli() -> None:
    """CLI handler for `python -m edgedash.health`."""
    parser = argparse.ArgumentParser(description="EdgeDash System Health Check")
    _ = parser.parse_args([])

    try:
        config = load_config()
        storage = get_storage_module(config)
        init_db(config)
        now = datetime.now(timezone.utc)
        report = evaluate_health(storage, now)
    except Exception as exc:
        print("\n==================================================")
        print("  EdgeDash System Health Report")
        print("==================================================")
        print(f"  [FAIL] Database / Initialization : {exc}")
        print("\nStatus: UNHEALTHY")
        sys.exit(1)

    print("\n==================================================")
    print("  EdgeDash System Health Report")
    print("==================================================")
    for c in report.checks:
        tag = "[PASS]" if c.passed else "[FAIL]"
        print(f"  {tag} {c.name:<28} : {c.observed}")

    status_str = "HEALTHY" if report.is_healthy else "UNHEALTHY"
    print(f"\nStatus: {status_str}")

    if not report.is_healthy:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    run_cli()
