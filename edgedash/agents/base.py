from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import ModuleType
from typing import Literal

from edgedash.config import Config

AgentStatus = Literal["ok", "failed"]


@dataclass(frozen=True)
class AgentResult:
    agent: str
    status: AgentStatus
    records_touched: int
    notes: str


class Agent(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def run(self, config: Config, storage: ModuleType) -> AgentResult:
        ...
