# Sprouts Farmers Market Label Audit

Audit of 81 flagged Sprouts Farmers Market receipts (out of 201 total).
Flagged by financial equation check: `SUM(LINE_TOTAL) - DISCOUNT = SUBTOTAL` and `SUBTOTAL + TAX = GRAND_TOTAL`.

## Summary

| Status | Count | Description |
|--------|-------|-------------|
| PASS | 39 | Financial equations hold with valid labels |
| FIXED | 19 | Label issues corrected (batch 1) |
| NEEDS RE-OCR | 1 | OCR missing word, cannot fix labels alone |
| REMAINING | 15 | Harder cases for batch 2 |
| N/A | 7 | Receipt fragments with no financial data |

## Batch 1 Fixes (39 label operations across 19 receipts)

Completed 2026-02-24. Four parallel agents performed invalidations, label flips, and new label creations.

### Agent 1: Payment/Change LINE_TOTAL Invalidations (9 ops, 5 receipts)

| Image ID | Receipt ID | Line | Word | Text | Label | Change |
|----------|------------|------|------|------|-------|--------|
| 687da832-1d9b-48c2-a295-1cc122c23ed8 | 2 | 27 | 1 | "$18.27" | LINE_TOTAL | VALID -> INVALID |
| 687da832-1d9b-48c2-a295-1cc122c23ed8 | 2 | 30 | 1 | "0.00" | LINE_TOTAL | VALID -> INVALID |
| 291cfd80-5b29-428e-b650-5e4505c4cbd7 | 1 | 25 | 1 | "$27.01" | LINE_TOTAL | VALID -> INVALID |
| 291cfd80-5b29-428e-b650-5e4505c4cbd7 | 1 | 27 | 1 | "0.00" | LINE_TOTAL | VALID -> INVALID |
| 4619f1bf-08b8-483b-a597-4b4f9ece48e6 | 1 | 24 | 1 | "8.59" | LINE_TOTAL | VALID -> INVALID |
| 645a8dcb-ee5e-4207-9b73-567ebd1ccc45 | 1 | 27 | 2 | "42.54" | LINE_TOTAL | VALID -> INVALID |
| ceb066a5-b621-46b6-9f8f-754d998670a7 | 2 | 26 | 1 | "$33.79" | LINE_TOTAL | VALID -> INVALID |
| ceb066a5-b621-46b6-9f8f-754d998670a7 | 2 | 32 | 1 | "0.00" | LINE_TOTAL | VALID -> INVALID |
| ceb066a5-b621-46b6-9f8f-754d998670a7 | 2 | 19 | 1 | "26.39" | SUBTOTAL | VALID -> INVALID |

### Agent 2: Duplicate/CRV Fixes (9 ops, 5 receipts)

| Image ID | Receipt ID | Line | Word | Text | Label | Change |
|----------|------------|------|------|------|-------|--------|
| b6e7af49-3802-46f2-822e-bbd49cd55ada | 2 | 39 | 5 | "3.00" | LINE_TOTAL | VALID -> INVALID |
| ef8c6b37-c8f5-40f1-bd01-36e32a4a0dc7 | 1 | 38 | 5 | "1.00" | LINE_TOTAL | VALID -> INVALID |
| e62eadbe-9449-4c2f-b5e8-2434d740887c | 1 | 44 | 1 | "1.53" | LINE_TOTAL | VALID -> INVALID |
| 646f8f0f-2907-4ec7-8196-b5223ceb222e | 2 | 19 | 1 | "0.10" | LINE_TOTAL | INVALID -> VALID |
| 646f8f0f-2907-4ec7-8196-b5223ceb222e | 2 | 19 | 1 | "0.10" | UNIT_PRICE | VALID -> INVALID |
| 646f8f0f-2907-4ec7-8196-b5223ceb222e | 2 | 21 | 1 | "0.10" | LINE_TOTAL | INVALID -> VALID |
| 646f8f0f-2907-4ec7-8196-b5223ceb222e | 2 | 21 | 1 | "0.10" | UNIT_PRICE | VALID -> INVALID |
| 37900099-30b4-4788-b128-73512a936045 | 1 | 18 | 1 | "0.10" | LINE_TOTAL | INVALID -> VALID |
| 37900099-30b4-4788-b128-73512a936045 | 1 | 18 | 1 | "0.10" | UNIT_PRICE | VALID -> INVALID |

### Agent 3: Tax Report / Coupon / OCR Fixes (15 ops, 5 receipts)

| Image ID | Receipt ID | Line | Word | Text | Label | Change |
|----------|------------|------|------|------|-------|--------|
| e936747a-4231-4ea6-be52-875975929a8a | 2 | 53 | 1 | "13.29" | SUBTOTAL | VALID -> INVALID |
| e936747a-4231-4ea6-be52-875975929a8a | 2 | 63 | 1 | "0.00" | TAX | VALID -> INVALID |
| 1d3b61e9-96b5-4cb4-a1a3-d2a35a30e2cd | 1 | 43 | 1 | "13.98" | TAX | VALID -> INVALID |
| 1d3b61e9-96b5-4cb4-a1a3-d2a35a30e2cd | 1 | 44 | 1 | "1.01" | MERCHANT_NAME | VALID -> INVALID |
| 1d3b61e9-96b5-4cb4-a1a3-d2a35a30e2cd | 1 | 44 | 1 | "1.01" | TAX | INVALID -> VALID |
| 93db2c10-ad41-4483-a48a-e6fe2b884e79 | 1 | 28 | 1 | "38.49" | SUBTOTAL | VALID -> INVALID |
| 44dc1b44-a656-4dc1-bd41-4abf9d832827 | 1 | 46 | 3 | "$30" | SUBTOTAL | VALID -> INVALID |
| 44dc1b44-a656-4dc1-bd41-4abf9d832827 | 1 | 24 | 1 | "0.10" | TAX | VALID -> INVALID |
| 44dc1b44-a656-4dc1-bd41-4abf9d832827 | 1 | 24 | 1 | "0.10" | LINE_TOTAL | INVALID -> VALID |
| 44dc1b44-a656-4dc1-bd41-4abf9d832827 | 1 | 26 | 1 | "1.01" | TAX | INVALID -> VALID |
| cf8e3a6e-0947-4a3d-9bc0-7ce3894c9912 | 1 | 86 | 1 | "64.99" | LINE_TOTAL | VALID -> INVALID |
| cf8e3a6e-0947-4a3d-9bc0-7ce3894c9912 | 1 | 86 | 1 | "64.99" | GRAND_TOTAL | INVALID -> VALID |
| cf8e3a6e-0947-4a3d-9bc0-7ce3894c9912 | 1 | 38 | 5 | "1.00" | LINE_TOTAL | VALID -> INVALID |
| cf8e3a6e-0947-4a3d-9bc0-7ce3894c9912 | 1 | 52 | 5 | "4.00" | LINE_TOTAL | VALID -> INVALID |
| cf8e3a6e-0947-4a3d-9bc0-7ce3894c9912 | 1 | 79 | 1 | "0.10" | TAX | VALID -> INVALID |

### Agent 4: Missing GT/LT Labels (6 ops, 4 receipts + 1 unfixable)

| Image ID | Receipt ID | Line | Word | Text | Label | Change |
|----------|------------|------|------|------|-------|--------|
| 54b34a70-fa63-4baa-b4a8-cb5473bc5720 | 1 | 47 | 1 | "6.48" | GRAND_TOTAL | INVALID -> VALID |
| c985752b-1ef2-41cf-9707-76d84b7e3320 | 1 | 25 | 1 | "15.98" | GRAND_TOTAL | INVALID -> VALID |
| ff649c4b-5c31-4247-b78c-d9b0c56610ad | 2 | 45 | 1 | "21.43" | GRAND_TOTAL | CREATED |
| ff649c4b-5c31-4247-b78c-d9b0c56610ad | 2 | 43 | 1 | "1.45" | TAX | CREATED |
| 1f48de98-8ae8-4fb3-bfe7-25558f5964e9 | 1 | 54 | 1 | "17.96" | GRAND_TOTAL | CREATED |
| 1f48de98-8ae8-4fb3-bfe7-25558f5964e9 | 1 | 48 | 1 | "4.99" | LINE_TOTAL | CREATED |

**Unfixable:** dbc78ee2-8d3c-4a31-b28a-0293583a2a25/1 -- "13.99" for RAW CREAM does not exist in OCR output. Needs re-OCR.

## REMAINING: No Valid Financial Labels (8)

Receipts with product/total text visible but zero valid financial labels assigned.

| Image ID | Receipt ID | Notes |
|----------|------------|-------|
| bf801942-b2a2-4393-a4a3-4aa2e022a161 | 1 | ORG WHOLE MILK 10.99 -- no LT, GT, ST, or TAX labels |
| 51cdfd0b-cced-44e8-bd54-6fc5a2051ac2 | 2 | WHOLE MILK 8.99 -- no valid labels |
| 51cdfd0b-cced-44e8-bd54-6fc5a2051ac2 | 4 | Duplicate of /2, same issue |
| 51cdfd0b-cced-44e8-bd54-6fc5a2051ac2 | 5 | Duplicate of /2, same issue |
| 51cdfd0b-cced-44e8-bd54-6fc5a2051ac2 | 6 | Duplicate of /2, same issue |
| 51cdfd0b-cced-44e8-bd54-6fc5a2051ac2 | 7 | WHOLE MILK 8.99 -- no valid labels |
| c6b66d97-1e0a-4be4-9e1f-731a7e4be25c | 1 | YOGURT 5.99 -- no valid labels |
| 93247564-a489-4970-9992-1a436af35ef5 | 1 | Garbled/doubled OCR text, no usable financial labels |

## FIXED: Missing GRAND_TOTAL Label (4)

Receipt has LINE_TOTALs but no valid GRAND_TOTAL.

| Image ID | Receipt ID | SUM(LT) | Expected GT | Notes |
|----------|------------|----------|-------------|-------|
| 54b34a70-fa63-4baa-b4a8-cb5473bc5720 | 1 | 6.48 | 6.48 | "Total: USD$ 6.48" text present but labeled as SUBTOTAL not GT |
| c985752b-1ef2-41cf-9707-76d84b7e3320 | 1 | 15.98 | 15.98 | "$15.98" on CREDIT line has no GT label |
| ff649c4b-5c31-4247-b78c-d9b0c56610ad | 2 | 19.98 | 21.43 | GT 21.43 and TAX 1.45 both missing labels |
| 1f48de98-8ae8-4fb3-bfe7-25558f5964e9 | 1 | 12.97 | 17.96 | Missing GT label; also butter 4.99 missing LT label |

## REMAINING: Missing LINE_TOTAL on Items (3)

GRAND_TOTAL exists but item prices lack LINE_TOTAL labels.

| Image ID | Receipt ID | GT | SUM(LT) | Gap | Missing Item |
|----------|------------|----|----------|-----|-------------|
| dbc78ee2-8d3c-4a31-b28a-0293583a2a25 | 1 | 29.97 | 15.98 | 13.99 | RAW CREAM price unlabeled |
| 87589280-36d1-4495-9ea2-9a4c5d921ba3 | 1 | ~39.09 | 22.76 | ~16.33 | Most items missing LT; voided item unlabeled; no valid GT |
| c2c5aadf-58e8-4276-95ae-0f46039946da | 1 | 23.26 | 20.27 | 2.99 | Garbled OCR merged two products, one price missing |

## FIXED: Payment/Change Lines Mislabeled as LINE_TOTAL (4)

CREDIT/BALANCE DUE/CHANGE amounts incorrectly labeled as LINE_TOTAL.

| Image ID | Receipt ID | GT | Mislabeled Value | What It Actually Is |
|----------|------------|----|-----------------|---------------------|
| 687da832-1d9b-48c2-a295-1cc122c23ed8 | 2 | 18.27 | $18.27 (LT), 0.00 (LT) | CREDIT tender, CHANGE |
| 291cfd80-5b29-428e-b650-5e4505c4cbd7 | 1 | 27.01 | $27.01 (LT), 0.00 (LT) | CREDIT tender, CHANGE |
| 4619f1bf-08b8-483b-a597-4b4f9ece48e6 | 1 | 8.59 | 8.59 (LT) | BALANCE DUE (duplicate of GT) |
| ceb066a5-b621-46b6-9f8f-754d998670a7 | 2 | 33.79 | $33.79 (LT), 0.00 (LT) | CREDIT tender, CHANGE; also ST is taxable subtotal not full ST |

## FIXED: Pricing Breakdown Duplicating LINE_TOTAL (2)

"X @ Y FOR Z" deal lines have Z labeled as LINE_TOTAL, duplicating the actual item total.

| Image ID | Receipt ID | GT | Duplicate Value | Pattern |
|----------|------------|----|----------------|---------|
| b6e7af49-3802-46f2-822e-bbd49cd55ada | 2 | 9.44 | 3.00 | "4 @ 4 FOR 3.00" duplicates LEMONS 3.00 |
| ef8c6b37-c8f5-40f1-bd01-36e32a4a0dc7 | 1 | 5.98 | 1.00 | "2 @ 2 FOR 1.00" duplicates LIMES 1.00 |

## FIXED: Pre/Post Discount Both as LINE_TOTAL (1)

Both original and sale prices labeled as LINE_TOTAL causing double-count.

| Image ID | Receipt ID | GT | Issue |
|----------|------------|----|-------|
| e62eadbe-9449-4c2f-b5e8-2434d740887c | 1 | 27.67 | Banana original 1.53 and sale 1.22 both LT; discount -0.31 present |

## FIXED: Terminal Total / OCR Duplication as LINE_TOTAL (2)

Card terminal totals or OCR-duplicated amounts incorrectly labeled.

| Image ID | Receipt ID | GT | Issue |
|----------|------------|----|-------|
| 645a8dcb-ee5e-4207-9b73-567ebd1ccc45 | 1 | 42.54 | "42.54" on terminal Total line labeled LT instead of GT |
| cf8e3a6e-0947-4a3d-9bc0-7ce3894c9912 | 1 | 64.99 | BALANCE DUE "64.99" as LT; massive OCR column merging duplicates |

## FIXED: Tax Report Taxable Amount Mislabeled (3)

Sprouts "Tax Report" shows `TAX 1 <taxable_amount> <tax> TT`. The taxable amount is mislabeled as TAX or SUBTOTAL.

| Image ID | Receipt ID | GT | Mislabeled | Actual Tax | Issue |
|----------|------------|----|-----------|------------|-------|
| e936747a-4231-4ea6-be52-875975929a8a | 2 | 50.21 | 13.29 as ST | 0.96 | ST is taxable subtotal, not receipt subtotal; also CHANGE 0.00 as TAX |
| 1d3b61e9-96b5-4cb4-a1a3-d2a35a30e2cd | 1 | 19.98 | 13.98 as TAX | 1.01 | Taxable amount mislabeled as TAX; actual tax 1.01 has wrong label |
| 93db2c10-ad41-4483-a48a-e6fe2b884e79 | 1 | 52.25 | 38.49 as ST | 2.79 | Taxable amount mislabeled as SUBTOTAL |

## FIXED: CRV / Coupon Labeling Issues (3)

California Redemption Value fees or coupon text incorrectly labeled.

| Image ID | Receipt ID | GT | Issue |
|----------|------------|----|-------|
| 44dc1b44-a656-4dc1-bd41-4abf9d832827 | 1 | 19.59 | "$30" from coupon text as SUBTOTAL; CRV 0.10 as TAX; real tax 1.01 unlabeled |
| 646f8f0f-2907-4ec7-8196-b5223ceb222e | 2 | 21.67 | Two CRV 0.10 fees labeled UNIT_PRICE instead of LINE_TOTAL (0.20 short) |
| 37900099-30b4-4788-b128-73512a936045 | 1 | 18.08 | CRV 0.10 labeled UNIT_PRICE not LINE_TOTAL |

## REMAINING: OCR Issues (3)

Garbled OCR, split words, or corrupted values.

| Image ID | Receipt ID | Issue |
|----------|------------|-------|
| 795bc26a-127f-4410-ad26-3e0ede90398f | 1 | Entirely garbled (upside-down/mirrored receipt). Unrecoverable. |
| d28fcc1e-642c-4d1b-89ec-8fd9f456ea78 | 1 | OCR split "$18" and ".08"; GT 13.08 does not match SUM(LT) 18.08 |
| b4b269e9-5213-4e9a-b3df-661bc57dd81a | 2 | "+3.93" OCR error (should be "43.93") labeled as TAX |

## REMAINING: Other (2)

| Image ID | Receipt ID | GT | Issue |
|----------|------------|----|-------|
| d076611b-e24b-4772-9d01-1fc9a52f85b7 | 1 | 6.20 | Multi-tender (credit + gift card); GT is only credit portion |
| 0fd6e62e-a15e-4f89-962f-ae6dc2c4a0c3 | 1 | 47.44 | SUM(LT) matches GT but missing ST/TAX labels (zero-tax receipt) |

## N/A: Receipt Fragments (7)

Footer-only fragments or receipts with no product/financial data.

| Image ID | Receipt ID | Notes |
|----------|------------|-------|
| ff649c4b-5c31-4247-b78c-d9b0c56610ad | 1 | Footer only (feedback/cashier info) |
| eaeee5ef-ba10-48c6-9cfe-591d043d7d65 | 1 | Footer only (return policy) |
| 51cdfd0b-cced-44e8-bd54-6fc5a2051ac2 | 1 | Footer only (return policy/timestamps) |
| 87589280-36d1-4495-9ea2-9a4c5d921ba3 | 2 | Footer only (feedback survey) |
| 34eecba2-adae-473f-8ce9-8cc7741e279b | 1 | Only LINE_TOTAL label was already invalidated |

## PASS (39 receipts)

These receipts have correct financial equations with valid labels.

| Image ID | Receipt ID |
|----------|------------|
| 5744e67f-1867-4c8a-a967-de567b4a0d71 | 1 |
| 04a1dfe7-1858-403a-b74f-6edb23d0deb4 | 1 |
| cdf8dbe6-8cde-47e4-a988-6bad54e67105 | 1 |
| 1b441d33-6434-449f-b4c7-c98e70861277 | 1 |
| ec17c1aa-612f-4466-91b2-075b836c9b3b | 1 |
| d5f79185-3747-4a3f-81cd-a9a8ce8ca6b4 | 1 |
| 2dd75581-e04f-4c55-8c8e-d18c8518af1e | 1 |
| 08239059-378e-4c09-ac15-5d7de5dc23ac | 1 |
| 3b594aaf-915e-49cc-97e6-3a924710cabf | 1 |
| 4d0bdfd5-4d5f-4c01-8f20-7f21090ab361 | 2 |
| a018fe66-e186-4b6d-8e15-a40d7ac55d04 | 1 |
| 45af80b3-28e6-4666-b398-3421c34f70aa | 1 |
| 79ad7b62-ea9d-4636-a411-8fe3f837ca96 | 2 |
| 25244d14-5069-4220-a9ff-4a8c56d50b34 | 1 |
| aabbf168-7a61-483b-97c7-e711de91ce5f | 1 |
| 5985d2dd-462e-4ccb-928c-4f4d596d6be2 | 1 |
| 87bbf863-e4e2-4079-ac28-82e7abee02fb | 1 |
| d48a0ef4-e518-4a53-8414-0abc5f677a95 | 1 |
| 0e222ed9-5a1f-49b3-be48-483d13442881 | 2 |
| 9e630e7a-64f1-4b03-aac2-5698e28efbb6 | 1 |
| 687da832-1d9b-48c2-a295-1cc122c23ed8 | 2 |
| 6c180601-5d7e-4429-88ed-ea908d19e9ae | 1 |
| 32e93177-6528-4d63-b637-45d53d1cf3f3 | 1 |
| dfb96b51-9c1e-43ea-be70-ec4b9d76080c | 1 |
| c77fcf9a-1b15-4147-ba71-09d56010520c | 1 |
| 6e58ca91-4d12-47e1-ac65-a51402090206 | 1 |
| 03fa2d0f-33c6-43be-88b0-dae73ec26c93 | 1 |
| 646f8f0f-2907-4ec7-8196-b5223ceb222e | 1 |
| 9af03fd2-e3a5-4798-99f5-3db3f5db00fd | 1 |
| c914012e-3fc3-4ba2-b314-fa7d423acac8 | 2 |
| 6d7db41f-6e9b-4f43-a577-3208194f64e5 | 2 |
| 18f583d7-a278-4311-bb36-7abd0c97b2a1 | 1 |
| 86cca8b7-db22-4844-b6f7-d9130e3bae57 | 1 |
| 11da3353-cc82-45d8-829f-26f2f124d3aa | 1 |
| 04ebdb8a-b560-4c00-aa09-f6afa3bda458 | 1 |
| 77b07cb3-baaf-459f-a268-57146f6a0020 | 3 |
| 79ad7b62-ea9d-4636-a411-8fe3f837ca96 | 1 |
| cee0fbe1-d84a-4f69-9af1-7166226e8b88 | 1 |
| 47343fdf-ec16-4e2a-81d1-b2ecd5af723a | 2 |
| fa4fd183-8208-48b3-93d2-8f2fe10e8ddc | 2 |
| 6e41d36d-f78d-4836-8aa7-da33d2fe3785 | 1 |

## Systemic Patterns

1. **No labels at all** (8 receipts): Mostly single-item milk receipts from image 51cdfd0b. The LayoutLM model assigned no financial labels.
2. **Payment lines as LINE_TOTAL** (4 receipts): CREDIT/DEBIT tender amounts and CHANGE 0.00 consistently mislabeled as LINE_TOTAL.
3. **Tax Report confusion** (3 receipts): Sprouts prints `TAX 1 <taxable_amt> <tax> TT` -- the model labels the taxable amount as TAX or SUBTOTAL instead of the actual tax value.
4. **Deal line duplication** (2 receipts): "X @ Y FOR Z" pricing lines have Z labeled as LINE_TOTAL, duplicating the actual item total.
5. **CRV fees** (3 receipts): California Redemption Value fees labeled as UNIT_PRICE or TAX instead of LINE_TOTAL.
6. **Missing GT** (4 receipts): BALANCE DUE or Total text present but no GRAND_TOTAL label assigned.
