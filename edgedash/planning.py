"""Orchestration planning — pure function, no I/O, no LLM, deterministic.

Public API
----------
build_plan(state: SystemState, config: Config) -> Plan
    Returns an ordered list of Tasks — one per agent, run OR skipped,
    each with an explicit goal, stop conditions, and the state value
    that caused the decision (rule 31).

Plan.render() -> str
    Compact, human-readable plan string, one line per agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edgedash.state import SystemState


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StopConditions:
    max_items:   int | None = None   # max listings / gaps to process
    max_pages:   int | None = None   # fetcher pages
    max_seconds: int | None = None   # wall-clock budget

    def render(self) -> str:
        parts = []
        if self.max_items   is not None: parts.append(f"max_items={self.max_items}")
        if self.max_pages   is not None: parts.append(f"max_pages={self.max_pages}")
        if self.max_seconds is not None: parts.append(f"max_seconds={self.max_seconds}")
        return ", ".join(parts) if parts else "n/a"


@dataclass(frozen=True)
class Task:
    agent_name:      str
    goal:            str
    stop_conditions: StopConditions
    run:             bool    # False = skipped
    reason:          str     # the state value or "skipped: <reason>"


@dataclass
class Plan:
    tasks: list[Task] = field(default_factory=list)

    def render(self) -> str:
        """One line per agent: RUN/SKIP, agent name, goal, stop conditions, reason."""
        lines = []
        for task in self.tasks:
            tag  = "RUN " if task.run else "SKIP"
            stop = task.stop_conditions.render()
            lines.append(
                f"  [{tag}] {task.agent_name:<10}  {task.goal:<40}  "
                f"stop: {stop:<30}  {task.reason}"
            )
        return "\n".join(lines)

    def agents_to_run(self) -> list[Task]:
        return [t for t in self.tasks if t.run]

    def agents_skipped(self) -> list[Task]:
        return [t for t in self.tasks if not t.run]


# ---------------------------------------------------------------------------
# Pure planning function
# ---------------------------------------------------------------------------

def build_plan(state: SystemState, config: Any) -> Plan:
    """Decide which agents to run based on system state and config thresholds.

    Pure function — no I/O, no side effects, no randomness.
    Same (state, config) always produces the same Plan.

    Decision rules
    --------------
    fetch   : run if hours_since_fetch is None (never fetched)
                  OR hours_since_fetch >= config.fetch_interval_hours
    score   : run if unscored_count > 0
    analyse : run if gaps_computed_at is None (never run)
                  OR gaps_stale is True (a score is newer than last snapshot)

    Every agent appears in the plan — skipped agents carry their reason
    (rule 31: the plan is logged before execution, silently absent is wrong).
    """
    tasks: list[Task] = []

    # ------------------------------------------------------------------ fetch
    never_fetched = state.hours_since_fetch is None
    stale_fetch   = (not never_fetched) and (
        state.hours_since_fetch >= config.fetch_interval_hours  # type: ignore[operator]
    )

    if never_fetched or stale_fetch:
        reason = (
            "hours_since_fetch=never"
            if never_fetched
            else f"hours_since_fetch={state.hours_since_fetch:.1f} >= {config.fetch_interval_hours}"
        )
        tasks.append(Task(
            agent_name      = "fetch",
            goal            = "pull latest listings for configured role and city",
            stop_conditions = StopConditions(
                max_pages   = config.fetch_max_pages,
                max_items   = config.fetch_max_listings,
            ),
            run    = True,
            reason = reason,
        ))
    else:
        tasks.append(Task(
            agent_name      = "fetch",
            goal            = "pull latest listings for configured role and city",
            stop_conditions = StopConditions(),
            run    = False,
            reason = (
                f"skipped: hours_since_fetch={state.hours_since_fetch:.1f} "
                f"< {config.fetch_interval_hours}"
            ),
        ))

    # ------------------------------------------------------------------ score
    if state.unscored_count > 0:
        tasks.append(Task(
            agent_name      = "score",
            goal            = f"score up to {config.score_batch_size} unscored listings",
            stop_conditions = StopConditions(
                max_items   = config.score_batch_size,
                max_seconds = config.score_max_seconds,
            ),
            run    = True,
            reason = f"unscored_count={state.unscored_count}",
        ))
    else:
        tasks.append(Task(
            agent_name      = "score",
            goal            = "score unscored listings",
            stop_conditions = StopConditions(),
            run    = False,
            reason = "skipped: unscored_count=0",
        ))

    # ---------------------------------------------------------------- analyse
    never_analysed = state.gaps_computed_at is None
    need_analyse   = never_analysed or state.gaps_stale

    if need_analyse:
        reason = (
            "gaps_computed_at=null"
            if never_analysed
            else "gaps_stale=true (score newer than last snapshot)"
        )
        tasks.append(Task(
            agent_name      = "analyse",
            goal            = "compute skill gaps across scored listings",
            stop_conditions = StopConditions(max_seconds=config.analyse_max_seconds),
            run    = True,
            reason = reason,
        ))
    else:
        tasks.append(Task(
            agent_name      = "analyse",
            goal            = "compute skill gaps across scored listings",
            stop_conditions = StopConditions(),
            run    = False,
            reason = "skipped: gaps up to date",
        ))

    return Plan(tasks=tasks)
