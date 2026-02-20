# Low-Label Receipt Investigation

## Problem

After adding the improved fast path (junk filter + GT_DIRECT equation), **76 receipts** are classified as "fast path balanced" with only **1 financial label type**. No real equation was checked — these are false confidence.

| Single-label type | Count |
|-------------------|-------|
| GRAND_TOTAL       | 56    |
| SUBTOTAL          | 8     |
| LINE_TOTAL        | 7     |
| UNIT_PRICE        | 4     |
| TAX               | 1     |

## Root Causes

Investigation of 6 representative receipts revealed **three distinct patterns**.

### Pattern 1: Genuinely Single-Label (terminal/service receipts)

**Examples:** Italia Deli (`7ca97b51` r=1), Roast & Rice (`6fa7cc7a` r=1)

These are customer copies from restaurants/delis. They show an "Amount: $53.60" with blank "Tip:" and "Total:" lines for the customer to fill in. There is truly only one financial value on the paper.

**What exists in DynamoDB:**
```
  L25 W1: "$53.6"  → GRAND_TOTAL=VALID
  L17 W1: "Gratuity:"  → no labels (blank line)
  L18 W1: "Total:"     → OTHER=VALID (blank)
```

The `scan_receipt_text` function would correctly identify these as terminal receipts with one amount and blank tip/total lines.

**Verdict:** Correctly classified. No cross-check is possible or needed.

### Pattern 2: Missing Labels in DynamoDB

**Example:** Sprouts (`34eecba2` r=1)

The receipt has a terminal slip with "Total: USD$ 18.99" and an itemized section with "RAW WHOLE MILK 18.99". The LINE_TOTAL on the milk item is labeled VALID, but the "18.99" on the terminal slip's "Total" line has **no financial label at all** — neither GRAND_TOTAL nor anything else.

**What exists in DynamoDB:**
```
  L36 W1: "18.99"  → LINE_TOTAL=VALID    (milk item)
  L27 W2: "18.99"  → NO LABELS           (terminal "Total" line!)
  L38 W1: "18.99"  → NO LABELS           ("BALANCE DUE" line)
  L40 W1: "$18.99" → NO LABELS           (credit payment line)
```

The `extract_financial_values_enhanced` pipeline only considers words that **have a financial label in DynamoDB** — so the terminal total is invisible. The `scan_receipt_text` fallback would find it (via the "Total" keyword), but the fallback only fires when enhanced extraction returns **zero** values. Since one LINE_TOTAL was found, the text scan never runs.

**Verdict:** Labeling gap — LayoutLM didn't assign GRAND_TOTAL to the terminal total line.

### Pattern 3: Column Filter Rejecting Valid Labels (MOST COMMON)

**Examples:** Sprouts `d48a0ef4` r=1, Sprouts `d5f79185` r=1, Sprouts `e62eadbe` r=1

These are Sprouts two-section receipts:
- **Top section:** Terminal authorization slip ("Total: USD$ 24.98")
- **Bottom section:** Itemized receipt ("ORG PSTR RSED EGGS 13.99", "RAW WHOLE MILK 10.99")

Both sections have VALID financial labels in DynamoDB:
```
  L13 W2: "24.98"  → GRAND_TOTAL=VALID   (terminal slip, right-edge x ≈ 0.45)
  L21 W1: "13.99"  → LINE_TOTAL=VALID    (itemized section, right-edge x ≈ 0.85)
  L22 W1: "10.99"  → LINE_TOTAL=VALID    (itemized section, right-edge x ≈ 0.85)
```

Math: 13.99 + 10.99 = 24.98. This should balance!

**But the column filter kills it.** `extract_financial_values_enhanced` computes a single "price column" x-position (median right-edge of all VALID financial words). When the terminal total at x≈0.45 and the itemized prices at x≈0.85 are averaged, the median lands somewhere in between, and the `x_tolerance = 0.20` rejects one group. Only the GRAND_TOTAL (or only the LINE_TOTALs) survive, and you end up with a single label type.

**Verdict:** Column filter false positive. Both label groups are correct — they're just in different visual sections of the receipt.

## Quantifying the Impact

**Likely breakdown of 76 single-label receipts:**

| Pattern | Est. Count | Action |
|---------|-----------|--------|
| Column filter false positive (two-section receipts) | ~40-50 | Fix column filter |
| Genuinely single-label (terminal/service copies) | ~15-20 | No fix needed |
| Missing labels in DynamoDB | ~10-15 | Fix labeling pipeline |

The column filter issue disproportionately affects **Sprouts** (largest merchant by receipt count, all use two-section layout) but likely also affects other merchants with terminal slip + itemized receipt combos.

## Existing Mechanisms That Could Help

### `scan_receipt_text` (Text Scanning)

Currently used as a **fallback only** — fires when enhanced extraction returns zero values. For Pattern 2 (missing labels), the text scan would find the unlabeled "Total: 18.99" via keyword matching. But it never runs because at least one labeled value was already found.

**Potential fix:** Run text scan as a **supplement** when fewer than 2 label types are found, not just as a fallback for zero values.

### `_COLUMN_FILTERED_LABELS` (Column Filter)

Currently applies a single column reference across the entire receipt. This works for single-section receipts but fails on two-section layouts.

**Potential fixes:**
1. **Section-aware columns:** Detect the summary boundary (first SUBTOTAL/GRAND_TOTAL line) and compute separate column references above and below it.
2. **Skip column filter for VALID labels:** If a label already has `validation_status=VALID`, trust it regardless of x-position. Column filtering was designed to catch OCR noise, not reject validated labels.
3. **Increase tolerance for two-section receipts:** If the receipt has values at two distinct x-clusters, widen the tolerance.

### `_build_valid_decisions` (Fast Path Output)

Currently marks all financial values as VALID when math balances. For single-label receipts where no equation was actually checked, these VALID decisions are misleading.

**Potential fix:** Only emit VALID decisions when at least 2 distinct label types participated in a real equation (e.g., GT+ST, GT+LT+TAX, ST+LT). Single-label receipts should get no decisions rather than fake-VALID ones.

## Recommended Approach

### Short term: Guard against false VALID decisions
Add a check before the fast path declares balanced: require at least 2 distinct label types with values. Single-label receipts skip the fast path and either go to the LLM (which can read the raw text) or return no decisions.

### Medium term: Fix the column filter for two-section receipts
Option 2 (skip column filter for VALID labels) is the simplest and most impactful — it trusts the label evaluator's existing VALID decisions. This alone would recover ~40-50 receipts from single-label status back to multi-label balanced.

### Long term: Use text scan as supplement
For receipts where the enhanced extraction finds <2 label types, merge text-scan findings (especially GRAND_TOTAL from "Total" keyword lines) as supplemental candidates. This covers Pattern 2 (missing labels) without waiting for the LayoutLM model to improve.
