# CORE_LABELS vs ReceiptMetadata Analysis

## What ReceiptMetadata Already Provides ✅

Looking at the `ReceiptMetadata` entity, it already contains:

### Already Covered by ReceiptMetadata
- ✅ **MERCHANT_NAME** - From Google Places API validation
- ✅ **PHONE_NUMBER** - From Google Places API 
- ✅ **ADDRESS_LINE** - Normalized address from Google Places
- ✅ **Merchant Category** - Business type/category

### Additional Fields in ReceiptMetadata
- `place_id` - Google Places API ID
- `merchant_category` - Business type
- `matched_fields` - Which fields matched
- `validated_by` - Source of validation (GPT+GooglePlaces)
- `timestamp` - When record was created
- `reasoning` - GPT justification
- `canonical_*` fields - Clustered merchant data

---

## CORE_LABELS We Should Extract via LangGraph

### HIGH PRIORITY: Transaction Timing
1. **`DATE`** - Transaction date (NOT in metadata)
2. **`TIME`** - Transaction time (NOT in metadata)

**Why**: ReceiptMetadata has a `timestamp` field, but that's when the metadata was **created** (photo taken), not when the **transaction** occurred. The receipt itself has the transaction date/time.

### MEDIUM PRIORITY: Transaction Details
3. **`PAYMENT_METHOD`** - How payment was made (NOT in metadata)
4. **`COUPON`** - Coupon codes used (NOT in metadata)
5. **`DISCOUNT`** - Discount applied (NOT in metadata)

**Why**: These are transaction-specific, not merchant metadata. Each receipt may have different payment methods.

### LOW PRIORITY
6. **`LOYALTY_ID`** - Customer loyalty identifier (NOT in metadata)
   - Useful for some users but not critical

### SKIP (Already in ReceiptMetadata or Low Value)
- ❌ **MERCHANT_NAME** - ✅ Already in ReceiptMetadata
- ❌ **PHONE_NUMBER** - ✅ Already in ReceiptMetadata  
- ❌ **ADDRESS_LINE** - ✅ Already in ReceiptMetadata
- ❌ **STORE_HOURS** - Low value
- ❌ **WEBSITE** - Low value

---

## Implementation Strategy

### Option 1: Add All Missing Labels at Once (Recommended)

Since LLMs can process the full receipt, extract all missing labels in a single unified flow:

**Phase 1: Currency** (existing)
- GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL

**Phase 2: Line Items** (existing)  
- PRODUCT_NAME, QUANTITY, UNIT_PRICE

**Phase 3: Transaction Context** (NEW)
- DATE, TIME, PAYMENT_METHOD, COUPON, DISCOUNT, LOYALTY_ID

**Benefits:**
- Single LLM call per receipt for all transaction context
- No need to break receipt into sections
- Efficient and clean

### Option 2: Add DATE/TIME Only First

Start with most critical labels:
- DATE
- TIME

Then add others incrementally.

---

## Current Labels vs ReceiptMetadata

| CORE_LABEL | ReceiptMetadata | LangGraph Needed? |
|------------|------------------|-------------------|
| MERCHANT_NAME | ✅ merchant_name | ❌ Skip |
| PHONE_NUMBER | ✅ phone_number | ❌ Skip |
| ADDRESS_LINE | ✅ address | ❌ Skip |
| DATE | ❌ | ✅ **YES** |
| TIME | ❌ | ✅ **YES** |
| PAYMENT_METHOD | ❌ | ✅ **YES** |
| COUPON | ❌ | ✅ **YES** |
| DISCOUNT | ❌ | ✅ **YES** |
| LOYALTY_ID | ❌ | ✅ **YES** |
| STORE_HOURS | ❌ | ❌ Skip |
| WEBSITE | ❌ | ❌ Skip |

---

## Recommended Labels to Add via LangGraph

### Essential (P0)
1. **DATE** - Critical for receipt validation
2. **TIME** - Often paired with date

### Important (P1)
3. **PAYMENT_METHOD** - Useful for expense categorization
4. **COUPON** - Marketing insights
5. **DISCOUNT** - Price analysis

### Optional (P2)
6. **LOYALTY_ID** - If you have users with loyalty programs

**Total: 6 labels to add**

---

## Next Steps

1. **Update models** to include transaction context labels
2. **Create Phase 3 node** (or extend Phase 1/2) for transaction context
3. **Use full receipt** - no sectioning needed
4. **Test and deploy**

Would you like me to implement this approach?

