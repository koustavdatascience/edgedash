from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from typing import Any

from edgedash.config import Config
from edgedash.sources.base import Source, normalized_job, register
from edgedash.sources.http import get_json

_API_URL = "https://www.arbeitnow.com/api/job-board-api"
_MAX_PAGES = 5
_MIN_RESULTS = 5
_RATE_LIMIT_SECONDS = 1.0
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@register
class ArbeitnowSource(Source):
    name = "arbeitnow"

    def fetch(self, config: Config) -> list[dict[str, Any]]:
        raw_jobs = self._fetch_pages(config.keywords)
        normalized = [_normalize(job) for job in raw_jobs]
        filtered, relaxed = _apply_filters(normalized, config.keywords, config.target_city)
        print(
            f"arbeitnow: {len(raw_jobs)} raw result(s), "
            f"{len(filtered)} after filtering"
            + (" (location filter relaxed)" if relaxed else "")
        )
        return filtered

    def _fetch_pages(self, keywords: list[str]) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for page in range(1, _MAX_PAGES + 1):
            if page > 1:
                time.sleep(_RATE_LIMIT_SECONDS)
            payload = get_json(_API_URL, params={"page": page})
            page_jobs = payload.get("data") or []
            if not page_jobs:
                break
            jobs.extend(page_jobs)
            if not _page_has_keyword_match(page_jobs, keywords):
                break
        return jobs


def _normalize(job: dict[str, Any]) -> dict[str, Any]:
    posted_at = _posted_at_iso(job.get("created_at"))
    description = _strip_html(job.get("description"))
    location = job.get("location")
    if job.get("remote"):
        location = f"{location} (remote)" if location else "remote"
    return normalized_job(
        source=ArbeitnowSource.name,
        external_id=job.get("slug"),
        title=job.get("title"),
        company=job.get("company_name"),
        location=location,
        url=job.get("url"),
        description=description,
        posted_at=posted_at,
        raw=job,
    )


def _apply_filters(
    jobs: list[dict[str, Any]],
    keywords: list[str],
    target_city: str,
) -> tuple[list[dict[str, Any]], bool]:
    keyword_matches = [job for job in jobs if _matches_keywords(job, keywords)]
    strict = [job for job in keyword_matches if _matches_city(job, target_city)]
    if len(strict) >= _MIN_RESULTS or not target_city:
        return strict, False
    print(
        f"arbeitnow: only {len(strict)} result(s) with city filter "
        f"({target_city!r}); relaxing location filter"
    )
    return keyword_matches, True


def _page_has_keyword_match(jobs: list[dict[str, Any]], keywords: list[str]) -> bool:
    if not keywords:
        return bool(jobs)
    return any(_matches_keywords(_normalize(job), keywords) for job in jobs)


def _matches_keywords(job: dict[str, Any], keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = " ".join(
        part for part in (job.get("title"), job.get("description")) if part
    ).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def _matches_city(job: dict[str, Any], target_city: str) -> bool:
    if not target_city:
        return True
    location = (job.get("location") or "").lower()
    for alias in _city_aliases(target_city):
        if alias in location:
            return True
    return False


def _city_aliases(city: str) -> set[str]:
    normalized = city.strip().lower()
    aliases = {normalized}
    if normalized == "bengaluru":
        aliases.add("bangalore")
    elif normalized == "bangalore":
        aliases.add("bengaluru")
    return aliases


def _posted_at_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _strip_html(value: Any) -> str | None:
    if value is None:
        return None
    text = _HTML_TAG_RE.sub(" ", str(value))
    return html.unescape(re.sub(r"\s+", " ", text)).strip() or None
