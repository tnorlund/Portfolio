# Synthetic receipt renderer — decision & re-OCR pathway

> Status: DECIDED 2026-06-29 on `feat/synthesis-hill-climb`. Supersedes the glyph-atlas approach.

## TL;DR
- **Glyph-atlas rendering is dropped** as the text source (kept only for the logo bitmap).
- **Hybrid (real font + paper texture + real logo) is the base renderer.**
- The strategic direction is the **re-OCR pathway**: render → re-OCR with Apple Vision → the OCR'd
  tokens (with propagated labels) become the training data.

## Why atlas died (adversarial opus eval, 3 merchants × 3 lenses, vs the real photos)
Unanimous **2/10** for glyph-atlas vs **6–7/10** hybrid. The atlas composites individual real character
crops, which **systematically corrupts the intended tokens**:
- The **decimal point is wrong in EVERY price, every merchant**: striped dagger (Amazon `$1ƚ49`), `)`
  (Costco `12).99`, `0).00`), dropped-underscore (Sprouts `7_99`). No monetary amount renders correctly.
- Reused single-glyph tiles float as superscripts on unrelated lines (Costco `5`: `⁵700`, `18:4⁵`).
- Letter/punctuation substitution (`SIUBTOTAL`, `Westlabke Villalge`, `BALAwCE`, hyphen→underscore).

Decisive criterion: the model trains on **JSON tokens**, and the image is only a human/opus **sanity check**.
A renderer that garbles the tokens it's meant to let you verify is worse than useless. `body_glyph_source=
"numeric"` is a **band-aid** (fixes digits/decimals only) and explicitly NOT worth a second renderer.

## The re-OCR pathway (chosen direction)
```
synthesize structure        (tokens + bboxes + GROUND-TRUTH labels)   [exists: the bundle]
  -> render hybrid (+ light, spatially-coherent thermal degradation)  [exists: thermal_variants hybrid]
  -> re-OCR the rendered PNG with Apple Vision                         [proven on the mini]
  -> propagate GT labels onto the re-OCR'd tokens (the hard part)      [TO BUILD]
  -> emit {re-OCR tokens, re-OCR bboxes, propagated BIO labels}        [training example]
  -> verify (verify_candidates.py)                                     [exists]
```
Three payoffs: (1) pixels now actually train the model; (2) displayed pixels and training tokens can never
diverge again; (3) training data carries **realistic OCR-error patterns** instead of clean synthetic tokens.

### OCR engine = Apple Vision (production parity, runs on the mini)
`receipt_upload.ocr.apple_vision_ocr([png])` shells `OCRSwift.swift` (VNRecognizeText), returns
production-format `Line`/`Word` with normalized **[0,1], origin bottom-left** bboxes. Same engine as prod →
the synthetic OCR noise matches real-receipt OCR noise. Verified: re-OCR of a hybrid render works on the mini.

### Label propagation (the one hard problem TO BUILD)
Re-OCR yields new tokens/bboxes that don't line up 1:1 with the synthesized GT (words split/merge). Plan:
1. Put GT tokens and OCR tokens in a common pixel frame:
   - GT bbox: 0–1000, y-high-is-top → pixels via render transform (coord_max=1000, margin=10, inner_w/h).
   - OCR bbox: [0,1] bottom-left → pixels (×W, ×H, flip y).
2. Match each OCR token to GT token(s) by **bbox IoU + text similarity** (greedy, IoU-gated).
3. Assign labels BIO-aware: first OCR token of a GT entity run → `B-`, continuation → `I-`; a GT word that
   OCR split into N tokens keeps the entity (B on first, I on rest); OCR merges → take the dominant GT label.
4. Unmatched OCR tokens → `O`. Flag low-confidence matches for the verifier.

## Realism work now has a real objective (not eyeballing)
The font-profile + degradation stack only matters because it shapes the OCR-error distribution. Calibrate it:
**render → degrade → Apple Vision OCR → compare error types/rates to real receipts → tune degradation.**
- Font: thermal printers use a small shared set (Epson Font A/B), NOT a unique font per merchant. Per-merchant
  config is a compact `{section → font,size,weight,pitch}` profile, semi-auto-fit by template-matching real
  crops. Monospace fixes the word-spacing/touching-words tells for free.
- Header/logo is a **bitmap**, not a font (atlas is correct THERE only).
- Degradation: keep it LIGHT + spatially coherent (global fade gradient, paper mottle, slight skew/curl).
  Consider `augraphy` for the physical passes. Heavy degradation has no model benefit until calibrated to OCR.

## Next steps
1. **Spike**: build `re_ocr_example.py` — render hybrid for one candidate → `apple_vision_ocr` → label
   propagation → emit a re-OCR'd training example → verify + eyeball. Prove the pathway on one receipt.
2. Composite real QR/barcode (keyed to token data) into hybrid — the one defect shared by both renderers.
3. Wire re-OCR into the synthesis pipeline behind a flag; A/B the LayoutLM F1 (clean-token vs re-OCR'd data).
4. Only then invest in the degradation calibration loop.
