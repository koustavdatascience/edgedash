from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypedDict

from edgedash.config import Config

SOURCES: dict[str, type[Source]] = {}


class NormalizedJob(TypedDict):
    source: str
    external_id: str | None
    title: str | None
    company: str | None
    location: str | None
    url: str | None
    description: str | None
    posted_at: str | None
    raw: dict[str, Any] | None


class Source(ABC):
    name: str

    @abstractmethod
    def fetch(self, config: Config) -> list[dict[str, Any]]:
        ...


def register(cls: type[Source]) -> type[Source]:
    if not cls.name:
        raise ValueError(f"Source {cls.__name__} must define a non-empty name")
    SOURCES[cls.name] = cls
    return cls


def normalized_job(
    *,
    source: str,
    external_id: str | None = None,
    title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    url: str | None = None,
    description: str | None = None,
    posted_at: str | None = None,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "external_id": external_id,
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "description": description,
        "posted_at": posted_at,
        "raw": raw,
    }
