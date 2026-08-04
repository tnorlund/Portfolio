# SMART Re-OCR: Strategy Ladder + Outcome Harvest

The re-OCR loop stopped being "crop and hope": every REGIONAL_REOCR
attempt now carries a *capture strategy* chosen from the diagnosed
OCR-failure *mechanism*, and every completed attempt reports metrics
that feed back into the strategy ordering. The Swift worker consumes
the contract fields below; this doc records the Python-side wiring.

## Contract (OCRJob optional fields)

All fields are optional and absent-tolerant — legacy jobs parse
unchanged (`receipt_dynamo/receipt_dynamo/entities/ocr_job.py`).

| Field | Writer | Meaning |
|---|---|---|
| `reocr_strategy` | trigger | `plain` \| `invert` \| `deskew` \| `upscale2x` |
| `reocr_mechanism` | trigger | free string, e.g. `reverse-video-total`, `tilted-0deg-quads`, `small-print`, `pen-stroke` |
| `reocr_words_accepted` | overlay | overlaid + added words at completion |
| `reocr_words_rejected` | overlay | guard-rejected overlay matches |
| `reocr_delta_before` | overlay | items-sum − printed subtotal before the overlay (null when no ITEMS section/subtotal) |
| `reocr_delta_after` | overlay | same, from the post-overlay word set |

## Strategy ladder

`receipt_upload/receipt_upload/line_items/reocr_strategy.py` (pure,
stdlib-only, bundled into the line-item updater Lambda next to
`blocks.py`/`reocr.py`).

Default mechanism → ordered strategies:

- `reverse-video*` → `[invert, plain]`
- `tilted*` → `[deskew, plain]`
- `small-print*` → `[upscale2x, plain]`
- anything else (incl. `pen-stroke`) → `[plain, upscale2x]`

`choose_strategy(mechanism, attempt_number, ledger)` returns the
ladder rung for the 1-based attempt: attempt 2 is always a DIFFERENT
strategy than attempt 1 (the full order appends the remaining
strategies, so the first four attempts never repeat). The `ledger`
argument overrides the hand-written order by measured success: a
mechanism × strategy pair with ≥ 3 harvested attempts is ranked by
(acceptance rate, mean |delta| improvement) ahead of unmeasured
strategies. `ledger=None` loads the committed asset.

## Outcome harvest

`scripts/harvest_reocr_outcomes.py --table <ReceiptsTable>` scans
completed REGIONAL_REOCR OCRJobs and aggregates, per mechanism ×
strategy: attempts, word-level acceptance rate, and mean
`|delta_before| − |delta_after|`. It writes
`receipt_upload/receipt_upload/line_items/assets/reocr_ladder.json` —
a committed asset (same pattern as the block-role priors), so a
harvest run is a reviewable diff, not hidden state. The aggregation
itself is `reocr_strategy.build_ledger()` and is unit-tested with
fixture jobs (`receipt_upload/tests/test_reocr_strategy.py`).

## Trigger wiring

1. **Reconciliation loop** —
   `infra/receipt_line_item_updater/line_item_processor.py::`
   `_maybe_trigger_items_reocr` counts prior `line_items_recon`
   attempts (cap 2) and passes `attempt_number = prior + 1` to
   `choose_strategy`, so the capped second attempt climbs the ladder
   instead of repeating the failed capture.
   `update_receipt_line_items(..., reocr_mechanism=...)` threads an
   optional mechanism from direct callers; the SQS summary-stream path
   never sets it (unknown ladder applies).
2. **Trigger Lambda** —
   `infra/trigger_reocr_lambda/lambdas/trigger_reocr.py` accepts
   optional `reocr_strategy` (validated against the contract set) and
   `reocr_mechanism`, and stamps both onto the created OCRJob. It
   deliberately does NOT compute a default strategy: it only bundles
   `receipt_dynamo`, so ladder decisions stay with callers.
3. **MCP tool** — `trigger_reocr` in BOTH server variants
   (`scripts/receipt_mcp_server.py`,
   `infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py`)
   gained optional `strategy` + `mechanism` args, forwarded to the
   Lambda as `reocr_strategy`/`reocr_mechanism`.

## Mechanism source: triage dossiers

Dossier v2 files (`.dev-harness/dossiers/<image_id>-<receipt_id>.json`,
schema in `RUNBOOK_TRIAGE.md`) carry the diagnosed failure in
`mode` + `visual_evidence`. `reocr_strategy.mechanism_from_dossier()`
derives the canonical mechanism string from that text
(reverse-video/inverted → `reverse-video-total`; tilt/skew/rotation →
`tilted`; small/fine print → `small-print`; pen/ink/handwriting →
`pen-stroke`; else None).

Escalation paths that hold a dossier (the adjudicator's T2 queue and
the writer's post-session flows) should call
`mechanism_from_dossier(dossier)` and pass the result as `mechanism`
to the MCP `trigger_reocr` tool (with `compute_reocr_region` first,
and a `strategy` only when deliberately overriding the ladder).
Neither `agentic_adjudicate.py` nor `agentic_writer.py` triggers
re-OCR programmatically today — re-OCR from those flows goes through
the MCP tool under the existing write rules (dev table, writer
serialization, backup first).

## Completion metrics

The container OCR handler's regional overlay
(`infra/upload_images/container_ocr/handler/ocr_processor.py::`
`_process_regional_reocr_job`) already counted accepted/rejected
overlays; it now persists them onto the OCRJob at completion together
with the items-zone delta before/after (computed with the same
`extract_items` decode the line-item updater uses, against the
ITEMS-section line ids and the printed subtotal; null when either is
missing). Metrics persistence is best-effort and never fails the
overlay.
