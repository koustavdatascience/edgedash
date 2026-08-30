from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import ModuleType

from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.fetcher import Fetcher
from edgedash.agents.gap_analyzer import GapAnalyzer
from edgedash.agents.mock_fetcher import MockFetcher
from edgedash.agents.scorer import Scorer
from edgedash.config import Config
from edgedash.storage_factory import get_storage_module, init_db

try:
    from edgedash.agents.verifier import Verifier
    HAS_VERIFIER = True
except ImportError:
    HAS_VERIFIER = False


# Registry — fetcher resolved per cycle from config (see _resolve_fetcher).
AGENT_REGISTRY: dict[str, Agent] = {
    "scorer": Scorer(),
    "gap_analyzer": GapAnalyzer(),
}

# Add verifier if available
if HAS_VERIFIER:
    AGENT_REGISTRY["verifier"] = Verifier()


def _resolve_fetcher(config: Config) -> Agent:
    if config.use_mock_fetcher:
        return MockFetcher()
    return Fetcher()


@dataclass(frozen=True)
class PlannedStep:
    agent: str
    action: str
    reason: str


def run_cycle(config: Config) -> list[AgentResult]:
    # Use storage factory to get appropriate backend
    storage = get_storage_module(config)
    init_db(config)
    
    last_fetch = storage.last_fetch_time()
    unscored = storage.count_unscored()

    _print_header("EdgeDash Cycle")
    _print_state(config, last_fetch, unscored)

    plan = _build_plan(unscored)
    _print_plan(plan)

    results: list[AgentResult] = []
    newly_scored = 0
    
    for step in plan:
        if step.action == "skip":
            continue
        agent = (
            _resolve_fetcher(config)
            if step.agent == "fetcher"
            else AGENT_REGISTRY[step.agent]
        )
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            # Pass newly_scored count to verifier if applicable
            if step.agent == "verifier" and HAS_VERIFIER:
                result = agent.run(config, storage, newly_scored)
            else:
                result = agent.run(config, storage)
        except Exception as exc:
            finished_at = datetime.now(timezone.utc).isoformat()
            result = AgentResult(
                agent=step.agent,
                status="failed",
                records_touched=0,
                notes=str(exc),
            )
            storage.log_cycle(
                step.agent, started_at, finished_at, 0, "failed", str(exc)
            )
            results.append(result)
            _print_agent_result(result)
            raise
        finished_at = datetime.now(timezone.utc).isoformat()
        storage.log_cycle(
            step.agent,
            started_at,
            finished_at,
            result.records_touched,
            result.status,
            result.notes,
        )
        results.append(result)
        _print_agent_result(result)
        
        # Track newly scored listings for verifier
        if step.agent == "scorer" and result.status == "ok":
            newly_scored = result.records_touched

    _print_summary(results, storage.count_unscored())
    return results


def _build_plan(unscored: int) -> list[PlannedStep]:
    scorer_reason = (
        f"{unscored} unscored listing(s) waiting — score next batch"
        if unscored > 0
        else "no unscored listings — scorer idle"
    )
    scorer_action = "run" if unscored > 0 else "skip"
    
    plan = [
        PlannedStep(
            agent="fetcher",
            action="run",
            reason="scheduled fetch — pull latest listings for configured role and city",
        ),
        PlannedStep(agent="scorer", action=scorer_action, reason=scorer_reason),
        PlannedStep(
            agent="gap_analyzer",
            action="run",
            reason="analyze skill gaps across scored listings",
        ),
    ]
    
    # Add verifier if available - it should run after scoring to validate outputs
    if HAS_VERIFIER:
        verifier_action = "run" if unscored > 0 else "skip"
        verifier_reason = (
            f"verify {unscored} newly scored listing(s)"
            if unscored > 0
            else "no new listings to verify"
        )
        plan.append(
            PlannedStep(
                agent="verifier",
                action=verifier_action,
                reason=verifier_reason,
            )
        )
    
    return plan


def _print_header(title: str) -> None:
    line = "=" * 52
    print(f"\n{line}\n  {title}\n{line}")


def _print_state(config: Config, last_fetch: str | None, unscored: int) -> None:
    fetch_label = last_fetch if last_fetch else "never"
    print("\nSTATE")
    print(f"  role       : {config.target_role}")
    print(f"  city       : {config.target_city}")
    print(f"  last_fetch : {fetch_label}")
    print(f"  unscored   : {unscored}")


def _print_plan(plan: list[PlannedStep]) -> None:
    print("\nPLAN")
    for step in plan:
        tag = "RUN " if step.action == "run" else "SKIP"
        print(f"  [{tag}] {step.agent:<14} — {step.reason}")


def _print_agent_result(result: AgentResult) -> None:
    print(
        f"\n  → {result.agent}: {result.status.upper()} | "
        f"{result.records_touched} record(s) | {result.notes}"
    )


def _print_summary(results: list[AgentResult], unscored: int) -> None:
    print("\nCYCLE SUMMARY")
    headers = ("Agent", "Status", "Records", "Notes")
    rows = [(r.agent, r.status, str(r.records_touched), r.notes) for r in results]
    _print_table(headers, rows)
    print(f"\n  unscored remaining: {unscored}")


def _print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]
    fmt = "  ".join(f"{{:{width}}}" for width in widths)
    print(f"  {fmt.format(*headers)}")
    print(f"  {'  '.join('-' * width for width in widths)}")
    for row in rows:
        print(f"  {fmt.format(*row)}")
