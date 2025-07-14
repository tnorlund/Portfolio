"""Pattern detection module for receipt processing.

This module provides high-performance pattern detection for various receipt elements
including currency, dates/times, contact information, and quantities.

PHASE 2-3 ENHANCEMENTS:
- Centralized pattern configuration and utilities
- Selective detector invocation based on content analysis
- Batch regex evaluation using alternation patterns
- True CPU parallelism with ThreadPoolExecutor
- Trie-based multi-word pattern detection (Aho-Corasick)
- Optimized keyword lookups with pattern automata
- Merchant-specific pattern integration
- Unified pattern engine combining all optimizations

For optimal performance, use the enhanced orchestrator:
    from receipt_label.pattern_detection import detect_patterns_optimized
"""

# Legacy individual detectors (Phase 0-1)
from receipt_label.pattern_detection.base import (
    PatternDetector,
    PatternMatch,
    PatternType,
)
from receipt_label.pattern_detection.contact import ContactPatternDetector
from receipt_label.pattern_detection.currency import CurrencyPatternDetector
from receipt_label.pattern_detection.datetime_patterns import (
    DateTimePatternDetector,
)
from receipt_label.pattern_detection.orchestrator import (
    ParallelPatternOrchestrator,
)
from receipt_label.pattern_detection.quantity import QuantityPatternDetector

# Enhanced pattern detection (Phase 2-3)
from receipt_label.pattern_detection.enhanced_orchestrator import (
    EnhancedPatternOrchestrator,
    detect_patterns_optimized,
    compare_optimization_performance,
    get_optimization_capabilities,
    OptimizationLevel,
    ENHANCED_ORCHESTRATOR,
)

# Specialized engines for advanced use cases
from receipt_label.pattern_detection.unified_pattern_engine import (
    UNIFIED_PATTERN_ENGINE,
)
from receipt_label.pattern_detection.parallel_engine import (
    OPTIMIZED_PATTERN_DETECTOR,
)

__all__ = [
    # Core types and base classes
    "PatternDetector",
    "PatternMatch", 
    "PatternType",
    
    # Legacy individual detectors (backward compatibility)
    "CurrencyPatternDetector",
    "DateTimePatternDetector",
    "ContactPatternDetector",
    "QuantityPatternDetector",
    "ParallelPatternOrchestrator",
    
    # Enhanced detection (recommended for new code)
    "EnhancedPatternOrchestrator",
    "detect_patterns_optimized",
    "compare_optimization_performance", 
    "get_optimization_capabilities",
    "OptimizationLevel",
    "ENHANCED_ORCHESTRATOR",
    
    # Specialized engines
    "UNIFIED_PATTERN_ENGINE",
    "OPTIMIZED_PATTERN_DETECTOR",
]
