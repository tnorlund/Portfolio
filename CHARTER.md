# Branch charter — `feat/receipt-glyph-rendering`

Branches off `integration/synthesis-next` (has #994 font analysis, font-render's
renderer + font profiles, merchant-intel's research/taxonomy, the tax gate).
Read `CONTEXT.md` first for the shared session context + cadence.

## Goal
Render synthesized receipts using each receipt's **actual letterforms** — a
per-merchant **glyph atlas** built from the real letter crops — **style-aware**
so intra-receipt font variation is reproduced, with an **embedding-matched
thermal/POS TTF fallback** for characters the atlas lacks. Build on #994's
`receipt_upload/receipt_upload/font_letter_analysis.py` + `font_analysis.py`
and `rendering/` (font-render). This is **review-realism only** — the structured
receipt (tokens+boxes+labels) stays the only training artifact; image-training is
OUT (no CoreML v3). Realistic renders make human review sharper.

## Resolve FIRST (empirically): do receipts have multiple fonts within them?
Almost certainly yes (header vs body vs totals vs bold; #994 reports
`style_label_counts` with multiple styles per merchant). Confirm with data:
per receipt, how many distinct letter styles/regions, with examples. The atlas
MUST be keyed by style/region, not one font per merchant.

## Milestones (review-first cadence; see CONTEXT.md)
- **M1 — Style-aware glyph atlas.** From a merchant's real receipts, cluster
  labeled letter crops by style/region (header / body / totals / bold) using
  #994's letter analysis + its style labels. Build `{style -> {char -> glyph
  image}}`. Report the intra-receipt style variation you find.
- **M2 — Glyph-stamping renderer.** Render a synthesized receipt by compositing
  the real glyph crops, picking the atlas style by each line's role (header line
  -> header glyphs, TOTAL -> totals glyphs, item -> body glyphs). Add a
  thermal-paper background + light noise for photo-realism. Emit real-vs-synthetic
  comparisons. Extends `rendering/`; do not change the existing geometry renderer's
  contract.
- **M3 — Embedding-matched TTF fallback.** For chars absent from the atlas, pick
  the nearest thermal/POS TTF by letter-crop embedding similarity (reuse #994's
  Chroma letter embeddings; optionally a HF vision embedder — DINOv2 or CLIP —
  over glyph crops; DeepFont is the reference but is NOT trained on thermal fonts,
  so use HF models as the *embedder*, not a turnkey classifier). Render missing
  glyphs from the matched TTF at atlas scale.
- **M4 — Realism evaluation + review gallery.** Render a sample across operations
  (add-item, remove, field-replace) and 2-3 merchants; iterate to convincing
  realism; produce a side-by-side gallery.

## Review is DUAL — code AND images (mandatory)
- **Code:** codex-review each milestone diff before committing (the cadence).
- **Images:** this output is visual — a visual review is REQUIRED.
  1. After each render milestone, **Read the rendered PNGs yourself** (you are
     multimodal — actually look at them) and critique realism vs the real receipt
     image; iterate until convincing. Do NOT declare "realistic" without looking.
  2. Save a sample gallery and surface it for human review (`SendUserFile` the
     comparison PNGs). If your codex build accepts image input, attach a sample
     render for an independent visual critique; otherwise state that the human
     reviews the gallery and codex reviewed the code.

## Boundaries (do NOT touch)
- Synthesis gates / tax config / `data_loader.py` / `merchant_research/`. You
  consume their outputs; you don't change them.
- Own: a NEW glyph-rendering module under `rendering/` (atlas + glyph renderer +
  TTF fallback + realism eval) + a render CLI/extension.

## Acceptance
A synthesized Vons add-item receipt rendered with the glyph atlas is, at a glance,
hard to tell from a real Vons receipt (same letterforms), and intra-receipt style
variation is reproduced (header differs from body as on the real). Code green;
codex-reviewed; images visually reviewed (gallery surfaced).
