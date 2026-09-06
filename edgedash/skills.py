"""Skill canonicalisation — deterministic only, no LLM, no network.

Public API
----------
canonical(raw: str, aliases: dict[str, str]) -> str
    Normalise a raw skill string to its canonical form.

CLI
---
python -m edgedash.skills --audit
    Read every extracted skill from the DB and print a frequency/alias
    report so you can find missing aliases.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from typing import Any


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def canonical(raw: str, aliases: dict[str, str]) -> str:
    """Return the canonical form of *raw* using *aliases*.

    Steps (in order):
    1. Lowercase and strip surrounding whitespace.
    2. Strip surrounding punctuation (.,;:!?"').
    3. Drop parenthetical qualifiers: "kubernetes (eks)" -> "kubernetes".
    4. Collapse internal whitespace runs to a single space.
    5. Look up in the alias map; return the mapped value or the
       normalised string itself if no alias exists.

    Pure function — same input always produces the same output.
    No network, no model.
    """
    if not raw:
        return ""

    s = raw.lower().strip()
    s = s.strip(".,;:!?\"'")
    s = re.sub(r"\s*\(.*?\)\s*", " ", s)   # drop parentheticals
    s = re.sub(r"\s+", " ", s).strip()

    return aliases.get(s, s)


# ---------------------------------------------------------------------------
# CLI audit
# ---------------------------------------------------------------------------

def _run_audit(config: Any, storage: Any) -> None:
    """Print a skill frequency / alias report from the extraction cache."""
    import json as _json

    storage.init_db(config.db_path)
    aliases: dict[str, str] = config.skill_aliases

    # Collect every raw skill from every cached extraction
    raw_counts: Counter[str] = Counter()

    with storage._connect() as conn:
        rows = conn.execute(
            "SELECT required_skills, nice_to_have FROM extraction_cache"
        ).fetchall()

    for row in rows:
        for col in row:
            try:
                skills = _json.loads(col)
                for s in skills:
                    if isinstance(s, str) and s.strip():
                        raw_counts[s.strip()] += 1
            except Exception:
                pass

    if not raw_counts:
        print("No extracted skills found. Run a scoring cycle first.")
        return

    # --- Top 40 ---
    top40 = raw_counts.most_common(40)
    col_w = max(len(s) for s, _ in top40)

    print("=" * 70)
    print(f"TOP {min(40, len(top40))} RAW SKILL STRINGS")
    print("=" * 70)
    print(f"{'Raw skill':<{col_w}}  {'Count':>5}  {'Canonical form'}")
    print("-" * 70)
    for raw, count in top40:
        canon = canonical(raw, aliases)
        marker = "  (aliased)" if canon != canonical(raw, {}) else ""
        print(f"{raw:<{col_w}}  {count:>5}  {canon}{marker}")

    # --- Singletons ---
    singletons = sorted(s for s, c in raw_counts.items() if c == 1)
    print()
    print("=" * 70)
    print(f"SINGLETONS ({len(singletons)}) — possible typos, junk, or full sentences")
    print("=" * 70)
    for s in singletons:
        canon = canonical(s, aliases)
        print(f"  {s!r:50s}  -> {canon!r}")

    print()
    print(f"Total unique raw strings : {len(raw_counts)}")
    print(f"Total skill occurrences  : {sum(raw_counts.values())}")
    print(f"Alias map entries        : {len(aliases)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EdgeDash skill audit")
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Print skill frequency and alias report from the extraction cache",
    )
    args = parser.parse_args()

    if args.audit:
        # Load dotenv for standalone use
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        import edgedash.storage as _storage
        from edgedash.config import load_config as _load_config

        _run_audit(_load_config(), _storage)
