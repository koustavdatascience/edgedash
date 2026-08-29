from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "target_role": "",
    "target_city": "",
    "keywords": [],
    "my_skills": [],
    "experience_years": 0,
    "db_path": "data/edgedash.db",
    "min_fit_score": 50,
}


@dataclass(frozen=True)
class Config:
    target_role: str
    target_city: str
    keywords: list[str]
    my_skills: list[str]
    experience_years: int
    db_path: str
    min_fit_score: int


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
        keywords=_as_str_list(merged, "keywords"),
        my_skills=_as_str_list(merged, "my_skills"),
        experience_years=_as_int(merged, "experience_years"),
        db_path=_as_str(merged, "db_path"),
        min_fit_score=_as_int(merged, "min_fit_score"),
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


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _parse_scalar(value: str) -> str | int:
    value = _strip_quotes(value.strip())
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    return value


def _parse_yaml_config(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    list_key: str | None = None

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

        if indent != 0:
            raise ValueError(f"config.yaml line {lineno}: nested YAML is not supported")

        list_key = None
        if ":" not in stripped:
            raise ValueError(f"config.yaml line {lineno}: invalid line {raw_line!r}")

        key, _, raw_value = stripped.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()

        if not raw_value:
            result[key] = []
            list_key = key
            continue

        result[key] = _parse_scalar(raw_value)

    return result
