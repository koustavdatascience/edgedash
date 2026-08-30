from __future__ import annotations

import time
from typing import Any

import requests

DEFAULT_TIMEOUT_SECONDS = 10
MAX_RETRIES = 2
USER_AGENT = "EdgeDash/0.1 (personal career intelligence; +https://github.com/edgedash)"


class SourceError(Exception):
    """Raised when an HTTP request to a job source fails after retries."""


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=request_headers,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(2**attempt)

    raise SourceError(
        f"GET {url} failed after {MAX_RETRIES + 1} attempt(s): {last_error}"
    ) from last_error
