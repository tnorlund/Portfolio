# Deterministic Receipt Resolution at Upload (post-LayoutLM)

**Status:** research + work order (for codex implementation)
**Goal:** after LayoutLM proposes word labels on upload, a deterministic AWS-side
resolver pins down the receipt's details — merchant, structure, sections, fields,
arithmetic — using the font-intelligence assets now on main, with per-field
provenance and confidence. LayoutLM proposes; determinism disposes.

## Assets on main (all merged 2026-07-14)
- **QA'd section corpus**: 5,712 ReceiptSection rows / 842 receipts / 91% VALID on
  final line_ids (3-pass adversarial QA). Per-merchant section ORDER is highly
  regular — learnable as priors.
- **ReceiptRow** (#1107): materialized visual rows, row_id == primary_line_id
  (joins Chroma), label+amount pairing by construction; backfill script included.
- **KNN section propagator** (#1078): per-receipt callable, 96.8% @ conf>=0.8 on
  word embeddings (async path; embeddings arrive after upload).
- **Face-map v2** (#1103): (merchant, section) -> face priors from QA'd sections.
- **ROM fonts** (#1110): six recreated printer fonts, crop-validated per merchant
  (Vons/CVS=C1-heavy, Smith's=pixCrog, HD=D1, Target~pixCrog, WholeFoods=D1).
- **GlyphScore + typography attribution** (#1111/#1106): per-line/per-crop
  typeface matching machinery — a *merchant fingerprint* independent of Places.
- **Compose consistency checks** (#1108 line): role cardinality, arithmetic
  reconciliation — invertible into validators for real receipts.
- **Section MCP tools + stream sync** (#1092/#1096): QA loop + Chroma propagation.

## The resolver: five deterministic passes (D0-D4)
- **D0 merchant fingerprint**: match the upload's letter crops against the
  typeface registry (ROM fonts + merchant atlases) via calibrated shifted-IoU.
  Output: (typeface, merchant-candidates, confidence). Cross-check Places result;
  disagreement -> flag. Cheap: a few hundred crops, pure numpy.
- **D1 row structure**: group lines into ReceiptRows at ingest (persist them —
  the entity + grouping are merged). Detect price column; pair label+amount.
- **D2 section assignment (sync)**: per-merchant section-order priors learned
  from the QA corpus (storefront->address->items->...->footer with merchant
  variants) + zone/row features -> deterministic section per row. The KNN
  propagator upgrades/verifies asynchronously when embeddings land; disagreements
  -> PENDING for QA.
- **D3 label reconciliation**: LayoutLM word labels x sections consistency
  (GRAND_TOTAL must sit in TOTAL_LINE/SUMMARY; PRODUCT_NAME not in FOOTER; the
  #1066 audit rules as *corrections*), arithmetic reconciliation
  (line totals -> subtotal, subtotal+tax -> total; tax-exempt handling), and
  merchant templates (Smith's fuel points, Vons member savings, Costco member#).
- **D4 structured output**: receipt details (merchant, date/time, items
  [name/qty/price], subtotal/tax/total, tender) each with provenance
  (layoutlm | section-rule | arithmetic | template | fingerprint) and confidence;
  conflicts land in the NEEDS_REVIEW queue, never silently resolved.

## Insertion points (verified in repo)
- infra/label_harmonizer_step_functions + label_validation_agent_step_functions:
  post-LayoutLM label flow — D3 lives here.
- receipt_upload/receipt_upload/line_items/{reconstructor,semantic_proposer}.py:
  item assembly — D1/D2 feed it.
- Ingest path (upload OCR processing): D0/D1 at entity-creation time.

## Merchant expansion (census: dev sectioned receipts)
Tier 1 — ROM-matched, immediate: **Whole Foods** (bitMatrix-D1 validated, 6 rcpts,
profile missing). Tier 2 — volume/local: **Smith's** (needs more scans; pixCrog
ready), **Dollar Tree** (6; test C1/D1), **BJ's Wholesale**. Tier 3 — the
long-tail POS class: Italia Deli (10), The Stand (9), Roast & Rice (9),
Neighborly (9), Moody (8), East Coast Bagel (7), Urth (6) — these are generic
POS printers; recreating **bitMatrix-A1/B1** (Xprinter/SNBC family — specimen
charts published on receiptfont.com like the six already recreated) likely
covers most of the class in one stroke. Also: unify 'Target Grocery' (10) with
Target; normalize TRADER JOE'S casing variants (normalize_merchant, PR #1118).

## Constraints
- Additive only; LayoutLM stays the proposer; deterministic layer corrects with
  provenance, never overwrites without recording why.
- Dev-first; prod via the established promotion path.
- Measurement rules apply (vetted receipts for any calibration; distributions
  not n=1; per-merchant fingerprint thresholds derived, not hand-set).
