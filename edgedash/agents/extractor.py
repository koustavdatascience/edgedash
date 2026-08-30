from __future__ import annotations

import hashlib
import json

from edgedash import storage

# ---------------------------------------------------------------------------
# 1. Schema — EXACTLY these fields, nothing else. Rule 16: no score field.
# ---------------------------------------------------------------------------
EXTRACTION_SCHEMA: dict[str, str] = {
    "required_skills": "list[str] — skills the role requires",
    "nice_to_have": "list[str] — preferred, not required",
    "seniority": 'one of "junior" | "mid" | "senior" | "lead" | "unknown"',
    "years_required": "int | null — null if not stated, never a guess",
    "remote_ok": "bool | null — null if the listing doesn't say",
}

_ALLOWED_SENIORITIES = {"junior", "mid", "senior", "lead", "unknown"}

# Schema passed to llm.complete_json — permissive so nulls pass llm's own
# validator; strict checks happen in _validate_extraction below.
# None as type means "skip isinstance check" in edgedash.llm._validate_response.
_LLM_SCHEMA: dict = {
    "required_skills": list,
    "nice_to_have": list,
    "seniority": str,
    "years_required": None,
    "remote_ok": None,
}

# ---------------------------------------------------------------------------
# 2. Prompt — asks ONLY for what the listing says, never mentions candidate.
# ---------------------------------------------------------------------------
PROMPT_TEMPLATE = """You are reading a single job listing. Extract structured facts from the listing text below.

Rules — follow them exactly:
- Extract ONLY what the listing explicitly states. Do not infer, do not guess, do not evaluate any candidate.
- If a value is not stated, use null for years_required / remote_ok, "unknown" for seniority, and [] for skill lists. Never invent a value.
- You are reading a document, nothing more. Do not use any outside knowledge.
- Return JSON with EXACTLY these 5 keys and no others: required_skills, nice_to_have, seniority, years_required, remote_ok. No prose, no markdown fences.

Definitions:
- required_skills: list of strings — skills the role states as required/must-have.
- nice_to_have: list of strings — skills stated as preferred/nice-to-have/bonus.
- seniority: one of "junior" | "mid" | "senior" | "lead" | "unknown".
- years_required: integer years of experience explicitly required, or null if not stated.
- remote_ok: true if the listing explicitly says remote/hybrid is allowed, false if it explicitly says on-site only, null if not stated.

Listing:
Title: {title}
Company: {company}

Description:
{description}
"""


def _description_hash(description: str | None) -> str:
    text = (description or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_skills(skills: list) -> list[str]:
    out: list[str] = []
    for s in skills:
        if not isinstance(s, str):
            s = str(s)
        s = s.strip().lower()
        if s:
            out.append(s)
    return out


def _validate_extraction(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("extraction result must be a JSON object")
    for key in ("required_skills", "nice_to_have", "seniority", "years_required", "remote_ok"):
        if key not in data:
            raise ValueError(f"missing required field: {key}")
    extra = set(data) - {"required_skills", "nice_to_have", "seniority", "years_required", "remote_ok"}
    if extra:
        raise ValueError(f"unexpected fields: {extra}")

    rs = data["required_skills"]
    if not isinstance(rs, list) or not all(isinstance(x, str) for x in rs):
        raise ValueError("required_skills must be list[str]")
    nh = data["nice_to_have"]
    if not isinstance(nh, list) or not all(isinstance(x, str) for x in nh):
        raise ValueError("nice_to_have must be list[str]")

    sen = data["seniority"]
    if not isinstance(sen, str) or sen not in _ALLOWED_SENIORITIES:
        raise ValueError(f"seniority must be one of {_ALLOWED_SENIORITIES}, got {sen!r}")

    yr = data["years_required"]
    if yr is not None and not isinstance(yr, int):
        raise ValueError(f"years_required must be int | null, got {type(yr).__name__}")
    if isinstance(yr, bool):
        raise ValueError("years_required must be int | null, got bool")
    if isinstance(yr, int) and yr < 0:
        raise ValueError("years_required must be >= 0")

    ro = data["remote_ok"]
    if ro is not None and not isinstance(ro, bool):
        raise ValueError(f"remote_ok must be bool | null, got {type(ro).__name__}")

    return {
        "required_skills": _normalize_skills(rs),
        "nice_to_have": _normalize_skills(nh),
        "seniority": sen,
        "years_required": yr,
        "remote_ok": ro,
    }


def extract(listing: dict) -> dict:
    """Extract structured facts from a job listing.

    Cache-first: hash(description) -> storage.get_cached_extraction.
    On miss calls llm.complete_json, validates, normalises skills to
    lowercase, stores via storage.put_cached_extraction, returns result.
    """
    description = listing.get("description") or ""
    title = listing.get("title") or ""
    company = listing.get("company") or ""
    h = _description_hash(description)

    cached = storage.get_cached_extraction(h)
    if cached is not None:
        return cached

    # Lazy import to keep module import side-effect free and keep
    # llm as the single doorway (rule 15) — no other file imports SDK.
    from edgedash.llm import complete_json

    prompt = PROMPT_TEMPLATE.format(
        title=title or "N/A",
        company=company or "N/A",
        description=description or "(no description provided)",
    )

    raw = complete_json(prompt, _LLM_SCHEMA, max_retries=1)
    validated = _validate_extraction(raw)

    storage.put_cached_extraction(h, validated)
    return validated
