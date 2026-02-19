# Financial Validation Analysis — 40 Receipt Run

**Step Function:** `30e9a559-bb2d-486c-8c97-abc1ea4bd379`
**LangSmith Project:** `financial-math-filter-empty-2026-02-19`
**Date:** 2026-02-19

## Summary

| Category | Count | % | Description |
|----------|------:|--:|-------------|
| A. Equations produced | 20 | 50% | Financial subagent produced VALID decisions forming equations |
| B. No OCR data | 5 | 12% | Receipt entity exists but never OCR-processed (0 words, 0 labels) |
| C. Could form equations but didn't | 11 | 27% | Has VALID financial labels in DynamoDB but enhanced extraction missed them |
| D. Genuinely insufficient | 4 | 10% | Financial labels can't form any equation |
| **Total** | **40** | **100%** | |

---

## A. Receipts WITH Equations (20)

| # | Merchant | image_id | r | Words | Labels | VALID financial | Equations |
|---|----------|----------|---|------:|-------:|-----------------|-----------|
| 1 | Brent's Delicatessen & Restaurant | `f008ea77` | 2 | 73 | 36 | GT=1, ST=1 | GRAND_TOTAL_BALANCED |
| 2 | Costco Wholesale | `314ac65b` | 1 | 136 | 104 | GT=2, ST=1, LT=8, TAX=1 | SUBTOTAL_MISMATCH |
| 3 | Dog Haus | `ce0da565` | 2 | 95 | 60 | GT=1, LT=3, QTY=1 | GRAND_TOTAL_MISMATCH |
| 4 | In-N-Out Burger | `678a7c94` | 2 | 94 | 39 | GT=2, ST=1, LT=2, QTY=2 | GRAND_TOTAL_MISMATCH |
| 5 | Los Angeles County Department of Beaches & Harbors | `935fccdc` | 1 | 43 | 32 | GT=1, LT=1 | GRAND_TOTAL_DIRECT_BALANCED |
| 6 | Smart & Final Extra! | `79626fd2` | 1 | 153 | 91 | ST=1, LT=2, TAX=1 | SUBTOTAL_BALANCED |
| 7 | Sprouts Farmers Market | `e9ea77d0` | 1 | 196 | 101 | GT=1, LT=8, QTY=2 | GRAND_TOTAL_DIRECT_BALANCED |
| 8 | Sprouts Farmers Market | `ed3f3909` | 2 | 207 | 93 | GT=3, LT=1 | GRAND_TOTAL_DIRECT_BALANCED |
| 9 | Sprouts Farmers Market | `c2bb8fff` | 1 | 207 | 83 | ST=1, LT=3, QTY=1 | SUBTOTAL_BALANCED |
| 10 | Sprouts Farmers Market | `082f1bfa` | 2 | 186 | 69 | GT=2, LT=3 | GRAND_TOTAL_DIRECT_BALANCED |
| 11 | Sprouts Farmers Market | `cf8e3a6e` | 1 | 203 | 258 | GT=2, LT=15, TAX=1, UP=3 | GRAND_TOTAL_MISMATCH |
| 12 | Sprouts Farmers Market | `1d3b61e9` | 1 | 203 | 99 | GT=3, LT=4, TAX=1, QTY=1, UP=1 | GRAND_TOTAL_BALANCED |
| 13 | Sprouts Farmers Market | `a44e5a5e` | 2 | 196 | 84 | GT=1, LT=4, QTY=1 | GRAND_TOTAL_DIRECT_BALANCED |
| 14 | The Home Depot | `a1c5d97c` | 1 | 311 | 359 | GT=1, ST=2, LT=17, TAX=2, QTY=2, UP=1 | GRAND_TOTAL_BALANCED, SUBTOTAL_BALANCED |
| 15 | The Local Peasant - Woodland Hills | `523febb6` | 1 | 94 | 64 | GT=1, ST=1, LT=3 | GRAND_TOTAL_MISMATCH |
| 16 | Vons | `fa23150b` | 1 | 100 | 121 | GT=2, LT=2, TAX=1, UP=2 | GRAND_TOTAL_BALANCED |
| 17 | Vons | `ce0da565` | 1 | 107 | 63 | GT=3, LT=3, TAX=1, UP=3 | GRAND_TOTAL_BALANCED |
| 18 | Vons | `6bb97b81` | 1 | 141 | 106 | GT=1, LT=6, TAX=1, QTY=3, UP=8 | GRAND_TOTAL_BALANCED |
| 19 | Wild Fork | `19a032ac` | 2 | 101 | 62 | GT=1, ST=1, LT=4, QTY=4, UP=4 | GRAND_TOTAL_BALANCED, SUBTOTAL_BALANCED |
| 20 | Wild Fork | `758cbedf` | 1 | 109 | 68 | GT=1, ST=1, LT=3, QTY=3, UP=3 | GRAND_TOTAL_BALANCED, SUBTOTAL_BALANCED |

### Equation type breakdown

| Equation Type | Count | Meaning |
|---------------|------:|---------|
| GRAND_TOTAL_BALANCED | 8 | SUBTOTAL + TAX + TIP = GRAND_TOTAL (confirmed) |
| GRAND_TOTAL_DIRECT_BALANCED | 5 | sum(LINE_TOTALs) + TAX = GRAND_TOTAL, no SUBTOTAL |
| SUBTOTAL_BALANCED | 5 | sum(LINE_TOTALs) + DISCOUNTs = SUBTOTAL (confirmed) |
| GRAND_TOTAL_MISMATCH | 4 | SUBTOTAL + TAX ≠ GRAND_TOTAL (mismatch) |
| SUBTOTAL_MISMATCH | 1 | sum(LINE_TOTALs) ≠ SUBTOTAL (mismatch) |

---

## B. No OCR Data (5)

Receipt entity exists in DynamoDB (bounding box detected) but **0 words and 0 labels**. OCR + LayoutLM has never run. The financial subagent has nothing to work with.

| # | Merchant | image_id | r |
|---|----------|----------|---|
| 1 | Sprouts Farmers Market | `2cd429a4` | 3 |
| 2 | Sprouts Farmers Market | `ddd538ee` | 3 |
| 3 | Sprouts Farmers Market | `0c3e58fe` | 3 |
| 4 | Sprouts Farmers Market | `cace73d4` | 1 |
| 5 | Urbane Cafe | `09b29b40` | 2 |

---

## C. Could Form Equations But Didn't (11)

These receipts have VALID financial labels in DynamoDB sufficient for equations, but the financial subagent's **enhanced extraction** (ChromaDB column alignment) didn't produce matching VALID decisions.

The financial subagent does NOT directly read DynamoDB labels. It runs its own enhanced extraction to identify financial values, then checks math. If enhanced extraction misses values that DynamoDB already has, no equations are built.

| # | Merchant | image_id | r | Words | Labels | VALID financial (DynamoDB) | Could form |
|---|----------|----------|---|------:|-------:|---------------------------|------------|
| 1 | DIY Home Center | `93db2c10` | 2 | 127 | 102 | GT=1, ST=1, LT=1, TAX=1, QTY=1 | GT_BAL + ST_BAL |
| 2 | In-N-Out Burger | `2360d36e` | 2 | 92 | 49 | GT=2, ST=1, LT=2, QTY=2 | GT_BAL + ST_BAL |
| 3 | Mendocino Farms | `d36c2083` | 2 | 94 | 55 | GT=1, ST=1, LT=1, TAX=1, QTY=2 | GT_BAL + ST_BAL |
| 4 | Sprouts Farmers Market | `93db2c10` | 1 | 181 | 116 | GT=2, ST=1, LT=3, TAX=2, QTY=1, UP=1 | GT_BAL + ST_BAL |
| 5 | Sprouts Farmers Market | `e936747a` | 1 | 198 | 80 | GT=3, LT=1 | GT_DIRECT |
| 6 | Sprouts Farmers Market | `3b594aaf` | 1 | 218 | 125 | GT=2, ST=1, LT=2, TAX=1, QTY=2 | GT_BAL + ST_BAL |
| 7 | Sprouts Farmers Market | `08239059` | 1 | 184 | 124 | GT=1, ST=1, LT=7, QTY=2, UP=1 | GT_BAL + ST_BAL |
| 8 | Sprouts Farmers Market | `7e1688d4` | 2 | 97 | 29 | GT=1, LT=1, QTY=1 | GT_DIRECT |
| 9 | The Home Depot | `e1f519d5` | 1 | 257 | 351 | GT=1, ST=1, LT=7, TAX=1, DISC=22, QTY=7, UP=13 | GT_BAL + ST_BAL |
| 10 | The Home Depot | `249eb736` | 1 | 201 | 116 | GT=2, ST=2, LT=2, TAX=2 | GT_BAL + ST_BAL |
| 11 | Vons | `1bfa17f4` | 1 | 112 | 82 | GT=1, LT=2, TAX=1, DISC=1, UP=2 | GT_BAL + GT_DIRECT |

**This is the biggest opportunity.** 11 receipts (27% of batch) have sufficient VALID financial labels but the enhanced extraction pipeline fails to surface them.

---

## D. Genuinely Insufficient (4)

These receipts have words and labels but their VALID financial labels cannot form any equation.

| # | Merchant | image_id | r | Words | Labels | VALID financial | Why |
|---|----------|----------|---|------:|-------:|-----------------|-----|
| 1 | 7-Eleven | `1746d34d` | 2 | 55 | 75 | LT=1, QTY=1, UP=1 | Gas station: QTY+UP on line 11, LT on line 14. No valid GT/ST. Line-grouping can't connect them. |
| 2 | Sparkling Image Car Wash | `c8a2468a` | 1 | 84 | 61 | LT=2, TAX=1, DISC=1, UP=3 | No valid GT or ST. Only LT, TAX, UP — no 'answer' side for any equation. |
| 3 | Vons | `ed3f3909` | 1 | 49 | 13 | DISC=11 | Only DISCOUNT labels (5%, OFF, Save). No GT/ST/LT/TAX. Coupon section, not transaction. |
| 4 | Westlake Physical Therapy, Inc | `4b326441` | 1 | 47 | 30 | GT=1 | Only 1 valid GT. No ST, LT, TAX to check against. Isolated total. |

---

## Key Takeaways

1. **Enhanced extraction recall is the biggest gap (27%).** 11 receipts have VALID financial labels in DynamoDB that could form equations, but the ChromaDB-based enhanced extraction doesn't surface them. Improving extraction recall would bring coverage from 50% to ~77%.

2. **No-OCR receipts should be filtered earlier (12%).** 5 receipts have 0 words. Excluding them from the step function's receipt selection query would avoid wasting compute.

3. **Genuinely insufficient receipts are a small minority (10%).** Gas station, car wash, coupon section, medical office — these receipt types don't fit the current equation model.

4. **Effective coverage potential:** 20/35 processable receipts (57%) currently produce equations. With extraction improvements: 31/35 (88%).