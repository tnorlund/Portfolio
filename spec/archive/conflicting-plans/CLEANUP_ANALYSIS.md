# Receipt Label Package Cleanup Analysis

## Overview
This document outlines the current state of the `receipt_label` package and identifies areas for improvement to enable better receipt word processing.

## Current Architecture

### Label Assignment Flow
1. **Structure Analysis** - Receipt sections are identified (header, body, footer)
2. **Field Labeling** - GPT-3.5-turbo assigns labels based on section context
3. **Line Item Processing** - Additional standardized labels for items
4. **Validation** - Consistency checks and confidence scoring

### Label Sources and Definitions

#### 1. Core Labels (`/receipt_label/constants.py`)
```python
CORE_LABELS = {
    "MERCHANT_NAME", "STORE_HOURS", "PHONE_NUMBER", "WEBSITE",
    "LOYALTY_ID", "ADDRESS_LINE", "DATE", "TIME", "PAYMENT_METHOD",
    "COUPON", "DISCOUNT", "PRODUCT_NAME", "QUANTITY", "UNIT_PRICE",
    "LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"
}
```
Total: 18 core labels

#### 2. Section-Specific Labels (`/receipt_dynamo/data/_gpt.py`)
- **Business Info/Header**: business_name, address_line, phone, store_id
- **Transaction**: date, time, transaction_id, cashier
- **Items**: item_name, quantity, price, discount
- **Payment**: subtotal, tax, total, payment_method, payment_status, card_info, amount
- **Footer**: message, policy, promo, survey

### OpenAI Integration Points

#### 1. Merchant Validation Step Function
- **Location**: `/infra/validate_merchant_step_functions/`
- **Method**: Agent-based approach using `@function_tool` decorator
- **Tools**:
  - `search_by_phone`
  - `search_by_address`
  - `search_nearby`
  - `search_by_text`
  - `tool_return_metadata`

#### 2. Label Validation via Batch API
- **Location**: `/receipt_label/completion/`
- **Method**: OpenAI function calling for label validation
- **Schema**: Enforces strict enum validation of CORE_LABELS

## Identified Issues

### 1. Duplicate Label Definitions

**Problem**: Labels are defined in multiple locations with varying formats

**Locations**:
- `/receipt_label/constants.py` - CORE_LABELS dictionary
- `/receipt_label/models/label.py:516` - Converts to uppercase
- `/receipt_label/completion/_format_prompt.py` - Uses CORE_LABELS for validation
- Various validation files - Independent label checks

**Impact**:
- Difficult to maintain consistency
- Changes require updates in multiple files
- Risk of labels getting out of sync

### 2. Inconsistent Label Naming Conventions

**Problem**: Mixed case handling throughout codebase

**Examples**:
```python
# Some places use uppercase
label.upper()  # models/label.py:516

# Others use lowercase
"address" in label.lower()  # field_extraction.py:44

# Multiple .upper() calls
if label.upper() == "MERCHANT_NAME"  # core/labeler.py
```

**Impact**:
- Confusion about canonical label format
- Potential matching failures
- Extra processing overhead

### 3. Hardcoded Configuration Values

**Problem**: Magic numbers and thresholds scattered throughout code

**Examples**:
```python
# completion/_format_prompt.py
EXAMPLE_CAP = int(os.getenv("EXAMPLE_CAP", "4"))  # Default: 4
LINE_WINDOW = int(os.getenv("LINE_WINDOW", "5"))  # Default: 5
window=2  # Line 91, not configurable

# field_extraction.py
merchant_name = " ".join([word.text for word in words[:5]])  # Takes first 5 words
```

**Impact**:
- Difficult to tune system behavior
- No central configuration management
- Requires code changes for adjustments

### 4. Complex Logic Patterns

**Problem**: Convoluted logic that could be simplified

**Example - Field Extraction** (`merchant_validation/field_extraction.py`):
```python
# Current: Substring matching for categorization
if "phone" in label.lower():
    phone_numbers.append(word.text)
elif "address" in label.lower():
    address_parts.append(word.text)
# ... continues for each type

# Fallback: Takes first 5 words as merchant name
if not merchant_name and words:
    merchant_name = " ".join([word.text for word in words[:5]])
```

**Impact**:
- Brittle substring matching
- No validation of extracted fields
- Arbitrary fallback logic

### 5. Limited Label Corpus

**Problem**: Current 18 core labels don't cover many common receipt scenarios

**Missing Labels**:
- Item categories (FOOD, BEVERAGE, etc.)
- Promotional text (PROMO_CODE, SAVINGS_MESSAGE)
- Transaction metadata (TRANSACTION_ID, REGISTER_NUMBER)
- Store metadata (STORE_NUMBER, CASHIER_NAME, CASHIER_ID)
- Additional payment info (CHANGE_DUE, TIP_AMOUNT)
- Loyalty info (POINTS_EARNED, REWARDS_BALANCE)

**Impact**:
- Cannot accurately label all receipt content
- Forces incorrect labeling or unlabeled words
- Limits analysis capabilities

### 6. Rigid Validation System

**Problem**: Schema enforcement makes adding new labels difficult

**Current Implementation**:
```python
# completion/_format_prompt.py
properties={
    label: {"type": "number", "minimum": 0, "maximum": 1}
    for label in CORE_LABELS
}
```

**Impact**:
- Must modify code to add new labels
- Cannot experiment with new label types
- No support for custom or dynamic labels

## Recommendations

### 1. Centralize Label Management
Create a single `LabelRegistry` class that:
- Defines all labels with metadata
- Handles case normalization
- Provides validation methods
- Supports label hierarchies/categories

### 2. Standardize Label Format
- Choose uppercase as canonical format
- Implement automatic normalization at entry points
- Remove redundant case conversions

### 3. Extract Configuration
Create `config.py` or use environment variables for:
- Window sizes and limits
- Confidence thresholds
- Processing parameters
- Model selection

### 4. Simplify Field Extraction
Replace substring matching with:
- Direct label-to-field mapping
- Configurable extraction rules
- Validation of extracted values

### 5. Expand Label Corpus
- Add comprehensive label set (50+ labels)
- Support hierarchical categorization
- Allow custom label definitions
- Include multi-word label support

### 6. Flexible Validation
- Make validation schema dynamic
- Support label addition without code changes
- Implement label discovery/learning

## Implementation Priority

1. **High Priority** (Blocking current work):
   - Centralize label definitions
   - Standardize label casing
   - Expand core label set

2. **Medium Priority** (Improves maintainability):
   - Extract configuration values
   - Simplify field extraction logic
   - Add label categories/hierarchy

3. **Low Priority** (Nice to have):
   - Dynamic validation system
   - Label learning/discovery
   - Advanced label metadata

## Migration Strategy

### 1. Backward Compatibility
Create label mapping to ensure existing data continues to work:

```python
# Create label mapping for backward compatibility
LEGACY_LABEL_MAPPING = {
    "MERCHANT_NAME": "MERCHANT_NAME",  # unchanged
    "STORE_HOURS": "STORE_HOURS",      # unchanged
    # Map old labels to new hierarchical structure
}
```

### 2. Gradual Rollout
Implement feature flags for safe transition:
- Run old and new systems in parallel initially
- Measure accuracy improvements before full cutover
- Monitor performance impact of expanded label set

### 3. Versioned Label Registry
```python
class LabelRegistry:
    def __init__(self, version="v2"):
        self.version = version
        self.labels = self._load_labels(version)

    def is_valid_label(self, label: str) -> bool:
        return label.upper() in self.labels

    def get_category(self, label: str) -> Optional[str]:
        # Return hierarchical category for label
        pass
```

### 4. Performance Optimizations
- Cache validation schemas to reduce API overhead
- Use label subsets for different receipt types (grocery vs restaurant)
- Implement lazy loading for rarely-used labels
- Consider label frequency analysis for optimization

### 5. Data Quality Metrics
Track improvement metrics:
- Label usage frequency by category
- Confidence scores by label type
- Identification of labels needing more training examples
- Success rate of field extraction by label

## Next Steps

1. **Phase 0 - Foundation** (Before Phase 1):
   - Implement migration strategy and legacy mapping
   - Create versioned `LabelRegistry` with feature flags
   - Set up metrics tracking infrastructure

2. **Phase 1 - Core Implementation**:
   - Create `LabelRegistry` class with all label definitions
   - Focus on high-impact labels first: `TRANSACTION_ID`, `CASHIER_NAME`, `CARD_LAST_FOUR`
   - Update all code to use centralized labels with backward compatibility

3. **Phase 2 - Expansion**:
   - Add new labels for missing receipt fields incrementally
   - Create configuration file for all parameters
   - Implement category-based validation

4. **Phase 3 - Optimization**:
   - Refactor field extraction to use direct mapping
   - Update validation to support new labels dynamically
   - Optimize performance based on metrics

5. **Phase 4 - Full Migration**:
   - Complete transition to new label system
   - Deprecate legacy labels
   - Document new label capabilities
