"""Verifier agent — judges output plausibility, never repairs data (rule 34).

Reads the current cycle's scores, extracted facts, gap snapshot, and
latest fetch time from storage.  Calls run_all_checks (deterministic
Python, no LLM) and returns an AgentResult carrying the Verdict.

Writes NO data other than logging the verdict to cycle_log.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import ModuleType
from typing import TYPE_CHECKING

from edgedash.agents.base import Agent, AgentResult
from edgedash.config import Config
from edgedash.verification import run_all_checks

if TYPE_CHECKING:
    from edgedash.planning import StopConditions


class Verifier(Agent):
    @property
    def name(self) -> str:
        return "verifier"

    def run(
        self,
        config:          Config,
        storage:         ModuleType,
        stop_conditions: "StopConditions | None" = None,
    ) -> AgentResult:
        from edgedash.planning import StopConditions as _SC
        sc = stop_conditions or _SC()
        # max_items = how many recently-scored listings to check
        limit = sc.max_items or 0

        if limit == 0:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="no new listings to verify",
            )

        now = datetime.now(timezone.utc)

        # ------------------------------------------------------------------
        # Gather data from storage (reads only — rule 34)
        # ------------------------------------------------------------------
        scored_listings = storage.get_scored_listings_with_components(limit)
        scores: list[int] = [
            int(r["fit_score"])
            for r in scored_listings
            if r.get("fit_score") is not None
        ]

        facts_list = storage.get_scored_listings_with_extractions(limit)

        # Latest gap snapshot (list[dict] with 'listings_blocked' etc.)
        gaps = storage.get_latest_snapshot(limit=10)

        latest_fetch_at = storage.last_fetch_time()

        # ------------------------------------------------------------------
        # Run all checks (pure functions — no LLM, no clock inside)
        # ------------------------------------------------------------------
        verdict, all_results = run_all_checks(
            scores          = scores,
            facts_list      = facts_list,
            gaps            = gaps,
            latest_fetch_at = latest_fetch_at,
            config          = config,
            now             = now,
        )

        # ------------------------------------------------------------------
        # Build AgentResult notes (rule 37 — name the check and observed value)
        # ------------------------------------------------------------------
        if verdict.passed:
            status = "ok"
            notes  = f"VERDICT: pass — {verdict.summary}"
        else:
            failed_detail = "; ".join(
                f"{r.name} observed {r.observed} (threshold {r.threshold})"
                for r in verdict.failed_checks
            )
            status = "failed"
            notes  = f"VERDICT: fail — {failed_detail}"

        return AgentResult(
            agent           = self.name,
            status          = status,
            records_touched = len(scores),
            notes           = notes,
            # Attach verdict so the Orchestrator can inspect it without
            # re-parsing the notes string.
            extra           = {"verdict": verdict, "all_results": all_results},
        )
