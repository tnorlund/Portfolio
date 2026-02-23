# Financial Validation LLM Fallback Diagnosis

**Step Function Execution:** `13b07a86-e97d-4e83-a300-330e0f7d59a4`
**Date:** 2026-02-23
**Branch:** `feat/financial-math-chroma-llm-fallback`

## Overview

The two-tier financial validation has:
- **Tier 1 (fast path):** Deterministic equation balancing — extract labeled values, check math
- **Tier 2 (LLM fallback):** Called when Tier 1 can't resolve — asks LLM to propose equations

## Aggregate Results

| Outcome | Count |
|---------|-------|
| **Fast path balanced** (gap_below_threshold) | 415 |
| **Fast path skip** (no_phantom_values_filtered) | 50 |
| **Already attempted re-OCR** | 3 |
| **Triggered re-OCR** | 1 |
| **Hit LLM fallback** | 13 |
| **Total receipts with REOCR decision** | 469 |

**Key observation:** 415/469 (88.5%) resolve via fast path. Only 13 (2.8%) hit the LLM.

---

## Per-Receipt Deep Dive (8 "Missing Labels" Receipts)

Every receipt CAN balance with correct labels. The problem is never truly missing data —
it's mislabeled or unlabeled values. A word should NOT have both UNIT_PRICE and LINE_TOTAL;
the two must be distinguished.

### 1. `7381dc76#1` — Sprouts (GRAND_TOTAL=41.87)

**Labels present:** GRAND_TOTAL=41.87, LINE_TOTAL={3.99, 18.99, 10.41}, UNIT_PRICE={3.49, 4.99}
**Labels missing:** No SUBTOTAL, no TAX (CA produce = tax-exempt)

**Root cause:** 3.49 (Russet Potato 5 LB) and 4.99 (Green Beans) are labeled UNIT_PRICE
but they are the actual charges — they should be LINE_TOTAL. The values are NOT per-unit
prices; they are the total cost for the item.

**Proof:** 3.49 + 4.99 + 3.99 + 18.99 + 10.41 = **41.87** = GRAND_TOTAL

**Fix:** Relabel 3.49 (L21W1) and 4.99 (L23W1) from UNIT_PRICE → LINE_TOTAL.

---

### 2. `2050f988#1` — Sprouts (total=19.97)

**Labels present:** LINE_TOTAL={2.49, 1.50, 10.99, 9.99}
**Labels missing:** No GRAND_TOTAL label at all. No SUBTOTAL, no TAX.

**Root cause:** Complex transaction with voided items and price overrides:
- 9.99 (Ground Beef) was voided but still labeled LINE_TOTAL
- 4.99 (price override amount) has no label
- 19.97 appears in text as "BALANCE DUE" but is unlabeled

**Proof:** 2.49 + 1.50 + 10.99 + 4.99 = **19.97**

**Fix:** Add GRAND_TOTAL on 19.97 (L65W1). Add LINE_TOTAL on 4.99 (L61W1).
Invalidate 9.99 LINE_TOTAL (L60W1) — item was voided.

---

### 3. `232ae902#1` — Sprouts (GRAND_TOTAL=4.49)

**Labels present:** GRAND_TOTAL=4.49, LINE_TOTAL={3.49, 1.00, 1.00}
**Labels missing:** No SUBTOTAL, no TAX, no DISCOUNT

**Root cause:** Sprouts multi-buy deal line mislabeled as LINE_TOTAL.
```
LIMES                1.00   ← LINE_TOTAL (actual charge)
2 @ 2 FOR            1.00   ← labeled LINE_TOTAL but is deal detail (UNIT_PRICE)
```
The "2 @ 2 FOR 1.00" is deal metadata, not a separate charge.

**Proof:** 3.49 + 1.00 = **4.49** = GRAND_TOTAL (excluding duplicate)

**Fix:** Invalidate LINE_TOTAL on L16W5 (the deal-detail 1.00). It's UNIT_PRICE.

---

### 4. `63999f30#1` — Sprouts (GRAND_TOTAL=30.47)

**Labels present:** GRAND_TOTAL=30.47, LINE_TOTAL={7.99, 1.50, 3.00, 13.99, 6.99}
**Labels missing:** No SUBTOTAL, no TAX, no DISCOUNT

**Root cause:** Same Sprouts multi-buy pattern:
```
ORGANIC GREEN ONIONS  1.50   ← LINE_TOTAL (actual charge)
1 @ 2 FOR             3.00   ← labeled LINE_TOTAL but is deal price (UNIT_PRICE)
```

**Proof:** 7.99 + 1.50 + 13.99 + 6.99 = **30.47** = GRAND_TOTAL (excluding 3.00)

**Fix:** Invalidate LINE_TOTAL on L37W5 (the deal-detail 3.00). It's UNIT_PRICE.

---

### 5. `94e4202b#1` — Sprouts (GRAND_TOTAL=22.25)

**Labels present:** GRAND_TOTAL=22.25, LINE_TOTAL={2.99, 2.00, 4.00, 2.99, 3.99, 4.29, 5.99}
**Labels missing:** No SUBTOTAL, no TAX

**Root cause:** Same Sprouts multi-buy pattern:
```
ORG ITALIAN PARSLEY   2.00   ← LINE_TOTAL (actual charge)
1 @ 2 FOR             4.00   ← labeled LINE_TOTAL but is deal price (UNIT_PRICE)
```

**Proof:** 2.99 + 2.00 + 2.99 + 3.99 + 4.29 + 5.99 = **22.25** = GRAND_TOTAL

**Fix:** Invalidate LINE_TOTAL on L39W5 (the deal-detail 4.00). It's UNIT_PRICE.

---

### 6. `a44e5a5e#2` — Sprouts (GRAND_TOTAL=29.06)

**Labels present:** GRAND_TOTAL=29.06, LINE_TOTAL={3.99, 6.99, 17.98*, 0.10}
**Labels missing:** No SUBTOTAL (none printed on receipt), no TAX (CA grocery = tax-exempt)

**Root cause:** OCR comma vs period — "17,98" parsed as 1798.0 instead of 17.98.
Also, no SUBTOTAL exists on the receipt because there's no tax (SUBTOTAL = GRAND_TOTAL).

**Proof:** 3.99 + 6.99 + 17.98 + 0.10 = **29.06** = GRAND_TOTAL

**Fix:** OCR correction: "17,98" → "17.98". System should handle missing SUBTOTAL
when no TAX exists (SUBTOTAL = GRAND_TOTAL in that case).

---

### 7. `c2bb8fff#2` — Ralphs (GRAND_TOTAL=16.95)

**Labels present:** TAX=1.15, GRAND_TOTAL=16.95 — but ALL labels are INVALID status.
LINE_TOTAL labels exist on savings/change amounts (wrong).
**Labels missing:** No valid LINE_TOTAL for the actual product.

**Root cause:** OCR error — product price "15./0" should be "15.80". All financial
labels marked INVALID by validation. The "LINE_TOTAL" labels are on the wrong words
(savings amounts, not product prices).

**Proof:** 15.80 + 1.15 = **16.95** = GRAND_TOTAL

**Fix:** Re-OCR line 11 to fix "15./0" → "15.80". Then label it LINE_TOTAL (VALID).
Validate TAX=1.15 and GRAND_TOTAL=16.95.

---

### 8. `3e071997#2` — In-N-Out (GRAND_TOTAL=11.91)

**Labels present:** LINE_TOTAL={4.75, 10.85}, TAX="1" (partial), GRAND_TOTAL=11.91
**Labels missing:** SUBTOTAL, second half of TAX

**Root cause:** Multiple mislabeling issues:
- "10.85" (COUNTER-Eat In) is the **SUBTOTAL**, not a LINE_TOTAL
- TAX is split: "1" (L21W1) labeled TAX, ".06" (L22W1) mislabeled MERCHANT_NAME
- Dbl-Dbl burger price garbled by OCR ("b" and "-10" on different lines)

**Proof:** SUBTOTAL(10.85) + TAX(1.06) = **11.91** = GRAND_TOTAL

**Fix:** Relabel 10.85 (L20W1) from LINE_TOTAL → SUBTOTAL. Relabel ".06" (L22W1)
from MERCHANT_NAME → TAX.

---

## Systematic Patterns

### Pattern 1: Sprouts multi-buy deal lines (4 receipts)

Sprouts prints promotions as `QTY @ N FOR PRICE` below the product line.
The LayoutLM model labels the deal price as LINE_TOTAL, double-counting it.

```
PRODUCT_NAME          actual_charge   ← real LINE_TOTAL
QTY @ N FOR           deal_price      ← should be UNIT_PRICE, NOT LINE_TOTAL
```

**Affected:** 232ae902#1, 63999f30#1, 94e4202b#1, (also 2050f988#1 has "1 @ 2 for 3.00")

### Pattern 2: UNIT_PRICE vs LINE_TOTAL confusion (1 receipt)

Values that are the actual charge for an item are labeled UNIT_PRICE instead of LINE_TOTAL.
A word should NOT have both labels — the two are semantically distinct:
- **UNIT_PRICE** = price per unit (per item, per lb, per deal bundle)
- **LINE_TOTAL** = actual amount charged for that line item

**Affected:** 7381dc76#1

### Pattern 3: SUBTOTAL mislabeled as LINE_TOTAL (1 receipt)

Order subtotals (e.g., "COUNTER-Eat In 10.85") labeled as LINE_TOTAL.

**Affected:** 3e071997#2

### Pattern 4: OCR errors on prices (2 receipts)

- "15./0" should be "15.80" (Ralphs)
- "17,98" should be "17.98" (Sprouts — comma vs period)

**Affected:** c2bb8fff#2, a44e5a5e#2

### Pattern 5: Missing GRAND_TOTAL label (1 receipt)

The total exists in the text but has no GRAND_TOTAL label.

**Affected:** 2050f988#1

### Pattern 6: Split TAX value across words (1 receipt)

TAX "1.06" is split as "1" (labeled TAX) and ".06" (mislabeled MERCHANT_NAME).

**Affected:** 3e071997#2

---

## TIP Label Research

### Current Status
- TIP has **23 word labels** in DynamoDB (15 VALID, 8 INVALID)
- TIP is in `FINANCIAL_MATH_LABELS` (receipt_agent constants) but **NOT** in `CORE_LABELS`
- 20 receipts (2.9%) have non-zero tips totaling $111.24
- ~100+ receipts are from tip-eligible categories (restaurants, bars, coffee shops, barber shops)

### TIP vs DISCOUNT Comparison

| Metric | TIP | DISCOUNT |
|--------|-----|----------|
| Total word labels | 23 | 2,141 |
| VALID labels | 15 | 429 |
| Valid rate | 65% | 20% |
| In CORE_LABELS? | **NO** | YES |
| In FINANCIAL_MATH_LABELS? | YES | YES |

### Impact on Financial Equation

Without TIP, tipped receipts fail the equation:
```
East Coast Bagel: SUBTOTAL($22.00) + TIP($2.20) = GRAND_TOTAL($24.20)
Little Calf:      SUBTOTAL($8.25)  + TIP($1.65) = GRAND_TOTAL($9.90)
Five07 Coffee:    SUBTOTAL($14.40) + TAX($1.04) + TIP($1.44) = GRAND_TOTAL($16.88)
```

### Recommendation

**Add TIP to CORE_LABELS.** It's already recognized as financially important
(in FINANCIAL_MATH_LABELS). Without it in CORE_LABELS, the LayoutLM model can't
learn to label tips, and the financial validator incorrectly flags tipped receipts.

---

## Recommendations

### Immediate (fix the LLM fallback)

1. **Skip LLM tier entirely** when Tier 1 finds math issues or no data — return
   structured result directly. The LLM never changes the outcome.

2. **Add DISCOUNT to the equation model:**
   `GRAND_TOTAL = SUBTOTAL + TAX + TIP - DISCOUNT`

### Label quality (fix the root causes)

3. **Fix Sprouts multi-buy deal labels** — invalidate LINE_TOTAL on "QTY @ N FOR PRICE"
   lines; they should be UNIT_PRICE.

4. **Add TIP to CORE_LABELS** — enables LayoutLM training and fixes tipped receipt equations.

5. **Handle missing SUBTOTAL** — when no TAX exists (grocery receipts),
   `sum(LINE_TOTALs) = GRAND_TOTAL` is valid without SUBTOTAL.

### OCR quality

6. **Fix comma-vs-period OCR** ("17,98" → "17.98")
7. **Re-OCR garbled prices** ("15./0" → "15.80")

## Re-OCR Triggered

Only 1 receipt triggered re-OCR this run:

| Receipt | Gap |
|---------|-----|
| `490a4076#2` | $30.96 |
