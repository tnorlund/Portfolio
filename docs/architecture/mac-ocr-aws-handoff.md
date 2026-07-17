# AWS → Mac OCR → AWS: the upload handoff contract

This document describes the round-trip contract between the Swift OCR worker
(`receipt_ocr_swift`, runs on a Mac) and the AWS post-processing pipeline
(receipt persistence, embeddings, Chroma, merchant resolution, and section
assignment). It pins where each stage executes, which artifacts cross the
boundary, and the invariants that local contract tests enforce.

Related docs: `docs/PHASE3_SINGLE_PASS_OCR.md` (history of the single-pass
migration), `docs/UPLOAD_DETERMINISM_D2_EVALUATION.md` (section-model
evaluation).

## End-to-end flow

```
            AWS                                Mac (Swift worker)
 ┌────────────────────────┐           ┌──────────────────────────────────┐
 │ upload → OCRJob row +  │  SQS jobs │ 1. poll ocr-job queue            │
 │ image in raw S3 bucket ├──────────►│ 2. download image from S3       │
 └────────────────────────┘           │ 3. Vision OCR (full image)      │
                                      │ 4. classify NATIVE/PHOTO/SCAN   │
                                      │ 5. cluster + warp receipts      │
                                      │ 6. Vision OCR (warped crops)    │
                                      │ 7. LayoutLM (CoreML) per line   │
                                      │ 8. upload warped crops → S3     │
                                      │ 9. upload OCR JSON → S3         │
                                      │10. PENDING ReceiptWordLabels    │
                                      │    → DynamoDB (direct write)    │
                                      │11. OCRRoutingDecision → Dynamo  │
 ┌────────────────────────┐  SQS      │12. send ocr-results message     │
 │ process-OCR-results    │◄──────────┴──────────────────────────────────┘
 │ Lambda (container)     │
 │  A. parse Swift JSON → Receipt, Line/Word/Letter,                     │
 │     ReceiptLine/Word/Letter, ReceiptBarcode, CDN formats              │
 │  B. persist_receipt_rows → ReceiptRow (visual rows)                   │
 │  C. MerchantResolvingEmbeddingProcessor                               │
 │     Phase 1: OpenAI row+word embeddings (+ Chroma snapshot download)  │
 │     Phase 2 lines pipeline: merchant resolution →                     │
 │        assign_and_persist_sections → verify_receipt_sections →        │
 │        build_row_payload(section metadata) → lines delta → S3         │
 │     Phase 2 words pipeline: label validation (Chroma + LLM) →         │
 │        words delta → S3                                               │
 │     Phase 3: CompactionRun row → async Chroma compaction              │
 │     Phase 4: ReceiptPlace enrichment                                  │
 └───────────────────────────────────────────────────────────────────────┘
```

## Where each stage executes

| Stage | Executes on | Code |
|---|---|---|
| Receipt detection, warp, Vision OCR | Mac | `receipt_ocr_swift/Sources/ReceiptOCRCore/{OCR,ReceiptProcessing,Clustering}` |
| Image classification (NATIVE/PHOTO/SCAN) | Mac | `Classification/ImageClassifier.swift` |
| LayoutLM word-label inference | Mac (CoreML) | `LayoutLM/LayoutLMInference.swift` |
| OCR JSON parse → Dynamo entities | AWS Lambda | `infra/upload_images/container_ocr/handler/ocr_processor.py` |
| Visual-row materialization (ReceiptRow) | AWS Lambda | `receipt_upload/receipt_processing/rows.py` |
| Row/word embeddings (OpenAI) | AWS Lambda | `receipt_chroma.embedding.download_and_embed_parallel` |
| Merchant resolution (Chroma + Places) | AWS Lambda (lines pipeline) | `receipt_upload/merchant_resolution/{resolver,embedding_processor}.py` |
| Section assignment (semi-Markov decoder + packaged priors) | AWS Lambda (lines pipeline) | `receipt_upload/section_assignment.py` + `receipt_upload/assets/section_order_priors_v2.json` |
| Section KNN verification | AWS Lambda (lines pipeline) | `receipt_upload/section_verifier.py` |
| Label validation (Chroma KNN + LLM) | AWS Lambda (words pipeline) | `receipt_upload/label_validation/` |
| Chroma compaction (snapshot merge) | AWS, async | triggered by `CompactionRun` Dynamo stream |

Section segmentation is deliberately **not** ported to Swift. The Mac worker
ships evidence (OCR geometry + LayoutLM labels); every merchant-conditioned
or cross-receipt decision (merchant resolution, sections, verification,
validation) runs in AWS where the priors, Chroma index, and DynamoDB state
live.

## Artifacts crossing the boundary

Downloaded to the Mac:
- the original image (`OCRJob.s3_bucket/s3_key`, raw bucket);
- the LayoutLM CoreML bundle (`LAYOUTLM_MODEL_S3_BUCKET/KEY`, cached locally
  via `AWS/ModelDownloader.swift`). The cache is **presence-based and
  ignores the S3 bucket/key entirely**: `ensureModelDownloaded` returns the
  fixed `layoutLMLocalCachePath` (default `.models/layoutlm`) whenever the
  required files exist there, before even looking at bucket or key. Neither
  republishing under the same key NOR pointing config at a new key
  invalidates a worker's cache. To roll a model you must delete the cache
  directory on each worker, or configure a fresh (e.g. key- or
  version-derived) `layoutLMLocalCachePath`.

Returned to AWS by the Swift worker:
- warped receipt crops → `raw-bucket/receipts/{image_id}/{file}` (S3);
- one OCR JSON per image → `raw-bucket/ocr_results/{image}-{job}.json` (S3);
- `ReceiptWordLabel` items (PENDING, `label_proposed_by="auto-inference"`)
  written directly to DynamoDB. **This write is best-effort**: a failure of
  `addReceiptWordLabels` is logged (`failed_upload_labels`) and the worker
  still sends the results message, completes the job, and deletes the SQS
  message — so a transient DynamoDB failure permanently drops that
  receipt's model labels (the words pipeline then runs with whatever
  labels exist, possibly none). A durable retry/reconciliation path is
  listed under follow-ups;
- `OCRRoutingDecision` (PENDING) pointing at the JSON;
- an `ocr-results` SQS message:
  `{image_id, job_id, s3_key, s3_bucket, receipt_count}`.

## The OCR JSON schema (Swift → Python)

Swift encodes with the shared `makeOCRResultEncoder()` factory
(`VisionOCREngine.swift`: `.convertToSnakeCase` + pretty-printing), used by
both production write sites and the contract tests so encoder settings
cannot silently diverge. Top level:

```jsonc
{
  "lines": [ Line ],                 // first-pass OCR on the ORIGINAL image
  "classification": {                // routes the Lambda to single-pass
    "image_type": "NATIVE|PHOTO|SCAN",
    "image_width": 3024, "image_height": 4032, ...
  },
  "receipts": [ {                    // one per detected receipt
    "cluster_id": 1,                 // becomes receipt_id
    "bounds": { "top_left": {x,y}, ... },   // normalized, bottom-left origin
    "warped_width": W, "warped_height": H,
    "s3_key": "IMG_X_receipt_1.png", // local name; Lambda prefixes receipts/{image_id}/
    "line_indices": [0, 1],
    "lines": [ Line ],               // REFINEMENT OCR on the warped crop
    "layoutlm_predictions": [ { "tokens": [...], "labels": [...], "confidences": [...] } ],
    "barcodes": [ { "payload", "symbology", geometry... } ],
    "needs_review": false
  } ],
  "barcodes": []                     // full-image detections (NATIVE path)
}
```

`Line` = `{text, bounding_box{x,y,width,height}, top_left, top_right,
bottom_left, bottom_right, angle_degrees, angle_radians, confidence,
words: [Word]}`; `Word` adds `letters` and optional `extracted_data`.

Routing rule (`ocr_processor.process_ocr_job`): a payload with non-empty
`receipts` **and** `classification` takes `_process_swift_single_pass`;
anything else falls back to the legacy multi-pass geometry flow.

### The array-position ID invariant

Both sides derive `line_id`/`word_id` from **1-based array positions**:

- Python (`_parse_receipt_ocr_from_swift`) enumerates `lines`/`words` from 1
  and *skips* invalid entries (empty text, confidence ≤ 0) **without
  reclaiming the id**, so skipped entries leave gaps.
- Swift (`ReceiptWordLabel.fromLinePredictions`) keys labels by
  `(prediction array index + 1, token index + 1)` and skips `O`/non-core
  labels the same way.

Labels written by the Mac only land on the right words because
`layoutlm_predictions` is parallel to `receipts[].lines` and each `tokens`
array is parallel to that line's `words` array. This is pinned by contract
tests on both sides (see below).

Note the LayoutLM predictions in the JSON are parsed by the **Swift worker
itself** (to write labels to DynamoDB before sending the results message);
the Python Lambda never reads `layoutlm_predictions`. The words pipeline
later reads the labels back from DynamoDB.

## Post-parse pipeline ordering (per receipt)

1. **Persist OCR entities.** `_process_swift_single_pass` writes Receipt,
   ReceiptLine/Word/Letter, ReceiptBarcode, and calls
   `persist_receipt_rows` (delete-then-put replacement) so **visual
   ReceiptRows exist before the embedding/section pipeline starts**. Row
   identity: `row_id` = primary (leftmost) line id of the visual row.
2. **Embeddings.** The handler invokes
   `MerchantResolvingEmbeddingProcessor.process_embeddings` only after the
   OCR result returns (`swift_single_pass=True` + `receipt_ids`).
3. **Lines pipeline** (`_run_lines_pipeline_worker`), strictly ordered:
   1. `MerchantResolver.resolve` (Chroma similarity → Places → …);
   2. merchant-name write-time validation against receipt text;
   3. `assign_and_persist_sections(dynamo, persisted_rows, lines,
      validated_merchant_name)` using the packaged priors
      (`section_order_priors_v2.json`); falls back to
      `build_receipt_rows` reconstruction only for legacy replays;
   4. `verify_receipt_sections` (cross-receipt VALID Chroma neighbors,
      annotate-only);
   5. `build_row_payload(..., section_by_line=...)` so the **first** lines
      delta already carries `section_label` row metadata;
   6. upsert + `upload_lines_delta` → S3.
4. **Words pipeline** runs concurrently (label validation; never touches
   sections).
5. **CompactionRun** is created only after both deltas exist; async
   compaction merges them into the shared snapshot.

### Section invariants

- `assign_and_persist_sections` is **additive**: it only `add`s sections of
  types absent for the receipt, always `PENDING` with
  `model_source="upload-determinism-v1"`. It never updates existing
  sections, so human/VALID sections are never overwritten. Concurrent adds
  are absorbed via `EntityAlreadyExistsError`.
- The returned `line_id → section_type` map prefers VALID sections over
  PENDING ones (`sections_to_line_map`), so human truth wins in Chroma
  metadata.
- `verify_receipt_sections` **annotates only**: it sets
  `verification_source/status/section_type/confidence`,
  `disagreement_row_ids`, `verified_at`. It never changes `section_type`,
  `line_ids`, or `row_ids`, never touches non-model sections, and never
  demotes a VALID section (disagreement leaves PENDING sections PENDING and
  records the independent prediction as provenance:
  AGREED / DISAGREED / ABSTAINED).
- Verification failure is non-fatal: an unavailable neighbor index must not
  discard the deterministic proposal.

## Contract tests (local, mocked)

| Contract point | Test |
|---|---|
| Swift output schema (snake_case keys, production `ImageResult` envelope via the shared encoder, prediction/word alignment, SQS message shape, PENDING labels) | `receipt_ocr_swift/Tests/ReceiptOCRCoreTests/OCRResultContractTests.swift` |
| Python parse of the same fixture (detection rule, id gaps, barcodes, label vocabulary parity, rows persisted before embeddings, handler ordering) | `infra/upload_images/container_ocr/handler/tests/test_swift_payload_contract.py` |
| Lines-pipeline ordering + section metadata reaching the row payload; additive PENDING persistence; annotate-only verification | `receipt_upload/tests/test_section_pipeline_contract.py` |

Both sides consume the same fixture:
`receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures/swift_single_pass_contract.json`.
Change the payload shape on either side and a local test fails before the
Lambda does.

## Operational provenance

### Implemented: metrics-only observability (this branch)

The lines pipeline now returns, and the handler emits via EMF (one log line
per receipt, `_emit_section_observability` in `handler.py`), with
`image_id`/`receipt_id`/`row_source` as properties:

| Metric | Meaning |
|---|---|
| `UploadLambdaReceiptRows` | visual-row count used for section assignment |
| `UploadLambdaReceiptRowsReconstructed` | 1 when ingest persisted no rows and the lines worker reconstructed them in-process (legacy/dev replays); alarm on sustained >0 for fresh uploads |
| `UploadLambdaSectionsProposed` | deterministic sections created (additive PENDING only) |
| `UploadLambdaSectionMeanConfidence` | mean confidence of those proposals |
| `UploadLambdaSectionAgreed` / `Disagreed` / `Abstained` | Chroma KNN verification outcomes for `upload-determinism-v1` sections |

The lines worker also logs a structured
`[ROW_PROVENANCE] image_id=... receipt_id=... row_source=persisted|reconstructed row_count=N`
line, so persisted vs reconstructed rows are distinguishable in logs as well
as metrics. Row identities, section behavior, and persistence semantics are
unchanged; every addition is read-only observability.

### Still proposed (not implemented)

1. **LayoutLM model version.** The worker knows the bundle S3 key, but no
   content hash exists anywhere today (the ModelDownloader cache is
   presence-based; see "Artifacts crossing the boundary"). Prerequisite:
   compute a bundle hash at download time (SHA-256 of the fetched archive,
   or record the S3 ETag/version-id) — which also enables fixing the
   stale-cache problem. Then emit
   `{"layoutlm_model": {"s3_key": ..., "bundle_hash": ...}}` in the OCR
   JSON top level and stamp labels with
   `label_proposed_by="auto-inference:<short-hash>"`. Safe today: nothing
   exact-matches `"auto-inference"` (verified; the only proposer
   exact-match is `_GEOMETRY_PROPOSER == "geometry_line_items"` in the
   words pipeline), but see the compatibility list below before landing.
2. **Section-priors artifact version.** Compute the SHA-256 of
   `section_order_priors_v2.json` once per process in `load_prior_model()`
   and stamp it into a **separate additive field** (e.g.
   `ReceiptSection.model_version` or `section_priors_sha256`).
   **Do NOT suffix or otherwise change `model_source`**: the verifier and
   the embedding statistics compare it *exactly* against
   `"upload-determinism-v1"`, so a suffixed value would silently exclude
   those sections from verification and stats (see below).
3. **Durable per-receipt provenance record.** Consider a compact
   `provenance` map attribute on `OCRRoutingDecision` (already updated at
   completion) holding the model hashes plus the metric values above for
   point lookups without CloudWatch queries.

### `model_source` compatibility: exact-comparison sites

Any future provenance-field change (or any change to the
`"upload-determinism-v1"` literal) must audit these sites, all of which
match `ReceiptSection.model_source` exactly:

- `receipt_upload/section_assignment.py:27` — `MODEL_SOURCE` constant
  (the writer).
- `receipt_upload/section_verifier.py:185` —
  `section.model_source != MODEL_SOURCE` skips everything else, so a
  suffixed source would make the verifier ignore its own pipeline's
  sections.
- `receipt_upload/merchant_resolution/embedding_processor.py`
  (verification-stats block) — filters on `model_source == MODEL_SOURCE`
  when counting AGREED/DISAGREED/ABSTAINED (was a hardcoded literal;
  now uses the constant, still an exact match).
- `scripts/build_section_order_priors.py:254` — stamps the literal into
  the trained artifact's output metadata. Note the training corpus itself
  is selected by `validation_status == VALID` regardless of
  `model_source`; the literal here is provenance stamping only, so a
  future provenance field changes what gets *recorded*, not what gets
  *trained on*.
- `scripts/remap_sections.py` — *appends* `+remap-v1` to `model_source`
  on remapped sections. This is a deliberate pre-existing exception: it
  intentionally takes remapped sections OUT of the exact-match population
  (the verifier and stats no longer treat them as live deterministic
  proposals). Any new provenance scheme must not copy this pattern for
  live sections.
- `tools/glyph-studio/py/face_map_v2_cli.py` (Counter grouping) and
  `tools/glyph-studio/py/write_section_seeds.py` (None check) — tolerant,
  but grouping output changes if values change.
- `infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py` — MCP
  tools read/write `model_source` as an opaque string (defaults
  `"mcp-claude-review"`); pass-through, no exact match on the
  deterministic literal.

## Future design: two-pass, top-K merchant resolution (design only)

Today merchant resolution runs once, returns a single best candidate, and
section assignment consumes it as a merchant prior. The structural signal
never flows back into identity. A two-pass design would close that loop
without letting layout alone decide identity:

1. **Pass 1: global sections.** Run section assignment with the *global*
   prior only.
2. **Focused evidence extraction.** Use predicted HEADER, ADDRESS,
   TRANSACTION_INFO, and FOOTER regions to focus merchant-evidence
   extraction (name candidates, address/phone lines, store numbers,
   domains) instead of scanning all lines uniformly.
3. **Top-K + UNKNOWN.** Merchant resolution returns the top few candidates
   *plus an explicit UNKNOWN candidate*, never just one winner.
4. **Pass 2: structural fit.** For candidates whose merchant profile has
   sufficient training support in the priors, rerun section decoding with
   that merchant's prior and measure whether it *improves* the receipt's
   structural fit (decoder log-likelihood delta vs. the global prior).
5. **Score combination.** Combine independent identity evidence with the
   structural score: merchant-name similarity; exact address/phone/domain/
   store-number matches; Chroma similarity to VALID receipts; Places
   evidence; LayoutLM word-label evidence (e.g. MERCHANT_NAME tokens);
   section-sequence fit.
6. **Identity gate.** Require receipt-local identity evidence before
   confirming a merchant. Section layout alone must never confirm one
   (many chains share near-identical layouts).
7. **Trusted-metadata guard.** Weak candidates are never written into
   trusted Chroma metadata; below-threshold results resolve to UNKNOWN and
   leave `merchant_name` unset, exactly like today's write-time validation.
8. **Cold-start path.** Preserve the global/UNKNOWN path for new merchants
   and merchants without profiles; pass 2 is an upgrade, not a gate.

Proposed candidate-result contract (per candidate, including UNKNOWN):

```jsonc
{
  "merchant_name": "Sprouts Farmers Market",
  "merchant_id": "places:ChIJ...",        // stable id (place_id or internal)
  "resolver_score": 0.83,                  // pass-1 identity-only score
  "evidence": ["name_similarity", "phone_exact", "chroma_knn", "places"],
  "profile_support": 41,                   // receipts backing the merchant prior
  "section_fit": {"global": -142.7, "merchant": -128.3, "improvement": 14.4},
  "combined_score": 0.91,                  // calibrated (e.g. isotonic/Platt)
  "decision": "CONFIRMED | CANDIDATE | UNKNOWN",
  "abstain_reason": null                   // e.g. "no_receipt_local_identity_evidence"
}
```

Evaluation metrics before any rollout:
- top-1 and top-3 merchant recall;
- UNKNOWN/abstention accuracy (abstaining exactly when truth is absent);
- section-accuracy improvement attributable to pass 2;
- confident-merchant error rate (wrong merchant at `CONFIRMED`), the metric
  that must stay near zero;
- score calibration (reliability curves / ECE on `combined_score`);
- all of the above on both receipt holdouts *and merchant-disjoint
  holdouts* (merchants absent from priors), so profile memorization cannot
  masquerade as recall.

This section is a design sketch only: the resolver, section model, priors,
Chroma writes, and infrastructure are unchanged by the current task.

## Known gaps / follow-ups

- `swift test` requires a full Xcode install (XCTest is not in Command Line
  Tools); on a CLT-only machine the Swift contract tests compile-check but
  cannot execute. Run them on the mini:
  `cd receipt_ocr_swift && swift test --filter OCRResultContractTests`.
  Status 2026-07-17: the mini was unreachable (WARP and LAN both down) when
  this branch was authored, so the Swift-side execution is still pending;
  the Python side of the same fixture passes locally.
- Run the Python suites per package
  (`receipt_upload/tests`, `infra/upload_images/container_ocr/handler/tests`)
  in separate pytest invocations: collecting both roots together imports
  `infra/upload_images/__init__.py`, which pulls Pulumi infrastructure and
  fails outside a Pulumi context (pre-existing).
- The words pipeline writes word labels to Chroma metadata concurrently
  with the lines pipeline; word-level labels and row-level sections are
  only mutually consistent after compaction.
- The Swift worker's ReceiptWordLabel write is best-effort (see above): a
  transient DynamoDB failure loses that receipt's LayoutLM labels with only
  a `failed_upload_labels` warning. Follow-up: fail the job before sending
  the results message (so SQS retries), or add a reconciliation sweep that
  re-derives labels from the persisted OCR JSON's `layoutlm_predictions`.
- Latent scoping bug in `_run_lines_pipeline_worker`: the tracing block's
  late `import logging` statements make `logging` a local of the outer
  worker, so nested `_do_lines_work` code referencing bare `logging` raises
  `NameError` on the no-tracing-headers path (several pre-existing call
  sites: write-time validation warning, verification exception handler).
  New code uses the module-level `logger`; the pre-existing sites should be
  migrated the same way in a follow-up.
