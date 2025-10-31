# Validation Status: How It Works

## ✅ Yes, We're Setting validation_status

### Current Implementation

**File**: `receipt_label/receipt_label/langchain/services/label_mapping.py`

When creating `ReceiptWordLabel` entities, we **always** set:

```python
receipt_word_label = ReceiptWordLabel(
    image_id=image_id,
    receipt_id=actual_receipt_id,
    line_id=line_id,
    word_id=word_id,
    label=label.label_type.value,
    reasoning=label.reasoning or "Identified by simple_receipt_analyzer",
    timestamp_added=current_time,
    label_proposed_by="simple_receipt_analyzer",
    validation_status="PENDING",  # ✅ Always set to PENDING
)
```

### Validation Status Values

**From**: `receipt_dynamo/receipt_dynamo/constants.py`

```python
class ValidationStatus(str, Enum):
    """Standardized validation state for receipt word labels."""
    
    NONE = "NONE"         # No validation has ever been initiated
    PENDING = "PENDING"   # Validation has been queued ✅ WE USE THIS
    VALID = "VALID"       # Validation succeeded
    INVALID = "INVALID"   # Validation rejected
    NEEDS_REVIEW = "NEEDS_REVIEW"  # Validation needs review
```

---

## What Happens at Each Stage

### 1. LangGraph Creates Labels
- **validation_status**: `"PENDING"`
- **label_proposed_by**: `"simple_receipt_analyzer"`
- Labels are **NOT YET SAVED** to DynamoDB

### 2. During Dry Run (Current Setup)
```python
# In dev.test_simple_currency_validation.py
save_labels=False,  # ✅ NOT saving to DynamoDB
dry_run=True,        # ✅ Preview only
```

**Result**: Labels are created in memory, logged for preview, **NOT saved to DynamoDB**

### 3. When Actually Saving
When `save_labels=True` and `dry_run=False`:
- Labels are saved to DynamoDB with `validation_status="PENDING"`
- A separate validation workflow can then:
  - Mark labels as `VALID` (correct)
  - Mark labels as `INVALID` (incorrect)
  - Mark labels as `NEEDS_REVIEW` (uncertain)

---

## Current Test Configuration

```python
# dev.test_simple_currency_validation.py
asyncio.run(
    analyze_receipt_simple(
        client,
        image_id,
        receipt_id,
        ollama_api_key=ollama_api_key,
        langsmith_api_key=langsmith_api_key,
        save_labels=False,  # ✅ NOT saving to DynamoDB
        dry_run=True,        # ✅ Dry run mode
        save_dev_state=True,  # Save state to JSON files
    )
)
```

**What actually happens**:
1. ✅ Extract labels with LangGraph
2. ✅ Create ReceiptWordLabel entities in memory
3. ✅ Set validation_status="PENDING" on all labels
4. ✅ Log proposed labels for preview
5. ❌ **DO NOT** save to DynamoDB (because `save_labels=False`)

---

## Proposed Labels Preview

```
📌 Proposed adds: 28, updates: 0 (existing: 0)

🧾 Word line=1 word=1 text='COSTCO' → proposed=['MERCHANT_NAME'] existing=[] add=['MERCHANT_NAME'] update=[]
🧾 Word line=3 word=1 text='Westlake' → proposed=['ADDRESS_LINE'] existing=[] add=['ADDRESS_LINE'] update=[]
...
🧾 Word line=30 word=1 text='09/06/2025' → proposed=['DATE'] existing=[] add=['DATE'] update=[]
```

These are **previewed** but **NOT SAVED** to DynamoDB.

---

## Summary

- ✅ **validation_status IS set** to "PENDING" when creating ReceiptWordLabel entities
- ✅ **NOT saving to DynamoDB** in current test configuration (dry_run=True)
- ✅ Labels include validation_status="PENDING" which is correct for new, unvalidated labels
- ⏳ A separate validation workflow can later mark them as VALID/INVALID/NEEDS_REVIEW

