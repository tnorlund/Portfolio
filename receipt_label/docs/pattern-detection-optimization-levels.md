# Pattern Detection Optimization Levels

## Overview

The receipt labeling system supports multiple pattern detection optimization levels, allowing you to choose the right balance between performance and functionality for your use case. All optimization levels now return a standardized format, ensuring compatibility with the decision engine regardless of which level you choose.

## Available Optimization Levels

### 1. Legacy (`legacy`)
- **Description**: Original pattern detection implementation
- **Use Case**: Baseline testing, debugging, or when maximum compatibility is needed
- **Performance**: ~11ms per receipt
- **Features**: Basic pattern detection for currency, datetime, contact, and quantity

### 2. Basic (`basic`)
- **Description**: Centralized pattern configuration with shared utilities
- **Use Case**: Improved maintainability with minimal risk
- **Performance**: ~8ms per receipt (25% faster than legacy)
- **Features**: Same detection capabilities as legacy but with centralized patterns

### 3. Optimized (`optimized`)
- **Description**: Advanced performance optimizations including selective invocation
- **Use Case**: Better performance when processing many receipts
- **Performance**: ~14ms per receipt (includes overhead for optimization analysis)
- **Features**: 
  - Selective detector invocation based on content
  - Batch regex evaluation
  - True CPU parallelism preparation

### 4. Advanced (`advanced`) - **Recommended**
- **Description**: Full suite of optimizations including intelligent pattern recognition
- **Use Case**: Production deployments requiring best accuracy and performance
- **Performance**: ~14ms per receipt with additional pattern coverage
- **Features**:
  - All optimizations from previous levels
  - Trie-based multi-word detection
  - Optimized keyword lookups
  - Merchant-specific patterns
  - Typically finds 1-2 additional patterns per receipt

## Usage Examples

### Basic Usage

```python
from receipt_label.decision_engine.integration import DecisionEngineOrchestrator
from receipt_label.decision_engine import DecisionEngineConfig

# Create orchestrator with specific optimization level
config = DecisionEngineConfig()
orchestrator = DecisionEngineOrchestrator(
    config=config,
    optimization_level='advanced'  # Choose: 'legacy', 'basic', 'optimized', 'advanced'
)

# Process receipt
result = await orchestrator.process_receipt(words)
```

### Convenience Function

```python
from receipt_label.decision_engine.integration import process_receipt_with_decision_engine

# Process with specific optimization level
result = await process_receipt_with_decision_engine(
    words,
    optimization_level='advanced'
)
```

### Direct Pattern Detection

```python
from receipt_label.pattern_detection.enhanced_orchestrator import (
    EnhancedPatternOrchestrator, OptimizationLevel
)

# Use enhanced orchestrator directly
orchestrator = EnhancedPatternOrchestrator(OptimizationLevel.ADVANCED)
results = await orchestrator.detect_patterns(words, merchant_name="Walmart")
```

## Performance Characteristics

| Level | Avg Time | Patterns Found | Best For |
|-------|----------|----------------|----------|
| Legacy | ~11ms | Baseline | Compatibility |
| Basic | ~8ms | Baseline | Simple improvements |
| Optimized | ~14ms | Baseline | Bulk processing |
| Advanced | ~14ms | Baseline + 1-3 | Production use |

## Decision Factors

Choose **Legacy** when:
- You need maximum compatibility with existing code
- You're debugging pattern detection issues
- You want a baseline for performance comparison

Choose **Basic** when:
- You want improved maintainability
- You need slightly better performance
- You're risk-averse about new features

Choose **Optimized** when:
- You're processing large batches of receipts
- CPU efficiency is important
- You don't need merchant-specific patterns

Choose **Advanced** when:
- You want the best pattern detection accuracy
- You're in production environment
- You need merchant-specific pattern support
- You want maximum GPT cost reduction

## Standardized Output Format

All optimization levels now return the same standardized format:

```python
{
    "pattern_results": {
        "approach": "advanced",  # Which approach was used
        "optimizations": [...],  # List of optimizations applied
        "results": {
            "currency": [...],   # List of StandardizedPatternMatch objects
            "datetime": [...],   # List of StandardizedPatternMatch objects
            "contact": [...],    # List of StandardizedPatternMatch objects
            "quantity": [...],   # List of StandardizedPatternMatch objects
            "_metadata": {...}   # Performance and diagnostic info
        }
    },
    "performance_metrics": {...},
    "optimization_level": "advanced",
    "total_processing_time_ms": 14.2
}
```

Each pattern match has consistent fields:
- `word`: Primary word object
- `extracted_value`: The detected value
- `confidence`: Confidence score (0.0-1.0)
- `pattern_type`: Category of pattern
- `words`: All words for multi-word patterns
- `metadata`: Additional context

## Migration Guide

Existing code continues to work without changes:

```python
# Old code (still works)
orchestrator = DecisionEngineOrchestrator()  # Defaults to 'advanced'

# New code (explicit optimization level)
orchestrator = DecisionEngineOrchestrator(optimization_level='advanced')
```

## Environment Variables

You can also control the optimization level via environment variable:

```bash
export PATTERN_OPTIMIZATION_LEVEL=advanced
python your_script.py
```

## Monitoring and Debugging

Enable debug logging to see which optimization level is being used:

```python
import logging
logging.getLogger('receipt_label.decision_engine.integration').setLevel(logging.INFO)

# Will log: "Using enhanced pattern orchestrator with advanced optimization"
```

## Future Improvements

The standardized format enables easy addition of new optimization levels without breaking existing code. Future levels might include:
- **Ultra**: Even more advanced AI-powered pattern detection
- **Minimal**: Stripped-down version for edge computing
- **Custom**: User-defined pattern sets for specific industries