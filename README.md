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

**Python:** 3.11 or newer.

```bash
git clone <repo-url>
cd ai-career-intelligence-agent
```

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy the example environment file and add your LLM API credentials:

```bash
cp .env.example .env
```

Edit `.env` with your chosen LLM provider:

**For Gemini (Google AI):**
```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_actual_api_key_here
LLM_MODEL=gemini-1.5-flash
```

**For Ollama (local):**
```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=llama3
```

### 3. Configure Your Profile

Edit `config.yaml` at the repo root with your target role, city, keywords, skills, and thresholds. All user-specific values live here; nothing is hardcoded in code.

### 4. Run a Manual Cycle

```bash
python run_cycle.py
```

This will:
- Fetch job listings from configured sources
- Extract structured facts using the LLM
- Score each listing for fit against your profile
- Analyze skill gaps across the market
- Verify data quality
- Log results to the database

Run it twice to confirm deduplication: the first cycle inserts new listings; the second reports duplicates ignored.

### 5. Run Scheduled Automation (Optional)

For continuous automated operation, use the scheduler:

```bash
python run_scheduler.py
```

Configure the schedule interval in `config.yaml`:
- `hourly` - Run every hour
- `daily` - Run once per day
- `weekly` - Run once per week
- `every_30_minutes` - Custom interval
- `every_2_hours` - Custom interval

The scheduler runs an initial cycle immediately, then follows the configured interval. Press Ctrl+C to stop.

### 6. View Dashboard (Optional)

For a visual overview of your job market intelligence:

```bash
streamlit run dashboard.py
```

The dashboard provides:
- **Job Matches**: Top job listings with fit scores and detailed analysis
- **Skill Gaps**: Analysis of missing skills across the market
- **Cycle History**: Log of all automated cycles and their results
- **Statistics**: Overview of database status and system health

### 7. Migrate to Postgres (Optional)

For production deployment with hosted Postgres:

1. Set up a Postgres database and get the connection URL
2. Add the DATABASE_URL to your `.env` file:
   ```env
   DATABASE_URL=postgresql://user:password@host:5432/database_name
   ```
3. Change the database backend in `config.yaml`:
   ```yaml
   db_backend: postgres
   ```
4. Install the Postgres dependency:
   ```bash
   pip install psycopg2-binary
   ```

The system will automatically use the Postgres storage module with the same interface - no code changes required.

## Design decisions

**Isolated storage module.** Every database call goes through `edgedash/storage.py`. No other module imports `sqlite3`. When we move to Postgres in week 4, only that file changes.

**Stable listing IDs.** Each listing ID is a SHA-256 hash of `source + url`. The same job from the same source always maps to the same row, so `INSERT OR IGNORE` deduplicates cleanly and `upsert_listings` can return an accurate count of genuinely new rows.

**Orchestrator delegates.** The Orchestrator reads state, decides which agents to run, and logs results. It does not fetch listings or compute scores itself. That keeps each agent independently testable and lets you swap Mock Fetcher for a real one by changing one line in the registry.

**Environment-based configuration.** Sensitive values (API keys) are loaded from environment variables via a `.env` file. The `.env.example` template shows required variables. This keeps secrets out of code and git.

**LLM abstraction.** All LLM calls go through `edgedash/llm.py` with a single `complete_json()` function. Provider and model are configured via environment variables, supporting Gemini and Ollama. Rate limiting (1 req/s, 15 req/min) is built-in to stay within free tiers.

**Storage backend abstraction.** Storage access goes through `edgedash/storage_factory.py` which selects between SQLite (default) and Postgres based on `db_backend` config. This enables one-file migration from SQLite to hosted Postgres by changing a single config value and setting the `DATABASE_URL` environment variable.
