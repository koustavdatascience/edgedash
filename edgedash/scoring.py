from __future__ import annotations

from datetime import datetime, timezone

# Ordered seniority bands — distance determines fit.
_BANDS = ["junior", "mid", "senior", "lead"]
_BAND_INDEX = {name: i for i, name in enumerate(_BANDS)}

_DEFAULT_WEIGHTS = {
    "skill_match": 0.45,
    "seniority_fit": 0.25,
    "location_fit": 0.15,
    "recency": 0.15,
}


def _get_weights(config) -> dict[str, float]:
    # Support flat keys, nested dict, or missing — always returns 4 weights summing ~1.0
    if hasattr(config, "score_weights") and isinstance(getattr(config, "score_weights"), dict):
        w = getattr(config, "score_weights")
        return {k: float(w.get(k, _DEFAULT_WEIGHTS[k])) for k in _DEFAULT_WEIGHTS}
    # flat keys like score_weight_skill_match
    out = {}
    for k, default in _DEFAULT_WEIGHTS.items():
        attr = f"score_weight_{k}"
        out[k] = float(getattr(config, attr, default)) if hasattr(config, attr) else default
        # also try dict-style getter
        if hasattr(config, "__dict__") and attr in getattr(config, "__dict__", {}):
            out[k] = float(getattr(config, attr))
    # fallback: if config is plain dict
    if isinstance(config, dict):
        sw = config.get("score_weights", {})
        for k in _DEFAULT_WEIGHTS:
            if k in sw:
                out[k] = float(sw[k])
    return out


def _get_my_skills(config) -> set[str]:
    # spec says config.skills; real config uses my_skills — support both
    for attr in ("my_skills", "skills", "candidate_skills"):
        if hasattr(config, attr):
            vals = getattr(config, attr)
            if isinstance(vals, list):
                return {s.strip().lower() for s in vals if s and str(s).strip()}
    if isinstance(config, dict):
        for k in ("my_skills", "skills"):
            if k in config:
                return {str(s).strip().lower() for s in config[k] if str(s).strip()}
    return set()


def _get_target_seniority(config) -> str:
    for attr in ("target_seniority", "seniority", "target_band"):
        if hasattr(config, attr):
            v = getattr(config, attr)
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
    if isinstance(config, dict) and "target_seniority" in config:
        return str(config["target_seniority"]).strip().lower()
    return "mid"


def _get_target_city(config) -> str:
    for attr in ("target_city", "city", "location"):
        if hasattr(config, attr):
            v = getattr(config, attr)
            if isinstance(v, str):
                return v.strip()
    if isinstance(config, dict):
        return str(config.get("target_city", "")).strip()
    return ""


# ---------------------------------------------------------------------------
# Component calculators — each 0.0-1.0, pure functions
# ---------------------------------------------------------------------------

def _skill_match(facts: dict, my_skills: set[str]) -> tuple[float, int, int, list[str]]:
    """Returns (score, matched_required, total_required, gaps). Case-insensitive.
    nice_to_have counts at 1/3 weight. Empty lists handled explicitly — no div/0.
    """
    req = [s.strip().lower() for s in facts.get("required_skills") or [] if str(s).strip()]
    nice = [s.strip().lower() for s in facts.get("nice_to_have") or [] if str(s).strip()]

    matched_req = sum(1 for s in req if s in my_skills)
    matched_nice = sum(1 for s in nice if s in my_skills)

    denom = len(req) + len(nice) / 3.0
    if denom == 0:
        # No requirements stated — treat as full match (no penalty)
        return 1.0, 0, 0, []

    numer = matched_req + matched_nice / 3.0
    score = numer / denom
    # gaps are required skills missing — most useful for Gap Analyzer
    gaps = [s for s in req if s not in my_skills]
    return max(0.0, min(1.0, score)), matched_req, len(req), gaps


def _seniority_fit(facts: dict, target_seniority: str) -> float:
    sen = (facts.get("seniority") or "unknown").strip().lower()
    tgt = (target_seniority or "mid").strip().lower()

    if sen == "unknown" or tgt == "unknown" or sen not in _BAND_INDEX or tgt not in _BAND_INDEX:
        return 0.5

    dist = abs(_BAND_INDEX[sen] - _BAND_INDEX[tgt])
    if dist == 0:
        return 1.0
    if dist == 1:
        return 0.6
    if dist == 2:
        return 0.25
    return 0.0


def _location_fit(listing: dict, facts: dict, target_city: str) -> float:
    remote_ok = facts.get("remote_ok")
    if remote_ok is True:
        return 1.0

    loc = (listing.get("location") or "").strip()
    tgt = (target_city or "").strip()

    # exact city substring match
    if loc and tgt and tgt.lower() in loc.lower():
        return 1.0

    # unknown location
    if not loc or loc.lower() in {"unknown", "n/a", "null", "none"}:
        return 0.5

    # remote not stated and location doesn't match => unknown
    if remote_ok is None:
        return 0.5

    # clearly elsewhere and explicitly not remote
    return 0.1


def _recency(listing: dict) -> tuple[float, str]:
    """Returns (score, label). posted today 1.0 -> 0.0 at 30d. null -> 0.5 no crash."""
    raw = listing.get("posted_at")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return 0.5, "posted date unknown"

    # try ISO / date-only / timestamp
    posted = None
    if isinstance(raw, (int, float)):
        try:
            posted = datetime.fromtimestamp(raw, tz=timezone.utc)
        except Exception:
            return 0.5, "posted date unknown"
    elif isinstance(raw, str):
        s = raw.strip()
        # try ISO
        try:
            # handle Z
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            posted = datetime.fromisoformat(s)
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
        except Exception:
            # try date-only YYYY-MM-DD
            try:
                posted = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                return 0.5, "posted date unknown"
    else:
        return 0.5, "posted date unknown"

    now = datetime.now(timezone.utc)
    delta_days = (now - posted).total_seconds() / 86400
    if delta_days < 0:
        delta_days = 0  # future postings = today
    if delta_days <= 0.5:
        label = "posted today"
    elif delta_days < 1.5:
        label = "posted 1d ago"
    else:
        label = f"posted {int(round(delta_days))}d ago"

    score = 1.0 - (delta_days / 30.0)
    score = max(0.0, min(1.0, score))
    return score, label


# ---------------------------------------------------------------------------
# Public API — pure, no model calls, no network, no llm imports
# ---------------------------------------------------------------------------

def score_listing(listing: dict, facts: dict, config) -> dict:
    """Deterministic scorer. Returns {score:int 0-100, reason:str, components:{...}}."""
    my_skills = _get_my_skills(config)
    target_seniority = _get_target_seniority(config)
    target_city = _get_target_city(config)
    weights = _get_weights(config)

    skill_score, matched_req, total_req, gaps = _skill_match(facts, my_skills)
    seniority_score = _seniority_fit(facts, target_seniority)
    location_score = _location_fit(listing, facts, target_city)
    recency_score, _recency_label = _recency(listing)

    components = {
        "skill_match": round(skill_score, 4),
        "seniority_fit": round(seniority_score, 4),
        "location_fit": round(location_score, 4),
        "recency": round(recency_score, 4),
        "gaps": gaps,  # Include skill gaps for GapAnalyzer
    }

    total = (
        skill_score * weights["skill_match"]
        + seniority_score * weights["seniority_fit"]
        + location_score * weights["location_fit"]
        + recency_score * weights["recency"]
    )
    score = int(round(max(0.0, min(1.0, total)) * 100))

    reason = build_reason(components, facts, config, listing, matched_req, total_req, gaps, _recency_label)

    return {"score": score, "reason": reason, "components": components}


def build_reason(
    components: dict,
    facts: dict,
    config,
    listing: dict | None = None,
    matched_req: int | None = None,
    total_req: int | None = None,
    gaps: list[str] | None = None,
    recency_label: str | None = None,
) -> str:
    """Compact human-readable reason GENERATED FROM NUMBERS per rule 19.
    Never free text from model. Style:
      "4/6 required skills · seniority fits · remote · posted 2d ago · gap: kubernetes, spark"
    Accepts both 3-arg (spec) and scorer-internal 8-arg calls.
    """
    # derive gaps/matched if not supplied (for direct 3-arg calls)
    if gaps is None or matched_req is None or total_req is None:
        my_skills = _get_my_skills(config)
        _, mr, tr, g = _skill_match(facts, my_skills)
        gaps = g if gaps is None else gaps
        matched_req = mr if matched_req is None else matched_req
        total_req = tr if total_req is None else total_req

    if listing is not None and recency_label is None:
        _, recency_label = _recency(listing)
    recency_label = recency_label or "posted date unknown"

    parts: list[str] = []

    # skill segment
    if total_req == 0:
        parts.append("no required skills listed")
    else:
        parts.append(f"{matched_req}/{total_req} required skills")

    # seniority segment from numeric component
    sen_score = components.get("seniority_fit", 0.5)
    if sen_score >= 1.0:
        parts.append("seniority fits")
    elif sen_score >= 0.6:
        parts.append("seniority close")
    elif sen_score >= 0.25:
        parts.append("seniority stretch")
    else:
        # show actual band
        actual = facts.get("seniority") or "unknown"
        parts.append(f"seniority {actual}")

    # location segment
    loc_score = components.get("location_fit", 0.5)
    remote_ok = facts.get("remote_ok")
    if remote_ok is True:
        parts.append("remote")
    elif loc_score >= 1.0:
        parts.append("location fits")
    elif loc_score <= 0.1:
        parts.append("location mismatch")
    else:
        parts.append("location unknown")

    parts.append(recency_label)

    if gaps:
        # cap gaps shown to keep reason compact
        shown = ", ".join(gaps[:4])
        if len(gaps) > 4:
            shown += f" +{len(gaps)-4} more"
        parts.append(f"gap: {shown}")

    return " · ".join(parts)
