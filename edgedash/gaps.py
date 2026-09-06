"""Morning gap report — prints the latest snapshot as a readable terminal table.

Usage
-----
python -m edgedash.gaps
"""
from __future__ import annotations

import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _bar(value: float, max_value: float, width: int = 20) -> str:
    """ASCII progress bar scaled to max_value."""
    if max_value <= 0:
        return " " * width
    filled = int(round(value / max_value * width))
    filled = max(0, min(filled, width))
    return "█" * filled + "░" * (width - filled)


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("  (no rows)")
        return

    max_cost = max(r["opportunity_cost"] for r in rows) or 1.0
    max_skill = max(len(r["skill"]) for r in rows)
    skill_w = max(max_skill, 20)

    # Header
    print(
        f"  {'#':>2}  "
        f"{'Skill':<{skill_w}}  "
        f"{'Blocked':>7}  "
        f"{'Cost':>6}  "
        f"{'Mean':>5}  "
        f"{'Top':>4}  "
        f"{'N2H':>3}  "
        f"{'':20}  "
        f"{'Flag'}"
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
        f"{'----'}"
    )

    for i, r in enumerate(rows, start=1):
        bar = _bar(r["opportunity_cost"], max_cost)
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


def main() -> None:
    import edgedash.storage as storage
    from edgedash.config import load_config

    config = load_config()
    storage.init_db(config.db_path)

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

    _print_table(rows)

    print()

    # Sample size note (rule 27)
    low = [r for r in rows if r["low_confidence"]]
    if low:
        print(f"  ⚠  {len(low)} gap(s) marked 'low confidence' — fewer than 3 listings.")
        print("     Treat these as signals, not conclusions.")
        print()

    # Drill-in hint (rule 26)
    print("  Tip: each gap tracks up to 5 example listing IDs.")
    print("       Query gap_snapshots WHERE run_id = '<id>' to drill in.")
    print()


if __name__ == "__main__":
    main()
