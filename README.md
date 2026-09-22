# EdgeDash

> Autonomous career intelligence for evidence-driven job searches.

**Live application:** [edgedash-project.streamlit.app](https://edgedash-project.streamlit.app/)

**Repository:** [github.com/koustavdatascience/edgedash](https://github.com/koustavdatascience/edgedash)

EdgeDash is a continuously running career-intelligence pipeline. It fetches live job listings, extracts structured requirements, scores each role against a configurable career profile, identifies high-value skill gaps, and verifies the resulting data before it reaches the dashboard. The application is designed to make job-market research more consistent, explainable, and actionable.

## Highlights

- **Live job intelligence:** Fetches listings from supported sources and keeps the local or hosted database current.
- **Explainable matching:** Produces deterministic fit scores from structured facts rather than asking a language model to invent a final rating.
- **Skill-gap analysis:** Connects missing skills to real listings and preserves timestamped snapshots for trend analysis.
- **Verified outputs:** Runs plausibility checks and allows at most one controlled retry when a verification check fails.
- **Safe natural-language queries:** Uses a fixed registry of typed, parameterised query tools; the language model never generates SQL.
- **Flexible storage:** Uses SQLite by default and supports PostgreSQL for hosted deployments.
- **Automated execution:** Runs on a daily GitHub Actions schedule or through the local scheduler.

## Architecture

```text
Scheduled trigger
      ↓
Orchestrator → Fetcher → Extractor → Scorer
      ↓                         ↓
  Gap analyzer ← Verifier ← Storage
      ↓
Read-only Streamlit dashboard
```

The orchestrator reads system state, creates an execution plan, delegates work to focused agents, records cycle metrics, and coordinates verification. The dashboard reads only verified data and does not run collection or scoring cycles.

## Repository layout

| Path | Purpose |
| --- | --- |
| `dashboard.py` | Streamlit dashboard entry point |
| `run_cycle.py` | Execute one orchestration cycle |
| `run_scheduler.py` | Run the local scheduled worker |
| `edgedash/agents/` | Fetching, extraction, scoring, gap analysis, and verification agents |
| `edgedash/storage.py` | SQLite storage implementation |
| `edgedash/storage_postgres.py` | PostgreSQL storage implementation |
| `edgedash/query/` | Safe, read-only natural-language query tools |
| `tests/` | Automated test suite |
| `.github/workflows/cycle.yml` | Daily and manually triggered GitHub Actions workflow |
| `config.yaml` | Career profile, source, scoring, and verification configuration |
| `landing/` | Optional TypeScript landing-page application |

## Getting started

### Prerequisites

- Python 3.11 or newer
- A supported LLM provider and API key for extraction and query phrasing
- PostgreSQL only when using the hosted database backend

### Installation

```bash
git clone https://github.com/koustavdatascience/edgedash.git
cd edgedash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Create a local environment file and set the provider credentials:

```bash
cp .env.example .env
```

For Gemini, configure the following values in `.env`:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_api_key
LLM_MODEL=gemini-1.5-flash
```

Then customize `config.yaml` with the target role, keywords, skills, scoring weights, thresholds, and database backend. Secrets belong in `.env` or the deployment provider's secret manager and must not be committed.

## Usage

Run one cycle:

```bash
python run_cycle.py
```

Useful options:

```bash
python run_cycle.py --dry-run       # inspect the plan without API calls or writes
python run_cycle.py --force scorer  # force a selected agent to run
python run_cycle.py --explain       # print the state decision trace
```

Launch the dashboard locally:

```bash
streamlit run dashboard.py
```

Run the read-only health check:

```bash
python -m edgedash.health
```

Run the test suite:

```bash
python -m pytest tests/ -q
```

## Automation

The workflow in `.github/workflows/cycle.yml` runs the pipeline daily at **00:30 UTC (06:00 IST)** and can also be started manually with GitHub Actions. It performs database setup, executes the cycle, uploads the cycle log, and runs the health check.

For local automation:

```bash
python run_scheduler.py
```

## Design principles

- **Deterministic scoring:** Language models extract facts; Python performs the scoring arithmetic.
- **Verification without repair:** The verifier reports plausibility failures but never silently rewrites data.
- **Verified data only:** Failed cycles cannot replace the last known-good dashboard state.
- **No model-generated SQL:** Query routing selects from a fixed set of safe, parameterised functions.
- **Auditable aggregates:** Skill-gap metrics retain their source listing IDs and sample sizes.

## Known limitations

Cross-source duplicate detection currently hashes the source and URL, so the same role listed on multiple job boards may appear more than once. Requirement extraction can miss skills when a job description uses unusual terminology or formatting. Trend analysis is also less informative during the first few cycles because it needs historical snapshots.

## Security notes

Never commit `.env`, database credentials, API keys, or generated database files. The natural-language query layer is intentionally read-only and parameterised. Review workflow permissions and deployment secrets before enabling the scheduled job on a fork.

## License

This project does not currently declare a license. Add a license file before distributing or reusing the code outside the repository.
