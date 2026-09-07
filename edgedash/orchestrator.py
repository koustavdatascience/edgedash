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
from edgedash.planning import Plan, Task, build_plan
from edgedash.state import SystemState, read_state
from edgedash.storage_factory import get_storage_module, init_db
from edgedash.verification import Verdict

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

def run_cycle(
    config:         Config,
    plan:           Plan | None = None,
    override_notes: str         = "",
) -> CycleOutcome:
    storage = get_storage_module(config)
    init_db(config)

    now = datetime.now(timezone.utc)
    started_at = now.isoformat()

    # 1. Read state and build plan — unless caller already did (e.g. --force)
    state: SystemState = read_state(config, storage, now)
    if plan is None:
        plan = build_plan(state, config)

        # 3. Print plan before execution (rule 31) — only when orchestrator owns it
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
        _write_summary_row(storage, outcome, plan, started_at, finished_at,
                           override_notes=override_notes)
        # Rule 28: nothing_to_do is a successful outcome — exit 0, no output.
        # Printing here trains you to ignore your own logs.
        return outcome

    # 5. Execute — per rule 32: one failure does not stop the cycle
    results: list[AgentResult]   = []
    durations: dict[str, float]  = {}
    any_failed                   = False

    for task in plan.tasks:
        if not task.run:
            continue

        agent = _resolve_agent(task.agent_name, config)

        t0 = time.monotonic()
        agent_started = datetime.now(timezone.utc).isoformat()
        try:
            result = agent.run(config, storage, task.stop_conditions)
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

    finished_at = datetime.now(timezone.utc).isoformat()
    outcome_str = "partial" if any_failed else "complete"

    # ------------------------------------------------------------------
    # Rule 36: run verifier; on fail retry the offending agent ONCE only
    # ------------------------------------------------------------------
    verifier_result, retry_count, degraded = _run_verification(
        config, storage, plan, results, durations
    )
    if verifier_result is not None:
        results.append(verifier_result)
    if degraded:
        outcome_str = "degraded"
    elif any_failed:
        outcome_str = "partial"

    finished_at = datetime.now(timezone.utc).isoformat()
    outcome = CycleOutcome(
        outcome    = outcome_str,
        plan       = plan,
        results    = results,
        durations_s= durations,
        started_at = started_at,
        finished_at= finished_at,
    )

    # Rule 33: exactly one summary row
    extra_notes = ""
    if retry_count:
        extra_notes = f"verification_retries={retry_count}"
    _write_summary_row(storage, outcome, plan, started_at, finished_at,
                       override_notes=" | ".join(filter(None, [override_notes, extra_notes])))
    _print_summary(outcome, storage.count_unscored())
    return outcome


# ---------------------------------------------------------------------------
# Rule 36 — verifier + at-most-one retry
# ---------------------------------------------------------------------------

# Maps a failing check name to the agent that produced the data it checks.
_CHECK_TO_AGENT: dict[str, str] = {
    "score_spread":       "scorer",
    "extraction_sanity":  "scorer",   # extractor runs inside scorer
    "gap_sample_size":    "gap_analyzer",
    "freshness":          "fetch",
}


def _run_verification(
    config:   Config,
    storage:  ModuleType,
    plan:     "Plan",
    results:  list[AgentResult],
    durations: dict[str, float],
) -> tuple[AgentResult | None, int, bool]:
    """Run verifier once; on failure retry the offending agent + re-verify.

    Returns (verifier_result, retry_count, degraded).
    - verifier_result: the FINAL verifier AgentResult (None if no scoring ran)
    - retry_count: 0 or 1
    - degraded: True if second verification also failed
    """
    # Only verify if scorer actually ran this cycle
    verifier_task = next(
        (t for t in plan.tasks if t.agent_name == "verifier" and t.run), None
    )
    if verifier_task is None:
        return None, 0, False

    verifier = _resolve_agent("verifier", config)

    # --- first verification pass ---
    v_result = _run_one_agent(verifier, "verifier", verifier_task.stop_conditions,
                               config, storage, results, durations)

    verdict = v_result.extra.get("verdict")
    if verdict is None or verdict.passed:
        return v_result, 0, False

    # --- verification failed — attempt ONE retry of the offending agent ---
    print(f"\n  [VERIFY FAIL] {v_result.notes}")
    failed_check_names = [r.name for r in verdict.failed_checks]
    print(f"  [RETRY] failing checks: {', '.join(failed_check_names)}")

    # Determine which agent to retry (first failing check wins)
    retry_agent_name = _CHECK_TO_AGENT.get(verdict.failed_checks[0].name)
    retry_task = next(
        (t for t in plan.tasks if t.agent_name == retry_agent_name and t.run), None
    )

    if retry_task is None:
        # Failing check maps to an agent that didn't run this cycle — can't retry
        print(f"  [RETRY] agent '{retry_agent_name}' did not run this cycle — marking degraded")
        _log_degraded(storage, v_result.notes)
        return v_result, 0, True

    # Build adjusted stop_conditions for the retry
    retry_sc = _adjusted_stop_conditions(retry_task, verdict)
    print(f"  [RETRY] re-running {retry_agent_name} with {retry_sc.render()}")

    retry_agent = _resolve_agent(retry_agent_name, config)
    _run_one_agent(retry_agent, retry_agent_name, retry_sc,
                   config, storage, results, durations)

    # --- second verification pass (final — rule 36: at most ONE retry) ---
    v_result2 = _run_one_agent(verifier, "verifier", verifier_task.stop_conditions,
                                config, storage, results, durations)

    verdict2 = v_result2.extra.get("verdict")
    if verdict2 is None or verdict2.passed:
        return v_result2, 1, False

    # Still failing after retry — mark degraded, stop
    print(f"\n  [DEGRADED] verification still failing after retry: {v_result2.notes}")
    _log_degraded(storage, v_result2.notes)
    return v_result2, 1, True


def _adjusted_stop_conditions(task: "Task", verdict: "Verdict") -> "StopConditions":
    """Return stop_conditions adjusted for the specific failure mode."""
    from edgedash.planning import StopConditions as _SC
    from edgedash.verification import Verdict as _Verdict

    failing_names = {r.name for r in verdict.failed_checks}

    if "score_spread" in failing_names:
        # Widen spread: scorer will amplify skill_match weight so high-fit
        # listings score distinctly higher than low-fit ones.
        return _SC(
            max_items    = task.stop_conditions.max_items,
            max_seconds  = task.stop_conditions.max_seconds,
            widen_spread = True,
        )

    # Default: re-run with same conditions
    return task.stop_conditions


def _run_one_agent(
    agent:           "Agent",
    name:            str,
    stop_conditions: "StopConditions",
    config:          Config,
    storage:         ModuleType,
    results:         list[AgentResult],
    durations:       dict[str, float],
) -> AgentResult:
    """Run a single agent, log result, append to results/durations."""
    t0 = time.monotonic()
    agent_started = datetime.now(timezone.utc).isoformat()
    try:
        result = agent.run(config, storage, stop_conditions)
    except Exception as exc:
        agent_finished = datetime.now(timezone.utc).isoformat()
        elapsed = time.monotonic() - t0
        result = AgentResult(agent=name, status="failed",
                             records_touched=0, notes=str(exc))
        storage.log_cycle(name, agent_started, agent_finished, 0, "failed", str(exc))
        results.append(result)
        durations[name] = round(elapsed, 2)
        _print_agent_result(result, elapsed)
        return result

    agent_finished = datetime.now(timezone.utc).isoformat()
    elapsed = time.monotonic() - t0
    durations[name] = round(elapsed, 2)
    storage.log_cycle(name, agent_started, agent_finished,
                      result.records_touched, result.status, result.notes)
    results.append(result)
    _print_agent_result(result, elapsed)
    return result


def _log_degraded(storage: ModuleType, reason: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    storage.log_cycle("orchestrator", now, now, 0, "degraded",
                      f"cycle degraded after verification retry: {reason}")


# ---------------------------------------------------------------------------
# Rule 33 — single cycle summary row
# ---------------------------------------------------------------------------

def _write_summary_row(
    storage:        ModuleType,
    outcome:        CycleOutcome,
    plan:           Plan,
    started_at:     str,
    finished_at:    str,
    override_notes: str = "",
) -> None:
    ran     = [t.agent_name for t in (plan.tasks if plan else []) if t.run]
    skipped = [(t.agent_name, t.reason) for t in (plan.tasks if plan else []) if not t.run]

    dur_parts  = [f"{a}={d:.1f}s" for a, d in outcome.durations_s.items()]
    skip_parts = [f"{a}({r})" for a, r in skipped]

    notes_parts: list[str] = [f"outcome={outcome.outcome}"]
    if override_notes:
        notes_parts.append(override_notes)   # visible in the log
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
