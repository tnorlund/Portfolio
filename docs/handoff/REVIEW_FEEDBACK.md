# Review Feedback — verbatim, per PR

Every inline comment posted by the six independent reviewers, reproduced here so
codex has them in-repo (the live comments are also on each PR). Severity: P1 =
blocker, P2 = fix before merge, P3 = should-fix / note. Anchors are
`path:line` at the reviewed commit.

---

## PR #1140 — [D1] Persist deterministic receipt rows at ingest — MERGE-READY

- **P2** `receipt_chroma/receipt_chroma/embedding/formatting/receipt_rows.py:18` —
  amount regex misses 4+ digit amounts without a thousands separator.
  `\d{1,3}(?:,\d{3})*` requires ≤3 integer digits or comma-grouped thousands.
- **P2** `receipt_upload/receipt_upload/receipt_processing/rows.py:24` —
  re-ingest / re-OCR orphans stale rows (idempotency gap). `add_receipt_rows` is
  an unconditional batch put keyed on `RECEIPT#{id}#ROW#{primary_line_id}`; none
  of the three call sites delete prior rows first. On re-OCR, lines are
  re-numbered, so old `ROW#{old_primary}` items whose primary is no longer a row
  leader persist as orphans. Since D2 sections and the Chroma lines collection
  join on `row_id == primary_line_id`, orphans corrupt the join. **Delete prior
  rows for the receipt before the batch put.**
- **P3** `receipt_chroma/tests/unit/test_receipt_rows.py:116` — edge coverage
  gaps: re-ingest idempotency, single-line/no-amount row, 3+ line merged row,
  price-column tie-break.

## PR #1150 — [D4] Publish structured receipt details — MERGE-READY

- **P2** `receipt_upload/receipt_upload/structured_details.py:132` (determinism) —
  `_PROVENANCE - {"fingerprint"}` builds a set and iterates it; provenance order
  depends on set hash-ordering (randomized `PYTHONHASHSEED` in Lambda). Iterate a
  fixed-precedence tuple: `("layoutlm", "template", "section-rule", "arithmetic")`.
- **P2** `receipt_upload/receipt_upload/structured_details.py:285` (contract) —
  `_merchant_field` returns a **flat** dict (`merchant.value` = name) but every
  persisted-shape test models merchant as a **container** (`merchant.name.value`).
  Builder and tests disagree; `merchant` is also structurally unlike its siblings
  `transaction`/`totals`/`tender`/`items`. Pick one shape, make builder+tests
  agree, document it in the TS type.
- **P2** `infra/label_evaluator_step_functions/lambdas/unified_receipt_evaluator.py:1278`
  (write side effects) — this calls `reconcile_receipt_labels` a **second** time;
  that function is not read-only (it applies label mutations + persists an
  artifact). So resolving D4 here re-runs a full D3 write pass and overwrites the
  earlier `d3_summary`. Read the existing labels/reconciliation instead of
  re-running D3, or comment that the re-run is deliberate.
- **P2** `receipt_dynamo/receipt_dynamo/entities/receipt_resolved_details.py:21`
  (confidence semantics) — `confidence` is filled from heterogeneous sources (OCR
  word confidence, D3 correction confidence, typeface IoU, hardcoded `0.0`) under
  one comparable-looking key; `0.0` doubles as "low" and "unknown". Document that
  it is source-relative and must be read with `confidence_basis`/`provenance`.

## PR #1151 — Merchant typeface expansion — MERGE-READY

- **P2** `receipt_upload/receipt_upload/typeface_fingerprint.py:159` — the
  Target-Grocery "unification" is redundant and fragmented, not a merge at the
  normalization layer. Effectively a no-op.
- **P2** `scripts/build_typeface_registry.py:192` — the anti-copy exception
  predicate is too weak. `left_hash != right_hash` only establishes the files
  aren't byte-identical, not that they're "independently published sibling
  printer-ROM specimens." Any re-saved/re-cropped chart silently downgrades an
  over-threshold overlap from FAILURE to audited PASS. Defeats the gate for
  exactly the case it exists to catch.
- **P3** `receipt_upload/receipt_upload/assets/typeface_registry_v1.json:1089` —
  `bitMatrix-B1` added with empty `merchant_candidates`/`validation_profiles` and
  is 77.66% identical to the already-present unassigned `bitMatrix-B2`. Zero
  matching value now, injects the median-IOU tie the companion change then breaks.
  Defer B1 until a merchant validates to it, or document the roadmap reason.
- **P3** `synthesis_loop/rom_font_manifests/wholefoods-bitMatrix-D1.profile.json:15`
  — thin winner margin: D1 beats pixCrog by only 0.0173, barely above the
  ambiguity band. Low-confidence pin; don't treat as a strong anchor downstream.

## PR #1149 — [D3] Reconcile receipt labels — MERGE AFTER 2 P2

> Spec named `infra/label_harmonizer_step_functions` +
> `infra/label_validation_agent_step_functions` as where D3 lives; **neither
> exists**. The PR wired D3 into `label_evaluator_step_functions` + the upload
> embedding path + the async LLM consumer. Confirm `label_evaluator` is the
> intended post-LayoutLM harmonization step.

- **P2** `infra/label_evaluator_step_functions/lambdas/Dockerfile.unified:28` —
  `--no-deps` means `receipt_upload`'s deps (notably **Pillow**) aren't installed.
  Importing any `receipt_upload.*` submodule runs `__init__.py` →
  `from .font_analysis import ...` → `from PIL import Image`. PIL is not in the
  base image → `ModuleNotFoundError` at runtime.
- **P2** `infra/label_evaluator_step_functions/lambdas/unified_receipt_evaluator.py:668`
  (same root cause) — D3's import raises `ModuleNotFoundError` in the built image,
  so **D3 never actually runs here** — always falls into the `except`. Verify
  end-to-end in the built image, not just unit tests.
- **P2** `receipt_upload/receipt_upload/label_reconciliation.py:481` — merchant
  templates are hardcoded inline substring checks (`"costco"`/`"vons"`/`"smith"
  in merchant`), the per-merchant sprawl the work order asked to avoid. `"smith"
  in merchant` also matches Smithfield/locksmith/Goldsmith. Use a declarative
  template registry keyed by normalized merchant token (`_merchant_key`).
- **P3** `.../unified_receipt_evaluator.py:708` — an infra failure (import error,
  transient Dynamo) is recorded as `conflicts: 1` and flows into `total_issues`.
  Count infra errors separately; leave `conflicts` at 0 on the ERROR path.
- **P3** `receipt_upload/receipt_upload/label_reconciliation.py:48` — footprint
  stricter than spec (PRODUCT_NAME/QUANTITY/UNIT_PRICE allowed only in ITEMS). On
  a mis-sectioned row a correct PRODUCT_NAME gets flagged NEEDS_REVIEW. Deny-list
  FOOTER/PAYMENT instead, or lower confidence when `section_validation_status !=
  VALID`.
- **P3** `receipt_upload/receipt_upload/label_reconciliation.py:611` — on multiple
  exact arithmetic candidates, writes a new NEEDS_REVIEW label on **each**
  candidate word (labels no proposer suggested). Record the ambiguity as a
  check/artifact only; let a reviewer add the winner.
- **P3** `receipt_upload/receipt_upload/label_reconciliation.py:715` — `tax_gap <
  0` (grand total < subtotal: post-subtotal discount/coupon or section error) is
  silently dropped. Record a CONFLICT so it lands in review.
- **P3** `receipt_upload/receipt_upload/label_validation/llm_runner.py:62` — drops
  the structured `label_proposed_by = "llm_valid"/...` signal; the LLM verdict now
  lives only in free-text `reasoning`. Carry it in a structured field.

## PR #1148 — D0 typeface fingerprint — REWORK

- **P1** `receipt_chroma/receipt_chroma/glyph_matching.py:102` (canonical reuse) —
  `normalize_glyph`, `clean_letter_mask`, `_components`, `_shift`, `shifted_iou`
  are verbatim logical copies of glyphstudio's `typography.py`/`family_cluster.py`
  /`glyph_score.py`. The work order forbids re-implementing calibrated machinery
  (#1111 de-vendored for this reason). Parity test pins only `shifted_iou`;
  `clean_letter_mask`/`normalize_glyph` can silently diverge and won't inherit
  #1142/#1147 fixes. **Import glyphstudio** (`tools/glyph-studio/py/pyproject.toml`);
  if the Lambda truly can't depend on it, add exhaustive parity tests over all
  four primitives on a real crop corpus.
- **P1** `receipt_chroma/receipt_chroma/merchant_fingerprint.py:109` (thin
  evidence) — evidence floor gates on raw crop count, not distinct letterforms.
  600 identical 'A' crops → `confidence=0.929` with a confident merchant
  attribution from a SINGLE distinct glyph. Any receipt <569 usable crops abstains
  entirely (excludes most non-jumbo receipts + the whole long-tail POS class).
  Gate on distinct-character coverage and/or per-char-median deviation
  (`line_attribution`/`per_char_medians`/`calibrated_deviation` exist for this).
- **P1** `scripts/build_typeface_registry.py:187` (threshold derivation) —
  `minimum_winner_iou = min(winners)` over 6 merchants is fit so every calibration
  example passes by construction (circular), derived only from the winner
  distribution. No impostor distribution → nothing bounds false-positive rate.
  Off-target atlases reach 0.60–0.68 on matching input, above the 0.4263 floor.
  **Derive the accept floor from the separation between genuine-match and
  cross-merchant impostor distributions.**
- **P2** `scripts/build_typeface_registry.py:182` — `letters_per_receipt` is the
  per-merchant *mean*; `min(...)` is min-over-means, and the "minimum vetted
  per-receipt crop count" label is factually wrong. All six calibration merchants
  are jumbo-format (569–1097 letters/receipt) → calibrated to large receipts only.
- **P2** `scripts/build_typeface_registry.py:173` (calibration scope) —
  distributions are ROM-only (`name.startswith("ROM:")`), but 9/15 registry
  atlases are merchant atlases that can win and then get judged against ROM-derived
  numbers. Over half the registry has no calibration basis. Exclude merchant
  atlases from winner selection, or extend calibration to cover them.
- **P2** `scripts/build_typeface_registry.py:191` (ambiguity honesty) —
  `typeface_ambiguity_band = median(margins) = 0.0214`, but margins range
  0.0001–0.057. Home Depot's top two differ by 0.0001 (tied). Emit multiple
  plausible typefaces / lower confidence for low-margin cases; consider a per-pair
  band.
- **P2** `receipt_chroma/tests/unit/test_typeface_fingerprint.py:92` (known-answer
  / negative-control) — every test uses synthetic bar glyphs. Add (a) a
  known-answer test on real crops (a real Costco crop set must win costco) and (b)
  a NEGATIVE control asserting a foreign receipt's crops do NOT clear
  `minimum_winner_iou` against the wrong atlas. Use the #1110 manifests' per-merchant
  crops.
- **P3** `receipt_chroma/receipt_chroma/merchant_fingerprint.py:150` — confidence
  is a mean of two empirical percentiles over n=6, collapses to ~6 levels; a
  perfect match (IoU=1.0) reports 0.929. Document as a relative score.
- **P3** `receipt_dynamo/receipt_dynamo/entities/receipt_typeface_fingerprint.py:114`
  — `to_item()` calls `__post_init__()`, re-running validation AND mutating state
  on every serialize. Make serialization pure.

## PR #1145 — [D2] Assign and verify deterministic sections — REWORK

> **The empirical failure is the headline** — see
> [evidence/d2_recent_uploads_eval.md](evidence/d2_recent_uploads_eval.md). On the
> 22 newest real dev uploads, row-level agreement vs QA'd ground truth is **58.7%**:
> structural anchors recover well (PAYMENT 0.82, ADDRESS 0.79, FOOTER 0.75,
> STOREFRONT 0.71) but content sections collapse (**ITEMS 0.09, SUMMARY 0.04,
> TOTAL_LINE 0.00, SURVEY 0.00**). ITEMS proposed on only 2 of 18 receipts. On
> unknown merchants the monotone path degenerates into 2–3 mega-sections. This is
> a quality failure, not a safety one — the additive/never-overwrite guards hold.

- **P2** `receipt_upload/receipt_upload/section_verifier.py:206` — verifier can
  demote a human-QA'd VALID section back to PENDING. `_record_verification`
  selects by `model_source == MODEL_SOURCE` and, on disagreement, unconditionally
  sets PENDING regardless of current status. A section this creates keeps
  `model_source=upload-determinism-v1` even after QA promotes it to VALID → a
  later re-verify (this project re-embeds at scale) silently regresses VALID →
  PENDING. **Guard: only demote when current status is PENDING** (still record the
  DISAGREED provenance). *Confirmed forward risk in the eval.*
- **P2** `receipt_upload/tests/test_section_assignment.py:139` — the "golden"
  fixtures assert only determinism + valid-enum, not expected sections. They stay
  green while ITEMS fails on 16/18 receipts. **Add per-row expected `section_type`
  assertions** (at least anchors). *Bites hard — this is the exact regression the
  eval found.* Suggested fixtures: Chick-fil-A `cb581977` (unknown-merchant
  collapse), Smith's `ff574b98` (good case).
- **P3** `receipt_upload/receipt_upload/section_assignment.py:260` — repeat-merchant
  priors drop all token/keyword evidence (`include_tokens=False`), so the 93
  repeat merchants are scored on 6 numeric Gaussians alone while unknown-merchant
  receipts get keyword evidence — higher-signal merchants get the *weaker* model.
  Fall back to the global model's tokens. **Likely a direct contributor to the
  ITEMS collapse.**
- **P3** `receipt_upload/receipt_upload/section_verifier.py:218` — rewrites every
  section on every run (verified_at always differs) → a stream event + Chroma sync
  per section per re-embed. Skip the write when outcome+status unchanged.
- **P3** `receipt_upload/receipt_upload/section_verifier.py:116` — verification
  recall can starve on re-embeds: same-receipt neighbors dominate top-15 → mostly
  abstain. Request more neighbors or exclude `image_id` via a Chroma `where`.
- **P3** `scripts/build_section_order_priors.py:217` — single-page assumption on
  `list_receipt_sections()`; aborts once the corpus outgrows one Scan page (near
  the 1MB boundary at 5,593). Loop the cursor.
- **P3** `receipt_upload/receipt_upload/merchant_resolution/embedding_processor.py:526`
  — the "async" verifier actually runs synchronously on the upload critical path.
  Note it's the sync path or gate it like the LLM async hand-off.
