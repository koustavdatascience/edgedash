from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from types import ModuleType

from edgedash.agents.base import Agent, AgentResult
from edgedash.config import Config


class GapAnalyzer(Agent):
    @property
    def name(self) -> str:
        return "gap_analyzer"

    def run(self, config: Config, storage: ModuleType) -> AgentResult:
        # Get recent scored listings with their components
        # We'll analyze the last 100 scored listings to identify skill gaps
        listings = storage.get_scored_listings_with_components(100)
        
        if not listings:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="no scored listings to analyze"
            )
        
        # Aggregate skill gaps from all listings
        skill_frequency: Counter[str] = Counter()
        
        for listing in listings:
            components = listing.get("fit_components", {})
            if not isinstance(components, dict):
                continue
            
            # The scoring module extracts gaps and stores them in components
            # We need to parse the reason or components to extract missing skills
            # For now, we'll extract from the components if available
            gaps = self._extract_gaps_from_components(components)
            for gap in gaps:
                skill_frequency[gap] += 1
        
        if not skill_frequency:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=len(listings),
                notes=f"analyzed {len(listings)} listing(s), no skill gaps found"
            )
        
        # Update skill_gaps table with new frequencies
        updates = dict(skill_frequency)
        updated_count = storage.upsert_skill_gaps(updates)
        
        # Get current state for reporting
        current_gaps = storage.get_skill_gaps()
        top_gaps = current_gaps[:5] if current_gaps else []
        
        # Build notes
        top_gap_names = [g["skill"] for g in top_gaps]
        notes = (
            f"analyzed {len(listings)} listing(s), "
            f"updated {updated_count} skill gap(s), "
            f"top gaps: {', '.join(top_gap_names)}"
        )
        
        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=updated_count,
            notes=notes
        )
    
    def _extract_gaps_from_components(self, components: dict) -> list[str]:
        """Extract missing skills from scoring components.
        
        The scoring module now stores gaps in the components dict
        under the 'gaps' key as a list of missing required skills.
        """
        gaps = components.get("gaps", [])
        if isinstance(gaps, list):
            return [str(g).strip().lower() for g in gaps if g and str(g).strip()]
        return []
