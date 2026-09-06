"""Entry point for a manual EdgeDash cycle.

Usage
-----
python run_cycle.py                         # normal run
python run_cycle.py --dry-run               # print plan, exit without executing
python run_cycle.py --force scorer          # force scorer even if state says skip
python run_cycle.py --force fetch --force scorer  # multiple forces
python run_cycle.py --explain               # print full state + decision trace
python run_cycle.py --dry-run --explain     # combine freely
"""
from __future__ import annotations

import argparse
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _explain(state, plan) -> None:
    """Print full SystemState alongside the decision each value drove."""
    from edgedash.planning import Task

    print("\nEXPLAIN — state values and the decisions they drove")
    print("=" * 62)

    def _row(label: str, value: object, decision: str) -> None:
        print(f"  {label:<26}  {str(value):<28}  → {decision}")

    # fetch decision
    fetch_task: Task = next(t for t in plan.tasks if t.agent_name == "fetch")
    _row(
        "last_fetch_at",
        state.last_fetch_at or "never",
        fetch_task.reason,
    )
    _row(
        "hours_since_fetch",
        f"{state.hours_since_fetch:.2f}h" if state.hours_since_fetch is not None else "never",
        fetch_task.reason,
    )

    # scorer decision
    scorer_task: Task = next(t for t in plan.tasks if t.agent_name == "scorer")
    _row(
        "unscored_count",
        state.unscored_count,
        scorer_task.reason,
    )
    _row(
        "last_scored_at",
        state.last_scored_at or "never",
        scorer_task.reason,
    )

    # gap_analyzer decision
    gap_task: Task = next(t for t in plan.tasks if t.agent_name == "gap_analyzer")
    _row(
        "gaps_computed_at",
        state.gaps_computed_at or "never",
        gap_task.reason,
    )
    _row(
        "gaps_stale",
        state.gaps_stale,
        gap_task.reason,
    )

    # last cycle — informational only
    _row(
        "last_cycle_verdict",
        state.last_cycle_verdict or "n/a",
        "informational",
    )
    _row(
        "last_cycle_at",
        state.last_cycle_at or "n/a",
        "informational",
    )
    print()


def _apply_forces(plan, forced_names: list[str]) -> tuple:
    """Return (new_plan, override_notes) with forced agents set to run=True."""
    from edgedash.planning import Plan, Task, StopConditions

    if not forced_names:
        return plan, ""

    known = {t.agent_name for t in plan.tasks}
    unknown = [n for n in forced_names if n not in known]
    if unknown:
        print(f"\n  ERROR: unknown agent(s) to force: {unknown}")
        print(f"  Known agents in plan: {sorted(known)}")
        sys.exit(1)

    new_tasks = []
    overridden = []
    for task in plan.tasks:
        if task.agent_name in forced_names and not task.run:
            new_tasks.append(Task(
                agent_name      = task.agent_name,
                goal            = task.goal,
                stop_conditions = task.stop_conditions,
                run             = True,
                reason          = f"forced by operator (was: {task.reason})",
            ))
            overridden.append(task.agent_name)
        else:
            new_tasks.append(task)

    override_notes = f"OPERATOR OVERRIDE: forced=[{', '.join(overridden)}]" if overridden else ""
    return Plan(tasks=new_tasks), override_notes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an EdgeDash cycle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read state, print plan, exit without executing anything",
    )
    parser.add_argument(
        "--force",
        metavar="AGENT",
        action="append",
        default=[],
        help="Force an agent to run even if state says skip (repeatable)",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Print full SystemState and the decision each value drove",
    )
    args = parser.parse_args()

    from edgedash.config import load_config
    from edgedash.orchestrator import (
        AGENT_REGISTRY, _print_header, _print_state,
        run_cycle,
    )
    from edgedash.planning import build_plan
    from edgedash.state import read_state
    from edgedash.storage_factory import get_storage_module, init_db
    from datetime import datetime, timezone

    config  = load_config()
    storage = get_storage_module(config)
    init_db(config)

    now       = datetime.now(timezone.utc)
    state     = read_state(config, storage, now)
    plan      = build_plan(state, config)

    # Apply --force overrides BEFORE printing so the plan shows the final intent
    plan, override_notes = _apply_forces(plan, args.force)

    _print_header("EdgeDash Cycle")
    _print_state(state)

    if override_notes:
        print(f"\n  ⚠  {override_notes}")

    print("\nPLAN")
    print(plan.render())
    print()

    if args.explain:
        _explain(state, plan)

    # --dry-run: stop here, no writes, no API calls
    if args.dry_run:
        print("  [DRY RUN] — plan printed above. Nothing was executed.")
        print()
        sys.exit(0)

    # Normal / forced execution — hand off to orchestrator
    # Pass override_notes so it appears in the cycle summary row
    run_cycle(config, plan=plan, override_notes=override_notes)


if __name__ == "__main__":
    main()
