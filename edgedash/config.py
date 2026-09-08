from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Load environment variables from .env if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional for manual env var setting

_DEFAULTS: dict[str, Any] = {
    "target_role": "",
    "target_city": "",
    "target_seniority": "mid",
    "keywords": [],
    "my_skills": [],
    "experience_years": 0,
    "db_path": "data/edgedash.db",
    "db_backend": "sqlite",  # "sqlite" or "postgres"
    "min_fit_score": 50,
    "sources": ["arbeitnow"],
    "use_mock_fetcher": False,
    "score_batch_size": 25,
    "score_weights": {"skill_match": 0.45, "seniority_fit": 0.25, "location_fit": 0.15, "recency": 0.15},
    "llm_provider": "gemini",
    "llm_model": "gemini-3.5-flash",
    "schedule_interval": "hourly",
    "skill_aliases": {},
    "fetch_interval_hours": 6,
    "fetch_max_pages": 5,
    "fetch_max_listings": 200,
    "score_max_seconds": 300,
    "analyse_max_seconds": 60,
    "verifier_max_seconds": 60,
    "min_score_spread": 10,
    "min_score_stdev": 5,
    "max_empty_extraction_pct": 20,
    "max_skills_per_listing": 20,
    "min_gap_sample": 3,
    "max_data_age_days": 3,
    "daily_question_cap": 200,
}


@dataclass(frozen=True)
class Config:
    target_role: str
    target_city: str
    target_seniority: str
    keywords: list[str]
    my_skills: list[str]
    experience_years: int
    db_path: str
    db_backend: str
    min_fit_score: int
    sources: list[str]
    use_mock_fetcher: bool
    score_batch_size: int
    score_weights: dict[str, float]
    llm_provider: str
    llm_model: str
    schedule_interval: str
    skill_aliases: dict[str, str]
    fetch_interval_hours: int
    fetch_max_pages: int
    fetch_max_listings: int
    score_max_seconds: int
    analyse_max_seconds: int
    verifier_max_seconds: int
    min_score_spread: int
    min_score_stdev: int
    max_empty_extraction_pct: int
    max_skills_per_listing: int
    min_gap_sample: int
    max_data_age_days: int
    daily_question_cap: int


def load_config(path: Path | str | None = None) -> Config:
    config_path = Path(path) if path is not None else Path("config.yaml")
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Config file not found: {config_path.resolve()}. "
            "Create config.yaml at the repo root (see the example fields in config.yaml)."
        )

    raw = _parse_yaml_config(config_path.read_text(encoding="utf-8"))
    merged = {**_DEFAULTS, **raw}
    return Config(
        target_role=_as_str(merged, "target_role"),
        target_city=_as_str(merged, "target_city"),
        target_seniority=_as_str(merged, "target_seniority"),
        keywords=_as_str_list(merged, "keywords"),
        my_skills=_as_str_list(merged, "my_skills"),
        experience_years=_as_int(merged, "experience_years"),
        db_path=_as_str(merged, "db_path"),
        db_backend=_as_str(merged, "db_backend"),
        min_fit_score=_as_int(merged, "min_fit_score"),
        sources=_as_str_list(merged, "sources"),
        use_mock_fetcher=_as_bool(merged, "use_mock_fetcher"),
        score_batch_size=_as_int(merged, "score_batch_size"),
        score_weights=_as_score_weights(merged, "score_weights"),
        llm_provider=_as_str(merged, "llm_provider"),
        llm_model=_as_str(merged, "llm_model"),
        schedule_interval=_as_str(merged, "schedule_interval"),
        skill_aliases=_as_str_dict(merged, "skill_aliases"),
        fetch_interval_hours=_as_int(merged, "fetch_interval_hours"),
        fetch_max_pages=_as_int(merged, "fetch_max_pages"),
        fetch_max_listings=_as_int(merged, "fetch_max_listings"),
        score_max_seconds=_as_int(merged, "score_max_seconds"),
        analyse_max_seconds=_as_int(merged, "analyse_max_seconds"),
        verifier_max_seconds=_as_int(merged, "verifier_max_seconds"),
        min_score_spread=_as_int(merged, "min_score_spread"),
        min_score_stdev=_as_int(merged, "min_score_stdev"),
        max_empty_extraction_pct=_as_int(merged, "max_empty_extraction_pct"),
        max_skills_per_listing=_as_int(merged, "max_skills_per_listing"),
        min_gap_sample=_as_int(merged, "min_gap_sample"),
        max_data_age_days=_as_int(merged, "max_data_age_days"),
        daily_question_cap=_as_int(merged, "daily_question_cap"),
    )


def _as_str(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"config.yaml field '{key}' must be a string, got {type(value).__name__}")
    return value


def _as_int(data: dict[str, Any], key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"config.yaml field '{key}' must be an integer, got {type(value).__name__}")
    return value


def _as_str_list(data: dict[str, Any], key: str) -> list[str]:
    value = data[key]
    if not isinstance(value, list):
        raise TypeError(f"config.yaml field '{key}' must be a list, got {type(value).__name__}")
    return [str(item) for item in value]


def _as_bool(data: dict[str, Any], key: str) -> bool:
    value = data[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    raise TypeError(f"config.yaml field '{key}' must be a boolean, got {type(value).__name__}")


def _as_str_dict(data: dict[str, Any], key: str) -> dict[str, str]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise TypeError(f"config.yaml field '{key}' must be a mapping, got {type(value).__name__}")
    return {str(k): str(v) for k, v in value.items()}


def _as_score_weights(data: dict[str, Any], key: str) -> dict[str, float]:
    value = data[key]
    if not isinstance(value, dict):
        raise TypeError(f"config.yaml field '{key}' must be a mapping, got {type(value).__name__}")
    out: dict[str, float] = {}
    for k in ("skill_match", "seniority_fit", "location_fit", "recency"):
        if k not in value:
            raise TypeError(f"config.yaml field '{key}' missing weight '{k}'")
        v = value[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise TypeError(f"config.yaml weight '{k}' must be a number, got {type(v).__name__}")
        out[k] = float(v)
    return out


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _parse_scalar(value: str) -> str | int | bool:
    value = _strip_quotes(value.strip())
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    return value


def _parse_yaml_config(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    list_key: str | None = None
    dict_key: str | None = None

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if stripped.startswith("- "):
            if list_key is None:
                raise ValueError(f"config.yaml line {lineno}: list item without a parent key")
            items = result[list_key]
            if not isinstance(items, list):
                raise ValueError(f"config.yaml line {lineno}: '{list_key}' is not a list")
            items.append(_parse_scalar(stripped[2:].strip()))
            continue

        if indent == 2 and dict_key is not None:
            # nested mapping under dict_key (e.g. score_weights, skill_aliases)
            if ":" not in stripped:
                raise ValueError(f"config.yaml line {lineno}: invalid nested line {raw_line!r}")
            k, _, v = stripped.partition(":")
            k = k.strip()
            v = v.strip()
            if not v:
                raise ValueError(f"config.yaml line {lineno}: nested value missing for {k!r}")
            # score_weights values are floats; skill_aliases values are strings
            if dict_key == "skill_aliases":
                result[dict_key][k] = _strip_quotes(v)  # type: ignore[index]
            else:
                parsed = _parse_scalar(v)
                if not isinstance(parsed, (int, float)) or isinstance(parsed, bool):
                    try:
                        parsed = float(v)
                    except ValueError:
                        raise ValueError(f"config.yaml line {lineno}: expected number for {k!r}")
                result[dict_key][k] = float(parsed)  # type: ignore[index]
            continue

        if indent != 0:
            raise ValueError(f"config.yaml line {lineno}: nested YAML is not supported")

        list_key = None
        dict_key = None
        if ":" not in stripped:
            raise ValueError(f"config.yaml line {lineno}: invalid line {raw_line!r}")

        key, _, raw_value = stripped.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()

        if not raw_value:
            # decide list vs dict based on key
            if key in ("score_weights", "skill_aliases"):
                result[key] = {}
                dict_key = key
            else:
                result[key] = []
                list_key = key
            continue

        result[key] = _parse_scalar(raw_value)

    return result
