"""Single door to any language model — see steering rule 15.

Public API
----------
complete_json(prompt, schema, *, config=None, max_retries=1) -> dict
    Send a prompt, get back a validated JSON dict.

CLI check
---------
python -m edgedash.llm --check
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Any, Callable, Protocol

import requests

# Load .env early so the CLI check works without a wrapper script
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Raised when an LLM call cannot produce a valid result after retries."""


# ---------------------------------------------------------------------------
# Provider protocol — adding a third provider never touches complete_json
# ---------------------------------------------------------------------------

class _Provider(Protocol):
    def call(self, prompt: str, model: str) -> str: ...


class _GeminiProvider:
    """Calls the Gemini REST API directly — no SDK, no hidden interceptors."""

    _URL = (
        "https://generativelanguage.googleapis.com/v1beta"
        "/models/{model}:generateContent?key={api_key}"
    )

    def __init__(self) -> None:
        self._api_key = os.environ.get("GEMINI_API_KEY", "")
        if not self._api_key:
            raise LLMError(
                "Missing GEMINI_API_KEY environment variable. "
                "Set it in your .env file: GEMINI_API_KEY=your_key_here"
            )

    def call(self, prompt: str, model: str) -> str:
        model_id = model.removeprefix("models/")
        url = self._URL.format(model=model_id, api_key=self._api_key)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code == 429:
            raise LLMError("429 rate limit from Gemini")
        if r.status_code == 503:
            raise LLMError("503 Gemini overloaded — will retry")
        if not r.ok:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except Exception:
                msg = r.text
            raise LLMError(f"{r.status_code} {msg}")
        data = r.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected Gemini response shape: {data}") from exc


class _OllamaProvider:
    """Calls a local Ollama instance — no API key required."""

    def __init__(self) -> None:
        self._base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    def call(self, prompt: str, model: str) -> str:
        payload = {"model": model, "prompt": prompt, "format": "json", "stream": False}
        r = requests.post(f"{self._base_url}/api/generate", json=payload, timeout=60)
        if r.status_code == 429:
            raise LLMError("429 from Ollama")
        r.raise_for_status()
        return r.json().get("response", "")


# Registry — adding a new provider = one line here, nothing else changes
_PROVIDERS: dict[str, Callable[[], _Provider]] = {
    "gemini": _GeminiProvider,
    "ollama": _OllamaProvider,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rate_limit(last_calls: list[float]) -> None:
    """Enforce 1 req/s and at most 15 req/min. Sleeps; never errors."""
    now = time.time()
    recent = [t for t in last_calls if now - t < 60]
    if len(recent) >= 15:
        sleep_for = 60 - (now - recent[0]) + 0.05
        if sleep_for > 0:
            time.sleep(sleep_for)
        now = time.time()
        recent = [t for t in last_calls if now - t < 60]
    if recent and (now - recent[-1]) < 1.0:
        time.sleep(1.0 - (now - recent[-1]))
    last_calls.append(time.time())


def _strip_markdown(text: str) -> str:
    """Remove code fences and surrounding prose before JSON parsing."""
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    cleaned: list[str] = []
    in_fence = False
    for line in lines:
        if re.match(r"^```", line):
            in_fence = not in_fence
            continue
        if not in_fence:
            cleaned.append(line)
    result = "\n".join(cleaned).strip()
    result = re.sub(r"^```[\w]*\n?", "", result)
    result = re.sub(r"\n?```$", "", result)
    return result.strip()


def _validate(data: Any, schema: dict) -> dict:
    """Validate parsed JSON against schema = {field: type | None}.

    Raises ValueError with a clear message on the first violation.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object, got {type(data).__name__}")
    for field, expected_type in schema.items():
        if field not in data:
            raise ValueError(f"Missing required field: '{field}'")
        if expected_type is not None and not isinstance(data[field], expected_type):
            raise ValueError(
                f"Field '{field}': expected {expected_type.__name__}, "
                f"got {type(data[field]).__name__}"
            )
    return data  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def complete_json(
    prompt: str,
    schema: dict,
    *,
    config: Any = None,
    max_retries: int = 1,
) -> dict:
    """Send *prompt* to the configured LLM; return a validated JSON dict.

    Parameters
    ----------
    prompt:      The full prompt text.
    schema:      ``{field_name: expected_type | None}`` — every field must
                 be present; type ``None`` skips the type check.
    config:      Optional ``Config`` object.  When supplied, ``llm_provider``
                 and ``llm_model`` are read from it; otherwise the
                 environment variables ``LLM_PROVIDER`` / ``LLM_MODEL`` are
                 used as fallback.
    max_retries: Number of extra attempts after the first failure (default 1).

    Raises
    ------
    LLMError if all attempts are exhausted or a non-retryable error occurs.
    """
    provider_name: str
    model: str

    if config is not None:
        provider_name = config.llm_provider
        model = config.llm_model
    else:
        provider_name = os.environ.get("LLM_PROVIDER", "gemini")
        model = os.environ.get("LLM_MODEL", "gemini-3.5-flash")

    factory = _PROVIDERS.get(provider_name)
    if factory is None:
        raise LLMError(
            f"Unknown LLM provider '{provider_name}'. "
            f"Supported: {', '.join(_PROVIDERS)}"
        )

    provider = factory()
    last_calls: list[float] = []
    last_error: Exception | None = None
    current_prompt = prompt

    for attempt in range(max_retries + 1):
        try:
            _rate_limit(last_calls)
            raw = provider.call(current_prompt, model)
            clean = _strip_markdown(raw)

            try:
                parsed = json.loads(clean)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON parse error: {exc}") from exc

            return _validate(parsed, schema)

        except LLMError as exc:
            last_error = exc
            exc_str = str(exc)
            if ("429" in exc_str or "503" in exc_str) and attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            break

        except ValueError as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            # Retry with explicit repair instruction
            current_prompt = (
                prompt
                + "\n\nPREVIOUS RESPONSE FAILED VALIDATION. "
                "Reply with JSON only — no prose, no markdown fence. "
                f"Exact error: {exc}"
            )
            continue

        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                current_prompt = prompt + "\n\nPREVIOUS ATTEMPT FAILED. Retrying."
                continue
            break

    raise LLMError(
        f"LLM call failed after {max_retries + 1} attempt(s). "
        f"Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# CLI check
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EdgeDash LLM health check")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Send one trivial prompt and print provider, model, and result",
    )
    args = parser.parse_args()

    if args.check:
        _provider = os.environ.get("LLM_PROVIDER", "gemini")
        _model = os.environ.get("LLM_MODEL", "gemini-3.5-flash")
        print(f"Provider : {_provider}")
        print(f"Model    : {_model}")
        try:
            _result = complete_json(
                prompt='Reply with only valid JSON and nothing else: {"status": "ok"}',
                schema={"status": str},
                max_retries=0,
            )
            print(f"Status   : OK")
            print(f"Response : {_result}")
        except LLMError as _exc:
            print(f"Status   : FAILED — {_exc}")
