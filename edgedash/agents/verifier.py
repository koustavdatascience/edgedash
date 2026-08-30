from __future__ import annotations

import json
from datetime import datetime, timezone
from types import ModuleType

from edgedash.agents.base import Agent, AgentResult
from edgedash.config import Config


class Verifier(Agent):
    @property
    def name(self) -> str:
        return "verifier"

    def run(self, config: Config, storage: ModuleType, newly_scored: int = 0) -> AgentResult:
        """Verify data quality and validate outputs from the current cycle.
        
        Args:
            config: Configuration object
            storage: Storage module
            newly_scored: Number of listings scored in this cycle (optional)
        """
        if newly_scored == 0:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="no new listings to verify"
            )
        
        # Get recently scored listings for verification
        recent_listings = storage.get_scored_listings_with_components(newly_scored)
        
        if not recent_listings:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="no scored listings found for verification"
            )
        
        verified = 0
        issues = []
        
        for listing in recent_listings:
            listing_issues = self._verify_listing(listing, config)
            if listing_issues:
                issues.extend([f"{listing.get('id', '?')[:8]}: {issue}" for issue in listing_issues])
            else:
                verified += 1
        
        # Verify score distribution
        distribution_issues = self._verify_score_distribution(recent_listings)
        if distribution_issues:
            issues.extend(distribution_issues)
        
        # Build status and notes
        if not issues:
            status = "ok"
            notes = f"verified {verified} listing(s), no issues found"
        else:
            status = "warning"
            notes = f"verified {verified} listing(s), {len(issues)} issue(s): {'; '.join(issues[:3])}"
            if len(issues) > 3:
                notes += f" (and {len(issues) - 3} more)"
        
        return AgentResult(
            agent=self.name,
            status=status,
            records_touched=verified,
            notes=notes
        )
    
    def _verify_listing(self, listing: dict, config: Config) -> list[str]:
        """Verify a single listing for data quality issues."""
        issues = []
        
        # Check required fields
        required_fields = ["id", "title", "company", "location", "url", "description", "source"]
        for field in required_fields:
            if not listing.get(field):
                issues.append(f"missing {field}")
        
        # Check score is valid
        score = listing.get("fit_score")
        if score is not None:
            if not isinstance(score, (int, float)) or score < 0 or score > 100:
                issues.append(f"invalid score {score}")
        
        # Check fit_components is valid JSON
        components = listing.get("fit_components")
        if components:
            if isinstance(components, str):
                try:
                    json.loads(components)
                except json.JSONDecodeError:
                    issues.append("invalid fit_components JSON")
            elif not isinstance(components, dict):
                issues.append("fit_components not dict or JSON string")
        
        # Check reason field exists if score exists
        if score is not None and not listing.get("fit_reason"):
            issues.append("missing fit_reason")
        
        return issues
    
    def _verify_score_distribution(self, listings: list[dict]) -> list[str]:
        """Verify score distribution for anomalies."""
        if not listings:
            return []
        
        scores = [listing.get("fit_score") for listing in listings if listing.get("fit_score") is not None]
        
        if not scores:
            return ["no valid scores found"]
        
        # Check for suspiciously uniform scores (all within 10 points)
        score_range = max(scores) - min(scores)
        if score_range < 10 and len(scores) > 1:
            return [f"suspicious score distribution: range {score_range}"]
        
        # Check for all scores being extreme (all < 20 or all > 80)
        if all(s < 20 for s in scores):
            return ["all scores extremely low (< 20)"]
        if all(s > 80 for s in scores):
            return ["all scores extremely high (> 80)"]
        
        return []
