"""Verification checks — deterministic Python, no LLM, no network, no clock.

A model cannot be the judge of a model's output (rule 34).
Every check is a pure function: same input → same output, always.
Thresholds come from config (rule 39).

Public API
----------
CheckResult   dataclass — name, passed, observed, threshold, message
Verdict       dataclass — passed, failed_checks, summary
run_all_checks(scores, facts_list, gaps, latest_fetch_at, config, now) -> Verdict
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckResult:
    name:      str
    passed:    bool
    observed:  str   # what was measured, e.g. "spread=4"
    threshold: str   # what was required, e.g. "min_score_spread=10"
    message:   str   # human-readable sentence naming the failure mode


@dataclass(frozen=True)
class Verdict:
    passed:        bool
    failed_checks: list[CheckResult]
    summary:       str               # one-line summary for cycle_log (rule 37)

    @property
    def all_checks(self) -> list[CheckResult]:
        """Convenience — not stored, reconstructed on demand."""
        return []   # callers use failed_checks; full list returned by run_all_checks


# ---------------------------------------------------------------------------
# Minimum sample required to evaluate spread
# ---------------------------------------------------------------------------
_MIN_SPREAD_SAMPLE = 5


# ---------------------------------------------------------------------------
# 1. Score spread
# ---------------------------------------------------------------------------

def check_score_spread(scores: list[int], config: Any) -> CheckResult:
    """Fail if the score distribution is suspiciously tight.

    Catches uniform score inflation: the scorer is assigning nearly the
    same score to every listing regardless of fit.

    Passes trivially when fewer than _MIN_SPREAD_SAMPLE scores exist —
    too few points to draw a distribution conclusion.
    """
    if len(scores) < _MIN_SPREAD_SAMPLE:
        return CheckResult(
            name      = "score_spread",
            passed    = True,
            observed  = f"n={len(scores)}",
            threshold = f"n>={_MIN_SPREAD_SAMPLE} required to check",
            message   = (
                f"only {len(scores)} score(s) — too few to evaluate spread, "
                "passes trivially"
            ),
        )

    spread = max(scores) - min(scores)
    stdev  = statistics.stdev(scores)

    if spread < config.min_score_spread:
        return CheckResult(
            name      = "score_spread",
            passed    = False,
            observed  = f"spread={spread}",
            threshold = f"min_score_spread={config.min_score_spread}",
            message   = (
                f"spread {spread} < {config.min_score_spread} — "
                "scorer may be inflating all scores uniformly"
            ),
        )

    if stdev < config.min_score_stdev:
        return CheckResult(
            name      = "score_spread",
            passed    = False,
            observed  = f"stdev={stdev:.2f}",
            threshold = f"min_score_stdev={config.min_score_stdev}",
            message   = (
                f"stdev {stdev:.2f} < {config.min_score_stdev} — "
                "distribution too tight even though raw spread looks OK"
            ),
        )

    return CheckResult(
        name      = "score_spread",
        passed    = True,
        observed  = f"spread={spread}, stdev={stdev:.2f}",
        threshold = (
            f"spread>={config.min_score_spread}, "
            f"stdev>={config.min_score_stdev}"
        ),
        message   = "score distribution looks healthy",
    )


# ---------------------------------------------------------------------------
# 2. Extraction sanity
# ---------------------------------------------------------------------------

def check_extraction_sanity(facts_list: list[dict], config: Any) -> CheckResult:
    """Fail if extractions are mostly empty or implausibly large.

    Two failure modes:
    - Empty required_skills on too many listings → broken extractor.
    - A single listing with more than max skills → model returned a
      whole sentence as a skill list rather than individual skills.
    """
    if not facts_list:
        return CheckResult(
            name      = "extraction_sanity",
            passed    = True,
            observed  = "n=0",
            threshold = "n/a",
            message   = "no extractions to check, passes trivially",
        )

    empty_count = sum(
        1 for f in facts_list
        if not f.get("required_skills")
    )
    empty_pct = empty_count / len(facts_list) * 100

    skill_counts = [
        len(f.get("required_skills") or [])
        for f in facts_list
    ]
    max_skills = max(skill_counts) if skill_counts else 0

    if empty_pct > config.max_empty_extraction_pct:
        return CheckResult(
            name      = "extraction_sanity",
            passed    = False,
            observed  = f"empty_pct={empty_pct:.1f}%",
            threshold = f"max_empty_extraction_pct={config.max_empty_extraction_pct}%",
            message   = (
                f"{empty_pct:.1f}% of extractions have empty required_skills "
                f"({empty_count}/{len(facts_list)}) — extractor may be broken"
            ),
        )

    if max_skills > config.max_skills_per_listing:
        return CheckResult(
            name      = "extraction_sanity",
            passed    = False,
            observed  = f"max_skills_per_listing={max_skills}",
            threshold = f"max_skills_per_listing={config.max_skills_per_listing}",
            message   = (
                f"a listing has {max_skills} required skills — "
                "model likely returned a sentence as a skill list"
            ),
        )

    return CheckResult(
        name      = "extraction_sanity",
        passed    = True,
        observed  = f"empty_pct={empty_pct:.1f}%, max_skills={max_skills}",
        threshold = (
            f"empty<={config.max_empty_extraction_pct}%, "
            f"skills<={config.max_skills_per_listing}"
        ),
        message   = "extraction shapes look healthy",
    )


# ---------------------------------------------------------------------------
# 3. Gap sample size
# ---------------------------------------------------------------------------

def check_gap_sample_size(gaps: list[dict], config: Any) -> CheckResult:
    """Fail if the top-ranked gap is computed from too few listings.

    Catches ranking a rumour: a skill that appeared in one or two
    listings is promoted to the top gap purely by noise.
    """
    if not gaps:
        return CheckResult(
            name      = "gap_sample_size",
            passed    = True,
            observed  = "n=0",
            threshold = "n/a",
            message   = "no gaps to check, passes trivially",
        )

    top      = gaps[0]
    skill    = top.get("skill", "?")
    n        = top.get("listings_blocked", 0)

    if n < config.min_gap_sample:
        return CheckResult(
            name      = "gap_sample_size",
            passed    = False,
            observed  = f"top_gap_sample={n} (skill={skill!r})",
            threshold = f"min_gap_sample={config.min_gap_sample}",
            message   = (
                f"top gap '{skill}' computed from only {n} listing(s) — "
                "too few to rank with confidence"
            ),
        )

    return CheckResult(
        name      = "gap_sample_size",
        passed    = True,
        observed  = f"top_gap_sample={n} (skill={skill!r})",
        threshold = f"min_gap_sample={config.min_gap_sample}",
        message   = f"top gap '{skill}' has adequate sample size ({n} listings)",
    )


# ---------------------------------------------------------------------------
# 4. Freshness
# ---------------------------------------------------------------------------

def check_freshness(
    latest_fetch_at: str | None,
    config: Any,
    now: datetime,
) -> CheckResult:
    """Fail if the newest listing is older than max_data_age_days.

    Catches a silent fetch failure: the fetcher ran but returned nothing
    new, leaving the pipeline operating on stale data.

    `now` is a parameter — datetime.now() is never called inside this
    function so it remains fully testable.
    """
    if latest_fetch_at is None:
        return CheckResult(
            name      = "freshness",
            passed    = False,
            observed  = "latest_fetch_at=null",
            threshold = f"max_data_age_days={config.max_data_age_days}",
            message   = "no listings in the database — fetch has never succeeded",
        )

    try:
        fetched_dt = datetime.fromisoformat(latest_fetch_at)
        if fetched_dt.tzinfo is None:
            fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return CheckResult(
            name      = "freshness",
            passed    = False,
            observed  = f"latest_fetch_at={latest_fetch_at!r}",
            threshold = f"max_data_age_days={config.max_data_age_days}",
            message   = f"could not parse latest_fetch_at timestamp: {latest_fetch_at!r}",
        )

    age_days = (now - fetched_dt).total_seconds() / 86_400

    if age_days > config.max_data_age_days:
        return CheckResult(
            name      = "freshness",
            passed    = False,
            observed  = f"age={age_days:.1f}d (fetched {latest_fetch_at})",
            threshold = f"max_data_age_days={config.max_data_age_days}",
            message   = (
                f"newest listing is {age_days:.1f} day(s) old — "
                "fetch may have silently failed"
            ),
        )

    return CheckResult(
        name      = "freshness",
        passed    = True,
        observed  = f"age={age_days:.2f}d",
        threshold = f"max_data_age_days={config.max_data_age_days}",
        message   = f"data is fresh ({age_days:.2f} day(s) old)",
    )


# ---------------------------------------------------------------------------
# 5. Run all checks
# ---------------------------------------------------------------------------

def run_all_checks(
    scores:          list[int],
    facts_list:      list[dict],
    gaps:            list[dict],
    latest_fetch_at: str | None,
    config:          Any,
    now:             datetime,
) -> tuple[Verdict, list[CheckResult]]:
    """Run every check and return (Verdict, all_results).

    Returns the full list alongside the Verdict so callers can log
    individual check details (rule 37 — never just 'failed').

    All checks always run — short-circuiting would hide multiple failures.
    """
    results: list[CheckResult] = [
        check_score_spread(scores, config),
        check_extraction_sanity(facts_list, config),
        check_gap_sample_size(gaps, config),
        check_freshness(latest_fetch_at, config, now),
    ]

    failed  = [r for r in results if not r.passed]
    passed  = not failed

    if passed:
        summary = "all checks passed"
    else:
        parts = [
            f"{r.name}: {r.observed} (threshold {r.threshold})"
            for r in failed
        ]
        summary = f"{len(failed)} check(s) failed: {'; '.join(parts)}"

    verdict = Verdict(
        passed        = passed,
        failed_checks = failed,
        summary       = summary,
    )
    return verdict, results
