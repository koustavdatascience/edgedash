"""Morning gap report — prints the latest snapshot or trend comparison.

Usage
-----
python -m edgedash.gaps           # latest snapshot table
python -m edgedash.gaps --trend   # earliest vs latest snapshot, change over time
"""
from __future__ import annotations

import argparse
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _bar(value: float, max_value: float, width: int = 20) -> str:
    """ASCII progress bar scaled to max_value."""
    if max_value <= 0:
        return " " * width
    filled = int(round(value / max_value * width))
    filled = max(0, min(filled, width))
    return "█" * filled + "░" * (width - filled)


def _short_date(iso: str) -> str:
    """Return just the date portion of an ISO timestamp."""
    return iso[:10] if iso else "?"


# ---------------------------------------------------------------------------
# Latest snapshot table
# ---------------------------------------------------------------------------

def _print_latest(rows: list[dict]) -> None:
    if not rows:
        print("  (no rows)")
        return

    max_cost = max(r["opportunity_cost"] for r in rows) or 1.0
    skill_w  = max(max(len(r["skill"]) for r in rows), 20)

    print(
        f"  {'#':>2}  "
        f"{'Skill':<{skill_w}}  "
        f"{'Blocked':>7}  "
        f"{'Cost':>6}  "
        f"{'Mean':>5}  "
        f"{'Top':>4}  "
        f"{'N2H':>3}  "
        f"{'':20}  "
        f"Flag"
    )
    print(
        f"  {'--':>2}  "
        f"{'-'*skill_w}  "
        f"{'-------':>7}  "
        f"{'------':>6}  "
        f"{'-----':>5}  "
        f"{'----':>4}  "
        f"{'---':>3}  "
        f"{'--------------------'}  "
        f"----"
    )
    for i, r in enumerate(rows, start=1):
        bar  = _bar(r["opportunity_cost"], max_cost)
        flag = "⚠ low confidence" if r["low_confidence"] else ""
        n2h  = str(r["also_nice_to_have"]) if r["also_nice_to_have"] else "-"
        print(
            f"  {i:>2}  "
            f"{r['skill']:<{skill_w}}  "
            f"{r['listings_blocked']:>7}  "
            f"{r['opportunity_cost']:>6.1f}  "
            f"{r['mean_score']:>5.1f}  "
            f"{r['top_score']:>4}  "
            f"{n2h:>3}  "
            f"{bar}  "
            f"{flag}"
        )


def cmd_latest(storage) -> None:
    rows = storage.get_latest_snapshot(limit=10)

    if not rows:
        print("\n  No gap snapshot found. Run a full cycle first:\n")
        print("    python run_cycle.py\n")
        sys.exit(0)

    computed_at = rows[0]["computed_at"]
    run_id      = rows[0]["run_id"]
    n           = len(rows)

    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║  SKILL GAP REPORT                                        ║")
    print("  ╚══════════════════════════════════════════════════════════╝")
    print(f"  Snapshot : {computed_at}")
    print(f"  Run ID   : {run_id}")
    print(f"  Gaps     : {n} (top {n} by opportunity cost)")
    print()
    print("  Columns: Blocked = listings requiring this skill I don't have")
    print("           Cost    = Σ(fit_score/100) — weighted by listing quality")
    print("           Mean    = mean fit score of blocked listings")
    print("           Top     = highest fit score among blocked listings")
    print("           N2H     = also appears as nice-to-have in N listings")
    print()
    _print_latest(rows)
    print()

    low = [r for r in rows if r["low_confidence"]]
    if low:
        print(f"  ⚠  {len(low)} gap(s) marked 'low confidence' — fewer than 3 listings.")
        print("     Treat these as signals, not conclusions.")
        print()

    print("  Tip: each gap tracks up to 5 example listing IDs.")
    print("       Query gap_snapshots WHERE run_id = '<id>' to drill in.")
    print()


# ---------------------------------------------------------------------------
# Trend comparison
# ---------------------------------------------------------------------------

def _trend_arrow(delta: float) -> str:
    if delta > 0.05:
        return "▲"
    if delta < -0.05:
        return "▼"
    return "─"


def cmd_trend(storage) -> None:
    runs = storage.get_distinct_snapshot_runs()

    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║  SKILL GAP TREND                                         ║")
    print("  ╚══════════════════════════════════════════════════════════╝")

    if not runs:
        print()
        print("  No snapshots found. Run a full cycle first:")
        print("    python run_cycle.py")
        print()
        sys.exit(0)

    if len(runs) == 1:
        d = _short_date(runs[0]["computed_at"])
        print()
        print(f"  Only one snapshot exists (taken {d}).")
        print()
        print("  A trend needs at least 2 snapshots taken on different days.")
        print("  Run the cycle again tomorrow — or whenever you want the next data point.")
        print()
        print("  Nothing is fabricated, interpolated, or extrapolated from a single point.")
        print()
        sys.exit(0)

    earliest_run = runs[0]
    latest_run   = runs[-1]
    n_snapshots  = len(runs)

    earliest_date = _short_date(earliest_run["computed_at"])
    latest_date   = _short_date(latest_run["computed_at"])

    # Load both snapshots as {skill: opportunity_cost} maps
    earliest_rows = storage.get_snapshot_by_run_id(earliest_run["run_id"])
    latest_rows   = storage.get_snapshot_by_run_id(latest_run["run_id"])

    earliest_map: dict[str, float] = {r["skill"]: r["opportunity_cost"] for r in earliest_rows}
    latest_map:   dict[str, float] = {r["skill"]: r["opportunity_cost"] for r in latest_rows}

    # Skills in each snapshot's top 10
    earliest_top10: set[str] = {r["skill"] for r in earliest_rows[:10]}
    latest_top10:   set[str] = {r["skill"] for r in latest_rows[:10]}

    new_skills     = latest_top10 - earliest_top10   # appeared since earliest
    dropped_skills = earliest_top10 - latest_top10   # fell out of top 10

    print()
    print(f"  Comparing {n_snapshots} snapshots")
    print(f"  Earliest : {earliest_run['computed_at']}  (run {earliest_run['run_id'][:8]}…)")
    print(f"  Latest   : {latest_run['computed_at']}  (run {latest_run['run_id'][:8]}…)")
    print(f"  Window   : {earliest_date}  →  {latest_date}")
    print()

    # --- Current top-10 trend table ---
    skill_w = max(max((len(r["skill"]) for r in latest_rows[:10]), default=10), 20)

    print(
        f"  {'#':>2}  "
        f"{'Skill':<{skill_w}}  "
        f"{'Earliest':>8}  "
        f"{'Latest':>6}  "
        f"{'Δ abs':>6}  "
        f"{'Δ %':>6}  "
        f"Dir   Note"
    )
    print(
        f"  {'--':>2}  "
        f"{'-'*skill_w}  "
        f"{'--------':>8}  "
        f"{'------':>6}  "
        f"{'------':>6}  "
        f"{'------':>6}  "
        f"---   ----"
    )

    for i, r in enumerate(latest_rows[:10], start=1):
        skill   = r["skill"]
        latest_cost   = r["opportunity_cost"]
        earliest_cost = earliest_map.get(skill)

        if earliest_cost is None:
            # Not in earliest snapshot at all
            earliest_str = "   n/a"
            delta_abs    = "   n/a"
            delta_pct    = "   n/a"
            arrow        = "▲"
            note         = "NEW"
        else:
            delta = latest_cost - earliest_cost
            pct   = (delta / earliest_cost * 100) if earliest_cost > 0 else 0.0
            earliest_str = f"{earliest_cost:>8.1f}"
            delta_abs    = f"{delta:>+6.1f}"
            delta_pct    = f"{pct:>+5.0f}%"
            arrow        = _trend_arrow(delta)
            note         = ""

        print(
            f"  {i:>2}  "
            f"{skill:<{skill_w}}  "
            f"{earliest_str}  "
            f"{latest_cost:>6.1f}  "
            f"{delta_abs}  "
            f"{delta_pct}  "
            f"  {arrow}     {note}"
        )

    # --- Dropped out of top 10 ---
    if dropped_skills:
        print()
        print(f"  Skills that DROPPED OUT of the top 10 since {earliest_date}:")
        for skill in sorted(dropped_skills):
            old_cost = earliest_map.get(skill, 0)
            new_cost = latest_map.get(skill, 0)
            if new_cost > 0:
                print(f"    {skill:<{skill_w}}  was {old_cost:.1f} → now {new_cost:.1f} (still present, ranked lower)")
            else:
                print(f"    {skill:<{skill_w}}  was {old_cost:.1f} → no longer appears in latest snapshot")

    print()
    print(f"  Based on {n_snapshots} snapshot(s).  Run more cycles to strengthen the signal.")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="EdgeDash gap report")
    parser.add_argument(
        "--trend",
        action="store_true",
        help="Compare earliest vs latest snapshot and show change over time",
    )
    args = parser.parse_args()

    import edgedash.storage as storage
    from edgedash.config import load_config

    config = load_config()
    storage.init_db(config.db_path)

    if args.trend:
        cmd_trend(storage)
    else:
        cmd_latest(storage)


if __name__ == "__main__":
    main()
