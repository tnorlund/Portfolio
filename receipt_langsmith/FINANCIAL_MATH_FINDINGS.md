# Financial Math Validation — Findings & Design

## Problem Statement

The financial math overlay visualizes how receipt math is validated. VALID decisions (math balanced) were shown as a flat "N confirmed ✓" card. We want to show them as proper equation structures.

## What the Financial Subagent Actually Validates

The subagent checks **two types of math**:

### Column Math (Vertical Sums)
- **GRAND_TOTAL = SUBTOTAL + TAX** — does the grand total equal subtotal plus tax?
- **SUBTOTAL = sum(LINE_TOTAL) - sum(DISCOUNT)** — does the subtotal equal the sum of line items minus discounts?

### Row Math (Horizontal Per-Item)
- **LINE_TOTAL = QUANTITY × UNIT_PRICE** — does each line item's total match qty × price?

When everything balances, the subagent emits **VALID** for every participating word. When something doesn't balance, it emits issue types: `GRAND_TOTAL_MISMATCH`, `SUBTOTAL_MISMATCH`, `LINE_ITEM_MISMATCH`.

## Key Findings

### 1. OCR line_id ≠ Visual Row

OCR produces `line_id` values that represent individual text chunks, not visual rows. Multi-column receipts (Vons, Wild Fork) have product names, prices, and totals on **different line_ids** even though they're on the same visual row.

**Vons example** (receipt 6bb97b81):
| Item | Product line_id | UNIT_PRICE line_id | LINE_TOTAL line_id |
|------|----------------|-------------------|-------------------|
| FIREWOOD BNDL | 9 | 23 | 30 |
| SIG RED SDLS GRAPE | 11 | 24 | 31 |
| ORG BANANAS | 13 | 26 | 33 |

Product names are on lines 9-17, prices on lines 22-28, totals on lines 29-35. The gap between columns is 10-20 line_ids.

**Sprouts example** (receipt 9a3a28ce):
```
Line 19: 0.86 [QUANTITY]
Line 20: @
Line 21: $3.99 [UNIT_PRICE]
```
Even "0.86 lb @ $3.99 / lb" — a single visual line — gets split across 3 OCR line_ids.

### 2. The Subagent Uses VisualLines, Not line_ids

The subagent handles this correctly internally using `VisualLine` objects that group words by y-coordinate proximity (not OCR line_id). The row-level math check (`check_line_item_math`) groups by `line_index` from VisualLine.

**However**, VALID decisions only output `line_id` and `word_id` (DynamoDB coordinates). The `line_index` (visual row) is not included in the output. The viz cache builder only has access to `line_id`, so it cannot reconstruct the visual row groupings.

### 3. Vons Receipts Misclassified as "service"

**Root cause**: When `line_item_patterns` (from pattern discovery LLM) is missing or lacks `receipt_type`, the text-scan fallback classifies any receipt with SUBTOTAL or TAX as "service". Every grocery receipt has both.

**Result**: All Vons receipts in the cache had `receipt_type="service"` and **zero equations**.

**Fix applied**: Added a label-based check — if the receipt has 2+ LINE_TOTAL labels, it's classified as "itemized" regardless of text-scan heuristics. Also fixed the text-scan logic so SUBTOTAL/TAX presence alone doesn't trigger "service".

### 4. Wild Fork Duplicate Items

Wild Fork receipts print each item 2-3 times (physical receipt format). The system labels each copy separately, creating duplicate LINE_TOTALs. The column math still works because the duplicates are consistent — sum of all LINE_TOTALs matches SUBTOTAL.

### 5. LINE_ITEM_BALANCED Equations Don't Work

Our initial implementation grouped QUANTITY, UNIT_PRICE, and LINE_TOTAL by `line_id` to build per-row equations. This fails because:
- These values are on different `line_id`s (see finding #1)
- We don't have access to the VisualLine index in the VALID decision output
- The subagent already checked this math internally — we're trying to reconstruct a relationship that was verified upstream but not exposed in the output

## Current Equation Model

### What Works
- **GRAND_TOTAL_BALANCED**: Groups SUBTOTAL + TAX + GRAND_TOTAL words → renders as stacked addition with ✓
- **SUBTOTAL_BALANCED**: Groups LINE_TOTALs + DISCOUNTs + SUBTOTAL words → renders as stacked addition with ✓

### What Doesn't Work
- **LINE_ITEM_BALANCED**: Attempted per-row grouping by `line_id` → produces broken single-word equations because QUANTITY, UNIT_PRICE, and LINE_TOTAL are on different line_ids

## Proposed Simpler Model

**Remove LINE_ITEM_BALANCED entirely.** Instead:

1. **GRAND_TOTAL_BALANCED** — unchanged. Shows SUBTOTAL + TAX = GRAND_TOTAL.
2. **SUBTOTAL_BALANCED** — unchanged. Shows sum of LINE_TOTALs = SUBTOTAL.
3. QUANTITY and UNIT_PRICE words are **not grouped into equations**. They're confirmed VALID by the subagent but don't form separate display equations.

### Tradeoffs

**Gains:**
- Eliminates broken single-word equations from line_id mismatch
- Matches what the viz cache can actually reconstruct from the data
- Column equations already prove the math is correct end-to-end

**Loses:**
- No per-item "2 × $3.99 = $7.98" display in the equation panel
- QUANTITY and UNIT_PRICE words don't appear in any equation card (though they're still highlighted on the receipt image via the VALID decision bboxes)

### Open Question

Should QUANTITY and UNIT_PRICE words be included as "supporting context" in the SUBTOTAL_BALANCED equation? This would mean they show up in the overlay highlighting but don't affect the equation notation (addends are still just LINE_TOTALs).

Alternatively, could the subagent output be extended to include the `line_index` (visual row) in VALID decisions? This would let the viz cache reconstruct per-row equations correctly, but requires changing the subagent output format.
