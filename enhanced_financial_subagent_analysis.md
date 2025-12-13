# Enhanced Financial Validation Sub-Agent Analysis

## Overview

The Enhanced Financial Validation Sub-Agent is a specialized component that validates financial relationships and mathematical consistency on receipts. It integrates with the table sub-agent to leverage structural information for better analysis.

## Input Structure

### Primary Inputs
1. **Receipt Data** (dict):
   ```python
   {
       "receipt_text": str,        # Full OCR text
       "labels": [                 # Existing labels
           {
               "line_id": int,
               "word_id": int,
               "label": str,           # CORE_LABEL type
               "validation_status": str
           }
       ],
       "words": [                  # OCR word-level data
           {
               "line_id": int,
               "word_id": int,
               "text": str,           # Raw word text
               "bbox": {...}          # Optional geometry
           }
       ]
   }
   ```

2. **Table Structure** (Optional dict from table sub-agent):
   ```python
   {
       "rows": [                   # Structured table rows
           {
               "row": int,
               "cells": [
                   {
                       "col": int,
                       "text": str,
                       "line_id": int,
                       "word_id": int,
                       "label": str,
                       "bbox": {...}
                   }
               ]
           }
       ],
       "columns": [                # Column definitions
           {
               "col": int,
               "x_center": float,
               "left": float,
               "right": float
           }
       ],
       "total_rows": int,
       "total_columns": int
   }
   ```

## Tools/Capabilities

### 1. `get_table_structure()`
**Purpose**: Access table structure analysis from table sub-agent
**Returns**:
- `has_structure`: bool - Whether table data is available
- `table_analysis`: dict - Full table structure if available
- `financial_rows`: list - Table rows containing financial data
- `columns`: list - Column layout information

### 2. `detect_currency()`
**Purpose**: Auto-detect currency from receipt text
**Logic**:
- Searches for currency symbols: $, €, £, ¥, ₹
- Searches for currency keywords: USD, Euro, etc.
- Defaults to USD if no clear indicators
**Returns**:
- `currency`: str - Detected currency code
- `confidence`: float - Detection confidence (0.0-1.0)
- `evidence`: list - Detection evidence

### 3. `get_financial_labels()`
**Purpose**: Extract and organize all financial labels by type
**Processes**: GRAND_TOTAL, SUBTOTAL, TAX, LINE_TOTAL, UNIT_PRICE, QUANTITY, DISCOUNT, COUPON
**Returns**:
- `financial_labels`: dict - Labels grouped by type with numeric values
- `label_counts`: dict - Count of each label type

### 4. `validate_grand_total_math()`
**Purpose**: Prove GRAND_TOTAL = SUBTOTAL + TAX + fees - discounts
**Tolerance**: ±0.01 for rounding errors
**Returns**:
- `is_valid`: bool - Whether math checks out
- `issues`: list - Specific math errors found
- `values`: dict - All values used in calculation

### 5. `validate_subtotal_math()`
**Purpose**: Prove SUBTOTAL = sum of all LINE_TOTAL values
**Tolerance**: ±0.01 for rounding errors
**Returns**:
- `is_valid`: bool - Whether math checks out
- `issues`: list - Specific math errors found
- `values`: dict - Subtotal vs sum of line totals

### 6. `validate_line_item_math()`
**Purpose**: Prove QUANTITY × UNIT_PRICE = LINE_TOTAL for each line item
**Process**:
- Groups labels by line_id to find complete line items
- Validates math for each line where all components exist
- Reports which lines have valid/invalid math
**Returns**:
- `is_valid`: bool - Whether all line math is correct
- `issues`: list - Specific math errors per line
- `validated_lines`: list - Details for each line item

### 7. `find_missing_line_item_fields()`
**Purpose**: Find LINE_TOTALs missing required supporting fields
**Requirements**: Each LINE_TOTAL should have PRODUCT_NAME, QUANTITY, UNIT_PRICE
**Returns**:
- `missing_fields`: list - Details of missing fields per line
- `lines_with_issues`: int - Count of problematic lines
- `total_line_items`: int - Total line items found

### 8. `propose_corrections()`
**Purpose**: Submit specific label corrections
**Input**: List of corrections with line_id, word_id, reasoning, confidence
**Returns**: Success status and correction count

## Output Structure

The sub-agent produces a comprehensive validation result:

```python
{
    "currency": str,                    # Detected currency
    "is_valid": bool,                   # Overall financial validity
    "corrections": [                    # Specific label corrections needed
        {
            "line_id": int,
            "word_id": int,
            "current_label": str,       # Current (incorrect) label
            "correct_label": str,       # Proposed correct label
            "reasoning": str,           # Detailed explanation
            "confidence": float         # Confidence (0.0-1.0)
        }
    ],
    "table_structure_used": bool,       # Whether table data was available
    "validation_details": {             # Detailed validation results
        "grand_total_valid": bool,
        "subtotal_valid": bool,
        "line_math_valid": bool,
        "missing_fields_count": int,
        "currency_confidence": float
    }
}
```

## Table Sub-Agent Integration

### How Table Structure Helps

1. **Better Line Item Grouping**:
   - Uses geometric layout to identify which fields belong together
   - Groups PRODUCT_NAME, QUANTITY, UNIT_PRICE, LINE_TOTAL by row position
   - More accurate than relying solely on line_id

2. **Column-Based Validation**:
   - Identifies which column typically contains prices, quantities, etc.
   - Validates that financial fields appear in expected column positions
   - Detects misaligned or mislabeled fields

3. **Financial Section Boundaries**:
   - Uses table structure to identify the financial section of the receipt
   - Focuses validation on relevant rows (line items, totals, tax)
   - Ignores header/footer content that might confuse validation

### Detection of Table Sub-Agent Usage

The sub-agent reports whether table structure was used via:

```python
"table_structure_used": table_structure is not None
```

**Indicators that table structure helped**:
1. `table_structure_used: true` in output
2. More accurate line item grouping (fewer missing field errors)
3. Better detection of misaligned financial fields
4. More precise identification of financial section boundaries

### When Table Structure is Most Valuable

1. **Complex Receipts**: Multiple columns, varying layouts
2. **Multi-line Products**: Product names spanning multiple words
3. **Quantity Variations**: Different quantity formats (lbs, each, etc.)
4. **Price Alignment**: Ensuring prices align with correct products

## Integration Points

### Called From Label Harmonizer
- **Tool**: `validate_financial_consistency()` in `factory.py`
- **Input**: Gets `table_structure` from `state.get("column_analysis")`
- **Output**: Returns validation results for harmonizer decision-making

### Workflow Position
1. Get receipt lines (`get_line_id_text_list`)
2. Run table sub-agent (`run_table_subagent`) → produces `column_analysis`
3. **Run enhanced financial validation** (`validate_financial_consistency`) ← Uses table structure
4. Apply validation results to harmonization decisions

## Key Advantages Over Basic Validation

1. **Comprehensive Math Proof**: Not just individual labels, but proves relationships
2. **Table-Aware**: Uses geometric layout for better accuracy
3. **Field Completeness**: Ensures line items have all required components
4. **Currency Consistency**: Auto-detects and validates currency usage
5. **Specific Corrections**: Provides actionable line_id/word_id corrections
6. **Confidence Scoring**: All corrections include confidence levels
7. **Detailed Reasoning**: Each correction explains why it's needed

This enhanced sub-agent transforms financial validation from basic label checking to comprehensive mathematical proof of receipt consistency.
