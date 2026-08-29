# EdgeDash

EdgeDash is an autonomous career intelligence loop that runs on a schedule: it fetches live job listings, scores each one for fit against your profile, identifies skill gaps across the market, verifies its own output, and publishes a read-only Streamlit dashboard. You configure your target role, city, keywords, and skills once; the system handles the rest.

## Architecture

```
  Trigger (scheduled)
        |
        v
  Orchestrator
        |
        +---> Fetcher
        +---> Scorer
        +---> GapAnalyzer
        |
        v
    Verifier
        |
        v
     Storage
        |
        v
   Dashboard (read-only)
```

The Orchestrator reads state and delegates work. It never fetches or scores directly. Each sub-agent has one goal and one stop condition.

## Current status

**Built**

- [x] `config.yaml` loading (`edgedash/config.py`)
- [x] Storage module with SQLite (`edgedash/storage.py`) — listings, skill gaps, cycle log
- [x] Agent base class and result type (`edgedash/agents/base.py`)
- [x] Mock Fetcher — **temporary**; returns 12 fake listings with stable IDs for dedup testing (`edgedash/agents/mock_fetcher.py`)
- [x] Orchestrator with agent registry and cycle logging (`edgedash/orchestrator.py`)
- [x] Manual cycle entry point (`run_cycle.py`)

**Week 2**

- [ ] Real Fetcher (live job listings; replaces Mock Fetcher)
- [ ] Scheduled trigger

**Week 3**

- [ ] Scorer (fit score and reason per listing)
- [ ] GapAnalyzer (skill gap frequency tracking)
- [ ] Verifier (output validation before persistence)

**Week 4**

- [ ] Streamlit dashboard (read-only)
- [ ] SQLite to hosted Postgres migration (one-file storage swap)

## Setup

**Python:** 3.11 or newer. No third-party dependencies yet — stdlib only.

```bash
git clone <repo-url>
cd ai-career-intelligence-agent
```

Edit `config.yaml` at the repo root with your target role, city, keywords, skills, and thresholds. All user-specific values live here; nothing is hardcoded in code.

```bash
python run_cycle.py
```

Run it twice to confirm deduplication: the first cycle inserts 12 listings; the second reports 0 new and 12 duplicates ignored.

## Design decisions

**Isolated storage module.** Every database call goes through `edgedash/storage.py`. No other module imports `sqlite3`. When we move to Postgres in week 4, only that file changes.

**Stable listing IDs.** Each listing ID is a SHA-256 hash of `source + url`. The same job from the same source always maps to the same row, so `INSERT OR IGNORE` deduplicates cleanly and `upsert_listings` can return an accurate count of genuinely new rows.

**Orchestrator delegates.** The Orchestrator reads state, decides which agents to run, and logs results. It does not fetch listings or compute scores itself. That keeps each agent independently testable and lets you swap Mock Fetcher for a real one by changing one line in the registry.
