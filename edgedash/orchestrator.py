from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import ModuleType

from edgedash import storage
from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.mock_fetcher import MockFetcher
from edgedash.config import Config


class _ScorerPlaceholder(Agent):
    @property
    def name(self) -> str:
        return "scorer"

    def run(self, config: Config, storage: ModuleType) -> AgentResult:
        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=0,
            notes="not implemented yet",
        )


class _GapAnalyzerPlaceholder(Agent):
    @property
    def name(self) -> str:
        return "gap_analyzer"

    def run(self, config: Config, storage: ModuleType) -> AgentResult:
        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=0,
            notes="not implemented yet",
        )


# Registry — swap MockFetcher() for a real Fetcher on Thursday.
AGENT_REGISTRY: dict[str, Agent] = {
    "fetcher": MockFetcher(),
    # PLACEHOLDER — not implemented yet
    "scorer": _ScorerPlaceholder(),
    # PLACEHOLDER — not implemented yet
    "gap_analyzer": _GapAnalyzerPlaceholder(),
}


@dataclass(frozen=True)
class PlannedStep:
    agent: str
    action: str
    reason: str


def run_cycle(config: Config) -> list[AgentResult]:
    storage.init_db(config.db_path)
    last_fetch = storage.last_fetch_time()
    unscored = storage.count_unscored()

    _print_header("EdgeDash Cycle")
    _print_state(config, last_fetch, unscored)

    plan = _build_plan(unscored)
    _print_plan(plan)

    results: list[AgentResult] = []
    for step in plan:
        if step.action == "skip":
            continue
        agent = AGENT_REGISTRY[step.agent]
        started_at = datetime.now(timezone.utc).isoformat()
        try:
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

    _print_summary(results, storage.count_unscored())
    return results


def _build_plan(unscored: int) -> list[PlannedStep]:
    scorer_reason = (
        f"{unscored} unscored listing(s) waiting — PLACEHOLDER, not implemented yet"
        if unscored > 0
        else "no unscored listings yet — PLACEHOLDER, not implemented yet"
    )
    return [
        PlannedStep(
            agent="fetcher",
            action="run",
            reason="scheduled fetch — pull latest listings for configured role and city",
        ),
        PlannedStep(agent="scorer", action="skip", reason=scorer_reason),
        PlannedStep(
            agent="gap_analyzer",
            action="skip",
            reason="skill-gap analysis — PLACEHOLDER, not implemented yet",
        ),
    ]


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
