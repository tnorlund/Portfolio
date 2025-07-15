# Pattern Detection Module

## Epic #190: Parallel Pattern Detection

This module implements high-performance parallel pattern detection for receipt processing, achieving <100ms execution time with 90%+ accuracy.

## Features

### Pattern Detectors

1. **CurrencyPatternDetector**
   - Detects: `$5.99`, `€10.00`, `5.99 USD`, `($2.00)` (negative)
   - Smart classification: GRAND_TOTAL, TAX, SUBTOTAL, DISCOUNT, UNIT_PRICE, LINE_TOTAL
   - Position-aware: Bottom 20% likely totals
   - Keyword-aware: "tax", "total", "subtotal" context

2. **DateTimePatternDetector**
   - Date formats: `01/15/2024`, `2024-01-15`, `Jan 15, 2024`
   - Time formats: `14:30`, `2:30 PM`, `14:30:45`
   - Combined: `01/15/2024 14:30:00`
   - Handles 2-digit years intelligently

3. **ContactPatternDetector**
   - Phone: `(555) 123-4567`, `+1-555-123-4567`
   - Email: `support@example.com`
   - Website: `www.example.com`, `https://example.com`
   - Validates formats for accuracy

4. **QuantityPatternDetector**
   - At symbol: `2 @ $5.99`
   - Multiplication: `3 x $4.50`, `Qty: 3 x $4.50`
   - Slash notation: `2/$10.00`
   - For pricing: `3 for $15.00`
   - With units: `2 items`, `3.5 lbs`

### Parallel Orchestration

The `ParallelPatternOrchestrator` runs all detectors concurrently using asyncio:

```python
orchestrator = ParallelPatternOrchestrator(timeout=0.1)  # 100ms timeout
results = await orchestrator.detect_all_patterns(words, merchant_patterns)
```

## Performance

Target: <100ms for 95% of receipts

Benchmarked performance:
- Small receipt (20 words): ~15-25ms
- Medium receipt (50 words): ~30-50ms  
- Large receipt (100 words): ~60-90ms

Parallel execution provides 3-4x speedup over sequential processing.

## Integration with Other Epics

### Epic #188: Noise Word Handling
- Input words are pre-filtered using `is_noise` flag
- Pattern detectors skip noise words automatically

### Epic #189: Merchant Pattern System
- Accepts merchant patterns from single Pinecone query
- Applies known patterns with high confidence
- Example: "Big Mac" → PRODUCT_NAME for McDonald's

### Epic #191: Phase 2 Spatial/Mathematical Currency Detection
- **81.7% cost reduction** by processing receipts without Pinecone/ChatGPT
- **Comprehensive spatial analysis** with enhanced price column detection
- **Mathematical validation** using subset sum algorithms for tax structure detection
- **Pattern-first approach** that extracts maximum information before AI decision
- **Phase 2 features**: X-alignment tightness, font analysis, multi-column support
- **Performance**: Sub-100ms processing (33ms average) with 96.1% success rate
- **Smart AI decision**: Only call expensive services when pattern+spatial+math insufficient

## Usage Example

```python
from receipt_label.pattern_detection import ParallelPatternOrchestrator

# Initialize orchestrator
orchestrator = ParallelPatternOrchestrator(timeout=0.1)

# Get merchant patterns from Epic #189
merchant_patterns = {
    "word_patterns": {
        "walmart": "MERCHANT_NAME",
        "sales tax": "TAX",
    },
    "confidence_threshold": 0.85
}

# Run detection (words already filtered by Epic #188)
results = await orchestrator.detect_all_patterns(
    receipt_words,  # List[ReceiptWord] with is_noise=False
    merchant_patterns
)

# Phase 2 spatial/mathematical analysis for Epic #191
from receipt_label.spatial.math_solver_detector import MathSolverDetector
from receipt_label.spatial.vertical_alignment_detector import VerticalAlignmentDetector

# Extract currency patterns for spatial analysis
currency_patterns = [r for r in results.matches if r.pattern_type in [
    PatternType.CURRENCY, PatternType.GRAND_TOTAL, PatternType.TAX
]]

# Enhanced spatial analysis with Phase 2 features
alignment_detector = VerticalAlignmentDetector(use_enhanced_clustering=True)
spatial_result = alignment_detector.detect_line_items_with_alignment(
    receipt_words, results.matches
)

# Mathematical validation with subset sum algorithms
math_solver = MathSolverDetector(use_numpy_optimization=True)
solutions = math_solver.solve_receipt_math(
    [(float(p.extracted_value), p) for p in currency_patterns]
)

# Combined confidence scoring
if spatial_result['best_column_confidence'] > 0.85 and solutions:
    # 81.7% of receipts: High confidence - skip expensive AI services
    confidence_level = "high_confidence"
else:
    # 18.3% of receipts: Require Pinecone/ChatGPT for validation
    confidence_level = "requires_ai"
```

## Testing

Run tests with:
```bash
pytest receipt_label/tests/pattern_detection/ -v
```

Performance tests:
```bash
pytest receipt_label/tests/pattern_detection/test_performance.py -v -s
```

## Architecture

```
pattern_detection/
├── base.py              # Abstract base classes
├── currency.py          # Currency detection & classification
├── datetime.py          # Date/time pattern detection
├── contact.py           # Phone/email/website detection
├── quantity.py          # Quantity pattern detection
├── orchestrator.py      # Parallel execution coordinator
└── integration_example.py  # Example integration
```

## Design Decisions

1. **Compiled Regex**: Patterns compiled once at initialization for performance
2. **Async/Await**: True parallel execution with asyncio
3. **Timeout Protection**: Hard 100ms timeout prevents runaway processing
4. **Error Isolation**: Failures in one detector don't affect others
5. **Confidence Scoring**: Each match includes confidence for downstream decisions

## Future Enhancements

1. **ML-based patterns**: Train models for complex patterns
2. **Language support**: Multi-language date/currency formats
3. **Custom patterns**: User-defined pattern configurations
4. **Caching**: Cache compiled patterns across requests
5. **GPU acceleration**: For very high-volume processing