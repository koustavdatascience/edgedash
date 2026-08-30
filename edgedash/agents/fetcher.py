from __future__ import annotations

from datetime import datetime, timezone
from types import ModuleType
from typing import Any

from edgedash.agents.base import Agent, AgentResult
from edgedash.config import Config
import edgedash.sources.arbeitnow  # noqa: F401 — registers sources
from edgedash.sources.base import SOURCES
from edgedash.storage import ListingInput, listing_id


class Fetcher(Agent):
    @property
    def name(self) -> str:
        return "fetcher"

    def run(self, config: Config, storage: ModuleType) -> AgentResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        fail_notes: dict[str, str] = {}
        success_notes: dict[str, str] = {}
        total_new = 0
        any_success = False
        combined: list[ListingInput] = []
        source_batches: dict[str, list[ListingInput]] = {}

        for source_name in config.sources:
            started_at = datetime.now(timezone.utc).isoformat()
            if source_name not in SOURCES:
                message = f"unknown source {source_name!r}"
                print(f"WARNING: fetcher skipping {message}")
                self._log_source(storage, source_name, started_at, 0, "failed", message)
                fail_notes[source_name] = f"{source_name}: FAILED ({message})"
                continue

            try:
                rows = SOURCES[source_name]().fetch(config)
            except Exception as exc:
                message = str(exc)
                print(f"WARNING: fetcher source {source_name} failed: {message}")
                self._log_source(storage, source_name, started_at, 0, "failed", message)
                fail_notes[source_name] = f"{source_name}: FAILED ({message})"
                continue

            listings = _to_listings(rows, fetched_at)
            source_batches[source_name] = listings
            combined.extend(listings)
            any_success = True
            self._log_source(
                storage,
                source_name,
                started_at,
                len(listings),
                "ok",
                f"fetched {len(listings)} row(s)",
            )

        deduped = _dedupe_by_id(combined)
        for source_name, listings in source_batches.items():
            source_rows = [row for row in deduped if row["source"] == source_name]
            new_count = storage.upsert_listings(source_rows)
            total_new += new_count
            success_notes[source_name] = (
                f"{source_name}: {len(source_rows)} rows ({new_count} new)"
            )

        note_parts = [
            fail_notes.get(name) or success_notes[name]
            for name in config.sources
            if name in fail_notes or name in success_notes
        ]

        if not config.sources:
            notes = "no sources configured"
            status = "ok"
        elif any_success:
            notes = " | ".join(note_parts)
            status = "ok"
        else:
            notes = " | ".join(note_parts)
            status = "failed"

        return AgentResult(
            agent=self.name,
            status=status,
            records_touched=total_new,
            notes=notes,
        )

    def _log_source(
        self,
        storage: ModuleType,
        source_name: str,
        started_at: str,
        records_touched: int,
        status: str,
        notes: str,
    ) -> None:
        finished_at = datetime.now(timezone.utc).isoformat()
        storage.log_cycle(
            f"fetcher:{source_name}",
            started_at,
            finished_at,
            records_touched,
            status,
            notes,
        )


def _to_listings(rows: list[dict[str, Any]], fetched_at: str) -> list[ListingInput]:
    listings: list[ListingInput] = []
    for row in rows:
        source = row.get("source")
        url = row.get("url")
        if not source or not url:
            continue
        listings.append(
            ListingInput(
                title=_as_str(row.get("title")),
                company=_as_str(row.get("company")),
                location=_as_str(row.get("location")),
                url=url,
                description=_as_str(row.get("description")),
                source=source,
                posted_at=_as_str(row.get("posted_at")),
                fetched_at=fetched_at,
            )
        )
    return listings


def _dedupe_by_id(rows: list[ListingInput]) -> list[ListingInput]:
    seen: set[str] = set()
    deduped: list[ListingInput] = []
    for row in rows:
        row_id = listing_id(row["source"], row["url"])
        if row_id in seen:
            continue
        seen.add(row_id)
        deduped.append(row)
    return deduped


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)
