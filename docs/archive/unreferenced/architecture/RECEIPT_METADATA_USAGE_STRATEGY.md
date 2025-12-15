# Using ReceiptMetadata in LangGraph

## Key Insight

ReceiptMetadata is **already available** for each receipt and contains merchant-specific info from Google Places API.

We should **use this** to:
1. ✅ Skip extracting MERCHANT_NAME, PHONE_NUMBER, ADDRESS_LINE (already have it)
2. ✅ Focus on transaction-specific labels that aren't in metadata
3. ✅ Potentially provide context to LLM about merchant

---

## How to Access ReceiptMetadata in LangGraph

### Current Flow

```python
# In analyze_receipt_simple
async def analyze_receipt_simple(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    ollama_api_key: str,
    ...
):
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
    
    # ✅ ADD THIS: Load ReceiptMetadata
    receipt_metadata = client.get_receipt_metadata(image_id, receipt_id)
    
    # Use metadata as context
    merchant_name = receipt_metadata.merchant_name
    merchant_category = receipt_metadata.merchant_category
    
    # Initial state
    initial_state = {
        ...
        "receipt_metadata": receipt_metadata,  # ADD THIS
    }
```

### Benefits

1. **Merchant Context**: LLM can use merchant name/category for better analysis
2. **Skip Redundant Extraction**: Don't need to extract merchant info
3. **Better Accuracy**: LLM knows which merchant it's analyzing

---

## Transaction Labels NOT in Metadata

These are **per-receipt** (transaction-specific):

- **DATE** - Each transaction has its own date
- **TIME** - Each transaction has its own time
- **PAYMENT_METHOD** - Can vary per transaction
- **COUPON** - Coupon codes used
- **DISCOUNT** - Discounts applied
- **LOYALTY_ID** - Customer loyalty number (if shown)

These cannot come from Google Places because they're **transaction-specific**, not merchant-specific.

---

## Implementation: Pass Metadata as Context

### Update State Model

```python
class CurrencyAnalysisState(BaseModel):
    # ... existing fields ...
    
    # ReceiptMetadata context (NEW)
    receipt_metadata: Optional[ReceiptMetadata] = None
```

### Use in Prompts

```python
async def phase1_currency_analysis(state, ollama_api_key):
    """Extract currency labels with merchant context."""
    
    llm = ChatOllama(...)
    
    # Build merchant context from ReceiptMetadata
    merchant_context = ""
    if state.receipt_metadata:
        metadata = state.receipt_metadata
        merchant_context = f"""
MERCHANT CONTEXT:
- Name: {metadata.merchant_name}
- Category: {metadata.merchant_category}
- Location: {metadata.address}
"""
    
    messages = [{
        "role": "user",
        "content": f"""{merchant_context}

RECEIPT TEXT:
{state.formatted_text}

Extract currency amounts and classify them.
"""
    }]
```

### Benefits for Accuracy

- LLM knows which merchant it's analyzing
- Can apply merchant-specific knowledge
- Better context for edge cases
- Reduces hallucinations

---

## Complete Label Extraction Strategy

### From ReceiptMetadata (Already have ✅)
1. ✅ MERCHANT_NAME
2. ✅ PHONE_NUMBER  
3. ✅ ADDRESS_LINE

### From LangGraph (Need to add ✅)
4. ✅ DATE
5. ✅ TIME
6. ✅ PAYMENT_METHOD
7. ✅ COUPON
8. ✅ DISCOUNT
9. ✅ LOYALTY_ID

---

## Summary

**ReceiptMetadata provides:** Merchant-specific info (name, phone, address) from Google Places  
**LangGraph should extract:** Transaction-specific info (date, time, payment, coupons) from receipt text

**No overlap** - perfect separation of concerns! ✅

