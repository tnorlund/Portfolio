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
    engine = DecisionEngine(config, pinecone_client)
    decision = await engine.decide(pattern_results, merchant_name)
"""

# Core exports will be added as components are implemented
__version__ = "0.1.0"
__all__ = []

# TODO: Add exports as components are implemented in phases:
# Phase 1: DecisionEngine, DecisionEngineConfig, DecisionResult, DecisionOutcome
# Phase 2: MerchantReliabilityScore, AdaptiveThresholds  
# Phase 3: FeedbackLoop, PerformanceMonitor