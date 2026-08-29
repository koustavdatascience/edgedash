from __future__ import annotations

from datetime import datetime, timezone
from types import ModuleType

from edgedash.agents.base import Agent, AgentResult
from edgedash.config import Config
from edgedash.storage import ListingInput

_SOURCE = "mock"
_STABLE_SUFFIXES = ("flipkart-da", "swiggy-da", "razorpay-sr-da", "cred-da")


class MockFetcher(Agent):
    @property
    def name(self) -> str:
        return "fetcher"

    def run(self, config: Config, storage: ModuleType) -> AgentResult:
        fetched_at = datetime.now(timezone.utc).isoformat()
        rows = _build_listings(config, fetched_at)
        new_count = storage.upsert_listings(rows)
        duplicate_count = len(rows) - new_count
        notes = (
            f"Submitted {len(rows)} listings; "
            f"{new_count} new, {duplicate_count} duplicate(s) ignored"
        )
        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=new_count,
            notes=notes,
        )


def _build_listings(config: Config, fetched_at: str) -> list[ListingInput]:
    role = config.target_role
    city = config.target_city
    templates = [
        ("Junior Data Analyst", "Flipkart", "entry",
         "Build SQL dashboards and Excel reports. Python and pandas for ad-hoc analysis."),
        ("Data Analyst", "Swiggy", "mid",
         "Own weekly reporting in Tableau. SQL joins across warehouse tables; data cleaning in Python."),
        ("Senior Data Analyst", "Razorpay", "senior",
         "Lead funnel analytics. Advanced SQL, Python, stakeholder communication with product teams."),
        ("Data Analyst", "CRED", "mid",
         "Experiment readouts and cohort analysis. Excel models, SQL, and Power BI dashboards."),
        ("Analytics Associate", "PhonePe", "entry",
         "Support payments reporting. SQL queries, Excel pivots, and basic Python scripts."),
        ("BI Analyst", "Meesho", "mid",
         "Maintain Tableau workbooks. SQL + data cleaning pipelines for marketplace metrics."),
        ("Data Analyst II", "Freshworks", "mid",
         "SaaS product analytics. Python/pandas, SQL, and dashboarding for retention KPIs."),
        ("Reporting Analyst", "Ola", "mid",
         "Daily ops dashboards. Excel, SQL, and data visualization for mobility trends."),
        ("Product Data Analyst", "Zomato", "senior",
         "Menu and search analytics. SQL, Python, A/B testing, and Tableau storytelling."),
        ("Data Analyst", "Infosys", "mid",
         "Client-facing analytics delivery. SQL, Excel, and Power BI for enterprise reporting."),
        ("Business Analyst (Data)", "Wipro", "mid",
         "Translate business asks into SQL. Data cleaning, pandas, and stakeholder communication."),
        ("Data Analyst", "Deloitte", "senior",
         "Consulting engagements. SQL, Python, Excel financial models, and executive dashboards."),
    ]

    listings: list[ListingInput] = []
    for index, (title, company, seniority, description) in enumerate(templates):
        suffix = _STABLE_SUFFIXES[index] if index < len(_STABLE_SUFFIXES) else f"cycle-{index}"
        full_title = f"{title} — {role}" if seniority != "entry" else f"{title} ({role})"
        listings.append(
            ListingInput(
                title=full_title,
                company=company,
                location=f"{city}, India",
                url=f"https://mock.edgedash/jobs/{suffix}",
                description=description,
                source=_SOURCE,
                posted_at="2026-08-20",
                fetched_at=fetched_at,
            )
        )
    return listings
