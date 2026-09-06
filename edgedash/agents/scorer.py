from __future__ import annotations

import statistics
from datetime import datetime, timezone
from types import ModuleType
from typing import TYPE_CHECKING

from edgedash.agents.base import Agent, AgentResult
from edgedash.config import Config

if TYPE_CHECKING:
    from edgedash.planning import StopConditions

# No llm / network imports here — pure deterministic file imports only.


class Scorer(Agent):
    @property
    def name(self) -> str:
        return "scorer"

    def run(
        self,
        config:          Config,
        storage:         ModuleType,
        stop_conditions: "StopConditions | None" = None,
    ) -> AgentResult:
        from edgedash.planning import StopConditions as _SC
        sc = stop_conditions or _SC()
        # Orchestrator sets max_items; fall back to config for backwards compat
        batch_size = sc.max_items if sc.max_items is not None else getattr(config, "score_batch_size", 25)
        listings = storage.get_unscored_listings(batch_size)

        if not listings:
            return AgentResult(agent=self.name, status="ok", records_touched=0, notes="no unscored listings")

        from edgedash.agents.extractor import extract
        from edgedash.scoring import score_listing

        scored = 0
        failed = 0
        scores: list[int] = []

        for listing in listings:
            try:
                facts = extract(listing, config, storage)
                result = score_listing(listing, facts, config)
                storage.update_listing_score(
                    listing["id"], result["score"], result["reason"], result["components"]
                )
                scores.append(int(result["score"]))
                scored += 1
            except Exception as exc:
                # per-listing isolation per rule 17 — log and continue
                failed += 1
                now = datetime.now(timezone.utc).isoformat()
                try:
                    storage.log_cycle(
                        "scorer:listing",
                        now,
                        now,
                        0,
                        "failed",
                        f"listing {listing.get('id','?')}: {exc}",
                    )
                except Exception:
                    pass
                continue

        # distribution per rule 20
        if scores:
            s_min = min(scores)
            s_max = max(scores)
            s_mean = int(round(statistics.mean(scores)))
            spread = s_max - s_min
            suspect = spread < 10
            status = "suspect" if suspect else "ok"
            dist_notes = f"range {s_min}-{s_max} · mean {s_mean} · spread {spread}"
            if suspect:
                dist_notes += " · SUSPECT (spread < 10)"
            # log distribution to cycle_log per rule 20
            now = datetime.now(timezone.utc).isoformat()
            try:
                storage.log_cycle(
                    "scorer:distribution",
                    now,
                    now,
                    len(scores),
                    status,
                    f"count {len(scores)} · min {s_min} · max {s_max} · mean {s_mean} · spread {spread}",
                )
            except Exception:
                pass
        else:
            s_min = s_max = s_mean = spread = 0
            status = "ok"
            dist_notes = "no scores"

        spread_label = "suspect" if (scores and (max(scores) - min(scores) < 10)) else "spread OK"
        notes = f"scored {scored} · range {s_min}-{s_max} · mean {s_mean} · {failed} failed · {spread_label}"
        if scores:
            notes += f" · {dist_notes}"

        return AgentResult(
            agent=self.name,
            status=status if status == "suspect" else "ok",
            records_touched=scored,
            notes=notes,
        )
