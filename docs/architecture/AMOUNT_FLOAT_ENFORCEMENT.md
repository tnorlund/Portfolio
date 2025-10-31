# Amount Field: Float Enforcement

## Problem

The user pointed out that amounts need to be floats only. The Pydantic models were already typed as `float`, but we needed to ensure:

1. **Pydantic coerces strings to floats** automatically
2. **Explicit float conversion** in the processing code
3. **Clear examples** in the schema showing numeric (not string) amounts

## Solution

### 1. Enhanced Pydantic Model (CurrencyLabel)

```python
class CurrencyLabel(BaseModel):
    amount: float = Field(
        ...,
        description="The numeric currency value as a float (e.g., 15.02 for $15.02, 24.01 for $24.01). Must be a number, not a string.",
        json_schema_extra={"examples": [15.02, 24.01, 0.00]}
    )
```

**Key changes:**
- Added explicit note that it must be a number, not a string
- Added example values showing float literals (not strings)

### 2. Explicit Float Conversion (phase1.py)

```python
currency_labels = [
    CurrencyLabel(
        line_text=item.line_text,
        amount=float(item.amount),  # Ensure float type
        label_type=getattr(CurrencyLabelType, item.label_type),
        line_ids=item.line_ids,
        confidence=float(item.confidence),  # Ensure float type
        reasoning=item.reasoning,
    )
    for item in response.currency_labels
]
```

**Why:** Even though Pydantic will coerce, explicit conversion ensures type safety at the processing level.

### 3. Schema Example (Phase1Response)

```python
class Phase1Response(BaseModel):
    model_config = {
        "json_schema_extra": {
            "examples": [{
                "currency_labels": [{
                    "line_text": "24.01",
                    "amount": 24.01,  # float, not "24.01"
                    ...
                }]
            }]
        }
    }
```

**Why:** Shows the LLM exactly what we expect - numeric amounts, not strings.

## How Pydantic Handles This

Pydantic automatically coerces strings to floats:

```python
# Test with string amount (should fail or coerce)
test_response = {
    'currency_labels': [{
        'amount': '24.01',  # string
        ...
    }]
}

resp = Phase1Response(**test_response)
print(type(resp.currency_labels[0].amount))  # <class 'float'> ✅
```

## Verification

All amounts are now guaranteed to be floats:

1. ✅ **Type annotation** in Pydantic model (`amount: float`)
2. ✅ **Explicit coercion** in processing code (`float(item.amount)`)
3. ✅ **Schema examples** show numeric literals, not strings
4. ✅ **Pydantic validation** ensures runtime compliance

## Files Changed

- `receipt_label/receipt_label/langchain/models/currency_validation.py`
  - Enhanced `amount` field description
  - Added schema examples to `Phase1Response`
- `receipt_label/receipt_label/langchain/nodes/phase1.py`
  - Added explicit `float()` conversion for amount and confidence

