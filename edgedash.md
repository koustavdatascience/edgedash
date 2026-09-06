# EdgeDash Steering

Persistent project guidance for every interaction in this repository.

## Project

**EdgeDash** is an autonomous AI career intelligence agent: a scheduled loop that fetches live job listings, scores them for fit against the user's profile, surfaces skill gaps, verifies its own output, and publishes a Streamlit dashboard.

## Architecture

Do not deviate from this pipeline without explicitly telling the user and getting agreement:

```
Trigger (scheduled) → Orchestrator → sub-agents (Fetcher, Scorer, GapAnalyzer) → Verifier → Storage → Dashboard (read-only)
```

| Component | Responsibility |
|-----------|----------------|
| **Trigger** | Starts a run on a schedule |
| **Orchestrator** | Reads state and delegates; never fetches or scores directly |
| **Fetcher** | One goal, one stop condition |
| **Scorer** | One goal, one stop condition |
| **GapAnalyzer** | One goal, one stop condition |
| **Verifier** | Validates agent output before persistence |
| **Storage** | Single module, thin interface (see Hard Rule 2) |
| **Dashboard** | Read-only Streamlit view of stored data |

## Hard Rules

1. **Python 3.11+.** Standard library first. Add a dependency only when it genuinely saves real work — explain why before adding it.

2. **Single storage module.** All storage access goes through one module with a thin interface. No other module may import `sqlite3` directly. SQLite will be swapped for hosted Postgres in week 4; that migration must be a one-file change.

3. **No hardcoded user profile.** Never hardcode role, city, keywords, or skills. All user-specific values live in config.

4. **No secrets in code.** Environment variables only, loaded in one place.

5. **Cycle logging.** Every agent run writes a row to `cycle_log`: what ran, when, how many records touched, pass/fail, and any retry reason.

6. **Fail loudly.** No bare `except: pass`. If something is wrong, surface it clearly.

7. **Types and docs.** Type hints on every function signature. Docstrings only where intent is not obvious from the name.

8. **File size.** Keep files under ~150 lines. Split before that becomes a problem.

## Network & Sources

9. **Source class interface.** Every external source lives behind a Source class with a uniform interface. The Fetcher never contains source-specific parsing. Adding a source must never require editing the Fetcher.

10. **Normalised job dicts.** Every Source returns a list of normalised dicts with EXACTLY these keys: `source`, `external_id`, `title`, `company`, `location`, `url`, `description`, `posted_at`, `raw`. Missing values are `None`, never empty string, never `"N/A"`.

11. **Single network helper.** All network calls go through one helper with a timeout (10s default), explicit retry (2 attempts, exponential backoff), and a User-Agent header. No bare `requests.get` anywhere else in the codebase.

12. **Per-source failure isolation.** A source failing must NEVER kill the cycle. Catch per-source, log the failure to `cycle_log` with status `"failed"`, continue to the next source. One dead job board must not stop the other sources.

13. **Secrets via environment.** Secrets come from environment variables via a `.env` file that is gitignored. Never a literal key in code, never a key in `config.yaml`. If a key is missing, that source skips itself with a clear log line — it does not crash the cycle.
14. **Respect the source.** Rate limit to at most 1 request per second per
    source, set a real User-Agent, and honour any documented page limits.

## Intelligence & Scoring

15. All LLM calls go through one module, `edgedash/llm.py`, exposing one function.
    The provider and model name come from config, never hardcoded. Rate limit to
    stay inside a free tier (default 1 request per second, max 15 per minute).
    No other file imports an LLM SDK.

16. NEVER ask a model for a final score, ranking, or numeric rating. The model
    extracts structured facts only. All scoring arithmetic is deterministic Python
    in ONE function. The model never sees the scoring weights.

17. Every model response is validated against an explicit schema before use.
    A response that fails validation is retried once, then logged as a failure for
    THAT listing only — it must not crash the cycle or stop the remaining
    listings. Never `json.loads` raw model text without a validation and repair
    path.

18. Scoring is idempotent. Never re-score a listing that already has a score.
    Select only listings WHERE score IS NULL. Cache extraction results keyed on a
    hash of the job description so the same text is never sent to the model twice.

19. Every score carries a human-readable reason GENERATED FROM THE SCORE
    COMPONENTS by our code — never free text written by the model.

20. Log the score distribution (count, min, max, mean, spread) to cycle_log on
    every scoring run. A run where all scores fall within 10 points is a suspect
    run and must be logged as such.

21. Cap listings scored per cycle at a configurable batch size (default 25) so a
    cost or rate-limit blowup is structurally impossible.

## Aggregate Analysis

22. Aggregate analysis is deterministic SQL and Python. No LLM call may
    produce, adjust, or rank an aggregate number. A model may only
    SUGGEST canonical groupings for a human to approve.

23. Skill names are canonicalised through an explicit alias map in
    `config.yaml` that I own and can read. Never auto-merge skill names by
    model judgement or string similarity alone.

24. Gap ranking is weighted by the fit score of the listing the gap came
    from. A gap in a listing I score 20 on is worth far less than a gap in
    a listing I score 85 on. Never rank gaps by raw frequency alone.

25. Every gap report run writes a timestamped SNAPSHOT. Never overwrite
    the previous report. Trend over time is a first-class output, not an
    afterthought.

26. Every aggregate number must be traceable to the rows that produced
    it. Any reported gap must be able to list the specific listing IDs it
    was computed from. No number appears in the dashboard that I cannot
    drill into.

27. Report the sample size alongside every aggregate. A gap computed from
    3 listings and a gap computed from 90 listings must never be
    presented as equally reliable.

## Style

- Small, testable functions.
- Plain, readable Python over clever Python.
- When asked for one module, build **one module** — do not scaffold the whole app.

## Examples

```python
# BAD — storage bypass
import sqlite3
conn = sqlite3.connect("jobs.db")

# GOOD — storage module only
from storage import get_connection, insert_cycle_log
```

```python
# BAD — silent failure
try:
    score_jobs()
except:
    pass

# GOOD — fail loudly
try:
    score_jobs()
except ScoringError as exc:
    log_cycle_failure(run_id, reason=str(exc))
    raise
```

```python
# BAD — hardcoded profile
TARGET_ROLE = "Senior Data Engineer"
CITY = "Bangalore"

# GOOD — config-driven
```
