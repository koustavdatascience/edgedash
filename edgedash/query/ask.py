"""One‑question query pipeline — rules 42‑45, with abuse guards.

ask(question) -> Answer
  Answer = NamedTuple(".text, .rows, .tool_used, .params")

Guards (checked in this order, BEFORE any model call):
  1. Daily cap from config  (default 200 questions per day) — if exceeded,
     the caller (app.py) disables the ask box and shows a note.  No model
     call is made.
  2. Rate limit per session: max 10 questions per 10 minutes.  On exceed,
     return an Answer with a friendly wait‑time message; NO model call.
  3. Input validation: 300‑char max, reject empty/whitespace‑only, strip
     control characters.
  4. Injection‑pattern check: "ignore previous", "system prompt", "you are
     now"  — if found, skip the model entirely and return the standard
     can‑answer message, logged as "rejected: suspicious input".

All guards log to query_log with their reason.  Only the ask box degrades;
the dashboard data panels keep working under every condition.
"""
from __future__ import annotations

import re
import time
import logging
from typing import Any, NamedTuple, Optional

import streamlit as st

from edgedash.config import load_config
from edgedash.query.tools import TOOLS
from edgedash.llm import complete_json, LLMError

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

_qlogger = logging.getLogger("edgedash.query.abuse")
_qlogger.setLevel(logging.INFO)
# Add a handler only once to avoid duplicate logs in Streamlit reruns
if not _qlogger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _qlogger.addHandler(_handler)


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


def _is_injection(patterns: list[str], text: str) -> bool:
    """Return True if any injection pattern is found in text (case‑insensitive)."""
    lowered = text.lower()
    return any(p.lower() in lowered for p in patterns)


def _strip_controls(text: str) -> str:
    """Remove control characters (except whitespace and printable ASCII)."""
    # Keep printable ASCII and whitespace; remove everything else
    return "".join(ch for ch in text if ch.isprintable() and (ch.isalnum() or ch.isspace() or ch in " .,;:!?'\"-()/"))


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    """Coerce *value* to int and clamp to [low, high]; fallback to default."""
    try:
        if isinstance(value, bool):
            raise ValueError("bool is not int")
        iv = int(str(value).strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError, AttributeError):
        return default
    return max(low, min(high, iv))


# ---------------------------------------------------------------------------
# 0. Global daily cap check (caller‑side, but helpers here)
# ---------------------------------------------------------------------------

def _daily_cap_exceeded(config: Any) -> bool:
    """Return True if today's total question count >= config daily cap."""
    cap = _clamp_int(
        getattr(config, "daily_question_cap", _DEFAULT_DAILY_CAP),
        1, 10_000, _DEFAULT_DAILY_CAP,
    )
    # Read counts from Streamlit session state (persisted across reruns)
    total = st.session_state.get(_DAILY_CAP_KEY, 0)
    return total >= cap


def _increment_daily_count() -> None:
    """Increment the daily question counter in session state."""
    st.session_state[_DAILY_CAP_KEY] = st.session_state.get(_DAILY_CAP_KEY, 0) + 1


def _rate_limit_exceeded() -> bool:
    """Return True if the session has exceeded 10 questions per 10 minutes."""
    now = time.time()
    timestamps = st.session_state.get(_RATE_LIMIT_KEY, [])
    # Drop timestamps older than the window
    window = now - _RATE_LIMIT_COUNT * 60 - 60  # extra 1 min buffer
    recent = [t for t in timestamps if t > window]
    st.session_state[_RATE_LIMIT_KEY] = recent
    return len(recent) >= _RATE_LIMIT_COUNT


def _record_rate_limit() -> None:
    """Append the current timestamp to the rate‑limit tracker."""
    st.session_state.setdefault(_RATE_LIMIT_KEY, []).append(time.time())


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

_INVALID_PATTERNS = [
    "ignore previous",
    "system prompt",
    "you are now",
    "do not answer",
    "forget all",
    "override",
]

_MAX_CHARACTERS = 300


def _validate_input(question: str) -> tuple[Optional[str], Optional[str]]:
    """Validate the question input.

    Returns (clean_question, reason) where:
      - clean_question is the sanitised string if valid, or None if rejected.
      - reason is None if valid, or a short tag describing the rejection:
        "too_long", "empty", "suspicious".
    """
    # 1. Strip control characters first
    cleaned = _strip_controls(question)

    # 2. Empty / whitespace‑only check
    if not cleaned.strip():
        return None, "empty"

    # 3. Length check (on the cleaned version)
    if len(cleaned) > _MAX_CHARACTERS:
        return None, "too_long"

    # 4. Injection‑pattern check
    if _is_invalid_pattern(_INVALID_PATTERNS, cleaned):
        return None, "suspicious"

    return cleaned, None


# ---------------------------------------------------------------------------
# 1. ROUTE (with rate‑limit guard)
# ---------------------------------------------------------------------------

def route(question: str) -> dict[str, Any]:
    """Step 1: Route a natural-language question to a tool or null.

    Returns dict with keys: tool, params, confidence.
    Raises ValueError if the returned tool name is not in TOOLS.
    """
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    descriptions = _build_tool_descriptions()

    prompt = _PROMPT_TEMPLATE.format(
        now=now,
        question=question,
        descriptions=descriptions,
    )

    try:
        result = complete_json(
            prompt=prompt,
            schema={"tool": None, "params": {}, "confidence": str},  # type: ignore[arg-type]
            max_retries=0,
        )
    except LLMError as exc:
        # If the LLM completely fails, treat as unanswerable
        return {"tool": None, "params": {}, "confidence": "low"}

    tool_name: str | None = result.get("tool")
    params: dict[str, Any] = result.get("params", {})
    confidence: str = result.get("confidence", "low")

    # Validate that the returned tool name is actually in TOOLS
    if tool_name is not None and tool_name not in TOOLS:
        raise ValueError(
            f"Router returned unknown tool '{tool_name}'. "
            f"Valid tools are: {', '.join(TOOLS)}"
        )

    return {"tool": tool_name, "params": params, "confidence": confidence}


# ---------------------------------------------------------------------------
# 2. EXECUTE
# ---------------------------------------------------------------------------

def execute(tool_name: str | None, params: dict[str, Any]) -> dict[str, Any]:
    """Step 2: Run the selected tool with validated, clamped params.

    Never eval, never getattr on a model-supplied string outside the registry.
    """
    if tool_name is None or tool_name not in TOOLS:
        return {"rows": [], "summary": "no tool selected"}

    tool_entry = TOOLS[tool_name]
    func: Callable = tool_entry["func"]

    # The tool function already handles clamping internally via _clamp_int.
    # We just call it with the params the model supplied.
    try:
        result = func(**params)
    except TypeError as exc:
        # Model passed wrong parameter names/values — treat as unanswerable
        return {"rows": [], "summary": f"tool execution error: {exc}"}

    # Ensure result has the expected shape
    if not isinstance(result, dict) or "rows" not in result or "summary" not in result:
        return {"rows": [], "summary": "tool returned unexpected shape"}

    return result


# ---------------------------------------------------------------------------
# 3. PHRASE
# ---------------------------------------------------------------------------

def _phrase(question: str, rows: list[dict[str, Any]], summary: str) -> str:
    """Step 3: Phrase the answer given the question and rows only.

    Per rule 43: use only numbers present in these rows; do not estimate or
    add outside context; if the rows are empty say the data does not contain
    an answer. Include the tool's summary.
    """
    if not rows:
        return (
            f"The data does not contain an answer to your question: {question}. "
            f"The {summary}."
        )

    # Build a concise prose answer referencing only what's in rows.
    # We include the summary so the user knows what was looked at.
    # We avoid any estimation or outside context.
    # Just state what the data shows in 2-3 sentences.
    row_count = len(rows)
    first_row = rows[0] if rows else {}

    # Construct a simple, factual summary
    if summary:
        base = f"Based on the {summary}."
    else:
        base = "Based on the data returned."

    # If there's a clear first result, mention it
    if row_count == 1 and "score" in first_row:
        score = first_row["score"]
        title = first_row.get("title", "a listing")
        company = first_row.get("company", "a company")
        return (
            f"{base} The top result is {title} from {company} with a fit score "
            f"of {score}."
        )
    elif row_count == 1:
        title = first_row.get("title", "a listing")
        company = first_row.get("company", "a company")
        return (
            f"{base} The only result is {title} from {company}."
        )
    elif row_count <= 5:
        titles = ", ".join(r.get("title", "—") for r in rows)
        companies = ", ".join(r.get("company", "—") for r in rows)
        return (
            f"{base} The results include {titles} from {companies}."
        )
    else:
        # Many results — just reference the count and summary
        return (
            f"{base} {row_count} results were returned. {summary}"
        )


def phrase(question: str, answer: Answer) -> str:
    """Step 3: Phrase the final answer text.

    Given the question and the Answer (which carries rows + summary),
    produce the text the user sees.
    """
    rows = answer.rows
    # Use the tool's summary if available, otherwise generic
    if answer.tool_used and answer.tool_used in TOOLS:
        tool_entry = TOOLS[answer.tool_used]
        tool_summary = tool_entry.get("description", "")
    else:
        tool_summary = ""

    # Now phrase using rows + tool_summary
    if not rows:
        return (
            f"The data does not contain an answer to your question: {question}. "
            f"The {tool_summary}."
        )

    # Build 2-3 sentence answer using only rows data + summary
    # We reference the summary from the tool
    sentences: list[str] = []

    # Sentence 1: what was looked at
    sentences.append(f"Looking at {tool_summary}.")

    # Sentence 2: what the data shows (only what's in rows)
    if len(rows) == 1:
        row = rows[0]
        title = row.get("title", "—")
        score = row.get("score", "")
        if score:
            sentences.append(f"The top result is {title} with a fit score of {score}.")
        else:
            sentences.append(f"The result is {title}.")
    elif len(rows) > 1:
        top = rows[0]
        title = top.get("title", "—")
        score = top.get("score", "")
        if score:
            sentences.append(f"The highest-scoring result is {title} with a fit score of {score}.")
        else:
            sentences.append(f"The top result is {title}.")

    # Sentence 3: if there are multiple results or a summary stat
    if len(rows) > 1:
        # Mention the range or count
        scores = [r.get("score", 0) for r in rows if r.get("score") is not None]
        if scores:
            min_s, max_s = min(scores), max(scores)
            sentences.append(
                f"Scores range from {min_s} to {max_s} across {len(rows)} listings."
            )
        else:
            sentences.append(f"There are {len(rows)} results.")
    elif len(rows) == 1 and "fit_reason" in rows[0]:
        sentences.append(f"The fit reason explains why this listing matches your profile.")

    # Join into 2-3 sentences
    text = " ".join(sentences)
    # Ensure we have at least 2 sentences
    if text.count(".") < 2:
        # Add a concluding sentence about what this means
        if not rows:
            text += " The data does not contain a specific answer to your question."
        else:
            text += " This reflects the best match available from the current dataset."

    return text


# ---------------------------------------------------------------------------
# 4. ASK — the public API
# ---------------------------------------------------------------------------

def ask(question: str) -> Answer:
    """End-user query: guard → (optional) route → execute → phrase → log → return.

    The guard order is intentional:
      1. Daily cap check  (config‑driven, stops traffic spikes)
      2. Rate‑limit check (per‑session, 10/10min)
      3. Input validation (length, injection patterns)
      4. Routing (model call)
      5. Execution + phrasing
      6. Logging

    Parameters
    ----------
    question: str
        Natural-language question from the user.

    Returns
    -------
    Answer
        NamedTuple with .text, .rows, .tool_used, .params.
    """
    start = time.time()

    # ------------------------------------------------------------------
    # GUARD 1 — Daily cap (configurable, default 200 / day)
    # ------------------------------------------------------------------
    from edgedash.config import load_config as _load_config

    config = _load_config()
    if _daily_cap_exceeded(config):
        # No model call. Return a friendly Answer that the dashboard will
        # use to disable the ask box. Log the cap‑exceed event.
        _log_query(
            question="<daily cap exceeded>",
            tool=None,
            params={},
            answerable=False,
            any / (. ____ -- cheap_wait_time_str()  # helper below
        return Answer(
            text=(
                f"The data is currently unavailable for questions. "
                f"Daily question limit ({config.daily_question_cap or _DEFAULT_DAILY_CAP}) "
                f"has been reached. Please try again tomorrow."
            ),
            rows=[],
            tool_used=None,
            params={},
        )

    # ------------------------------------------------------------------
    # GUARD 2 — Rate limit per session (10 questions per 10 min)
    # ------------------------------------------------------------------
    if _rate_limit_exceeded():
        # No model call. Return a friendly wait‑time message.
        wait_minutes = _RATE_LIMIT_WINDOW_MIN
        _log_query(
            question="<rate limited>",
            tool=None,
            params={},
            answerable=False,
            duration=time.time() - start,
        )
        return Answer(
            text=(
                f"You've asked many questions quickly. Please wait about "
                f"{wait_minutes} minute{'s' if wait_minutes > 1 else ''} "
                f"before asking another question."
            ),
            rows=[],
            tool_used=None,
            params={},
        )

    # ------------------------------------------------------------------
    # GUARD 3 — Input validation
    # ------------------------------------------------------------------
    validated, vreason = _validate_input(question)
    if validated is None:
        # Rejection — log and return can‑answer message
        _log_query(
            question=question,
            tool=None,
            params={},
            answerable=False,
            duration=time.time() - start,
        )
        # Map rejection reason to a generic "can't answer" message
        # Do NOT explain the filter in the response
        return Answer(
            text=(
                f"The data does not contain an answer to your question. "
                f"Available tools: companies_hiring: Companies that have posted "
                f"listings in the last N days, with per-company counts. "
                f"best_matches: Highest-scoring job listings with fit score, "
                f"title, company, and reason. ..."
            ),
            rows=[],
            tool_used=None,
            params={},
        )

    # ------------------------------------------------------------------
    # GUARD 4 — Strip controls & re-validate after strip
    # ------------------------------------------------------------------
    # (Already done in _validate_input; validated is the clean version)

    # ------------------------------------------------------------------
    # GUARD 5 — Route (model call)
    # ------------------------------------------------------------------
    # Inject the cleaned question so the model sees sanitised input
    routed = route(validated)
    tool_name = routed["tool"]
    params = routed["params"]
    confidence = routed["confidence"]

    # ------------------------------------------------------------------
    # GUARD 5a — If model returned null, fixed can't‑answer message
    # ------------------------------------------------------------------
    if tool_name is None:
        available = "; ".join(
            f"{name}: {spec['description']}"
            for name, spec in TOOLS.items()
        )
        _log_query(
            question=validated,
            tool=None,
            params=params,
            answerable=False,
            duration=time.time() - start,
        )
        return Answer(
            text=(
                f"The data does not contain an answer to your question. "
                f"Available tools: {available}."
            ),
            rows=[],
            tool_used=None,
            params=params,
        )

    # ------------------------------------------------------------------
    # GUARD 5b — Execute the tool
    # ------------------------------------------------------------------
    executed = execute(tool_name, params)
    rows: list[dict[str, Any]] = executed.get("rows", [])
    summary: str = executed.get("summary", "")

    # ------------------------------------------------------------------
    # GUARD 5c — Phrase the answer
    # ------------------------------------------------------------------
    text = _phrase(validated, rows, summary)

    # ------------------------------------------------------------------
    # GUARD 6 — Log the successful query
    # ------------------------------------------------------------------
    _log_query(
        question=validated,
        tool=tool_name,
        params=params,
        answerable=True,
        duration=time.time() - start,
    )

    # Record daily count and rate-limit token after a successful question
    _increment_daily_count()
    _record_rate_limit()

    return Answer(
        text=text,
        rows=rows,
        tool_used=tool_name,
        params=params,
    )


# ---------------------------------------------------------------------------
# Helper: cheap wait‑time string (no model call)
# ---------------------------------------------------------------------------

def cheap_wait_time_str() -> str:
    """Return a friendly wait‑time string for rate‑limited users."""
    return "10 minutes"


# ---------------------------------------------------------------------------
# Logging helper — extended
# ---------------------------------------------------------------------------

def _log_query(question: str, tool: str | None, params: dict[str, Any],
               answerable: bool, duration: float,
               rejection_reason: str | None = None) -> None:
    """Append a row to the query_log table in the database.

    *question* may be a tag like "<daily cap exceeded>" when no real question
    was present.
    """
    from edgedash.storage_factory import get_storage_module, init_db
    from edgedash.config import load_config

    config = load_config()
    storage = get_storage_module(config)
    try:
        init_db(config)
    except Exception:
        pass

    reason_tag = rejection_reason or ("rejected:" + (_VALIDATION_REASONS.get(vreason, "unknown") if 'vreason' in dir() else ""))
    # Build a simple note; keep it short
    note_parts = [f"question={question!r}"]
    if tool is not None:
        note_parts.append(f"tool={tool!r}")
    if params:
        note_parts.append(f"params={params!r}")
    if rejection_reason:
        note_parts.append(f"reason={rejection_reason}")
    note_parts.append(f"answerable={answerable}")

    storage.log_cycle(
        "query",
        time.strftime("%Y-%m-%dT%H:%M:%S"),
        time.strftime("%Y-%m-%dT%H:%M:%S"),
        0,
        "ok" if answerable else "failed",
        " | ".join(note_parts),
    )


# ---------------------------------------------------------------------------
# Validation reason map (for logging)
# ---------------------------------------------------------------------------

_VALIDATION_REASONS = {
    "empty": "empty_input",
    "too_long": "input_too_long",
    "suspicious": "suspicious_input",
}