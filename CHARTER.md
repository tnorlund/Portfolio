# Branch charter — `feat/receipt-font-render`

> One of three parallel branches off `integration/synthesis-next` (= PR #1003
> tax-config work + PR #994 font analysis, merged). You are a fresh session.

## Goal
Use PR #994's font analysis to (a) drive synthesized-receipt **spacing /
typography** so layouts match each merchant's real receipts, and (b) **render
synthesized receipts to PNG images** — for visual QA now, shaped so the same
output can feed LayoutLMv3-style image features later (decision: both).

## What's already here (from #994, on the integration base)
`receipt_upload/receipt_upload/font_analysis.py` and `font_letter_analysis.py`.
Public surface includes `ReceiptFontAnalysis`, `FontCluster`,
`FontSimilarityMatch`, `analyze_dynamo_image_fonts`. Import from inside
`receipt_upload/`:  `from receipt_upload import font_analysis`. Read
`docs/receipt-font-analysis-pilot.md` and `scripts/run_letter_font_embedding_pilot.py`
for how the pilots use it.

## Organizing principle (do not violate)
Deterministic safety gates stay deterministic. Font metrics feed the *geometry
parameters* of synthesis (row pitch, char width, price-column x) and the
*renderer*; they must not weaken the structure-similarity / arithmetic gates. A
synthesized receipt whose typography you render must still pass the same
loader/structure gates.

## You own (file boundaries)
- NEW renderer module, e.g. `receipt_agent/.../label_evaluator/rendering/`
  (synthesized receipt dict {lines/words/bboxes} + merchant font profile → PNG).
- A per-merchant **font profile** extractor (family/size/char-width/line-spacing)
  built on #994's analyzer.
- The font-metric → geometry hooks: ONLY the spacing-param helpers in
  `merchant_synthesis.py` (row step, char width, price-column alignment). Keep
  edits additive and on those helpers only.

## Do NOT touch (other branches / sacred gates)
- Tax-config gate logic (`_taxable_edit_rate_for_receipt`,
  `_consistent_validated_edit_rate`, `_apply_taxable_delta`, etc.) — owned by
  `feat/merchant-intelligence-agents`.
- Loader quality/arithmetic gates in `receipt_layoutlm/.../data_loader.py`.
- The orchestration entrypoint (→ `feat/synthesis-orchestration`).

## First milestones
1. **Font profile per merchant** from real receipts via #994's
   `analyze_dynamo_image_fonts` (cluster → family/size/char-width/line-pitch).
   Export receipts via the ReceiptPlace path (see Run/verify).
2. **Renderer**: synthesized-receipt dict → PNG at the merchant's typography.
   Produce a side-by-side real-vs-synthetic visual diff for QA.
3. **Feed metrics into geometry**: wire font char-width / line-pitch into the
   synthesis spacing helpers; confirm structure-similarity gate STILL passes
   (run the local pipeline; the 5 Vons taxable adds must still be high-fidelity).
4. **Training-ready shape**: document + stub the contract for emitting images as
   LayoutLMv3 visual input (target size, normalization, token-box alignment) —
   don't wire training yet, just make the output convertible.

## Run / verify
    # Export receipts via ReceiptPlace (ReceiptMetadata is DEPRECATED):
    #   get_receipt_places_by_merchant(name) -> image ids -> export_image(table,id,out)
    python3.12 scripts/verify_synthetic_replay.py local-pipeline \
      --receipt-dir <dir> --artifact-output-dir .tmp/art --bundle-output .tmp/bundle.json \
      --min-grounded-candidate-share 0.0 --max-candidates 80 \
      --max-per-merchant 100 --max-per-merchant-operation 60
    # Font module tests (keep green) + synthesis regression:
    (cd receipt_upload && python3.12 -m pytest tests/test_font_analysis.py \
       tests/test_font_letter_analysis.py -q)
    python3.12 -m pytest receipt_agent/tests/test_merchant_synthesis.py -q
Pillow (PIL) is available (font_analysis already uses it). Review with
`git diff | codex exec --skip-git-repo-check "<prompt>"`.

## Done when
Each merchant has a font profile; the renderer produces a recognizable PNG of a
synthesized receipt that visually matches the real one; font metrics drive
spacing without breaking the structure gate (Vons taxable adds still
high-fidelity); the training-image contract is documented. Tests green;
codex-reviewed.
