# Modular Label Validation System

A flexible, extensible system for validating receipt word labels using ChromaDB similarity search and LLM-based validation.

## Architecture Overview

The modular validator system consists of four core components:

1. **Base Validator Classes** (`base.py`) - Abstract base class and configuration
2. **Validator Registry** (`registry.py`) - Central registry for auto-discovery of validators
3. **Prompt Builder** (`prompt_builder.py`) - Comprehensive LLM prompt construction
4. **Validation Orchestrator** (`orchestrator.py`) - Coordinates validation for multiple labels

## Core Components

### BaseLabelValidator

All validators inherit from `BaseLabelValidator` and implement:

```python
from receipt_label.label_validation.base import BaseLabelValidator, ValidatorConfig
from receipt_label.label_validation.data import LabelValidationResult

class MyValidator(BaseLabelValidator):
    @property
    def supported_labels(self) -> list[str]:
        return ["MY_LABEL"]
    
    def validate(self, word, label, client_manager=None, **kwargs):
        # Validation logic here
        return LabelValidationResult(...)
```

### ValidatorConfig

Configure validator behavior:

```python
config = ValidatorConfig(
    similarity_threshold=0.75,        # ChromaDB similarity threshold
    n_neighbors=10,                   # Number of neighbors to retrieve
    requires_receipt_metadata=True,   # Needs ReceiptMetadata
    requires_merchant_name=False,     # Needs merchant name
    enable_llm_validation=False,      # Enable LLM-based validation
    use_chroma_examples=True,         # Include ChromaDB examples in LLM prompt
)
```

### Prompt Builder

The `build_validation_prompt()` function creates comprehensive prompts including:

- Full receipt text with line numbers
- ReceiptMetadata context (merchant info, address, phone)
- ChromaDB similarity neighbors (examples from other receipts)
- Local context (nearby lines and labels)
- Label definitions and pattern examples
- Validation instructions

Example usage:

```python
from receipt_label.label_validation.prompt_builder import build_validation_prompt

prompt = build_validation_prompt(
    word=word,
    label=label,
    lines=receipt_lines,
    receipt_metadata=receipt_metadata,
    chroma_neighbors=neighbors,  # From ChromaDB
    all_labels=all_labels,
    words=all_words,
    receipt_count=receipt_count,
    label_confidence=0.92,
)
```

### Validation Orchestrator

The orchestrator handles batch validation:

```python
from receipt_label.label_validation import ValidationOrchestrator

orchestrator = ValidationOrchestrator(client_manager)

results = orchestrator.validate_and_update(
    labels_and_words=[(label1, word1), (label2, word2)],
    receipt_lines=lines,
    receipt_metadata=metadata,
    merchant_name="COSTCO",
    receipt_count=50,
    all_labels=all_labels,
    all_words=all_words,
)
```

## Creating a New Validator

### Step 1: Create Validator Class

```python
# receipt_label/receipt_label/label_validation/validate_product_name.py

from receipt_label.label_validation.base import BaseLabelValidator, ValidatorConfig
from receipt_label.label_validation.data import LabelValidationResult

class ProductNameValidator(BaseLabelValidator):
    def __init__(self, config=None):
        super().__init__(config or ValidatorConfig(
            similarity_threshold=0.7,
            enable_llm_validation=True,  # Enable LLM for product names
        ))
    
    @property
    def supported_labels(self) -> list[str]:
        return ["PRODUCT_NAME"]
    
    def validate(self, word, label, client_manager=None, **kwargs):
        # Get ChromaDB neighbors
        neighbors = self.get_chroma_neighbors(word, label, client_manager)
        
        # If LLM validation enabled, build prompt
        if self.config.enable_llm_validation:
            llm_prompt = kwargs.get("llm_prompt")  # Built by orchestrator
            # Call LLM here...
        
        # Calculate similarity
        avg_similarity = sum(n["similarity_score"] for n in neighbors) / len(neighbors) if neighbors else 0.0
        
        # Validation logic
        is_consistent = avg_similarity > self.config.similarity_threshold
        
        return LabelValidationResult(
            image_id=label.image_id,
            receipt_id=label.receipt_id,
            line_id=label.line_id,
            word_id=label.word_id,
            label=label.label,
            status="VALIDATED",
            is_consistent=is_consistent,
            avg_similarity=avg_similarity,
            neighbors=[n["id"] for n in neighbors],
            pinecone_id=chroma_id_from_label(label),
        )
```

### Step 2: Register Validator

```python
# In __init__.py or a registration module
from receipt_label.label_validation.registry import register_validator
from .validate_product_name import ProductNameValidator

register_validator(ProductNameValidator)
```

## Current Validators

### Implemented (Function-based)
- ✅ `validate_currency` - Currency labels (GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL, UNIT_PRICE)
- ✅ `validate_date` - Date validation
- ✅ `validate_time` - Time validation
- ✅ `validate_phone_number` - Phone number validation
- ✅ `validate_merchant_name_pinecone` - Merchant name (similarity-based)
- ✅ `validate_merchant_name_google` - Merchant name (Google Places-based)
- ✅ `validate_address` - Address validation

### Missing Validators (8/18 CORE_LABELS)
- ❌ `PRODUCT_NAME` - Product/item names
- ❌ `QUANTITY` - Item quantities
- ❌ `PAYMENT_METHOD` - Payment types
- ❌ `COUPON` - Coupon codes
- ❌ `DISCOUNT` - Discount amounts
- ❌ `LOYALTY_ID` - Loyalty/rewards IDs
- ❌ `STORE_HOURS` - Business hours
- ❌ `WEBSITE` - Web/email addresses

## Usage Examples

### Basic Validation

```python
from receipt_label.label_validation import (
    ValidationOrchestrator,
    get_validator,
)
from receipt_label.utils import get_client_manager

client_manager = get_client_manager()
orchestrator = ValidationOrchestrator(client_manager)

# Validate labels for a receipt
results = orchestrator.validate_and_update(
    labels_and_words=[(label, word) for label, word in zip(labels, words)],
    receipt_lines=lines,
    receipt_metadata=metadata,
    merchant_name="COSTCO",
    receipt_count=100,
    all_labels=labels,
    all_words=words,
)
```

### Using Individual Validators

```python
from receipt_label.label_validation import get_validator

validator = get_validator("GRAND_TOTAL")
if validator:
    result = validator.validate(
        word=word,
        label=label,
        client_manager=client_manager,
    )
```

### Checking Coverage

```python
from receipt_label.label_validation import (
    get_all_supported_labels,
    get_missing_labels,
)

supported = get_all_supported_labels()
missing = get_missing_labels()

print(f"Supported: {len(supported)}/{len(CORE_LABELS)}")
print(f"Missing: {missing}")
```

## Prompt Structure

The prompt builder creates comprehensive prompts with:

1. **Task Definition** - Clear validation task
2. **Label Definition** - What the label means
3. **Merchant Information** - ReceiptMetadata context
4. **Full Receipt Text** - Complete receipt with line numbers
5. **Target Word Context** - Specific word being validated
6. **Local Context** - Nearby lines (±3 lines)
7. **ChromaDB Examples** - Similar examples from other receipts (top 5)
8. **Other Labels** - Labels on same/nearby lines for consistency
9. **Validation Instructions** - What to consider
10. **Output Schema** - Expected JSON structure
11. **Allowed Labels** - List of valid CORE_LABELS
12. **Pattern Examples** - Common patterns for the label type

## Integration with Existing Code

The system is backward-compatible with existing function-based validators. The orchestrator can use both:

- **New validators** (class-based, registered)
- **Legacy validators** (function-based, direct calls)

To migrate, gradually refactor function-based validators to class-based validators.

## Best Practices

1. **Use ChromaDB Neighbors** - Similar examples improve validation accuracy
2. **Enable LLM Validation** - For complex labels (product names, descriptions)
3. **Include Context** - Always pass `receipt_lines`, `all_labels`, `all_words`
4. **Set Appropriate Thresholds** - Adjust `similarity_threshold` per label type
5. **Handle Missing Data** - Gracefully handle missing ChromaDB vectors or metadata

## Future Enhancements

- [ ] Automatic validator discovery from file system
- [ ] LLM validation integration (calling Ollama/OpenAI)
- [ ] Batch validation optimizations
- [ ] Validation result caching
- [ ] Confidence score calibration
- [ ] A/B testing framework for validation strategies

