# Line-Item Extraction — State of the System (2026-08-03)

One-page orientation for any session (Claude, Codex, or human) picking up this
work. The companion PLAN.md holds the forward plan; retro/ holds the full
retrospective this snapshot distills.

## What the system is now

A receipt photographed on a phone is OCR'd, sectioned, summarized, and decoded
into named/priced/quantity-aware **line items at ingestion** — deterministically,
with no LLM in the loop — then judged against three independent truths:

1. **Its own arithmetic**: decoded items must sum to the printed
   subtotal/total (`reconciliation_status` on every RECEIPT_LINE_ITEM row).
2. **Printed-figure hygiene**: three-figure baseline (subtotal, else
   grand_total − tax), impossible figures (SKU/MID-as-money, subtotal>total)
   classified no-baseline, and a 1..3 `baseline_figures_agreeing` grade.
3. **Bank records**: Chase + Apple Card ledgers joined per receipt
   (tender_class / card_last4 / ledger / bank_amount on the summary).
   Bank match is a strong POSITIVE signal and an unusable negative one.

Self-repair: reconciliation mismatch → capped (2×) regional re-OCR of the
ITEMS zone → corrected words → summary recompute → line items regenerate via
the DynamoDB stream. Any repair to inputs (sections, labels, summaries)
auto-regenerates line items; nothing writes line items directly except the
canonical decoder.

## Key components

| Piece | Where |
|---|---|
| Band-block decoder (canonical) | `receipt_upload/receipt_upload/line_items/{geometry,blocks}.py` + priors JSON assets |
| Ingest stage (stream → SQS → Lambda) | `receipt_dynamo_stream/*`, `infra/receipt_line_item_updater/`, wiring in `infra/chromadb_compaction/components/` |
| Re-OCR loop | trigger in `line_item_processor.py`, region math `line_items/reocr.py`, completion hook in container OCR handler |
| Swift port (see WARNING) | `receipt_ocr_swift/Sources/ReceiptOCRCore/LineItems/` + `Sections/` |
| MCP repair tools | `get_receipt_line_items`, `extend_items_section` (arithmetic-guarded), `list_reconciliation_worklist` — both server variants |
| Tender/bank | `receipt_upload/tender.py`, summary entity fields, `scripts/backfill_tender_bank.py` (ledger data is LOCAL-only: `~/receipts-email/email_receipts.db` + Apple Card PDFs) |
| Validation workstation | `/dev/validation` on branch `codex/geometric-reader` (local-only; shim `portfolio/dev-harness/validation_shim.py`; `VALIDATION_MATH_AUDIT.md`) |
| Golden set + gates | `receipt_upload/tests/fixtures/line_items_golden*.json` (33 receipts), per-merchant floors in `test_line_item_golden_regression.py`, corpus sweep as label-free second gate |
| CDN repair tool | `scripts/rewarp_receipt_cdn_images.py` (--force, stale_timestamp verdict, raw-bucket backups) |

## Current numbers (dev, 679 baselined receipts)

- Corpus match 452 / near 38 / mismatch 122 / no-baseline 67 (was 411 match at
  session start). Golden set 25→33. CORD-v2 external: recall 79.7 / names 76.5 /
  precision 98.0 on pure structural fallback.
- Bank: honest coverage 75.9% of eligible (349/460); 96% of bank-matched
  printed totals exact. Dev summaries carry tender (815) + bank (425).
- Prod: fully rolled out structurally (~2,600 line items, sections, repaired
  CDN assets ~424) but rows predate the quality fixes (mismatch 848) and carry
  ZERO tender/bank fields → the 822-receipt sweep in PLAN.md is the fix.

## Standing WARNINGS (each cost real hours; do not relearn)

1. **Swift decoder is FORKED from Python** as of #1320/#1321: the 33/33 parity
   fixture froze at #1313 (no band filter). Regenerate expectations from live
   Python in CI before trusting or wiring the Swift decoder as producer.
2. MCP receipt tools default to DEV (`PORTFOLIO_ENV`); prod writes need
   explicit `DynamoClient("ReceiptsTable-d7ff76a")`.
3. CI deploys PROD only — after merging Lambda/entity changes, deploy the dev
   stack too, then re-run any backfills stale code recomputed.
4. Site bucket deploy runs `s3 sync --delete` excluding only `assets/*` —
   any other runtime-written prefix is silently destroyed (killed the rewarp
   backups once; bucket unversioned). Runtime data → raw/artifacts buckets.
5. Reseg applies: plan + confirm the apply path executes BEFORE destructive
   pre-steps (label strips); serialized single-flight; local `apply_plan` with
   `create_embeddings=False` is the working recipe. #1327 hardened the tool
   (async job-id, apply_token vs S3 ETag ABA, summary tombstone) — merge state
   should be checked.
6. Two CI lint jobs have OPPOSITE isort personalities (no root config):
   replicate the exact bare venv from main.yml before "fixing" imports.
7. New Lambdas under `ignore_changes=["layers"]` are born with stale layers.
8. Batch label writes have no condition expression and clobber VALID rows —
   use the conditional singular path.
9. Never merge frontend/visual changes without the user's review (#1316/#1317).

## Session-conduct rules (from the retrospective — retro/retro_strategist.md)

- One session = ONE declared numeric metric delta.
- Never mix exploratory analysis with prod writes in the same thread.
- Agents: bounded read-only analysis only; ONE serialized writer for any
  shared mutable state; ≤3 open PRs per session.
- Metric = **PROVEN receipts (count, in prod)**: reconciles to printed
  baseline AND matches a ledger amount. Rates are gameable; counts are not.
