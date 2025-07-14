"""Smart Decision Engine Configuration.

This module provides configuration classes for the Smart Decision Engine,
including static thresholds and feature flags for Phase 1 implementation.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Set


@dataclass
class DecisionEngineConfig:
    """Configuration for the Smart Decision Engine.

    Phase 1 focuses on static thresholds and conservative decision making.
    All thresholds can be overridden via environment variables for easy tuning.
    """

    # === Core Decision Thresholds ===

    # Coverage thresholds - what percentage of words must be labeled
    min_coverage_percentage: float = field(
        default_factory=lambda: float(
            os.getenv("DECISION_ENGINE_MIN_COVERAGE", "90.0")
        )
    )

    # Maximum unlabeled meaningful words to allow skipping GPT
    max_unlabeled_words: int = field(
        default_factory=lambda: int(
            os.getenv("DECISION_ENGINE_MAX_UNLABELED", "5")
        )
    )

    # Minimum confidence for pattern matches to trust them
    min_pattern_confidence: float = field(
        default_factory=lambda: float(
            os.getenv("DECISION_ENGINE_MIN_CONFIDENCE", "0.7")
        )
    )

    # === Essential Fields Configuration ===

    # Always require these fields - never skip GPT without them
    required_essential_fields: Set[str] = field(
        default_factory=lambda: {"MERCHANT_NAME", "DATE", "GRAND_TOTAL"}
    )

    # Prefer these fields but can batch process without them
    preferred_essential_fields: Set[str] = field(
        default_factory=lambda: {"PRODUCT_NAME"}  # At least one product name
    )

    # === Merchant Reliability Settings ===

    # Minimum receipts needed to consider merchant "known"
    min_receipts_for_reliability: int = field(
        default_factory=lambda: int(
            os.getenv("DECISION_ENGINE_MIN_RECEIPTS", "5")
        )
    )

    # Success rate threshold for "reliable" merchants
    reliable_merchant_threshold: float = field(
        default_factory=lambda: float(
            os.getenv("DECISION_ENGINE_RELIABLE_THRESHOLD", "0.8")
        )
    )

    # Unknown merchants - be conservative
    unknown_merchant_strategy: str = field(
        default_factory=lambda: os.getenv(
            "DECISION_ENGINE_UNKNOWN_STRATEGY", "conservative"
        )
    )  # Options: "conservative", "moderate", "aggressive"

    # === Performance Settings ===

    # Maximum time allowed for decision (milliseconds)
    max_decision_time_ms: float = field(
        default_factory=lambda: float(
            os.getenv("DECISION_ENGINE_MAX_TIME_MS", "10.0")
        )
    )

    # Enable/disable Pinecone lookups during decision
    enable_pinecone_validation: bool = field(
        default_factory=lambda: os.getenv(
            "DECISION_ENGINE_PINECONE_ENABLED", "true"
        ).lower()
        == "true"
    )

    # Maximum Pinecone queries per decision
    max_pinecone_queries: int = field(
        default_factory=lambda: int(
            os.getenv("DECISION_ENGINE_MAX_PINECONE_QUERIES", "2")
        )
    )

    # === Feature Flags ===

    # Master enable/disable for entire decision engine
    enabled: bool = field(
        default_factory=lambda: os.getenv(
            "DECISION_ENGINE_ENABLED", "false"
        ).lower()
        == "true"
    )

    # Enable adaptive thresholds (Phase 2 feature)
    enable_adaptive_thresholds: bool = field(
        default_factory=lambda: os.getenv(
            "DECISION_ENGINE_ADAPTIVE", "false"
        ).lower()
        == "true"
    )

    # Enable A/B testing mode
    enable_ab_testing: bool = field(
        default_factory=lambda: os.getenv(
            "DECISION_ENGINE_AB_TEST", "false"
        ).lower()
        == "true"
    )

    # Percentage of traffic to apply decision engine (for gradual rollout)
    rollout_percentage: float = field(
        default_factory=lambda: float(
            os.getenv("DECISION_ENGINE_ROLLOUT_PCT", "0.0")
        )
    )

    # === Debug and Monitoring ===

    # Enable detailed decision logging
    enable_detailed_logging: bool = field(
        default_factory=lambda: os.getenv(
            "DECISION_ENGINE_DEBUG_LOGGING", "false"
        ).lower()
        == "true"
    )

    # Log all decisions (vs only skip decisions)
    log_all_decisions: bool = field(
        default_factory=lambda: os.getenv(
            "DECISION_ENGINE_LOG_ALL", "false"
        ).lower()
        == "true"
    )

    # Enable performance timing
    enable_performance_timing: bool = field(
        default_factory=lambda: os.getenv(
            "DECISION_ENGINE_TIMING", "true"
        ).lower()
        == "true"
    )

    def get_coverage_threshold_for_merchant(
        self, merchant_reliability: Optional[float] = None
    ) -> float:
        """Get coverage threshold, potentially adjusted for merchant reliability.

        In Phase 1, this returns the static threshold. Phase 2 will make this dynamic.
        """
        if not self.enable_adaptive_thresholds or merchant_reliability is None:
            return self.min_coverage_percentage

        # Phase 2: Adjust based on merchant reliability
        # For now, just return static value
        return self.min_coverage_percentage

    def get_unlabeled_threshold_for_merchant(
        self, merchant_reliability: Optional[float] = None
    ) -> int:
        """Get max unlabeled words threshold, potentially adjusted for merchant reliability."""
        if not self.enable_adaptive_thresholds or merchant_reliability is None:
            return self.max_unlabeled_words

        # Phase 2: Adjust based on merchant reliability
        # For now, just return static value
        return self.max_unlabeled_words

    def should_process_receipt(self) -> bool:
        """Determine if this receipt should be processed by decision engine.

        Used for gradual rollout - returns True if this receipt should use
        the decision engine based on rollout percentage.
        """
        if not self.enabled:
            return False

        if self.rollout_percentage >= 100.0:
            return True

        if self.rollout_percentage <= 0.0:
            return False

        # Simple randomization - in production might use receipt ID hash
        import random

        return random.random() * 100.0 < self.rollout_percentage

    def validate(self) -> None:
        """Validate configuration values are reasonable."""
        if not 0.0 <= self.min_coverage_percentage <= 100.0:
            raise ValueError(
                f"min_coverage_percentage must be 0-100, got {self.min_coverage_percentage}"
            )

        if self.max_unlabeled_words < 0:
            raise ValueError(
                f"max_unlabeled_words must be >= 0, got {self.max_unlabeled_words}"
            )

        if not 0.0 <= self.min_pattern_confidence <= 1.0:
            raise ValueError(
                f"min_pattern_confidence must be 0-1, got {self.min_pattern_confidence}"
            )

        if not 0.0 <= self.reliable_merchant_threshold <= 1.0:
            raise ValueError(
                f"reliable_merchant_threshold must be 0-1, got {self.reliable_merchant_threshold}"
            )

        if not 0.0 <= self.rollout_percentage <= 100.0:
            raise ValueError(
                f"rollout_percentage must be 0-100, got {self.rollout_percentage}"
            )

        if self.max_decision_time_ms <= 0:
            raise ValueError(
                f"max_decision_time_ms must be > 0, got {self.max_decision_time_ms}"
            )

        if self.unknown_merchant_strategy not in {
            "conservative",
            "moderate",
            "aggressive",
        }:
            raise ValueError(
                f"unknown_merchant_strategy must be conservative/moderate/aggressive"
            )


# Default configuration instance
DEFAULT_CONFIG = DecisionEngineConfig()


def create_config_from_env() -> DecisionEngineConfig:
    """Create configuration from environment variables.

    This allows easy overriding of defaults for different environments
    (dev, staging, production) without code changes.
    """
    config = DecisionEngineConfig()
    config.validate()
    return config


def create_conservative_config() -> DecisionEngineConfig:
    """Create a conservative configuration for initial rollout.

    Uses stricter thresholds to minimize risk of missing labels.
    """
    return DecisionEngineConfig(
        enabled=True,  # Enable the decision engine
        min_coverage_percentage=95.0,  # Higher coverage required
        max_unlabeled_words=3,  # Fewer unlabeled words allowed
        min_pattern_confidence=0.8,  # Higher confidence required
        reliable_merchant_threshold=0.9,  # Stricter reliability requirement
        unknown_merchant_strategy="conservative",
        rollout_percentage=10.0,  # Start with 10% of traffic
    )


def create_aggressive_config() -> DecisionEngineConfig:
    """Create an aggressive configuration for maximum cost savings.

    Uses more lenient thresholds to skip GPT more often.
    WARNING: Only use after extensive testing!
    """
    return DecisionEngineConfig(
        enabled=True,  # Enable the decision engine
        min_coverage_percentage=85.0,  # Lower coverage acceptable
        max_unlabeled_words=8,  # More unlabeled words allowed
        min_pattern_confidence=0.6,  # Lower confidence acceptable
        reliable_merchant_threshold=0.7,  # More lenient reliability
        unknown_merchant_strategy="moderate",
        rollout_percentage=100.0,  # Full rollout
    )
