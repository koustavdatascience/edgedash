from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import ModuleType
from typing import TYPE_CHECKING, Literal

from edgedash.config import Config

if TYPE_CHECKING:
    from edgedash.planning import StopConditions

AgentStatus = Literal["ok", "failed", "suspect", "warning"]


@dataclass(frozen=True)
class AgentResult:
    agent:           str
    status:          AgentStatus
    records_touched: int
    notes:           str


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
