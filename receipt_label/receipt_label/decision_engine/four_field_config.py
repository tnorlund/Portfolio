"""
Decision Engine Configuration for 4 Required Fields Only.

Focus on: MERCHANT_NAME, DATE, TIME, GRAND_TOTAL
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from receipt_label.decision_engine.config import DecisionEngineConfig


@dataclass
class FourFieldDecisionEngineConfig(DecisionEngineConfig):
    """
    Configuration that only requires 4 essential fields:
    - MERCHANT_NAME
    - DATE
    - TIME
    - GRAND_TOTAL

    If these 4 fields are found, we can skip GPT processing.
    """

    # Override parent's required fields to only our 4
    required_essential_fields: Set[str] = field(
        default_factory=lambda: {
            "MERCHANT_NAME",
            "DATE",
            "TIME",
            "GRAND_TOTAL",
        }
    )

    # Remove PRODUCT_NAME requirement
    preferred_essential_fields: Set[str] = field(
        default_factory=lambda: set()  # No preferred fields beyond required
    )

    # Much more practical thresholds
    min_coverage_percentage: float = field(
        default_factory=lambda: 15.0  # Very low - we only care about 4 fields
    )

    max_unlabeled_words: int = field(
        default_factory=lambda: 200  # Very generous
    )

    # Always enabled for testing
    enabled: bool = field(default=True)
    rollout_percentage: float = field(default=100.0)

    def check_four_fields_only(
        self, essential_fields: Dict[str, bool]
    ) -> Tuple[bool, List[str]]:
        """
        Check if all 4 required fields are present.

        Args:
            essential_fields: Dict mapping field names to whether they're found

        Returns:
            (all_found, missing_fields)
        """
        missing = []

        # Check exactly these 4 fields
        required_fields = ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]

        for field in required_fields:
            if not essential_fields.get(field, False):
                missing.append(field)

        return len(missing) == 0, missing

    def make_four_field_decision(
        self,
        essential_fields: Dict[str, bool],
        coverage_percentage: float,
        unlabeled_count: int,
        merchant_category: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Make decision based ONLY on the 4 required fields.

        Returns:
            (decision, reasoning)
        """

        # Check if we have all 4 fields
        all_four_found, missing = self.check_four_fields_only(essential_fields)

        if all_four_found:
            # We have all 4 required fields - we can skip!
            return (
                "SKIP",
                f"All 4 required fields found: MERCHANT_NAME, DATE, TIME, GRAND_TOTAL",
            )

        # Missing some required fields
        if len(missing) == 1:
            # Only missing one field - might be suitable for batch
            if missing[0] in ["TIME", "GRAND_TOTAL"]:
                return (
                    "BATCH",
                    f"Only missing {missing[0]} - suitable for batch processing",
                )

        # Missing multiple critical fields
        return ("REQUIRED", f'Missing required fields: {", ".join(missing)}')


def create_four_field_config() -> FourFieldDecisionEngineConfig:
    """Create a decision engine config that only requires 4 fields."""
    return FourFieldDecisionEngineConfig(
        enabled=True,
        rollout_percentage=100.0,
        required_essential_fields={
            "MERCHANT_NAME",
            "DATE",
            "TIME",
            "GRAND_TOTAL",
        },
        preferred_essential_fields=set(),  # No additional requirements
        min_coverage_percentage=15.0,  # Low threshold
        max_unlabeled_words=200,  # Generous threshold
        enable_adaptive_thresholds=True,
    )
