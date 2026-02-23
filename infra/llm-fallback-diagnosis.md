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

## The 13 LLM Fallback Receipts

### Category 1: Math doesn't balance — missing labels (8 receipts)

These have LINE_TOTALs and/or GRAND_TOTAL but are missing SUBTOTAL, TAX, or DISCOUNT labels needed to balance the equation. The LLM confirms "no equation possible."

| Receipt | Merchant | LLM Conclusion |
|---------|----------|----------------|
| `7381dc76#1` | Sprouts | GRAND_TOTAL=41.87, 3 LINE_TOTALs sum to 33.39, no TAX/SUBTOTAL/DISCOUNT |
| `2050f988#1` | Sprouts | LINE_TOTALs sum to 24.97, total in text is 19.97, no SUBTOTAL/TAX/DISCOUNT |
| `232ae902#1` | Sprouts | GRAND_TOTAL=4.49, LINE_TOTALs sum to 5.49, difference is a DISCOUNT (1.00) not in equation |
| `63999f30#1` | Sprouts | LINE_TOTALs=33.47, GRAND_TOTAL=30.47, no SUBTOTAL/TAX/DISCOUNT |
| `94e4202b#1` | Sprouts | LINE_TOTALs=26.25, GRAND_TOTAL=22.25, $4.00 difference, no TAX/DISCOUNT |
| `a44e5a5e#2` | Sprouts | SUBTOTAL label not present |
| `c2bb8fff#2` | Ralphs | Missing SUBTOTAL, sum(LINE_TOTALs)+TAX ≠ GRAND_TOTAL |
| `3e071997#2` | In-N-Out | LINE_TOTALs=15.60, tax options (1.00 or 0.06), GRAND_TOTAL=11.91 — no combination works |

**Root cause:** These receipts have incomplete financial labels. The structured check correctly identifies the math doesn't balance, but there's nothing an LLM can do about it — the labels are simply missing. The LLM just confirms what Tier 1 already knew.

### Category 2: Discount not in equation model (2 receipts)

| Receipt | Merchant | LLM Conclusion |
|---------|----------|----------------|
| `b9fe3f38#4` | Moody Market | GRAND_TOTAL=21.00 ≠ SUBTOTAL(22.52)+TAX(0.00). Discount of 1.52 not in formula |
| `232ae902#1` | Sprouts | (also in Category 1) Discount=1.00 explains the gap but not in equation model |

**Root cause:** The equation model doesn't account for DISCOUNT in the `grand_total` formula (GRAND_TOTAL = SUBTOTAL + TAX). Should be: GRAND_TOTAL = SUBTOTAL + TAX - DISCOUNT.

### Category 3: No valid financial data (3 receipts)

| Receipt | Merchant | LLM Conclusion |
|---------|----------|----------------|
| `98491e9b#1` | Ocean Perinatal Medical | No valid numeric financial values at all |
| `2ab39193#1` | Ranch Hand BBQ | Only tip suggestion LINE_TOTALs, no subtotal/grand total |
| `eaeee5ef#1` | Sprouts | SUBTOTAL and GRAND_TOTAL are placeholders ("....") with no numeric values |

**Root cause:** These receipts genuinely have no financial data to validate. Either bad OCR, partial receipts, or non-standard formats.

### Category 4: OCR/label quality issue (1 receipt)

| Receipt | Merchant | LLM Conclusion |
|---------|----------|----------------|
| `fe9d469e#1` | Roast & Rice | Only 3 LINE_TOTALs (3.75, 4.17, 54.59), no SUBTOTAL/GRAND_TOTAL/TAX. "$20.80 is not a labeled financial value" |

**Root cause:** The receipt has a total that isn't labeled as GRAND_TOTAL.

## Diagnosis Summary

**None of the 13 LLM calls changed the outcome.** In every case the LLM confirmed what Tier 1 already determined: the equation doesn't balance or can't be formed. The LLM adds latency (~5-15s per receipt) and cost without providing actionable information.

### Why Tier 1 falls through to LLM

Looking at `_run_two_tier_core()` line 702:
```python
if not skip_fast_path and not math_issues and has_values:
```

Tier 1 returns `"__need_llm__"` when:
1. `math_issues` is non-empty (equation doesn't balance) — **Categories 1, 2**
2. `has_values` is False (no financial values extracted) — **Category 3**
3. `label_type_count < 2` (only one label type found) — **Category 4**

### Recommendation

For all 3 cases, the LLM provides no additional value:
1. **Math issues found:** Return `("fast_path", "issues", decisions)` with the math issues as decisions
2. **No values:** Return `("fast_path", "no_data", [])` — nothing to validate
3. **Single label type:** Already returns `("fast_path", "single_label", [])` — but only when `math_issues` is empty

**Additional fix:** Add DISCOUNT to the equation model: `GRAND_TOTAL = SUBTOTAL + TAX - DISCOUNT` to fix Category 2 receipts.

## Re-OCR Triggered

Only 1 receipt triggered re-OCR this run:

| Receipt | Gap |
|---------|-----|
| `490a4076#2` | $30.96 |
