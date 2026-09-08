# EdgeDash

EdgeDash is an autonomous AI career intelligence loop that continuously fetches live job listings, scores them for fit against your target career profile, surfaces market skill gaps, and verifies pipeline plausibility. It publishes a read-only Streamlit dashboard powered by persistent storage, keeping your job market intelligence up-to-date automatically.

## Architecture

```
Trigger → Orchestrator → sub-agents → Verifier → storage → dashboard
```

- **Trigger**: Starts execution on a schedule (GitHub Actions daily cron or local scheduler) or via manual dispatch.
- **Orchestrator**: Reads system state (`SystemState`), builds a dynamic execution plan, delegates work to sub-agents, and logs cycle summary metrics. It never fetches or scores directly.
- **Sub-agents**: Focused execution units (`Fetcher`, `Scorer`, `GapAnalyzer`, `Extractor`) with single goals and explicit stop conditions.
- **Verifier**: Asserts output plausibility across score distributions, extraction sanity, gap sample sizes, and data freshness.
- **Storage**: Unified database abstraction layer supporting SQLite and PostgreSQL.
- **Dashboard**: Read-only Streamlit view displaying verified market insights, live health status, and cycle logs.

## Why Key Design Decisions Were Made

- **Storage Behind One Module**: All storage logic is encapsulated behind a single interface (`edgedash/storage_factory.py`). Business logic and agents never import `sqlite3` or `psycopg2` directly. This enables a zero-friction, one-file swap between local SQLite and hosted PostgreSQL without changing a single line of agent code.
- **No Model-Generated SQL**: The natural language query engine (`edgedash/query/`) prohibits text-to-SQL. Allowing LLMs to compose raw SQL introduces severe prompt injection vectors, non-deterministic database mutations, and silent query failures. Instead, the model acts strictly as a router selecting from a fixed registry of parameterised, typed query functions written in Python.
- **The Verifier Cannot Repair**: The `Verifier` evaluates output plausibility and emits a pass/fail verdict with explicit reasons—it *never* rewrites, repairs, or mutates data. Allowing a validator to auto-correct outputs introduces synthetic, unverified state corruption. If verification fails, the Orchestrator executes at most one controlled retry before marking the cycle degraded.
- **Deterministic Scoring**: Models extract structured facts (skills, seniority, years of experience); scoring arithmetic is 100% deterministic Python. Asking LLMs to output final numerical scores leads to score inflation, hallucinated ratings, and non-reproducible rankings. Separating fact extraction from scoring keeps fit evaluation auditable, transparent, and reproducible.

## Known Limitations

- **Cross-Source Duplicates**: Deduplication relies on a SHA-256 hash of `source + url`. While this reliably prevents duplicate ingestion from the same source, identical job postings listed on multiple distinct job boards will be ingested as separate listings.
- **Extraction Misses**: Fact extraction depends on LLM parsing of unstructured text. Non-standard job description layouts or heavy jargon can occasionally lead to omitted required or nice-to-have skills.
- **Thin Trend Data**: Gap analysis snapshots build longitudinal trend insights over time. On fresh installations or early cycles, skill gap metrics reflect a limited sample size until multiple cycles accumulate historical data.

---

## Quickstart & Usage

### 1. Prerequisites & Installation

**Python:** 3.11 or newer.

```bash
git clone https://github.com/koustavdatascience/edgedash.git
cd edgedash
pip install -r requirements.txt
```

### 2. Configure Environment & Profile

Copy `.env.example` to `.env` and set your LLM API credentials:

```bash
cp .env.example .env
```

Edit `.env` with your API key:
```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_actual_api_key_here
LLM_MODEL=gemini-1.5-flash
```

Customize target role, keywords, skills, and thresholds in `config.yaml`. All user profile settings live in `config.yaml`; no profile values are hardcoded.

### 3. Run a Manual Cycle

```bash
python run_cycle.py
```

Options:
- `python run_cycle.py --dry-run`: Inspect state and execution plan without making state writes or API calls.
- `python run_cycle.py --force scorer`: Force specific sub-agents to run.
- `python run_cycle.py --explain`: Display detailed decision trace for each state variable.

### 4. Health Check

Run the system health check CLI (read-only):

```bash
python -m edgedash.health
```

Evaluates database connectivity, listing freshness ($\le 3$ days), cycle age ($\le 48$ hours), and verification failure trends. Exits `0` if healthy, `1` if unhealthy.

### 5. Launch Dashboard

```bash
streamlit run dashboard.py
```

Features:
- Live health status indicator bar (`🟢 Live`, `🟡 Stale`, `🔴 Critical`)
- Top scored job matches & market skill gaps
- Complete agent activity log
- Natural language query interface (powered by parameterised tools)

### 6. Automated Scheduled Execution (GitHub Actions)

A GitHub Actions workflow is provided in `.github/workflows/cycle.yml`:
- **Schedule**: Daily at `06:00 IST` (`00:30 UTC`).
- **Manual Trigger**: Supports `workflow_dispatch` via GitHub Web UI or CLI (`gh workflow run cycle.yml`).
- **CI Safety**: Executes migrations, runs the cycle with a 10-minute timeout, uploads `cycle.log` artifacts, and executes the system health check as a final step.
