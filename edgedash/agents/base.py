from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING, Any, Literal

from edgedash.config import Config

if TYPE_CHECKING:
    from edgedash.planning import StopConditions

AgentStatus = Literal["ok", "failed", "suspect", "warning", "degraded"]


@dataclass
class AgentResult:
    agent:           str
    status:          AgentStatus
    records_touched: int
    notes:           str
    # Optional structured payload — verifier uses this to pass the Verdict
    # to the Orchestrator without re-parsing the notes string.
    extra:           dict[str, Any] = field(default_factory=dict)


class Agent(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def run(
        self,
        config:          Config,
        storage:         ModuleType,
        stop_conditions: "StopConditions | None" = None,
    ) -> AgentResult: ...
