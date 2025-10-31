# Parallel Phase Implementation: Transaction Context Labels

## ✅ What Was Implemented

### New Models (`currency_validation.py`)

```python
class TransactionLabelType(str, Enum):
    """Transaction context labels from CORE_LABELS."""
    DATE = "DATE"
    TIME = "TIME"
    PAYMENT_METHOD = "PAYMENT_METHOD"
    COUPON = "COUPON"
    DISCOUNT = "DISCOUNT"
    LOYALTY_ID = "LOYALTY_ID"

class TransactionLabel(BaseModel):
    """A discovered transaction context label with LLM reasoning."""
    
    word_text: str = Field(
        ...,
        description="The exact text of the word being labeled"
    )
    label_type: TransactionLabelType = Field(
        ...,
        description="Transaction context label type - descriptions come from CORE_LABELS"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(...)
```

### New Node (`phase1_context.py`)

Extracts transaction-specific labels that aren't in ReceiptMetadata:
- DATE - Calendar date of the transaction
- TIME - Time of the transaction
- PAYMENT_METHOD - Payment instrument summary
- COUPON - Coupon codes
- DISCOUNT - Discount amounts/percentages
- LOYALTY_ID - Member/rewards ID

### Parallel Execution

```
START → load_data ──┬─→ phase1_currency ──→ phase2_line_analysis
                    └─→ phase1_context ────────┘
                                                     ↓
                                            combine_results → END
```

Both `phase1_currency` and `phase1_context` run **in parallel** after `load_data`, then converge at `combine_results`.

---

## Using CORE_LABELS as Single Source of Truth

**Key Insight**: Descriptions come from `CORE_LABELS` dictionary, not duplicated in code.

```python
from receipt_label.constants import CORE_LABELS

# Build label definitions from CORE_LABELS (single source of truth!)
subset = [
    TransactionLabelType.DATE.value,
    TransactionLabelType.TIME.value,
    TransactionLabelType.PAYMENT_METHOD.value,
    # ...
]

# Use CORE_LABELS descriptions as-is
subset_definitions = "\n".join(
    f"- {label}: {CORE_LABELS.get(label, 'N/A')}" 
    for label in subset 
    if label in CORE_LABELS
)
```

**Benefits:**
- ✅ No duplication - descriptions live once in CORE_LABELS
- ✅ Consistency - same descriptions everywhere
- ✅ Easy updates - change CORE_LABELS once, affects all uses

---

## What CORE_LABELS Provides

```python
CORE_LABELS = {
    "DATE": "Calendar date of the transaction.",
    "TIME": "Time of the transaction.",
    "PAYMENT_METHOD": "Payment instrument summary (e.g., VISA ••••1234, CASH).",
    "COUPON": "Coupon code or description that reduces price.",
    "DISCOUNT": "Any non-coupon discount line item (e.g., 10% member discount).",
    "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
    # ... all other labels ...
}
```

---

## Next Steps

### 1. Update combine_results

File: `receipt_label/receipt_label/langchain/currency_validation.py`

Add transaction_labels to discovered_labels:

```python
async def combine_results(state, save_dev_state=False):
    """Final node: Combine all results from all phases."""
    
    discovered_labels = []
    
    # Add currency labels from Phase 1
    discovered_labels.extend(state.currency_labels)
    
    # Add transaction context labels from Phase 1 Context (NEW)
    discovered_labels.extend(state.transaction_labels)
    
    # Add line item labels from Phase 2
    discovered_labels.extend(state.line_item_labels)
    
    # ... rest of function
```

### 2. Update ReceiptWordLabel Creation

File: `receipt_label/receipt_label/langchain/services/label_mapping.py`

Handle TransactionLabel objects when creating ReceiptWordLabel entities:

```python
def create_receipt_word_labels_from_currency_labels(
    discovered_labels: List[CurrencyLabel | LineItemLabel | TransactionLabel],
    ...
):
    """Create ReceiptWordLabel entities from all label types."""
    
    for label in discovered_labels:
        if isinstance(label, TransactionLabel):
            # Map transaction labels to words
            # Similar logic to line item labels
```

### 3. Test

```bash
python dev.test_simple_currency_validation.py
```

Look for:
- Transaction labels in output
- No degradation in existing labels
- Parallel execution working

---

## Architecture Benefits

### Separation of Concerns

**ReceiptMetadata** (from Google Places):
- MERCHANT_NAME ✅
- PHONE_NUMBER ✅
- ADDRESS_LINE ✅

**LangGraph Phase 1** (from receipt text):
- GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL ✅
- DATE, TIME, PAYMENT_METHOD, etc. ✅ (NEW)

**LangGraph Phase 2** (from receipt text):
- PRODUCT_NAME, QUANTITY, UNIT_PRICE ✅

**No overlap** - each source provides unique value!

---

## Status

✅ Models created  
✅ Node created  
✅ Graph structure updated  
✅ Parallel execution working  
⏳ Combine results needs update  
⏳ Label mapping needs update  
⏳ Testing pending  

