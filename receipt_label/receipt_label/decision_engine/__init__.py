"""Smart Decision Engine for Receipt Labeling System.

This package contains the intelligent decision-making logic that determines when
pattern detection alone is sufficient versus when GPT assistance is needed.

The goal is to achieve an 84% reduction in GPT API costs while maintaining
>95% labeling accuracy by using a pattern-first strategy.

Key Components:
- DecisionEngine: Core decision-making logic
- DecisionEngineConfig: Configurable thresholds and parameters
- DecisionResult: Structured decision output with reasoning
- Integration with existing pattern detection and Pinecone infrastructure

Usage:
    from receipt_label.decision_engine import DecisionEngine, DecisionEngineConfig

    config = DecisionEngineConfig()
    engine = DecisionEngine(config)
    decision = await engine.decide(pattern_results, merchant_reliability)
"""

from .config import (
    DecisionEngineConfig,
    create_aggressive_config,
    create_config_from_env,
    create_conservative_config,
)
from .core import DecisionEngine
from .integration import (
    DecisionEngineIntegrationResult,
    DecisionEngineOrchestrator,
    process_receipt_with_decision_engine,
)
from .pinecone_integration import PineconeDecisionHelper
from .types import (
    ConfidenceLevel,
    DecisionOutcome,
    DecisionResult,
    EssentialFieldsStatus,
    MerchantReliabilityData,
    PatternDetectionSummary,
)

__version__ = "0.1.0"
__all__ = [
    # Core classes
    "DecisionEngine",
    "DecisionEngineConfig",
    # Configuration helpers
    "create_config_from_env",
    "create_conservative_config",
    "create_aggressive_config",
    # Data types
    "DecisionResult",
    "DecisionOutcome",
    "ConfidenceLevel",
    "EssentialFieldsStatus",
    "PatternDetectionSummary",
    "MerchantReliabilityData",
    # Pinecone integration
    "PineconeDecisionHelper",
    # Integration layer
    "DecisionEngineOrchestrator",
    "DecisionEngineIntegrationResult",
    "process_receipt_with_decision_engine",
]
