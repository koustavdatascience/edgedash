"""Extraction step — the only part of the Scorer that calls a model.

Public API
----------
extract(listing: dict, config: Config, storage) -> dict
    Returns the extraction result for a listing's job description.
    Hits the cache first; calls the model only on a miss.

The model is asked to parse a document only — it never sees a candidate
profile, scoring weights, or any evaluation task (rule 16).
"""
from __future__ import annotations

import hashlib
from typing import Any

from edgedash.config import Config
from edgedash.llm import LLMError, complete_json

# ---------------------------------------------------------------------------
# Extraction schema
# ---------------------------------------------------------------------------
# Rule 16: no score field here, ever.
# `None` as the type value means "present but skip type check" — used for
# fields that can be int | None or bool | None, validated manually below.

EXTRACTION_SCHEMA: dict[str, type | None] = {
    "required_skills": list,
    "nice_to_have":    list,
    "seniority":       str,
    "years_required":  None,   # int | null
    "remote_ok":       None,   # bool | null
}

_VALID_SENIORITY = {"junior", "mid", "senior", "lead", "unknown"}

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a document parser. Extract structured information from the job description below.

Rules:
- Report only what the job description explicitly states.
- Do not infer, guess, or assume anything not written.
- Do not evaluate any candidate. There is no candidate. You are reading a document.
- If the listing does not state something, use null or an empty list.
- Skill names must be lowercase (e.g. "python", "sql", "power bi").

Return a single JSON object with exactly these fields:

{{
  "required_skills": [ list of skills the role explicitly requires ],
  "nice_to_have":    [ list of skills stated as preferred or nice-to-have, not required ],
  "seniority":       one of: "junior", "mid", "senior", "lead", "unknown",
  "years_required":  integer years of experience if explicitly stated, otherwise null,
  "remote_ok":       true if remote is explicitly offered, false if explicitly excluded, null if not stated
}}

No other fields. No explanation. No markdown. JSON only.

JOB DESCRIPTION:
{description}"""


def _build_prompt(description: str) -> str:
    return _PROMPT_TEMPLATE.format(description=description)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_extraction(data: dict[str, Any]) -> dict[str, Any]:
    """Apply post-schema checks that complete_json's schema can't express."""
    # years_required: int or None only
    yr = data.get("years_required")
    if yr is not None and not isinstance(yr, int):
        # model returned a float like 3.0 — coerce; anything else → None
        try:
            data["years_required"] = int(yr)
        except (TypeError, ValueError):
            data["years_required"] = None

    # remote_ok: bool or None only
    ro = data.get("remote_ok")
    if ro is not None and not isinstance(ro, bool):
        data["remote_ok"] = None

    # seniority: must be one of the valid values
    if data.get("seniority") not in _VALID_SENIORITY:
        data["seniority"] = "unknown"

    # skill lists: ensure all items are strings
    data["required_skills"] = [str(s) for s in data.get("required_skills") or []]
    data["nice_to_have"] = [str(s) for s in data.get("nice_to_have") or []]

    return data


def _normalise_skills(data: dict[str, Any]) -> dict[str, Any]:
    """Lowercase all skill names so 'Postgres' and 'postgres' match later."""
    data["required_skills"] = [s.lower().strip() for s in data["required_skills"]]
    data["nice_to_have"] = [s.lower().strip() for s in data["nice_to_have"]]
    return data


# ---------------------------------------------------------------------------
# Description hash
# ---------------------------------------------------------------------------

def description_hash(text: str) -> str:
    """SHA-256 of the description text, stable across runs."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract(listing: dict[str, Any], config: Config, storage: Any) -> dict[str, Any]:
    """Extract structured facts from a job listing's description.

    Parameters
    ----------
    listing:  A listing dict with at least a ``description`` key.
    config:   Config object passed to the LLM call.
    storage:  The storage module (rule 2 — no direct sqlite3 here).

    Returns
    -------
    A dict matching EXTRACTION_SCHEMA.  On a cache hit the model is never
    called.  On a model failure, raises LLMError — the caller (Scorer)
    handles it per rule 17.
    """
    desc: str = listing.get("description") or ""
    desc_hash = description_hash(desc)

    # --- Cache check (rule 18) ---
    cached = storage.get_cached_extraction(desc_hash)
    if cached is not None:
        return cached

    # --- Model call ---
    prompt = _build_prompt(desc)
    raw = complete_json(prompt, EXTRACTION_SCHEMA, config=config, max_retries=1)

    # --- Post-validation and normalisation ---
    result = _normalise_skills(_validate_extraction(raw))

    # --- Store in cache via storage module (rule 2) ---
    storage.put_cached_extraction(desc_hash, result)

    return result
