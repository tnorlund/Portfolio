# Synthetic receipt realism roadmap

> Source: 25 opus analyses (8 merchants x synthesis-operations) vs the real receipts, synthesized by codex.
> Mean realism today: **2.6/10** (range 1-4). Goal: renders convincing enough for a frontend demo.

## Verdict (codex)
Receipts fail FIRST as typography/layout renders, THEN as unconstrained merchant content. **Fix the renderer
grid before polishing details.** For a demo, uniform thermal type + aligned columns + coherent totals are what
sell realism; exact logo geometry / `WAS:` wording / phone provenance matter far less. Texture goes LAST
(grain over bad typography just looks noisy).

## Root cause (the single thing to do first)
`receipt_renderer.py:_fit_font` sizes EVERY token to its OWN bbox independently. That one choice produces the
most visible tells across all 25: tiny totals, prices floating at different x, mixed glyph weights, broken
baselines, and inserted lines that look pasted. Fix: make `render_receipt` render LINE GROUPS on a shared grid
(merchant-profiled body size, common baseline, fixed pitch, shared right-aligned amount columns) instead of
per-token fitting.

## The 5 highest-leverage changes (ranked)
1. **Typography (25/25)** — one merchant-profiled monospace thermal size/weight per line/section; stop
   per-word shrinking. Code: `rendering/receipt_renderer.py` (`render_receipt`,`_fit_font`,`_draw_text`),
   `rendering/font_profile.py`, `scripts/render_synthetic_receipts.py` (`_render_cached_hybrid`).
2. **Layout (23/25)** — fixed grid: stable baselines, constant line pitch, ONE right-aligned money/decimal
   column, separate tax-flag lanes; reflow rows after add/remove. Code: `merchant_synthesis.py` (`_line_step`,
   `_label_x_p50`, item/totals placement), `receipt_renderer.py` line grouping.
3. **Content (25/25)** — merchant grammar validators/templates for item rows, headers, payment, totals, field
   replacements; reject catalog dumps, impossible addresses, orphan tokens, stale item counts, unreconciled
   tax/tender math. Code: `merchant_synthesis.py` (`generate_merchant_synthesis_candidates`, totals/tax,
   `_reconcile_item_count`), `synthesis_reconcile.py`, `synthesis_text_clean.py`.
4. **Graphics (19/25)** — real logos/barcodes/QR at merchant-correct positions (Costco total bar, Amazon
   savings box, separators). Code: `scripts/render_synthetic_receipts.py` (`_overlay_cached_logo`,
   `_overlay_qr_and_barcode`, `_draw_barcode`), placement in `merchant_synthesis.py`.
5. **Paper-texture (25/25)** — global scan/thermal model AFTER rendering: paper tint, skew/curl, edge shadow,
   banding, grain, ink bleed, density variation, light blur; blend inserted glyphs into the SAME degradation
   pass as untouched text. Code: `scripts/render_synthetic_receipts.py` (`_composite_paper_texture`).

## Ordered execution
#1 grid typography -> #2 column layout -> #3 content reconciliation -> #4 graphics -> #5 texture.

## Status
- Content cleaning v1 (`synthesis_text_clean.py`) already fixes source-OCR noise in GT tokens (a slice of #3).
- #4 renderer (de-fusion/clip/QR/logo) from the re-OCR spike is merged onto this branch.
- NEXT: extensive research per item (1-5), then codex turns the research into a concrete implementation plan.
