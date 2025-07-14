"""
Enhanced Decision Engine Configuration with practical thresholds.

This configuration focuses on essential fields rather than arbitrary coverage percentages.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from receipt_label.decision_engine.config import DecisionEngineConfig


@dataclass
class EnhancedDecisionEngineConfig(DecisionEngineConfig):
    """
    Enhanced configuration that prioritizes essential fields over coverage metrics.

    Key changes:
    - Merchant name is absolutely required
    - Removed impractical 5-word unlabeled limit
    - Focus on essential fields rather than coverage percentage
    - Category-aware thresholds
    """

    # Essential field requirements (these are MUST HAVE)
    require_merchant_name: bool = field(default=True)
    require_date: bool = field(default=True)
    require_total: bool = field(default=True)
    require_at_least_one_item: bool = field(default=False)  # Nice to have

    # Practical thresholds
    min_meaningful_words: int = field(
        default=20
    )  # Receipts with less than this are probably errors

    # Override parent's impractical thresholds
    max_unlabeled_words: int = field(
        default_factory=lambda: int(
            os.getenv("DECISION_ENGINE_MAX_UNLABELED", "100")
        )
    )

    # Coverage is less important than having key fields
    min_coverage_percentage: float = field(
        default_factory=lambda: float(
            os.getenv("DECISION_ENGINE_MIN_COVERAGE", "30.0")
        )
    )

    # Category-specific overrides
    category_thresholds = {
        "Grocery Store": {
            "min_coverage": 20.0,  # Grocery receipts have many items
            "max_unlabeled": 200,
            "require_items": True,
        },
        "Restaurant": {
            "min_coverage": 40.0,  # Restaurants have fewer items
            "max_unlabeled": 50,
            "require_items": True,
        },
        "Gas Station": {
            "min_coverage": 50.0,  # Gas receipts are simple
            "max_unlabeled": 30,
            "require_items": False,  # Just need fuel amount
        },
        "Pharmacy": {
            "min_coverage": 40.0,
            "max_unlabeled": 60,
            "require_items": True,
        },
    }

    def get_thresholds_for_category(self, category: Optional[str]) -> dict:
        """Get thresholds adjusted for merchant category."""
        if category and category in self.category_thresholds:
            return self.category_thresholds[category]

        # Default thresholds
        return {
            "min_coverage": self.min_coverage_percentage,
            "max_unlabeled": self.max_unlabeled_words,
            "require_items": self.require_at_least_one_item,
        }

    def validate_essential_fields(
        self, found_fields: dict
    ) -> tuple[bool, list[str]]:
        """
        Check if all required essential fields are found.

        Returns:
            (all_found, missing_fields)
        """
        missing = []

        if self.require_merchant_name and not found_fields.get(
            "MERCHANT_NAME"
        ):
            missing.append("MERCHANT_NAME")

        if self.require_date and not found_fields.get("DATE"):
            missing.append("DATE")

        if self.require_total and not found_fields.get("GRAND_TOTAL"):
            missing.append("GRAND_TOTAL")

        return len(missing) == 0, missing

    def make_practical_decision(
        self,
        essential_fields: dict,
        coverage_percentage: float,
        unlabeled_count: int,
        merchant_category: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        Make a practical decision based on what really matters.

        Returns:
            (decision, reasoning)
        """
        # Get category-specific thresholds
        thresholds = self.get_thresholds_for_category(merchant_category)

        # Check essential fields
        all_essential, missing = self.validate_essential_fields(
            essential_fields
        )

        # Decision logic
        if not essential_fields.get("MERCHANT_NAME"):
            # Merchant is absolutely required
            return (
                "REQUIRED",
                "Missing merchant name - cannot process without knowing the merchant",
            )

        if all_essential:
            # We have all essential fields
            if coverage_percentage >= thresholds["min_coverage"]:
                return (
                    "SKIP",
                    f"All essential fields found with {coverage_percentage:.1f}% coverage",
                )
            else:
                # Low coverage but have essentials - good for batch
                return (
                    "BATCH",
                    f"Essential fields found but low coverage ({coverage_percentage:.1f}%)",
                )

        # Missing some essential fields
        if len(missing) == 1 and missing[0] == "GRAND_TOTAL":
            # Only missing total - might be OK for batch
            return (
                "BATCH",
                "Missing only grand total - suitable for batch processing",
            )

        # Missing critical fields
        return ("REQUIRED", f'Missing essential fields: {", ".join(missing)}')


def create_practical_config() -> EnhancedDecisionEngineConfig:
    """Create a decision engine config with practical thresholds."""
    return EnhancedDecisionEngineConfig(
        enabled=True,
        rollout_percentage=100.0,
        # Focus on essential fields, not arbitrary coverage
        require_merchant_name=True,
        require_date=True,
        require_total=True,
        # Practical thresholds
        min_coverage_percentage=30.0,  # Much more realistic
        max_unlabeled_words=100,  # Receipts have many words
        # Enable smart features
        enable_adaptive_thresholds=True,
    )
