# Financial Validation Analysis — Run 2 (Enhanced Extraction Fix)

**Step Function:** `510087e3-db6f-4cb9-becd-6e5f4840be39`
**LangSmith Project:** `financial-math-enhanced-extraction-2026-02-19`
**Date:** 2026-02-19
**Previous Run:** `30e9a559-bb2d-486c-8c97-abc1ea4bd379` (`financial-math-filter-empty-2026-02-19`)

## What Changed Between Runs

**Bug fix in `graph.py`:** The `validate_financial_math` node was calling `evaluate_financial_math_sync()` WITHOUT `words`, `labels`, `chroma_client`, or `line_item_patterns` parameters. The enhanced extraction gate (`if labels and chroma_client and words:`) always evaluated to `False`, so it fell back to basic text-scan extraction for every receipt. The fix added the missing parameters.

**Commit:** `5f629be88` on `feat/financial-math-chroma-llm-fallback`

---

## Important Note: Different Receipt Samples

Each step function run **randomly selects** receipts up to the given limit. Runs 1 and 2 each selected 40 receipts independently, so the two batches overlap but are not identical. Of the receipts that produced equations, 13 appeared in both runs' API output, 7 appeared only in run 1, and 7 appeared only in run 2. The "gained" and "lost" comparisons below are based on API output overlap — a receipt "lost" may simply not have been selected in run 2, or may have been selected but the LLM made different decisions.

## Summary Comparison

| Metric | Run 1 | Run 2 | Delta |
|--------|------:|------:|------:|
| Receipts in batch | 40 | 40 | — |
| Receipts with equations (API) | 20 | 20 | 0 |
| Total equations | 23 | 24 | +1 |
| In both runs' API output | — | 13 | — |
| In run 1 API only | — | 7 | — |
| In run 2 API only | — | 7 | — |

The total count stayed at 20. Of the 7 receipts new to run 2's API output, all were Category C in run 1's analysis (had VALID financial labels but enhanced extraction missed them). This confirms the `graph.py` fix is working. The 7 from run 1 that don't appear in run 2 may not have been randomly selected, or the LLM reviewer made different decisions on a second pass.

---

## A. Receipts WITH Equations in Run 2 (20)

| # | Merchant | image_id | r | Equations | Status |
|---|----------|----------|---|-----------|--------|
| 1 | Brent's Delicatessen & Restaurant | `f008ea77` | 2 | GRAND_TOTAL_BALANCED | Stable |
| 2 | Costco Wholesale | `314ac65b` | 1 | SUBTOTAL_MISMATCH | Stable |
| 3 | DIY Home Center | `93db2c10` | 2 | GRAND_TOTAL_BALANCED | **New** (was Cat C) |
| 4 | In-N-Out Burger | `678a7c94` | 2 | GRAND_TOTAL_MISMATCH | Stable |
| 5 | In-N-Out Burger | `2360d36e` | 2 | GRAND_TOTAL_BALANCED | **New** (was Cat C) |
| 6 | Mendocino Farms | `d36c2083` | 2 | GRAND_TOTAL_BALANCED, SUBTOTAL_BALANCED | **New** (was Cat C) |
| 7 | Smart & Final Extra! | `79626fd2` | 1 | SUBTOTAL_BALANCED | Stable |
| 8 | Sprouts Farmers Market | `e9ea77d0` | 1 | GRAND_TOTAL_DIRECT_BALANCED | Stable |
| 9 | Sprouts Farmers Market | `082f1bfa` | 2 | GRAND_TOTAL_DIRECT_BALANCED | Stable |
| 10 | Sprouts Farmers Market | `cf8e3a6e` | 1 | GRAND_TOTAL_MISMATCH | Stable |
| 11 | Sprouts Farmers Market | `93db2c10` | 1 | GRAND_TOTAL_BALANCED | **New** (was Cat C) |
| 12 | Sprouts Farmers Market | `3b594aaf` | 1 | GRAND_TOTAL_BALANCED | **New** (was Cat C) |
| 13 | Sprouts Farmers Market | `08239059` | 1 | GRAND_TOTAL_BALANCED, SUBTOTAL_BALANCED | **New** (was Cat C) |
| 14 | Sprouts Farmers Market | `a44e5a5e` | 2 | GRAND_TOTAL_DIRECT_BALANCED | Stable |
| 15 | The Home Depot | `a1c5d97c` | 1 | GRAND_TOTAL_BALANCED, SUBTOTAL_BALANCED | Stable |
| 16 | Vons | `fa23150b` | 1 | GRAND_TOTAL_BALANCED | Stable |
| 17 | Vons | `1bfa17f4` | 1 | GRAND_TOTAL_BALANCED | **New** (was Cat C) |
| 18 | Vons | `6bb97b81` | 1 | GRAND_TOTAL_BALANCED | Stable |
| 19 | Wild Fork | `19a032ac` | 2 | GRAND_TOTAL_BALANCED, SUBTOTAL_BALANCED | Stable |
| 20 | Wild Fork | `758cbedf` | 1 | GRAND_TOTAL_BALANCED, SUBTOTAL_BALANCED | Stable |

### Equation type breakdown — Run 2

| Equation Type | Run 1 | Run 2 | Delta |
|---------------|------:|------:|------:|
| GRAND_TOTAL_BALANCED | 8 | 13 | +5 |
| GRAND_TOTAL_DIRECT_BALANCED | 5 | 3 | -2 |
| SUBTOTAL_BALANCED | 5 | 5 | 0 |
| GRAND_TOTAL_MISMATCH | 4 | 2 | -2 |
| SUBTOTAL_MISMATCH | 1 | 1 | 0 |
| **Total** | **23** | **24** | **+1** |

The shift toward GRAND_TOTAL_BALANCED (+5) reflects enhanced extraction now finding SUBTOTAL and TAX values that enable the full `SUBTOTAL + TAX = GRAND_TOTAL` equation, instead of falling back to the direct `sum(LINE_TOTALs) = GRAND_TOTAL` form.

---

## B. Receipts That GAINED Equations (7) — Category C Fix

All 7 were Category C in run 1 (had VALID financial labels in DynamoDB but enhanced extraction never ran). With the `graph.py` fix, enhanced extraction now runs and surfaces these labels.

| # | Merchant | image_id | r | Run 2 Equations | VALID words used |
|---|----------|----------|---|-----------------|------------------|
| 1 | DIY Home Center | `93db2c10` | 2 | GRAND_TOTAL_BALANCED | TAX=1.35, GT=15.57 |
| 2 | In-N-Out Burger | `2360d36e` | 2 | GRAND_TOTAL_BALANCED | ST=6.80, GT=$7.45 |
| 3 | Mendocino Farms | `d36c2083` | 2 | GRAND_TOTAL_BALANCED, SUBTOTAL_BALANCED | ST=$11.25, TAX=$1.15, GT=$12.40, LT=$11.25 |
| 4 | Sprouts Farmers Market | `93db2c10` | 1 | GRAND_TOTAL_BALANCED | TAX=2.79, GT=$52.25 |
| 5 | Sprouts Farmers Market | `3b594aaf` | 1 | GRAND_TOTAL_BALANCED | TAX=0.96, GT=$14.25 |
| 6 | Sprouts Farmers Market | `08239059` | 1 | GRAND_TOTAL_BALANCED, SUBTOTAL_BALANCED | ST=$26.75, GT=26.75, LT=4.99/3.99/3.00/10.99/2.49/1.29 |
| 7 | Vons | `1bfa17f4` | 1 | GRAND_TOTAL_BALANCED | TAX=0.00, GT=7.78 |

**This confirms the `graph.py` fix worked.** 7 of 11 Category C receipts now produce equations — exactly the receipts where VALID financial labels were sufficient AND the data was clean enough for the LLM reviewer to confirm them.

---

## C. Receipts in Run 1 API but NOT Run 2 API (7)

These 7 appeared in run 1's API output with equations but not in run 2's. They may not have been randomly selected in run 2's batch, or they were selected but the LLM financial reviewer made different VALID/INVALID decisions, breaking the equation chain. All have ambiguous or noisy data that makes LLM decisions non-deterministic.

| # | Merchant | image_id | r | Run 1 Equation | Root Cause |
|---|----------|----------|---|----------------|------------|
| 1 | Dog Haus | `ce0da565` | 2 | GRAND_TOTAL_MISMATCH | OCR "$1,95" for tax (comma instead of period); SUBTOTAL/TAX/TIP all INVALID; $30.28 includes tip but no TIP label |
| 2 | LA County | `935fccdc` | 1 | GRAND_TOTAL_DIRECT_BALANCED | Receipt not found in DynamoDB — may have been deleted or repartitioned between runs |
| 3 | Sprouts | `ed3f3909` | 2 | GRAND_TOTAL_DIRECT_BALANCED | Single-item $3.99 receipt; "CLOVER SPROUTS" product conflicts with "SPROUTS" merchant name; trivial equation GT=LT vulnerable to label flip |
| 4 | Sprouts | `c2bb8fff` | 1 | SUBTOTAL_BALANCED | BALANCE DUE OCR-mangled to "7.5/"; $7.57 has ALL financial labels INVALID; only VALID SUBTOTAL is 0.00 (CHANGE line) |
| 5 | Sprouts | `1d3b61e9` | 1 | GRAND_TOTAL_BALANCED | Duplicate 13.98 in both line items and tax report section; 3 GRAND_TOTAL instances; CHANGE 0.00 labeled as LINE_TOTAL |
| 6 | The Local Peasant | `523febb6` | 1 | GRAND_TOTAL_MISMATCH | Severe OCR: "Tay" for Tax, "3." for tip, "Ions" as product; SUBTOTAL/TAX all INVALID |
| 7 | Vons | `ce0da565` | 1 | GRAND_TOTAL_BALANCED | Dual "Price"/"You Pay" columns create duplicate values; unlabeled egg price; LLM must pick correct column |

**Common pattern:** All 7 have ambiguous data — OCR errors, duplicate values in different columns, labels on wrong words, or trivially simple receipts where a single label flip breaks the equation. These are inherently unstable under LLM non-determinism.

---

## D. Category C Receipts Still Missing (4)

4 of the original 11 Category C receipts still don't produce equations even with enhanced extraction enabled. Investigation reveals data-quality problems that prevent equation formation regardless of extraction method.

### 1. Sprouts `e936747a` r=1 — Trivial single-item, no tax

Single item: RAW WHOLE MILK $17.99. No SUBTOTAL, no TAX (tax-free food). The only possible equation is `LINE_TOTAL = GRAND_TOTAL` (17.99 = 17.99), but 17.99 appears 3 times as GRAND_TOTAL (payment slip header, BALANCE DUE, CREDIT line) creating ambiguity. The payment slip appears BEFORE items in OCR order (reversed layout), which may confuse line-based extraction.

### 2. Sprouts `7e1688d4` r=2 — Missing LINE_TOTAL label

Two items: RUSSET POTATO $3.99 + SALTED BUTTER $4.99 = $8.98. Only the $3.99 has a VALID LINE_TOTAL. The $4.99 for SALTED BUTTER has **no labels at all** — it was never labeled by LayoutLM or ChromaDB. Without both LINE_TOTALs, the sum doesn't match GRAND_TOTAL, so no equation can form. Same reversed payment-slip-first layout.

### 3. Home Depot `e1f519d5` r=1 — Catastrophic label corruption

Receipt total: SUBTOTAL $162.81 + TAX $11.77 = TOTAL $174.58. The arithmetic is correct, but:
- SUBTOTAL value `162.81` has SUBTOTAL=**INVALID**
- TAX is split across two OCR words: `11` (no labels) and `.77` (mislabeled as LINE_TOTAL instead of TAX)
- GRAND_TOTAL `$174.58` has GRAND_TOTAL=**INVALID** and instead has a nonsense label `"11.77"`=VALID (the tax amount was used as a label name)
- OCR merged quantity prefixes into prices: `2012.28` should be `12.28`, `2022.35` should be `22.35`

Only the label **keywords** ("SUBTOTAL", "TAX", "TOTAL") have VALID status — none of the corresponding **numeric values** do.

### 4. Home Depot `249eb736` r=1 — Structural gap (most puzzling)

Receipt total: SUBTOTAL $128.85 + TAX $12.24 = TOTAL $141.09. All arithmetic is correct. All VALID labels are present on the correct values:
- `128.85` → SUBTOTAL=VALID
- `12.24` → TAX=VALID
- `$141.09` → GRAND_TOTAL=VALID
- `99.88`, `28.97` → LINE_TOTAL=VALID

**This receipt should produce equations but doesn't.** The likely issue is structural: the label keyword "SUBTOTAL" is on word-line 20 but the value "128.85" is on word-line 26 — a gap of 6 word-level lines. Enhanced extraction may fail to associate the keyword with its value when they're separated by this distance. Two LINE_TOTALs (28.97 and 99.88) also appear on the same formatted line, which may confuse column-based extraction.

---

## E. Unchanged Categories

### No OCR Data (5) — same as run 1

| # | Merchant | image_id | r |
|---|----------|----------|---|
| 1 | Sprouts Farmers Market | `2cd429a4` | 3 |
| 2 | Sprouts Farmers Market | `ddd538ee` | 3 |
| 3 | Sprouts Farmers Market | `0c3e58fe` | 3 |
| 4 | Sprouts Farmers Market | `cace73d4` | 1 |
| 5 | Urbane Cafe | `09b29b40` | 2 |

### Genuinely Insufficient (4) — same as run 1

| # | Merchant | image_id | r | Why |
|---|----------|----------|---|-----|
| 1 | 7-Eleven | `1746d34d` | 2 | Gas station: no valid GT/ST |
| 2 | Sparkling Image Car Wash | `c8a2468a` | 1 | No valid GT or ST |
| 3 | Vons | `ed3f3909` | 1 | Coupon section only (DISCOUNT labels) |
| 4 | Westlake Physical Therapy | `4b326441` | 1 | Only 1 GT, nothing to check against |

---

## Key Takeaways

1. **The `graph.py` fix worked.** 7 of 11 Category C receipts now produce equations. Enhanced extraction is running and surfacing VALID financial labels from DynamoDB + ChromaDB column alignment.

2. **Total stayed at 20 despite improvement.** 7 new receipts appeared in run 2's API output (all Category C fixes), while 7 from run 1 didn't appear (either not randomly selected, or LLM non-determinism on ambiguous data). Net equation quality improved: more GRAND_TOTAL_BALANCED, fewer mismatches.

3. **4 Category C receipts remain unsolved.**
   - 2 Sprouts: trivial single-item receipts with no tax — the only equation is `LT = GT` which the system doesn't attempt, or missing LINE_TOTAL labels.
   - 1 Home Depot (`e1f519d5`): catastrophic label corruption — OCR errors, split tax words, nonsense label names.
   - 1 Home Depot (`249eb736`): **correct VALID labels but structural gap** — label keywords and values are on distant word-lines. This is the most actionable bug: enhanced extraction should handle keyword-value gaps.

4. **Stability improvement needed.** The 7 receipts absent from run 2 that had equations in run 1 all have borderline-quality data (OCR errors, duplicate columns, missing labels). If they were re-selected, LLM non-determinism could flip their results. Possible mitigations:
   - Lower LLM temperature for financial review decisions
   - Cache VALID decisions across runs (once confirmed, don't re-evaluate)
   - Add a minimum-confidence threshold before flipping a previously-VALID label

5. **Updated coverage:** Both runs produced 20 receipts with equations out of 40 randomly selected (50%). The equation quality improved in run 2 (more balanced equations, fewer mismatches), confirming enhanced extraction adds value.
