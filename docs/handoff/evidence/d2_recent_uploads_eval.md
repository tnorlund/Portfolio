# D2 Deterministic Section Assignment — Evaluation on Recent Dev Uploads

**PR:** #1145 `[D2] Assign and verify deterministic receipt sections` (branch `codex/d2-deterministic-sections`)
**Table:** dev `ReceiptsTable-dc5be22` (READ-ONLY; prod `ReceiptsTable-d7ff76a` never touched)
**Method:** offline — imported `receipt_upload.section_assignment.assign_row_sections` from the PR branch and drove it with each receipt's real `ReceiptRow`s + `ReceiptLine`s fetched read-only from dev. No DynamoDB writes, no commits, no PR comments.
**Priors artifact under test:** `section_order_priors_v1.json` (generated 2026-07-14, 93 merchant priors, 10 global section types).

## Verdict

**Not safe to run on new uploads as a section-labeling source as-is.** D2 reliably recovers the receipt *skeleton* — the structural anchor sections (STOREFRONT, ADDRESS, PAYMENT, FOOTER) at 71–82% row recall — but it **systematically fails to segment the item body**: it proposes an `ITEMS` section on only 2 of 18 receipts that have one, `TOTAL_LINE` on 1/17, `SUMMARY` on 1/14, `SURVEY` on 0/4. On unknown merchants it degenerates into 2–3 mega-sections (a giant top ADDRESS block and a giant bottom FOOTER block) that swallow everything in between. Aggregate row-level agreement against QA-VALID ground truth is **58.7% (252/429 rows)**.

No crashes, no data-loss risk, no prod exposure — the failure is **quality**, not safety. The additive-only persistence and VALID-never-overwritten guards the review credited hold up. But if these PENDING proposals feed the payload builder / downstream typography as-is, the largest and most important section of most receipts (the items) will be mislabeled as PAYMENT/ADDRESS/FOOTER.

## Per-receipt results (22 most-recent dev uploads, 2026-07-07 → 2026-07-14)

| Merchant | image (8) | rows | prior? | prior recs | QA VALID secs | GT rows | proposed secs | agreement |
|---|---|---|---|---|---|---|---|---|
| The Mob Museum | db5af7d3 | 19 | – | – | 7 | 17 | 3 | 0.471 |
| Chick-fil-A | cb581977 | 39 | – | – | 6 | 25 | 3 | **0.160** |
| Twin Peaks | 319170bf | 37 | Y | 3 | 5 | 27 | 3 | 0.778 |
| Twin Peaks | e0767471 | 28 | Y | 3 | 7 | 24 | 3 | 0.333 |
| Smith's | 91b14596 | 16 | Y | 10 | 7 | 14 | 4 | 0.643 |
| Smith's | ff574b98 | 51 | Y | 10 | 7 | 23 | 5 | 0.826 |
| Smith's | abb331f5 | 19 | Y | 10 | 6 | 15 | 4 | 0.467 |
| Green NV Henderson | 64d9d630 | 41 | – | – | 5 | 32 | 3 | 0.406 |
| Trader Joe's | 0580fbbf | 14 | Y | 22 | 6 | 13 | 3 | 0.615 |
| Flower Child | d95ff790 | 30 | – | – | 7 | 25 | 4 | 0.480 |
| Trader Joe's | 03556b8f | 19 | Y | 22 | 6 | 17 | 3 | 0.647 |
| Costco Wholesale | 3885af9f | 27 | Y | 39 | 8 | 21 | 6 | 0.714 |
| TRADER JOE'S | 0e75127f | 19 | Y | 22 | 6 | 19 | 3 | 0.474 |
| Walmart Supercenter | 0901ce80 | 34 | – | – | 8 | 22 | 4 | 0.727 |
| Sparkling Image Car Wash | 75ef30e5 | 13 | Y | 4 | 6 | 8 | 5 | 0.750 |
| Sprouts Farmers Market (r1) | b48578ca | 13 | Y | 200 | 2 | 5 | 2 | 0.800 |
| SPROUTS FARMERS MARKET (r2) | b48578ca | 31 | Y | 200 | 7 | 29 | 4 | 0.655 |
| TRADER JOE'S | 4cdbdb57 | 17 | Y | 22 | 6 | 17 | 3 | 0.412 |
| Rachel's Kitchen | 170f55ed | 35 | – | – | 4 | 17 | 4 | 0.647 |
| Smith's | 3b2cb4f3 | 22 | Y | 10 | 5 | 11 | 4 | 0.545 |
| CVS | c019638a | 46 | Y | 14 | 6 | 27 | 4 | 0.741 |
| Bristol Farms | 803649d8 | 38 | – | – | 6 | 21 | 4 | 0.905 |

Notes: all 22 are real uploads (no `test-*` in this window). The Sprouts image `b48578ca` carries two receipts (r1/r2) — both included. "GT rows" = rows covered by a QA-VALID section that carries `row_ids` (the scorable ground truth). "prior recs" = `receipt_count` in that merchant's learned prior.

## Aggregate

- **Row-level agreement (proposed vs QA-VALID): 252/429 = 0.587**
- Mean proposed sections/receipt: **3.7** vs mean QA-VALID sections/receipt: **6.0** — D2 under-segments by ~2 sections.
- With merchant prior (n=15): mean agreement **0.627**; global fallback / unknown merchant (n=7): **0.542**. The prior helps modestly but does not fix the structural collapse.

### Per-section-type row recall (aggregated)

| Section | recall | | Section | recall |
|---|---|---|---|---|
| PAYMENT | 133/162 = **0.82** | | ITEMS | 5/58 = **0.09** |
| ADDRESS | 38/48 = **0.79** | | SUMMARY | 1/25 = **0.04** |
| FOOTER | 59/79 = **0.75** | | TOTAL_LINE | 0/23 = **0.00** |
| STOREFRONT | 15/21 = **0.71** | | SURVEY | 0/9 = **0.00** |
| BARCODE | 1/3 = 0.33 | | SECTION_HEADER | 0/1 = 0.00 |

Clean split: the four structural anchors are recovered at 71–82%; every content/middle section is at or near zero.

### Section-presence coverage (receipts where D2 proposed the type / receipts where QA-VALID has it)

| Section | coverage | | Section | coverage |
|---|---|---|---|---|
| STOREFRONT | 16/16 | | ITEMS | **2/18** |
| PAYMENT | 18/19 | | TOTAL_LINE | **1/17** |
| FOOTER | 17/20 | | SUMMARY | **1/14** |
| ADDRESS | 16/21 | | SURVEY | **0/4** |
| | | | SECTION_HEADER | 0/1 |

### Top confusion flows (QA-VALID row → mislabeled-as)

```
 36  ITEMS       -> PAYMENT     14  ITEMS      -> ADDRESS
 21  PAYMENT     -> FOOTER      10  SUMMARY    -> PAYMENT
 17  FOOTER      -> PAYMENT      7  SURVEY     -> FOOTER
 16  TOTAL_LINE  -> PAYMENT      7  SUMMARY    -> ADDRESS
```

## Root-cause read

The monotone Viterbi (`assign_row_sections`) constrains each `section_type` to one contiguous block ordered by learned `position` mean. In practice the emission model — 6 numeric Gaussians (position, y_center, x_span, alpha/digit ratio, has_amount), plus tokens only for the global prior — makes the structural anchors dominant and gives the content sections no distinctive signal, so the optimal monotone path collapses the middle of the receipt into the neighboring anchor. This is worst on unknown merchants:

- **Chick-fil-A (unknown, 0.16):** proposed only `ADDRESS`(13 rows) + `FOOTER`(25) + `STOREFRONT`(1). The entire item block became ADDRESS; PAYMENT/SUMMARY/SURVEY became FOOTER. Two mega-sections, item body destroyed.
- **Smith's (prior, 0.826):** best case — did propose ITEMS(11)/PAYMENT(16)/FOOTER(22)/ADDRESS/STOREFRONT and matched the block structure. Where a strong merchant prior exists the skeleton is right, but ITEMS boundaries still drift (e.g. TOTAL_LINE and SUMMARY rows absorbed into ITEMS).
- **Bristol Farms (unknown, 0.905):** the high score is misleading — it proposed no ITEMS section at all; it scored well only because this receipt is PAYMENT-heavy and the PAYMENT block happened to align. High agreement here does not mean good segmentation.

## The two known P2s — do they bite on real uploads?

**P2 #1 — verifier can demote a human-QA'd VALID section to PENDING** (`section_verifier.py:206`).
*Does not bite on the current dev corpus, but is a real forward risk.* `_record_verification` selects sections purely by `model_source == "upload-determinism-v1"`. Every existing QA'd section on these 22 receipts carries a different `model_source` (`section-qa-v3`, `section-knn-v2-gen4`, `section-knn-v1+remap-v1`, `section-seed-*`), so a re-verify would not select — and therefore could not demote — any of today's human-VALID sections. The hazard is specifically: a section D2 *creates* (model_source `upload-determinism-v1`), that QA later promotes to VALID **without changing model_source**, then a later re-embed/drain re-verify whose KNN disagrees silently flips it back to PENDING. Given this project re-embeds at scale, that path will eventually be exercised. Fix before D2 sections enter the QA loop: gate the PENDING demotion on current status (never demote a VALID section), or stamp a distinct model_source on QA promotion. (Not exercised live here — the verifier needs Chroma embeddings; this is a static + data-verified finding.)

**P2 #2 — golden fixtures assert only determinism + valid-enum, not expected sections** (`test_section_assignment.py:139`).
**Bites hard.** The exact regression these tests cannot see is the one happening on real uploads: ITEMS/SUMMARY/TOTAL_LINE collapsing into anchors. `test_golden_merchants_have_deterministic_learned_priors` checks `first == second` and that each type is a real `SectionType` — both stay green while D2 fails to produce an ITEMS section on 16 of 18 receipts. This eval is concrete evidence that per-row expected-`section_type` assertions are needed before the quality of this path can be trusted or defended against regression.

## What must change before D2 labels new uploads

1. **Fix content-section recall (blocking for labeling use).** As-is, D2 should be treated as a *skeleton/anchor* detector, not a section labeler. The item body — the largest section on grocery/retail receipts — is mislabeled on ~90% of receipts. Options: stronger emission features for ITEMS/SUMMARY/TOTAL_LINE (price-column geometry, per-row amount alignment, quantity tokens), token evidence for merchant priors too (see P3 below), or a minimum-support penalty so the path can't collapse a 20-row middle into an anchor.
2. **Land P2 #2's per-row assertions** so this regression is caught in CI, using a few of these receipts (Chick-fil-A as the unknown-merchant collapse case, Smith's ff574b98 as the good case) as fixtures.
3. **Land P2 #1's status guard** before any D2-created section can be QA-promoted, so re-embeds can't silently demote human VALIDs.
4. **P3 (repeat-merchant priors drop token evidence)** is likely contributing: merchant priors are fit with `include_tokens=False`, so the 93 repeat merchants are scored on the 6 Gaussians alone — no keyword signal to distinguish ITEMS from PAYMENT. Worth fixing alongside item #1.

## Reproduction

- Worktree: `/private/tmp/d2_eval_wt` (branch `codex/d2-deterministic-sections`)
- venv: `/private/tmp/d2_venv` (`receipt_dynamo` -e, `receipt_upload` -e --no-deps, pillow, numpy)
- Driver: `/private/tmp/d2_eval_wt/d2_eval_driver.py`; targets `/private/tmp/d2_targets.json`; raw results `/private/tmp/d2_eval_results.json`
- Run: `DYNAMODB` reads default to dev via hardcoded `ReceiptsTable-dc5be22`; asserts it is not the prod table.
