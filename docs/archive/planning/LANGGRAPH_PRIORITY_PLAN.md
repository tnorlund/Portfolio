# LangGraph Label Priority Plan

## Current Implementation Status

### ✅ Already Supported in LangGraph

**Phase 1 (Currency Analysis):**
- `GRAND_TOTAL` - Final amount due after all discounts, taxes and fees
- `TAX` - Any tax line (sales tax, VAT, bottle deposit)
- `SUBTOTAL` - Sum of all line totals before tax and discounts
- `LINE_TOTAL` - Extended price for that line (quantity x unit price)

**Phase 2 (Line Item Analysis):**
- `PRODUCT_NAME` - Descriptive text of a purchased product (item name)
- `QUANTITY` - Numeric count or weight of the item (e.g., 2, 1.31 lb)
- `UNIT_PRICE` - Price per single unit / weight before tax

**Total: 7 labels supported**

---

## Missing CORE_LABELS (21 labels)

### High Priority (Transaction essentials)
1. **`DATE`** - Calendar date of the transaction
   - **Why**: Critical for receipt validation, expense tracking
   - **Easy to detect**: Usually top of receipt, consistent format
   - **Pattern**: Date formats (MM/DD/YYYY, DD/MM/YYYY, etc.)

2. **`TIME`** - Time of the transaction  
   - **Why**: Often paired with DATE, useful for fraud detection
   - **Easy to detect**: Usually HH:MM format near date
   - **Pattern**: Time formats

### Medium Priority (Merchant context - already have metadata)
3. **`MERCHANT_NAME`** - Trading name or brand
   - **Why**: You already have this in ReceiptMetadata!
   - **Note**: Don't duplicate effort - merchant name comes from merchant validation flow
   - **Recommendation**: SKIP - already covered

4. **`PHONE_NUMBER`** - Telephone number printed on receipt
   - **Why**: Useful for verification, contact info
   - **Pattern**: Phone number formats
   - **Location**: Usually header/footer of receipt

5. **`ADDRESS_LINE`** - Full address line
   - **Why**: Sometimes different from receipt metadata location
   - **Pattern**: Street addresses with city/state/zip
   - **Location**: Usually header or footer

### Low Priority (Nice to have)
6. **`STORE_HOURS`** - Printed business hours
   - **Why**: Rarely used, low value
   - **Location**: Footer
   - **Priority**: SKIP for now

7. **`WEBSITE`** - Web or email address
   - **Why**: Low value, rarely actionable
   - **Location**: Footer
   - **Priority**: SKIP for now

8. **`LOYALTY_ID`** - Customer loyalty/rewards/membership identifier
   - **Why**: Useful for some users, but low priority
   - **Pattern**: Member ID formats
   - **Location**: Header or footer

9. **`PAYMENT_METHOD`** - Payment instrument (e.g., VISA ••••1234, CASH)
   - **Why**: Useful for expense categorization
   - **Pattern**: Card numbers, payment types
   - **Location**: Usually bottom of receipt

10. **`COUPON`** - Coupon code or description
    - **Why**: Useful for marketing analysis
    - **Pattern**: Coupon codes, discount descriptions
    - **Priority**: LOW

11. **`DISCOUNT`** - Non-coupon discount (e.g., 10% member discount)
    - **Why**: Useful for price analysis
    - **Pattern**: Discount percentages, amounts
    - **Priority**: LOW

---

## Recommended Implementation Order

### Phase 1: Essential Transaction Data
**Priority: P0 (Critical)**

Add `DATE` and `TIME` to a new **Phase 3: Header Analysis**

```python
# Suggested Phase 3 node structure:
async def phase3_header_analysis(state, ollama_api_key):
    """Extract DATE and TIME from receipt header."""
    llm = ChatOllama(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        client_kwargs={"headers": {"Authorization": f"Bearer {ollama_api_key}"}}
    )
    
    # Focus on first 5-10 lines (header region)
    header_lines = state.lines[:10]
    header_text = "\n".join([line.text for line in header_lines])
    
    subset = ["DATE", "TIME"]
    # ... rest of implementation
```

**Why start here:**
- Most receipts have date/time
- Easy to identify (top section)
- Critical for receipt validation
- Minimal implementation effort

### Phase 2: Merchant Context (If needed)
**Priority: P1 (Important)**

After date/time, consider these IF you find many receipts missing merchant metadata:

**Option A**: Skip - merchant data comes from `resolve_receipt()` (merchant validation flow)

**Option B**: Add if you want redundancy:
- `PHONE_NUMBER`
- `ADDRESS_LINE` (sometimes different from metadata)

### Phase 3: Transaction Details
**Priority: P2 (Nice to have)**

Only if you have high-value use cases:
- `PAYMENT_METHOD`
- `COUPON`
- `DISCOUNT`

### Skip Entirely (Low value)
- `STORE_HOURS` - Rarely useful
- `WEBSITE` - Low value
- `LOYALTY_ID` - Only useful for specific merchants

---

## Testing with Dev Scripts

### Option 1: Test Current LangGraph Implementation

```bash
# Test currency validation locally
python dev.test_simple_currency_validation.py
```

This script:
- Tests the full LangGraph workflow
- Saves dev state to `./dev.states/`
- Uses actual Ollama API keys
- Runs for a specific image_id/receipt_id

**Prerequisites:**
```bash
export OLLAMA_API_KEY="your-key"
export LANGCHAIN_API_KEY="your-key"  # Optional for tracing
```

### Option 2: Validation Review Script

```bash
# Review existing labels for a specific label type
python dev.langgraph_validation_review.py --label GRAND_TOTAL --status NONE --limit 20 --dry-run

# Actually update invalid labels
python dev.langgraph_validation_review.py --label DATE --status NONE --limit 50
```

**What it does:**
- Queries DynamoDB for labels needing validation
- Groups by receipt for efficient processing
- Uses LangGraph to validate (1 LLM call per receipt)
- Updates database with validation results

**Note**: This script appears to reference `receipt_label.langchain_validation.graph_design` which may not exist yet based on the imports.

### Option 3: Create Your Own Test Script

Create `dev.test_langgraph_with_date.py`:

```python
import os
import asyncio
from receipt_label.langchain.currency_validation import analyze_receipt_simple
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env

# Load environment
env = load_env()

# Initialize client
client = DynamoClient(env.get("dynamodb_table_name"))

# Test with a specific receipt
image_id = "your-image-id"
receipt_id = 1

# API keys
ollama_api_key = os.environ.get("OLLAMA_API_KEY")
langsmith_api_key = os.environ.get("LANGCHAIN_API_KEY")

# Run analysis
result = asyncio.run(
    analyze_receipt_simple(
        client,
        image_id,
        receipt_id,
        ollama_api_key=ollama_api_key,
        langsmith_api_key=langsmith_api_key,
        save_labels=False,  # Preview only
        dry_run=True,
        save_dev_state=True,  # Save state for debugging
    )
)

print(f"Confidence: {result.confidence_score}")
print(f"Labels discovered: {len(result.discovered_labels)}")
```

---

## Implementation Strategy

### Step 1: Add Date/Time Support (Recommended First)

Since you mentioned you already have `receipt_metadata` (which includes merchant_name), the highest value addition is **DATE and TIME**.

**Quick implementation:**

1. **Add to models** (`receipt_label/langchain/models/currency_validation.py`):
```python
class HeaderLabelType(str, Enum):
    DATE = "DATE"
    TIME = "TIME"

class HeaderLabel(BaseModel):
    word_text: str
    label_type: HeaderLabelType
    confidence: float
    reasoning: str
```

2. **Update state** (`receipt_label/langchain/state/currency_validation.py`):
```python
header_labels: List[HeaderLabel] = Field(default_factory=list)
```

3. **Create Phase 3 node** (similar to phase1.py):
```python
async def phase3_header_analysis(state, ollama_api_key):
    # Extract first 10 lines (header)
    # Run LLM analysis for DATE and TIME
    # Return header_labels
```

4. **Update graph** (`receipt_label/langchain/currency_validation.py`):
```python
workflow.add_node("phase3_header", phase3_with_key)
workflow.add_edge("phase1_currency", "phase3_header")
workflow.add_edge("phase3_header", "phase2_line_analysis")
```

5. **Update combine_results** to include header_labels in discovered_labels

### Step 2: Testing

```bash
# Test with a real receipt
python dev.test_langgraph_with_date.py

# Check saved state
ls -la ./dev.states/
cat ./dev.states/state_combine_results_*_metadata.json
```

### Step 3: Deploy

Once working locally, it automatically deploys via:
- Lambda: `infra/currency_validation_step_functions/handlers/process_receipt.py`
- Invoked by Step Functions workflow

---

## Summary

**Currently Supported (7 labels):**
- ✅ GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL
- ✅ PRODUCT_NAME, QUANTITY, UNIT_PRICE

**Recommended Next Steps:**
1. **Add DATE** (P0 - Critical)
2. **Add TIME** (P0 - Critical)  
3. **Skip MERCHANT_NAME** (already have in ReceiptMetadata)
4. **Consider PHONE_NUMBER, ADDRESS_LINE** (P1 - if needed)
5. **Skip STORE_HOURS, WEBSITE, LOYALTY_ID** (low value)

**Test Command:**
```bash
python dev.test_simple_currency_validation.py
```

**Next Development:**
Create `dev.test_langgraph_with_date.py` to test new DATE/TIME phase.

