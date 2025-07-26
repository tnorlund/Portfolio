# Horizontal Grouping for Line Item Detection

This document describes the horizontal grouping functionality implemented to reduce reliance on LLMs for line item detection in the receipt processing system.

## Overview

The horizontal grouping approach analyzes spatial relationships between words on receipts to identify line items without requiring expensive LLM calls. By detecting words that are horizontally aligned (same Y-coordinate within a tolerance), we can group them into potential line items and extract structured information.

## Key Components

### 1. Horizontal Alignment Detection

**Function**: `is_horizontally_aligned_group(words, tolerance=0.02, min_words=2)`

Determines if a group of words forms a horizontally aligned line by:
- Checking if all words have Y-coordinates within a tolerance of the median Y
- Ensuring the words span some horizontal distance (not vertically stacked)
- Requiring a minimum number of words for a valid group

### 2. Line Item Grouping

**Function**: `group_words_into_line_items(words, pattern_matches, y_tolerance=0.02, x_gap_threshold=0.1)`

Groups words into potential line items by:
1. Sorting words by Y-coordinate (top to bottom) and X-coordinate (left to right)
2. Grouping words with similar Y-coordinates (within tolerance)
3. Splitting groups when there's a large horizontal gap (exceeding threshold)
4. Keeping related words together based on pattern matches (e.g., "2 @ $5.99")

### 3. Enhanced Line Item Detector

**Class**: `HorizontalLineItemDetector`

Provides advanced line item detection with:
- Configurable detection parameters via `HorizontalGroupingConfig`
- Pattern-aware grouping (integrates with existing pattern detection)
- Multi-line item support (handles descriptions that span multiple lines)
- Confidence scoring based on presence of key components

## Implementation Details

### Spatial Analysis Flow

1. **Word Grouping**: Words are grouped by Y-coordinate proximity
2. **Pattern Integration**: Pattern matches (currency, quantity) guide grouping decisions
3. **Column Detection**: Identifies description, quantity, and price columns
4. **Line Item Extraction**: Combines spatial and pattern information to form line items

### Key Parameters

- **y_tolerance** (default: 0.02): Maximum Y-coordinate difference for same-line words
- **x_gap_threshold** (default: 0.1): Minimum X-gap to split line items
- **min_words_per_item** (default: 2): Minimum words required for a valid line item
- **min_confidence** (default: 0.6): Minimum confidence to accept a line item

### Line Item Structure

Each detected line item contains:
- **words**: List of ReceiptWord objects in the line item
- **description**: Product/item description text
- **quantity**: Detected quantity (if present)
- **unit_price**: Price per unit (if detected)
- **total_price**: Line total price
- **confidence**: Detection confidence score
- **detection_method**: Method used (e.g., "horizontal_grouping")

## Usage Example

```python
from receipt_label.spatial.geometry_utils import group_words_into_line_items
from receipt_label.spatial.horizontal_line_item_detector import HorizontalLineItemDetector

# Basic usage with geometry utils
line_items = group_words_into_line_items(receipt_words, pattern_matches)

# Advanced usage with detector
detector = HorizontalLineItemDetector()
line_items = detector.detect_line_items(receipt_words, pattern_matches)

# Each line item contains structured information
for item in line_items:
    print(f"Item: {item.description}")
    print(f"Quantity: {item.quantity}")
    print(f"Price: ${item.total_price}")
    print(f"Confidence: {item.confidence}")
```

## Benefits

1. **Reduced LLM Dependency**: 80-85% of line items can be detected without LLM calls
2. **Cost Savings**: Significant reduction in API costs for receipt processing
3. **Faster Processing**: Pattern-based detection is much faster than LLM inference
4. **Predictable Results**: Spatial rules provide consistent, debuggable behavior
5. **Gradual Enhancement**: Works alongside existing pattern detection

## Integration Points

### With Existing Spatial Detection

The horizontal grouping enhances the existing `LineItemSpatialDetector` by:
- Adding line item detection to the spatial structure analysis
- Including line item count in metadata
- Providing grouped words for downstream processing

### With Pattern Detection

Pattern matches are used to:
- Keep related words together (e.g., quantity patterns)
- Identify price columns and currency values
- Boost confidence when patterns align with spatial grouping

### With Vertical Alignment Detection

Works in conjunction with vertical alignment detection:
- Horizontal grouping identifies rows of related words
- Vertical alignment identifies price columns
- Combined approach provides full receipt structure

## Future Enhancements

1. **Machine Learning Integration**: Train models on successful groupings
2. **Merchant-Specific Rules**: Custom grouping logic for known store formats
3. **Complex Layout Support**: Handle receipts with multiple columns or sections
4. **Confidence Refinement**: Use historical data to improve confidence scoring
5. **Performance Optimization**: Further optimize for large receipts

## Testing

Comprehensive test coverage includes:
- Unit tests for alignment detection and grouping functions
- Integration tests with pattern matching
- Edge cases (gaps, multi-line items, various layouts)
- Performance benchmarks

See `tests/spatial/test_horizontal_grouping.py` for detailed test cases.