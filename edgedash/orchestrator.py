"""State-driven Orchestrator — rules 28-33.

Reads state, builds a plan, prints it, executes it.  Knows nothing about
what agents do internally.  Resolves agents by name from the registry.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import ModuleType
from typing import Any

from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.fetcher import Fetcher
from edgedash.agents.gap_analyzer import GapAnalyzer
from edgedash.agents.mock_fetcher import MockFetcher
from edgedash.agents.scorer import Scorer
from edgedash.config import Config
from edgedash.planning import Plan, StopConditions, Task, build_plan
from edgedash.state import SystemState, read_state
from edgedash.storage_factory import get_storage_module, init_db

try:
    from edgedash.agents.verifier import Verifier
    _HAS_VERIFIER = True
except ImportError:
    _HAS_VERIFIER = False


# ---------------------------------------------------------------------------
# Agent registry — adding a new agent is ONE entry here (rule 30, rule 28)
# ---------------------------------------------------------------------------

AGENT_REGISTRY: dict[str, Agent] = {
    "scorer":       Scorer(),
    "gap_analyzer": GapAnalyzer(),
}
if _HAS_VERIFIER:
    AGENT_REGISTRY["verifier"] = Verifier()


def _resolve_agent(name: str, config: Config) -> Agent:
    """Return the agent for `name`, handling the fetcher mock switch."""
    if name == "fetch":
        return MockFetcher() if config.use_mock_fetcher else Fetcher()
    agent = AGENT_REGISTRY.get(name)
    if agent is None:
        raise KeyError(f"No agent registered for '{name}'")
    return agent


# ---------------------------------------------------------------------------
# Cycle execution result
# ---------------------------------------------------------------------------

@dataclass
class CycleOutcome:
    outcome:      str                   # "complete" | "partial" | "nothing_to_do"
    plan:         Plan | None
    results:      list[AgentResult]     = field(default_factory=list)
    durations_s:  dict[str, float]      = field(default_factory=dict)
    started_at:   str                   = ""
    finished_at:  str                   = ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_cycle(config: Config) -> CycleOutcome:
    storage = get_storage_module(config)
    init_db(config)

    now = datetime.now(timezone.utc)
    started_at = now.isoformat()

    # 1. Read state — cheap queries only
    state: SystemState = read_state(config, storage, now)

    # 2. Build plan — pure function, no I/O
    plan: Plan = build_plan(state, config)

    # 3. Print plan before execution (rule 31)
    _print_header("EdgeDash Cycle")
    _print_state(state)
    print("\nPLAN")
    print(plan.render())
    print()

    # 4. Determine if there is any work at all
    runnable = plan.agents_to_run()
    if not runnable:
        finished_at = datetime.now(timezone.utc).isoformat()
        outcome = CycleOutcome(
            outcome="nothing_to_do",
            plan=plan,
            started_at=started_at,
            finished_at=finished_at,
        )
        _write_summary_row(storage, outcome, plan, started_at, finished_at)
        print("  Nothing to do — all agents skipped.\n")
        return outcome

    # 5. Execute — per rule 32: one failure does not stop the cycle
    results: list[AgentResult]   = []
    durations: dict[str, float]  = {}
    any_failed                   = False
    newly_scored                 = 0   # passed forward to verifier via stop_conditions

    for task in plan.tasks:
        if not task.run:
            continue

        agent = _resolve_agent(task.agent_name, config)

        # Thread verifier's "how many were just scored" through stop_conditions
        sc = task.stop_conditions
        if task.agent_name == "verifier" and newly_scored > 0:
            from edgedash.planning import StopConditions as _SC
            sc = _SC(
                max_items   = newly_scored,
                max_seconds = task.stop_conditions.max_seconds,
            )

        t0 = time.monotonic()
        agent_started = datetime.now(timezone.utc).isoformat()
        try:
            result = agent.run(config, storage, sc)
        except Exception as exc:
            # Rule 32: log, continue, mark partial
            agent_finished = datetime.now(timezone.utc).isoformat()
            elapsed = time.monotonic() - t0
            result = AgentResult(
                agent=task.agent_name,
                status="failed",
                records_touched=0,
                notes=str(exc),
            )
            any_failed = True
            storage.log_cycle(
                task.agent_name, agent_started, agent_finished,
                0, "failed", str(exc),
            )
            results.append(result)
            durations[task.agent_name] = round(elapsed, 2)
            _print_agent_result(result, elapsed)
            continue

        agent_finished = datetime.now(timezone.utc).isoformat()
        elapsed = time.monotonic() - t0
        durations[task.agent_name] = round(elapsed, 2)

        storage.log_cycle(
            task.agent_name, agent_started, agent_finished,
            result.records_touched, result.status, result.notes,
        )
        results.append(result)
        _print_agent_result(result, elapsed)

        # Track scored count for verifier stop_conditions
        if task.agent_name == "scorer":
            newly_scored = result.records_touched

    finished_at = datetime.now(timezone.utc).isoformat()
    outcome_str = "partial" if any_failed else "complete"

    outcome = CycleOutcome(
        outcome    = outcome_str,
        plan       = plan,
        results    = results,
        durations_s= durations,
        started_at = started_at,
        finished_at= finished_at,
    )

    # Rule 33: exactly one summary row
    _write_summary_row(storage, outcome, plan, started_at, finished_at)
    _print_summary(outcome, storage.count_unscored())
    return outcome


# ---------------------------------------------------------------------------
# Rule 33 — single cycle summary row
# ---------------------------------------------------------------------------

def _write_summary_row(
    storage:    ModuleType,
    outcome:    CycleOutcome,
    plan:       Plan,
    started_at: str,
    finished_at: str,
) -> None:
    ran     = [t.agent_name for t in (plan.tasks if plan else []) if t.run]
    skipped = [(t.agent_name, t.reason) for t in (plan.tasks if plan else []) if not t.run]

    dur_parts = [f"{a}={d:.1f}s" for a, d in outcome.durations_s.items()]
    skip_parts = [f"{a}({r})" for a, r in skipped]

    notes_parts: list[str] = [f"outcome={outcome.outcome}"]
    if ran:
        notes_parts.append(f"ran=[{', '.join(ran)}]")
    if skipped:
        notes_parts.append(f"skipped=[{'; '.join(skip_parts)}]")
    if dur_parts:
        notes_parts.append(f"duration=[{', '.join(dur_parts)}]")

    storage.log_cycle(
        "orchestrator",
        started_at,
        finished_at,
        sum(r.records_touched for r in outcome.results),
        outcome.outcome,
        " | ".join(notes_parts),
    )


# ---------------------------------------------------------------------------
# Terminal output helpers
# ---------------------------------------------------------------------------

def _print_header(title: str) -> None:
    line = "=" * 52
    print(f"\n{line}\n  {title}\n{line}")


def _print_state(state: SystemState) -> None:
    hours = (
        f"{state.hours_since_fetch:.1f}h ago"
        if state.hours_since_fetch is not None
        else "never"
    )
    print("\nSTATE")
    print(f"  last_fetch       : {state.last_fetch_at or 'never'} ({hours})")
    print(f"  unscored_count   : {state.unscored_count}")
    print(f"  gaps_computed_at : {state.gaps_computed_at or 'never'}")
    print(f"  gaps_stale       : {state.gaps_stale}")
    print(f"  last_cycle       : {state.last_cycle_verdict or 'n/a'} at {state.last_cycle_at or 'n/a'}")


def _print_agent_result(result: AgentResult, elapsed: float) -> None:
    print(
        f"  → {result.agent}: {result.status.upper()} | "
        f"{result.records_touched} record(s) | {elapsed:.1f}s | {result.notes}"
    )


def _print_summary(outcome: CycleOutcome, unscored: int) -> None:
    print("\nCYCLE SUMMARY")
    headers = ("Agent", "Status", "Records", "Duration", "Notes")
    rows = [
        (r.agent, r.status, str(r.records_touched),
         f"{outcome.durations_s.get(r.agent, 0):.1f}s", r.notes)
        for r in outcome.results
    ]
    _print_table(headers, rows)
    print(f"\n  outcome          : {outcome.outcome}")
    print(f"  unscored remaining: {unscored}")


def _print_table(headers: tuple, rows: list[tuple]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(c)) for w, c in zip(widths, row)]
    fmt = "  ".join(f"{{:{w}}}" for w in widths)
    print(f"  {fmt.format(*headers)}")
    print(f"  {'  '.join('-' * w for w in widths)}")
    for row in rows:
        print(f"  {fmt.format(*row)}")
