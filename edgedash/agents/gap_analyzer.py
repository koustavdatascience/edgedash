"""Gap Analyzer — deterministic, no LLM anywhere in this file.

Reads every scored listing that has extracted facts, computes the
opportunity cost of each missing skill, and writes a timestamped
snapshot. Rules 22–27.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from types import ModuleType
from typing import TYPE_CHECKING, Any

from edgedash.agents.base import Agent, AgentResult
from edgedash.config import Config
from edgedash.skills import canonical

if TYPE_CHECKING:
    from edgedash.planning import StopConditions

# How many gaps to report per snapshot
_TOP_N = 10
# Minimum listings for "high confidence"
_CONFIDENCE_THRESHOLD = 3
# Max example listing IDs stored per gap (rule 26)
_MAX_EXAMPLES = 5


class GapAnalyzer(Agent):
    @property
    def name(self) -> str:
        return "gap_analyzer"

    def run(
        self,
        config:          Config,
        storage:         ModuleType,
        stop_conditions: "StopConditions | None" = None,
    ) -> AgentResult:
        # ----------------------------------------------------------------
        # 1. Load all scored listings that have extraction data
        # ----------------------------------------------------------------
        listings = storage.get_scored_listings_with_extractions(limit=5000)

        if not listings:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="no scored listings to analyze",
            )

        # ----------------------------------------------------------------
        # 2. Build canonical set of skills I already have
        # ----------------------------------------------------------------
        aliases = config.skill_aliases
        my_skills: set[str] = {
            canonical(s, aliases) for s in config.my_skills if s.strip()
        }

        # ----------------------------------------------------------------
        # 3. Accumulate gap data per canonical skill
        # ----------------------------------------------------------------
        # skill -> list of (fit_score, listing_id)
        required_by: dict[str, list[tuple[int, str]]] = defaultdict(list)
        # skill -> count of listings where it appears as nice_to_have only
        nice_to_have_count: dict[str, int] = defaultdict(int)

        for listing in listings:
            score: int = listing["fit_score"]
            lid: str = listing["id"]

            # Required skills — gaps only
            for raw in listing.get("required_skills") or []:
                skill = canonical(raw, aliases)
                if not skill:
                    continue
                if skill not in my_skills:
                    required_by[skill].append((score, lid))

            # Nice-to-have — tracked separately, never mixed into required
            for raw in listing.get("nice_to_have") or []:
                skill = canonical(raw, aliases)
                if not skill:
                    continue
                if skill not in my_skills:
                    nice_to_have_count[skill] += 1

        # ----------------------------------------------------------------
        # 4. Compute metrics per gap skill
        # ----------------------------------------------------------------
        # opportunity_cost = sum(score / 100) for all blocked listings
        # This weights gaps by listing quality, not raw frequency (rule 24)
        gaps: list[dict[str, Any]] = []

        for skill, entries in required_by.items():
            entries_sorted = sorted(entries, key=lambda x: x[0], reverse=True)
            scores = [s for s, _ in entries_sorted]

            listings_blocked = len(scores)
            opportunity_cost = sum(s / 100.0 for s in scores)
            mean_score = sum(scores) / listings_blocked
            top_score = scores[0]
            example_ids = [lid for _, lid in entries_sorted[:_MAX_EXAMPLES]]
            also_nice = nice_to_have_count.get(skill, 0)
            low_confidence = listings_blocked < _CONFIDENCE_THRESHOLD

            gaps.append({
                "skill":            skill,
                "listings_blocked": listings_blocked,
                "opportunity_cost": round(opportunity_cost, 3),
                "mean_score":       round(mean_score, 1),
                "top_score":        top_score,
                "also_nice_to_have": also_nice,
                "low_confidence":   low_confidence,
                "example_ids":      example_ids,
            })

        # ----------------------------------------------------------------
        # 5. Rank by opportunity_cost, take top N
        # ----------------------------------------------------------------
        gaps.sort(key=lambda g: g["opportunity_cost"], reverse=True)
        top_gaps = gaps[:_TOP_N]

        # ----------------------------------------------------------------
        # 6. Write timestamped snapshot (rule 25 — never overwrite)
        # ----------------------------------------------------------------
        run_id = str(uuid.uuid4())
        computed_at = datetime.now(timezone.utc).isoformat()
        storage.write_gap_snapshot(run_id, computed_at, top_gaps)

        # ----------------------------------------------------------------
        # 7. Build AgentResult notes
        # ----------------------------------------------------------------
        n_gaps = len(top_gaps)
        n_analysed = len(listings)

        if top_gaps:
            top = top_gaps[0]
            top_desc = (
                f"top: {top['skill']} "
                f"({top['listings_blocked']} listings, "
                f"cost {top['opportunity_cost']:.1f})"
            )
        else:
            top_desc = "no gaps found"

        notes = f"{n_gaps} gaps · {top_desc} · {n_analysed} listings analysed"

        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=n_gaps,
            notes=notes,
        )
