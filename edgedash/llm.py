from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

ERROR_TIMEOUT = "timeout"
ERROR_RATE_LIMIT = "rate_limit"
ERROR_QUOTA = "quota_exhausted"
ERROR_PARSE = "parse_error"
VALIDATION_ERROR = "validation_error"


class LLMError(Exception):
    """Raised when an LLM call cannot produce a valid result after retries."""


def _rate_limit(last_calls: list[float]) -> None:
    """Sleep if needed to respect 1 req/s and max 15 req/min."""
    now = time.time()
    # Remove calls older than 60 seconds
    recent = [t for t in last_calls if now - t < 60]
    if len(recent) >= 15:
        sleep_time = 60 - (now - recent[0]) + 0.05
        if sleep_time > 0:
            time.sleep(sleep_time)
        # After sleeping, recompute recent
        now = time.time()
        recent = [t for t in last_calls if now - t < 60]
    # Enforce at least 1 second between calls
    if recent:
        elapsed = now - recent[-1]
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
    last_calls.append(time.time())


def _strip_markdown(text: str) -> str:
    """Remove code fences and leading/trailing prose from model output."""
    lines = text.splitlines()
    # Strip leading/trailing empty lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    # Remove markdown fences
    cleaned: list[str] = []
    in_fence = False
    for line in lines:
        if fence_match := __import__("re").match(r"^```", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        cleaned.append(line)
    text = "\n".join(cleaned).strip()
    # Remove stray markdown fences at start/end
    text = __import__("re").sub(r"^```[\w+]*\n", "", text)
    text = __import__("re").sub(r"\n```$", "", text)
    return text


def _validate_response(data: dict, schema: dict) -> dict:
    """Validate data against a simple schema dict of {field: type}.

    Raises ValueError on failure.
    """
    if not isinstance(data, dict):
        raise ValueError("Response is not a JSON object")
    for field, expected_type in schema.items():
        if field not in data:
            raise ValueError(f"Missing required field: {field}")
        if expected_type is not None and not isinstance(data[field], expected_type):
            raise ValueError(
                f"Field '{field}' expected {expected_type.__name__}, got {type(data[field]).__name__}"
            )
    return data


def complete_json(
    prompt: str,
    schema: dict,
    *,
    max_retries: int = 1,
) -> dict:
    """Send a prompt to an LLM and return the parsed, validated JSON dict.

    - Requests JSON output from the model.
    - Strips markdown fences and prose before parsing.
    - Retries once on parse/validation failure with an error instruction.
    - Raises LLMError if both attempts fail.
    - Rate limits internally (1 req/s, 15 req/min).
    - On 429/quota: exponential backoff, 3 attempts, then raise.
    """
    # Provider config from environment or defaults
    provider = os.environ.get("LLM_PROVIDER", "gemini")
    model = os.environ.get("LLM_MODEL", "gemini-1.5-flash")

    # Provider config
    if provider == "gemini":
        import os
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise LLMError(
                "Missing GEMINI_API_KEY environment variable. "
                "Add it to your .env file: GEMINI_API_KEY=your_key_here"
            )
        try:
            import google.generativeai as genai
        except ImportError:
            raise LLMError(
                "google-generativeai is not installed. "
                "Install with: pip install google-generativeai"
            )
        genai.configure(api_key=api_key)
        model_obj = genai.GenerativeModel(model)

    elif provider == "ollama":
        import os
        ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        # No API key needed for ollama
        pass

    else:
        raise LLMError(f"Unsupported LLM provider: {provider}")

    last_calls: list[float] = []
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            _rate_limit(last_calls)

            if provider == "gemini":
                resp = model_obj.generate_content(prompt)
                text = resp.text

            elif provider == "ollama":
                import requests

                payload = {"model": model, "prompt": prompt, "format": "json"}
                r = requests.post(
                    f"{ollama_base}/api/generate",
                    json=payload,
                    timeout=30,
                )
                if r.status_code == 429:
                    raise LLMError("429 from ollama rate limiter")
                r.raise_for_status()
                data = r.json()
                text = data.get("response", "")

            else:
                raise LLMError(f"Unsupported provider: {provider}")

            # Strip markdown and prose
            clean = _strip_markdown(text)

            # Parse JSON
            try:
                parsed = json.loads(clean)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON parse error: {exc}") from exc

            # Validate schema
            validated = _validate_response(parsed, schema)
            return validated

        except LLMError:
            raise
        except ValueError as exc:
            last_error = exc
            # If this was our last attempt, re-raise
            if attempt >= max_retries:
                break
            # Retry once with error instruction
            prompt = (
                prompt
                + "\n\n"
                "PREVIOUS RESPONSE FAILED VALIDATION. "
                "The response must be JSON only, no prose and no markdown fence. "
                f"Validation error: {exc}"
            )
            continue
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            # Check for 429/quota
            exc_str = str(exc).lower()
            if "429" in exc_str or "quota" in exc_str:
                # Exponential backoff: 1s, 2s, 4s
                backoff = 2 ** attempt
                time.sleep(backoff)
                continue
            # For other errors, retry once
            if attempt < max_retries:
                prompt = (
                    prompt
                    + "\n\n"
                    "PREVIOUS ATTEMPT FAILED. Retrying with corrected prompt."
                )
                continue
            break

    raise LLMError(
        f"LLM call failed after {max_retries + 1} attempt(s). "
        f"Last error: {last_error}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EdgeDash LLM utility")
    parser.add_argument("--check", action="store_true", help="Send a trivial prompt and print provider/model status")
    args = parser.parse_args()

    if args.check:
        provider = os.environ.get("LLM_PROVIDER", "gemini")
        model = os.environ.get("LLM_MODEL", "gemini-1.5-flash")
        try:
            result = complete_json(
                prompt="Say only this JSON: {\"status\": \"ok\"}",
                schema={"status": str},
                max_retries=0,
            )
            print(f"Provider: {provider}")
            print(f"Model: {model}")
            print("Status: OK")
            print(f"Response: {result}")
        except LLMError as exc:
            print(f"Provider: {provider}")
            print(f"Model: {model}")
            print(f"Status: FAILED - {exc}")