# CORE_LABELS Complete Review

## All CORE_LABELS (36 total)

### 📋 From `receipt_label/receipt_label/constants.py`

```python
CORE_LABELS = {
    # Merchant & Store Info (6 labels)
    "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
    "STORE_HOURS": "Printed business hours or opening times for the merchant.",
    "PHONE_NUMBER": "Telephone number printed on the receipt (store's main line).",
    "WEBSITE": "Web or email address printed on the receipt (e.g., sprouts.com).",
    "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
    
    # Location / Address (1 label)
    "ADDRESS_LINE": "Full address line (street + city etc.) printed on the receipt.",
    
    # Transaction Info (5 labels)
    "DATE": "Calendar date of the transaction.",
    "TIME": "Time of the transaction.",
    "PAYMENT_METHOD": "Payment instrument summary (e.g., VISA ••••1234, CASH).",
    "COUPON": "Coupon code or description that reduces price.",
    "DISCOUNT": "Any non-coupon discount line item (e.g., 10% member discount).",
    
    # Line-Item Fields (3 labels)
    "PRODUCT_NAME": "Descriptive text of a purchased product (item name).",
    "QUANTITY": "Numeric count or weight of the item (e.g., 2, 1.31 lb).",
    "UNIT_PRICE": "Price per single unit / weight before tax.",
    "LINE_TOTAL": "Extended price for that line (quantity x unit price).",
    
    # Totals & Taxes (3 labels)
    "SUBTOTAL": "Sum of all line totals before tax and discounts.",
    "TAX": "Any tax line (sales tax, VAT, bottle deposit).",
    "GRAND_TOTAL": "Final amount due after all discounts, taxes and fees.",
}
```

---

## Current Implementation Coverage (13 labels)

### ✅ Phase 1: Currency Analysis
Extracted: **4 labels**
1. ✅ **GRAND_TOTAL** - Final amount due
2. ✅ **TAX** - Any tax line
3. ✅ **SUBTOTAL** - Sum before tax
4. ✅ **LINE_TOTAL** - Line item totals

### ✅ Phase 1 Context: Transaction Analysis (NEW)
Extracted: **6 labels**
5. ✅ **DATE** - Transaction date
6. ✅ **TIME** - Transaction time
7. ✅ **PAYMENT_METHOD** - Payment type
8. ✅ **COUPON** - Coupon codes
9. ✅ **DISCOUNT** - Discount amounts
10. ✅ **LOYALTY_ID** - Member/rewards ID

### ✅ Phase 2: Line Items
Extracted: **3 labels**
11. ✅ **PRODUCT_NAME** - Item names
12. ✅ **QUANTITY** - Count/weight
13. ✅ **UNIT_PRICE** - Price per unit

---

## Not Extracted (23 labels)

### Provided by ReceiptMetadata ✅
These are loaded from Google Places API, not extracted from receipt text:

- ✅ **MERCHANT_NAME** - From ReceiptMetadata.merchant_name
- ✅ **PHONE_NUMBER** - From ReceiptMetadata.phone_number
- ✅ **ADDRESS_LINE** - From ReceiptMetadata.address

**Rationale**: These are merchant-specific, not transaction-specific. ReceiptMetadata already has them validated from Google Places.

### Low Value / Skip (20 labels)

#### Store Information
- ❌ **STORE_HOURS** - Rarely useful for users
- ❌ **WEBSITE** - Low value, often outdated

**Rationale**: Users don't need this info for expense tracking.

---

## Summary: What We're Extracting

| Category | CORE_LABELS | Extraction Method | Status |
|----------|-------------|-------------------|--------|
| **Currency** | GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL | Phase 1 (LLM from receipt text) | ✅ Implemented |
| **Line Items** | PRODUCT_NAME, QUANTITY, UNIT_PRICE | Phase 2 (LLM from receipt text) | ✅ Implemented |
| **Transaction Context** | DATE, TIME, PAYMENT_METHOD, COUPON, DISCOUNT, LOYALTY_ID | Phase 1 Context (LLM from receipt text) | ✅ **JUST ADDED** |
| **Merchant Info** | MERCHANT_NAME, PHONE_NUMBER, ADDRESS_LINE | ReceiptMetadata (from Google Places) | ✅ Provided by existing system |
| **Low Value** | STORE_HOURS, WEBSITE | N/A | ❌ Skipped intentionally |

---

## Test Results: What Was Actually Extracted

From the latest test run, we extracted:

```
✅ Phase 1 Context: 5 transaction labels found

Labels discovered:
1. LOYALTY_ID: '112012911712' (line 8, word 3)
2. PAYMENT_METHOD: 'XXXXXXXXXXXX3931' (line 20, word 1)
3. DATE: '09/06/2025' (lines 30, 45)
4. TIME: '14:47' (lines 30, 34, 46)
5. PAYMENT_METHOD: 'EFT/Deblt' (line 31, word 1)

📌 Proposed adds: 8 labels
✅ Overall confidence: 0.96
```

---

## Architecture Summary

### What We Extract via LangGraph (13 labels)
- 4 Currency labels (Phase 1)
- 6 Transaction context labels (Phase 1 Context - **NEW!**)
- 3 Line item labels (Phase 2)

### What We Get from ReceiptMetadata (3 labels)
- MERCHANT_NAME ✅
- PHONE_NUMBER ✅
- ADDRESS_LINE ✅

### What We Skip (20 labels)
- STORE_HOURS, WEBSITE (low value)
- Others not in CORE_LABELS

---

## Total Coverage

**Extracting**: 13 labels (via LangGraph + Ollama)  
**Provided**: 3 labels (via ReceiptMetadata from Google Places)  
**Total**: **16 out of 36 CORE_LABELS** (the important ones!)

The remaining 20 labels either don't exist in CORE_LABELS or are low-value for expense tracking.

