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

### Label propagation — REVISED to character-level alignment (prior art: Microsoft Genalog)
The spike used bbox-IoU matching (99% spatial match, but dropped SUBTOTAL/TAX on low-IoU key/value
lines). Research says that's the wrong primary algorithm. **Microsoft Genalog** implements this exact
pipeline (render → real OCR → align → `propagate_label_to_ocr`) and propagates by **character-level
sequence alignment, not boxes** — and the consensus rationale is decisive: *we control the synthetic
source text*, so aligning the OCR string to the known GT string is strictly more reliable than IoU.
Revised algorithm:
1. Build a reading-order GT string carrying a **label per character span**, and a reading-order OCR string.
2. Align OCR↔GT with **Needleman-Wunsch** (Biopython `pairwise2`) — use **RETAS** anchor-based recursion
   for long receipts (NW is O(n²)). `dinglehopper`/`ocreval` are tested alignment cores to reuse.
3. **Project labels by character span**: each OCR token gets the label of the GT chars it aligns to; on
   split, copy the GT label to ALL overlapping OCR fragments; on merge, inherit the overlapped GT label.
4. Boxes come from Apple Vision directly (no box transform needed for labels). Use bbox-overlap **only as a
   tiebreaker** when two adjacent fields collide in the alignment.
Copy Genalog's `ner_label.py` projection logic (it's in maintenance mode + Apple Vision isn't a built-in
backend, so wrap our own OCR call around its algorithm).

## Realism work now has a real objective (not eyeballing)
Our re-OCR loop IS **RoundTripOCR** (arXiv:2412.15248): it harvests Apple Vision's *real* error distribution
by construction — more faithful than any hand-injected confusion matrix. The degradation stack only matters
because it shapes that distribution. **Close the calibration loop with two measured targets from our REAL
receipt corpus:** (1) Apple Vision **CER** on real receipts, and (2) the **fraction of errors at
edit-distance ≤2** (prior art: ~80%). Tune degradation *intensity* until re-OCR'd synthetic receipts match
both. That is the principled stopping criterion, replacing "looks real."
- **Font:** thermal printers use a small shared set (Epson Font A/B), NOT a unique font per merchant.
  Per-merchant config is a compact `{section → font,size,weight,pitch}` profile, semi-auto-fit by
  template-matching real crops. Monospace fixes the word-spacing/touching-words tells for free.
- **Header/logo is a bitmap**, not a font (atlas is correct THERE only).
- **Degradation libraries:** `augraphy` (ICDAR'23) ink/paper/post passes — use SPATIAL-level augs so boxes
  follow distortion; curated thermal subset = LowInkLines + DotMatrix + Faxify + Letterpress + Brightness/
  Gamma + Folding. `DocCreator` ink-degradation + 3D-mesh for phone-capture geometry. NOTE: no turnkey
  thermal augmenter exists — compose these and calibrate against our reals.

## Opus's role in this pipeline (it FLIPS)
Stop reviewing renders for "garble" — OCR noise is now a FEATURE (the whole point), so garble-review fights
the design. Opus becomes the **sparse adversarial auditor of the two things deterministic checks can't judge**:
1. **Label-propagation semantics** — sample N re-OCR'd examples, render the propagated labels onto the image,
   ask opus "is each labeled token actually that field; which GT labels failed to land?" (catches the
   SUBTOTAL/TAX-type silent drops the verifier misses).
2. **Degradation realism** — degraded-hybrid vs a real photo: "believable capture, too clean, or too noisy?"
   drives calibration. Same adversarial-vs-originals method that killed the atlas, re-aimed at hybrid.
Deterministic verifier = cheap per-example gate (repurposed to score PROPAGATION success, not text cleanliness);
opus = expensive audit on sampled examples. Not per-example.

## Training strategy (prior art consensus)
**Pretrain on synthetic → fine-tune on real** (beat naive mixing in 78% of configs; Donut/SynthDoG precedent).
Add synthetic only until a held-out **REAL** validation F1 stops improving — synthetic saturates then HURTS
(arXiv:2510.01631). Expect modest gains on already-strong receipt fields (SROIE/CORD ~96-98 F1, near-saturated)
and the real payoff on **rare fields + layout robustness** (RIDGE CVPR'25: +6.6 F1 FUNSD vs +0.2 SROIE).

## Honest risks of the re-OCR divergence
Render-then-re-OCR is UNCOMMON in published KIE (most use perfect known boxes, or go OCR-free like Donut);
Genalog is the standout precedent. The divergence is **defensible** — it fixes the two best-documented
synthetic-KIE failures (idealized boxes, over-clean renders / label leakage) by routing labels through the
same noisy OCR production uses. Risks: (a) thermal degradation realism has thin prior art → harvested errors
are only as good as render realism, so calibrate hard against real CER; (b) cross-engine shift — this only
works re-OCR'ing with **Apple Vision specifically**; (c) saturated receipt fields cap visible F1 gains →
measure on rare/scarce fields.

## Next steps
1. **DONE — spike**: `re_ocr_example.py` proved render→Apple Vision→propagation (Amazon Fresh: 99% spatial,
   30/35 labels carried, OCR noise preserved with labels). Used IoU (superseded below).
2. **Rebuild propagation on char-level alignment** (Genalog method) — NW + char-span projection, boxes from
   OCR, IoU only as tiebreaker. Re-run on the lost SUBTOTAL/TAX cases to confirm recovery.
3. **Repurpose the verifier** for re-OCR data: score label-propagation success, not text cleanliness.
4. **Add the opus propagation-audit** sampler (above).
5. **Calibration loop**: measure Apple Vision CER + edit-dist-≤2 fraction on real receipts; tune augraphy/
   DocCreator degradation to match.
6. Composite real QR/barcode into hybrid (the one defect shared by both renderers).
7. Wire re-OCR into the pipeline behind a flag; pretrain→fine-tune; A/B LayoutLM F1 vs clean-token data on a
   REAL held-out set.

## Prior-art references
Genalog (github.com/microsoft/genalog — re-OCR + label propagation, char alignment) · SynthDoG/Donut
(arXiv:2111.15664) · augraphy (arXiv:2208.14558) · DocCreator (IJDAR'17) · RoundTripOCR (arXiv:2412.15248) ·
ScrambledText (arXiv:2409.19735, P(C)=1−CER dial) · Guan&Greene post-OCR (arXiv:2408.02253, 5:1:1 sub:ins:del)
· RIDGE (arXiv:2504.10659) · synthetic-saturation (arXiv:2510.01631) · SROIE/CORD schemas.
