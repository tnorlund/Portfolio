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
