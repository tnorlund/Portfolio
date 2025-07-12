"""
Configuration constants for the Decision Engine.

This module contains configurable thresholds and parameters used by the
decision engine for making GPT processing decisions.
"""

import os
from typing import Optional


class DecisionEngineConfig:
    """Configuration for decision engine thresholds and behavior."""
    
    # Threshold for meaningful unlabeled words
    # If a receipt has fewer than this many meaningful unlabeled words after
    # pattern detection, it can SKIP GPT processing
    MEANINGFUL_UNLABELED_WORDS_THRESHOLD: int = int(
        os.getenv("DECISION_ENGINE_UNLABELED_THRESHOLD", "5")
    )
    
    # Minimum confidence threshold for pattern detection results
    # Patterns with confidence below this are not considered reliable
    MIN_PATTERN_CONFIDENCE: float = float(
        os.getenv("DECISION_ENGINE_MIN_CONFIDENCE", "0.7")
    )
    
    # Maximum percentage of missing core essential labels to allow BATCH
    # If more than this percentage of core labels are missing, require immediate GPT
    MAX_CORE_MISSING_PERCENTAGE: float = float(
        os.getenv("DECISION_ENGINE_MAX_CORE_MISSING", "0.5")
    )
    
    # Noise word length threshold
    # Words shorter than this are considered potential noise
    NOISE_WORD_MIN_LENGTH: int = int(
        os.getenv("DECISION_ENGINE_NOISE_MIN_LENGTH", "2")
    )
    
    # Common noise patterns to ignore
    NOISE_PATTERNS = {
        # Single characters and symbols
        r'^[a-z]$',  # Single letters
        r'^[0-9]$',  # Single digits
        r'^[^\w\s]+$',  # Only special characters
        
        # Common receipt noise
        r'^-+$',  # Dashes
        r'^\*+$',  # Asterisks
        r'^=+$',  # Equals signs
        r'^\|+$',  # Vertical bars
        
        # Formatting artifacts
        r'^\.+$',  # Dots
        r'^_+$',  # Underscores
    }
    
    # Merchant-specific configuration
    # Whether to use merchant-specific essential label requirements
    USE_MERCHANT_SPECIFIC_LABELS: bool = os.getenv(
        "DECISION_ENGINE_USE_MERCHANT_LABELS", "true"
    ).lower() == "true"
    
    # Shadow testing configuration
    # Percentage of SKIP/BATCH decisions to validate with shadow testing
    SHADOW_TEST_PERCENTAGE: float = float(
        os.getenv("DECISION_ENGINE_SHADOW_TEST_PCT", "0.05")
    )
    
    @classmethod
    def get_config_summary(cls) -> dict:
        """Get a summary of current configuration values."""
        return {
            "meaningful_unlabeled_words_threshold": cls.MEANINGFUL_UNLABELED_WORDS_THRESHOLD,
            "min_pattern_confidence": cls.MIN_PATTERN_CONFIDENCE,
            "max_core_missing_percentage": cls.MAX_CORE_MISSING_PERCENTAGE,
            "noise_word_min_length": cls.NOISE_WORD_MIN_LENGTH,
            "use_merchant_specific_labels": cls.USE_MERCHANT_SPECIFIC_LABELS,
            "shadow_test_percentage": cls.SHADOW_TEST_PERCENTAGE,
        }
    
    @classmethod
    def validate_config(cls) -> Optional[str]:
        """
        Validate configuration values.
        
        Returns:
            Error message if validation fails, None if valid
        """
        errors = []
        
        if cls.MEANINGFUL_UNLABELED_WORDS_THRESHOLD < 1:
            errors.append("MEANINGFUL_UNLABELED_WORDS_THRESHOLD must be at least 1")
        
        if not 0 <= cls.MIN_PATTERN_CONFIDENCE <= 1:
            errors.append("MIN_PATTERN_CONFIDENCE must be between 0 and 1")
        
        if not 0 <= cls.MAX_CORE_MISSING_PERCENTAGE <= 1:
            errors.append("MAX_CORE_MISSING_PERCENTAGE must be between 0 and 1")
        
        if cls.NOISE_WORD_MIN_LENGTH < 1:
            errors.append("NOISE_WORD_MIN_LENGTH must be at least 1")
        
        if not 0 <= cls.SHADOW_TEST_PERCENTAGE <= 1:
            errors.append("SHADOW_TEST_PERCENTAGE must be between 0 and 1")
        
        return "; ".join(errors) if errors else None


# Create a singleton instance for easy import
decision_engine_config = DecisionEngineConfig()