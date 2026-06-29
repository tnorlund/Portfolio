# Codex review task — re-OCR synthetic-receipt training-data pathway

You are an independent senior reviewer. Your job is a DIRECTION CHECK: are we heading the right way, is the
approach sound, what are we missing? Be skeptical and specific. Read the actual code and artifacts below
before answering — do not review from this summary alone.

## Read these (in this worktree, ~/Portfolio_reocr)
- `synthesis_loop/RENDERER_PATHWAY.md` — the full decision + pathway + plan + prior-art references.
- `synthesis_loop/re_ocr_align.py` — the label-propagation implementation (per-line char-alignment + spatial fallback).
- `synthesis_loop/reocr_score.py` — the re-OCR propagation scorer.
- `synthesis_loop/*.reocr-align.json` — sample re-OCR'd training examples produced by the pipeline.

## The context / decisions to scrutinize
1. **We dropped glyph-atlas rendering.** An adversarial opus panel (3 merchants × 3 lenses) scored it 2/10 vs
   hybrid 6-7/10: the atlas composites real character crops and systematically corrupts the decimal in EVERY
   price ($15.99->$15#99) plus floating/garbled glyphs. Hybrid (real font + paper texture + real logo) is now
   the base renderer.
2. **Chosen direction: the re-OCR pathway.** synthesize structure (tokens+bboxes+GT labels) -> render hybrid
   -> re-OCR the render with Apple Vision (the SAME engine production uses; runs on this Mac) -> propagate the
   GT labels onto the OCR tokens -> train on the OCR tokens. Rationale: pixels actually train the model, tokens
   carry realistic OCR noise, and pixels/labels can never diverge. Prior art: Microsoft Genalog (same pattern),
   RoundTripOCR (distribution matching by construction).
3. **Label propagation algorithm.** We first tried global Needleman-Wunsch char-alignment; it DRIFTED on
   ~800-char receipts (scrambled labels). We switched to PER-LINE anchoring: spatially match each OCR line to
   its GT line (we control the boxes, so spatial line-matching is reliable), then char-align WITHIN the line.
   Result on 3 merchants: propagation_score 0.94 / 0.97 / 0.84, **0 mislabels**. The only label losses were
   SUBTOTAL/GRAND_TOTAL, which a diagnostic showed are OCR-recall misses (Apple Vision didn't detect them in
   the render) — i.e. a RENDER problem, not a propagation problem.
4. **We reordered the pipeline to put an IMAGE-REVIEW GATE before labels** — because a render OCR can't read
   silently corrupts labels downstream (the SUBTOTAL case).
5. **Scoring repurposed.** The clean-data verifier penalizes OCR noise; reocr_score.py instead measures label
   propagation (type_recall, region_label_recall, mislabel_rate).
6. **Calibration plan:** tune render degradation (augraphy/DocCreator) until re-OCR'd synthetic matches real
   receipts' Apple Vision CER and the ~80% edit-distance-≤2 error fraction.
7. **Training plan:** pretrain on synthetic -> fine-tune on real; stop when held-out REAL F1 plateaus.

## Answer these, concretely (cite the code where relevant)
1. Is the overall DIRECTION right (drop atlas, hybrid base, re-OCR pathway)? If not, what would you do instead?
2. Is the per-line char-alignment + spatial-fallback algorithm sound? What are its failure modes (e.g. multi-
   line fields, lines OCR splits/merges across, rotated/curved receipts)? Read re_ocr_align.py and be specific.
3. Is `reocr_score.py` measuring the right thing? What would you add (precision? a CER/alignment-coverage term?)?
4. Is "image-review gate before labels" the right ordering, or is there a better place to catch OCR-recall misses?
5. The calibration plan (match CER + edit-dist-≤2) — sound? Anything better for matching Apple Vision's noise?
6. Biggest risks / blind spots in this whole direction that a senior engineer would push back on?
7. The single highest-value next step before wiring this into the training pipeline + A/B-ing LayoutLM F1.

Output a structured review: a one-line VERDICT (right direction / course-correct / wrong), then numbered
answers to 1-7, then a prioritized action list. Be direct about disagreements.

## ROUND 2 UPDATE — we acted on your last review; re-verify it landed
Since the first review (which ruled "course-correct"), we implemented 3 of your prioritized actions on this
branch — READ these files, they are new/changed:
- **#2 `synthesis_loop/reocr_gate.py`** — deterministic quarantine gate: pre-OCR (arithmetic reconciles +
  no empty value fields) and post-OCR (every REQUIRED field has raw OCR coverage, else quarantine). Replaces
  opus image-review as the recall gate.
- **#3 `synthesis_loop/reocr_score.py`** — hardened: added label_precision, false_positive_rate (labels on
  GT-'O' regions), value_cer, and degenerate-label rejection so a punctuation token ('='->TAX) no longer
  counts as correct.
- **#4 `scripts/render_synthetic_receipts.py`** — root-cause renderer fix: `_render_cached_hybrid` capped
  fonts at 5/10px so thin synthesized totals/payment bboxes shrank to ~5px (faint micro-font, decimals as
  dashes). Floored at 9/14px.

NEW measured results on the SAME 3 merchants after #4 (re-rendered, re-OCR'd):
- Gate: ALL THREE now PASS (0 uncovered required fields; previously all quarantined).
- Hardened scorer: Amazon f1 1.0 / Costco 0.966 / Sprouts 0.967; value_cer 0.0 (was up to 0.2);
  0 degenerate labels; 0 mislabels; all entity types recovered.

Re-verify, specifically and skeptically:
A. Did the course-correction actually resolve the problems you raised, or is the font-floor a band-aid that
   trades one failure for another (e.g. 9px tokens overflowing their boxes and colliding -> new OCR errors,
   merged tokens, or bbox drift)? Inspect re_ocr_align.py / reocr_gate.py / reocr_score.py logic, not just
   the headline numbers.
B. Is the hardened scorer now measuring the right thing, or is it still gameable?
C. Is the deterministic gate's "required fields + coverage" definition correct and complete?
D. With these fixes in, what is the SINGLE highest-value next step before a real LayoutLM A/B? Restate the
   VERDICT (right direction / course-correct / wrong) given the new state.

## ROUND 3 UPDATE — we fixed everything you flagged AND committed the evidence this time
Your last review proved: (a) committed artifacts were STALE (=->B-TAX), (b) the scorer skipped degenerate
labels so garbage left f1=1.0, (c) the font floor was a band-aid causing word-fusion/clipping. We addressed
all three and COMMITTED the evidence (read these on feat/synthesis-reocr-spike):
- **`synthesis_loop/artifacts/*.gate.json` + `*.report.txt` + `*.reocr-align.json`** — FRESH, committed
  artifacts regenerated from the current renderer. Verify they match the numbers below (no more /tmp-only claims).
- **`synthesis_loop/reocr_score.py`** — degenerate/punctuation labels now COUNT AS FAILURES (fp), and value_cer
  is over ALL value tokens, not just covered ones.
- **`synthesis_loop/reocr_gate.py`** — mandatory fields (MERCHANT_NAME/GRAND_TOTAL/DATE) must EXIST in GT.
- **`receipt_agent/.../rendering/receipt_renderer.py` + `scripts/render_synthetic_receipts.py`** (commit
  a55aaa6d1) — per-token width squeeze (kills word-fusion + margin clipping), QR/barcode drawn in blank bands,
  darkened logo.
Committed results: amazon f1 1.0; costco 0.984 (up from 0.966); sprouts 0.954 (DOWN from 0.967 — honest: the
de-fusion exposed a punctuation-only '#:' PAYMENT_METHOD token in the GT that the hardened scorer now correctly
flags as degenerate; the old fused render masked it). OCR-word vs GT-token counts now near-1:1 (sprouts 106/105,
was 69/105). Gate passes all 3.
Re-verify SKEPTICALLY: E. Do the committed artifacts actually match these numbers? F. Is the '#:' degenerate
token a SYNTHESIS bug we should fix (drop punctuation-only supervised tokens) rather than a scorer artifact?
G. Is anything still gameable / still a band-aid? H. Are we now ready for the batch harness + real-F1 A/B, or
what is the ONE remaining blocker? Restate the VERDICT.
