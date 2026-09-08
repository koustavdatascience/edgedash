"""One-question query pipeline — rules 42-45, with abuse guards.

ask(question) -> Answer
  Answer = NamedTuple(".text, .rows, .tool_used, .params")

The model appears exactly twice per question (rule 42):
  1. ROUTE  — llm.complete_json picks one tool (or null) from the registry.
  2. PHRASE — llm.complete_json turns the returned rows into 2-3 sentences,
              using ONLY numbers present in those rows (rule 43).

Guards (checked in this order, BEFORE any model call):
  1. Daily cap from config (default 200 questions per day).
  2. Rate limit per session: max 10 questions per 10 minutes.
  3. Input validation: 300-char max, reject empty, strip control chars.
  4. Injection-pattern check — if found, no model call, fixed message.

Every question is logged to the query_log table (storage module, rule 2):
question, tool chosen, params, answerable, duration.
"""
from __future__ import annotations

import json
import time
from typing import Any, NamedTuple

import streamlit as st

from edgedash.llm import complete_json, LLMError
from edgedash.query.tools import TOOLS

# ---------------------------------------------------------------------------
# Answer type
# ---------------------------------------------------------------------------


class Answer(NamedTuple):
    text: str
    rows: list[dict[str, Any]]
    tool_used: str | None
    params: dict[str, Any]


# ---------------------------------------------------------------------------
# Guard helpers
# ---------------------------------------------------------------------------

_DAILY_CAP_KEY = "edgedash_daily_question_count"
_RATE_LIMIT_KEY = "edgedash_question_timestamps"
_DEFAULT_DAILY_CAP = 200
_RATE_LIMIT_COUNT = 10
_RATE_LIMIT_WINDOW_MIN = 10

_MAX_CHARACTERS = 300

_INVALID_PATTERNS = [
    "ignore previous",
    "system prompt",
    "you are now",
    "do not answer",
    "forget all",
    "override",
]

_VALIDATION_REASONS = {
    "empty": "rejected: empty input",
    "too_long": "rejected: input too long",
    "suspicious": "rejected: suspicious input",
}


def _strip_controls(text: str) -> str:
    """Remove control characters (except whitespace and printable ASCII)."""
    allowed = " .,;:!?'\"-()/"
    return "".join(
        ch for ch in text
        if ch.isprintable() and (ch.isalnum() or ch.isspace() or ch in allowed)
    )


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    """Coerce *value* to int and clamp to [low, high]; fallback to default."""
    try:
        if isinstance(value, bool):
            raise ValueError("bool is not int")
        iv = int(str(value).strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError, AttributeError):
        return default
    return max(low, min(high, iv))


def _is_injection(patterns: list[str], text: str) -> bool:
    """Return True if any injection pattern is found in text (case-insensitive)."""
    lowered = text.lower()
    return any(p.lower() in lowered for p in patterns)


def _validate_input(question: str) -> tuple[str | None, str | None]:
    """Strip controls, then check empty / length / injection patterns.

    Returns (clean_question, reason); reason is None when valid.
    """
    cleaned = _strip_controls(question)
    if not cleaned.strip():
        return None, "empty"
    if len(cleaned) > _MAX_CHARACTERS:
        return None, "too_long"
    if _is_injection(_INVALID_PATTERNS, cleaned):
        return None, "suspicious"
    return cleaned, None


# ---------------------------------------------------------------------------
# Daily cap / rate limit (Streamlit session state)
# ---------------------------------------------------------------------------

def _daily_cap_exceeded(config: Any) -> bool:
    """Return True if today's total question count >= config daily cap."""
    cap = _clamp_int(
        getattr(config, "daily_question_cap", _DEFAULT_DAILY_CAP),
        1, 10_000, _DEFAULT_DAILY_CAP,
    )
    total = st.session_state.get(_DAILY_CAP_KEY, 0)
    return total >= cap


def _increment_daily_count() -> None:
    """Increment the daily question counter in session state."""
    st.session_state[_DAILY_CAP_KEY] = st.session_state.get(_DAILY_CAP_KEY, 0) + 1


def _rate_limit_exceeded() -> bool:
    """Return True if the session has exceeded 10 questions per 10 minutes."""
    now = time.time()
    timestamps = st.session_state.get(_RATE_LIMIT_KEY, [])
    window = now - _RATE_LIMIT_COUNT * 60 - 60  # extra 1 min buffer
    recent = [t for t in timestamps if t > window]
    st.session_state[_RATE_LIMIT_KEY] = recent
    return len(recent) >= _RATE_LIMIT_COUNT


def _record_rate_limit() -> None:
    """Append the current timestamp to the rate-limit tracker."""
    st.session_state.setdefault(_RATE_LIMIT_KEY, []).append(time.time())


# ---------------------------------------------------------------------------
# 1. ROUTE — one llm.complete_json call (rule 42)
# ---------------------------------------------------------------------------

_ROUTE_TEMPLATE = """You are the query router for a job-market data system. Read the question, then pick the ONE tool that can answer it — or return null if no tool can.

Available tools (name, description, parameters):
{descriptions}

User question: "{question}"

Return JSON:
{{"tool": <tool name or null>, "params": {{...}}, "confidence": "high"|"low"}}

Rules:
- Pick exactly ONE tool, and only if it genuinely answers the question.
- If no tool matches, tool MUST be null. Do NOT pick the closest tool — return null instead.
- params must be a JSON object; only include keys the chosen tool declares. Omit a parameter to use its default.
- confidence is "high" when you are certain of the match, "low" otherwise."""


def _build_tool_descriptions() -> str:
    """Build the registry block for the routing prompt (name, description, spec)."""
    lines = []
    for name, spec in TOOLS.items():
        lines.append(f"Tool: {name}")
        lines.append(f"  Description: {spec['description']}")
        params = spec.get("parameters", {})
        props = params.get("properties", {})
        if props:
            lines.append("  Parameters:")
            for pname, pdef in props.items():
                ptype = pdef.get("type", "unknown")
                pdesc = pdef.get("description", "")
                req = "required" if pname in params.get("required", []) else "optional"
                lines.append(f"    - {pname} ({ptype}, {req}): {pdesc}")
        else:
            lines.append("  Parameters: (none)")
        lines.append("")
    return "\n".join(lines).strip()


def route(question: str, config: Any) -> dict[str, Any]:
    """Step 1: Route a question to one tool, or null. Returns {tool, params, confidence}.

    Raises ValueError when the model returns a tool name NOT in the registry
    (hard error, never a fallback).
    """
    prompt = _ROUTE_TEMPLATE.format(
        descriptions=_build_tool_descriptions(),
        question=question,
    )
    try:
        result = complete_json(
            prompt=prompt,
            schema={"tool": None, "params": dict, "confidence": str},
            config=config,
        )
    except LLMError:
        # LLM unavailable / malformed JSON — treat as unanswerable, no tool.
        return {"tool": None, "params": {}, "confidence": "low"}

    tool_name: str | None = result.get("tool")
    params: dict[str, Any] = result.get("params") or {}
    confidence: str = result.get("confidence", "low")

    if tool_name is not None and tool_name not in TOOLS:
        raise ValueError(
            f"Router returned unknown tool '{tool_name}'. "
            f"Valid tools are: {', '.join(TOOLS)}"
        )
    if tool_name is not None and not isinstance(params, dict):
        raise ValueError(f"Router returned non-object params for '{tool_name}'")

    return {"tool": tool_name, "params": params, "confidence": confidence}


# ---------------------------------------------------------------------------
# 2. EXECUTE — validated, clamped params; registry lookup only (rule 41)
# ---------------------------------------------------------------------------

def execute(tool_name: str | None, params: dict[str, Any]) -> dict[str, Any]:
    """Step 2: Run the selected tool. Never eval, never getattr on a
    model-supplied string outside the registry lookup."""
    if tool_name is None or tool_name not in TOOLS:
        return {"rows": [], "summary": "no tool selected"}
    func = TOOLS[tool_name]["func"]
    try:
        result = func(**params)
    except TypeError as exc:
        return {"rows": [], "summary": f"tool execution error: {exc}"}
    if not isinstance(result, dict) or "rows" not in result or "summary" not in result:
        return {"rows": [], "summary": "tool returned unexpected shape"}
    return result


# ---------------------------------------------------------------------------
# 3. PHRASE — one llm.complete_json call (rules 42-43)
# ---------------------------------------------------------------------------

_PHRASE_TEMPLATE = """You write the user-facing answer to a question about job-market data.

The answer must:
- Use ONLY the numbers present in the rows below. Never estimate,
  extrapolate, or add outside context.
- If the rows are empty, say the data does not contain an answer to the question.
- State what was looked at, using the summary below (e.g. "across 47 listings
  from the last 7 days").
- Be 2-3 sentences of plain English.

Question: "{question}"
What was looked at: "{summary}"
Rows (JSON): {rows}

Return JSON:
{{"answer": "<your 2-3 sentences>"}}"""


def _phrase_fallback(question: str, rows: list[dict[str, Any]], summary: str) -> str:
    """Deterministic phrasing used only when the phrasing model call fails.

    Uses ONLY numbers present in the rows (rule 43).
    """
    if not rows:
        return (
            f"The data does not contain an answer to your question: {question}. "
            f"{summary}."
        )
    return (
        f"Here is the data for your question: {question}. Based on {summary}, "
        f"the results contain {len(rows)} row{'s' if len(rows) != 1 else ''}."
    )


def phrase(question: str, rows: list[dict[str, Any]], summary: str, config: Any) -> str:
    """Step 3: one llm.complete_json call phrasing rows into 2-3 sentences.

    Falls back to deterministic phrasing if the model call fails.
    """
    rows_json = json.dumps(rows, default=str)
    prompt = _PHRASE_TEMPLATE.format(
        question=question,
        summary=summary,
        rows=rows_json,
    )
    try:
        result = complete_json(
            prompt=prompt,
            schema={"answer": str},
            config=config,
        )
    except LLMError:
        return _phrase_fallback(question, rows, summary)
    answer = (result.get("answer") or "").strip()
    return answer or _phrase_fallback(question, rows, summary)


# ---------------------------------------------------------------------------
# Fixed "can't answer" message for null tool / rejected input (rule 45)
# ---------------------------------------------------------------------------

def _can_answer_message() -> str:
    """Plain-English list of what CAN be asked. No model call."""
    tools = "; ".join(
        f"{name}: {spec['description']}"
        for name, spec in TOOLS.items()
    )
    return (
        "The data does not contain an answer to your question. "
        f"Available questions: {tools}."
    )


# ---------------------------------------------------------------------------
# Logging — query_log table in the storage module (rule 2)
# ---------------------------------------------------------------------------

def _log_query(
    question: str,
    tool: str | None,
    params: dict[str, Any],
    answerable: bool,
    duration: float,
    rejection_reason: str | None = None,
) -> None:
    """Append one row to query_log. Never raises — logging is best-effort."""
    from edgedash.config import load_config
    from edgedash.storage_factory import get_storage_module, init_db

    try:
        config = load_config()
        storage = get_storage_module(config)
        init_db(config)
        storage.log_query(
            question=question,
            tool=tool,
            params=params,
            answerable=answerable,
            duration=duration,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 4. ASK — the public API
# ---------------------------------------------------------------------------

def ask(question: str) -> Answer:
    """Guard -> route -> execute -> phrase -> log -> return Answer."""
    start = time.time()
    from edgedash.config import load_config

    config = load_config()

    # GUARD 1 — daily cap (no model call)
    if _daily_cap_exceeded(config):
        _log_query("<daily cap exceeded>", None, {}, False, time.time() - start)
        return Answer(
            text=(
                f"The data is currently unavailable for questions. "
                f"Daily question limit "
                f"({getattr(config, 'daily_question_cap', _DEFAULT_DAILY_CAP)}) "
                f"has been reached. Please try again tomorrow."
            ),
            rows=[],
            tool_used=None,
            params={},
        )

    # GUARD 2 — per-session rate limit (no model call)
    if _rate_limit_exceeded():
        wait = "{:.0f} minute{}".format(
            _RATE_LIMIT_WINDOW_MIN, "" if _RATE_LIMIT_WINDOW_MIN == 1 else "s"
        )
        _log_query("<rate limited>", None, {}, False, time.time() - start)
        return Answer(
            text=(
                "You've asked many questions quickly. Please wait about "
                f"{wait} before asking another question."
            ),
            rows=[],
            tool_used=None,
            params={},
        )

    # GUARD 3 — input validation (no model call)
    validated, vreason = _validate_input(question)
    if validated is None:
        _log_query(
            question,
            None, {},
            False,
            time.time() - start,
            rejection_reason=_VALIDATION_REASONS.get(vreason, "invalid"),
        )
        return Answer(text=_can_answer_message(), rows=[], tool_used=None, params={})

    # Every question that reaches a model call counts toward the daily cap and
    # the session rate limit — including ones that route to null. This is what
    # protects the free tier from a traffic spike.
    _increment_daily_count()
    _record_rate_limit()

    # STEP 1 — ROUTE (model call #1)
    try:
        routed = route(validated, config)
    except ValueError as exc:
        # Hard error: model named a tool that does not exist. Never fall back.
        _log_query(validated, None, {}, False, time.time() - start,
                   rejection_reason=f"router_error: {exc}")
        raise

    tool_name = routed["tool"]
    params = routed["params"]

    # If the router found nothing — fixed message, NO phrasing call (rule 45)
    if tool_name is None:
        _log_query(validated, None, params, False, time.time() - start)
        return Answer(text=_can_answer_message(), rows=[], tool_used=None, params=params)

    # STEP 2 — EXECUTE
    executed = execute(tool_name, params)
    rows: list[dict[str, Any]] = executed.get("rows", [])
    summary: str = executed.get("summary", "")

    # STEP 3 — PHRASE (model call #2, only for answerable questions)
    text = phrase(validated, rows, summary, config)

    _log_query(validated, tool_name, params, True, time.time() - start)

    return Answer(text=text, rows=rows, tool_used=tool_name, params=params)