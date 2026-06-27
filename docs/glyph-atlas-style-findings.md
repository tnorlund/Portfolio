# Glyph atlas — intra-receipt style findings (M0/M1)

> Branch: `feat/receipt-glyph-rendering`. Builds on PR #994's letter/line font
> analysis. This is **review-realism only** — the atlas is DATA consumed by the
> glyph renderer; it never feeds or relaxes a training gate (see CHARTER.md).

## The question the charter told us to resolve first

> "Do receipts have multiple fonts within them? Almost certainly yes (header vs
> body vs totals vs bold)."

We resolved it empirically against real Vons receipts (dev table
`ReceiptsTable-dc5be22`) before designing the atlas, using #994's letter- and
line-level clustering **and by looking at the receipt images directly**.

## What the data actually shows

The "header / body / totals / bold = four fonts" framing is **not** what real
Vons receipts contain. On the densest Vons receipt (image `d38424eb…`, 1247
letters):

1. **The body is one monospace family.** Items, prices, addresses, the
   `Price / You Pay` column headers, and the `TAX` / `BALANCE` totals block are
   all the same face at the same size. Letter-level #994 clustering keeps the
   whole receipt as **one cluster** from `eps=0.78` down to `eps=0.32`; only at
   `eps=0.25` does it shatter into 3 noisy, non-semantic groups. **Totals are
   not a distinct font.**

2. **The only genuine in-body variation is a bold weight**, applied to whole
   lines — the `Member Savings -X.XX` promo lines and the category section
   headers (`GROCERY`, `PRODUCE`, `LIQUOR`). The letterforms are identical; the
   ink is heavier. #994's *letter* vectors are character-centered and miss this;
   #994's *line* clustering **does** separate those lines (the line signature
   carries ink/stroke aggregates) — e.g. line clusters `lc3`/`lc5` are exactly
   the `Member Savings` lines.

3. **A logo wordmark (`VONS`) sits at the top** — height `0.047` normalized vs a
   body median of `~0.0135` (≈3.5×). It is a large display mark, effectively a
   fixed image, not glyph-composable body text.

Visual confirmation (the receipt crops are in the review gallery): the `VONS`
mark is a heavy custom wordmark; the address block and every line item share one
clean thermal monospace; the `Member Savings` lines are visibly bolder.

## How the atlas is keyed (consequence)

`GlyphAtlas` is keyed by **weight tier**, not by header/body/totals:

- `body` — the dominant regular monospace (covers items, prices, **totals**,
  addresses, column headers).
- `bold` — the heavier weight, derived at the **line** level: a line whose
  median ink_ratio exceeds the receipt's body-ink baseline by ≥`1.18×` is bold.
  Reported in `variation_report()` so a reviewer sees the separation that
  justified it. Absent → noted, atlas is single-weight.
- The **logo** is captured separately as an image asset (`atlas.logo`), cropped
  from the tallest top line (≥`1.9×` body height); its glyphs are excluded from
  the per-char styles.

This stays generic: a merchant that genuinely prints a second *face* forms a
second #994 letter cluster, which the builder keeps as its own `clusterN` style;
a merchant that only varies weight (the common case) gets the regular/bold split
the builder derives at the line level. The atlas retains the **actual ink crops**
(ink-as-alpha RGBA, tight to content) so the renderer stamps real letterforms.

## Atlas aggregate (real Vons, 6 receipts)

`build_glyph_atlas_from_dynamo("ReceiptsTable-dc5be22", "Vons", max_receipts=6)`
→ `variation_report()`:

| field | value |
|---|---|
| receipts | 6 |
| #994 letter clusters (median) | **1** (single face — confirms the finding) |
| logo | present, text `VONS.` |
| chars covered | 76 |
| `body` style | 2054 glyph samples, 171 lines, 75 distinct chars, median ink 0.117 |
| `bold` style | 645 glyph samples, 64 lines, 60 distinct chars, median ink **0.180** |

The bold ink ratio (0.180) is ~1.5x the body baseline (0.117) — a clean,
well-separated weight tier, exactly the `Member Savings` / section-header lines.

### Crop quality guards (why the stamped glyphs are single letterforms)

The atlas must stamp ONE letterform per cell, so the extractor rejects merged
crops with three layered guards (a visual review of the first build caught
neighbor-bleed doubles and whole-word crops like `Main:` / `questions`):

1. **Tight crop, zero horizontal pad** — `_glyph_crop_bounds` crops to the OCR
   letter box and never expands sideways into the neighbor glyph.
2. **Box-size guards** — skip any letter whose box is wider than `2.4x` the
   receipt median *and* above an absolute `0.055` normalized ceiling (a receipt
   that mis-segments most letters into word boxes inflates its own median, so
   the absolute ceiling is the backstop), or taller than the logo cut.
3. **Glyph-aspect guard** — after extraction, reject any glyph wider than `1.7x`
   its height; removes multi-char / horizontal-streak crops the box guards miss.
   Char-agnostic (no punctuation exemption) so it leaves no merge hole: on real
   thermal receipts a tight-ink hyphen / equals crop measures only ~1.1-1.6
   aspect (the mark is thick), so it passes the guard on its own.

Representatives are then ordered cleanest-first by nearest-median **aspect** (a
bled crop is wider, so it sorts last) and height, so `glyph_for()` returns the
cleanest variant.

Ink polarity is decided from the crop's **border ring** (the background), not a
global minority-class count, so a dense or bold glyph that has more ink than
paper is not wrongly inverted.

## Rendering (M2) + TTF fallback (M3)

`glyph_renderer.render_receipt_glyphs(receipt, atlas, fallback=…)` stamps a
synthesized receipt with the atlas's real glyph crops:

- It reuses `receipt_renderer`'s coordinate helpers (`_to_pixel_box`,
  `_detect_coord_max`, `_iter_words`), so the **geometry contract is unchanged** —
  only glyph drawing is swapped (generic TTF → real-crop stamping).
- Per line it picks **body vs bold** (line ink / emphasis hints) and stamps the
  real **logo wordmark** on the genuine display-height `MERCHANT_NAME` line.
- Each glyph is rescaled by its own height-relative-to-style-median (clamped to
  `[0.5, 1.0]`, which removes per-instance jitter), bottom-aligned to the word
  baseline, and **width-fitted to its monospace cell** so glyphs never bleed into
  neighbours. A thermal-paper background + light speckle/blur complete the look.

What looking at the renders caught (and fixed): neighbour-glyph **slivers** that
stamped as stray strokes between letters (now trimmed at extraction, see above);
glyph **overlap** from missing cell width-fit; and the logo stamping over footer
lines (now gated on display height). After those, re-stamping a real Vons
receipt's own tokens is recognizable as the real receipt at a glance — same
letterforms, with the logo / body / bold variation reproduced.

`glyph_ttf_fallback.make_ttf_fallback(atlas)` covers characters the atlas lacks:
it picks the monospace TTF whose glyphs best match the merchant's real glyphs
(mean cosine over a small normalized ink vector) and renders missing chars from
it at atlas scale. On the real Vons atlas it selects Andale Mono / PT Mono over
serif Courier — a sensible thermal-ish match. This is a **pixel-embedding** match,
not a learned DINOv2/DeepFont one (those need weights unavailable here); a learned
embedder drops into the `score_font` hook without touching callers.

### Honest residual limits

- Letter *rhythm* is good at a glance but not pixel-perfect: the body font is
  treated as monospace (it nearly is), so proportional nuance is approximated.
- A few low-sample glyphs (e.g. a bold `a`) render faint; more receipts per
  atlas would supply cleaner representatives.
- The fallback is visual-similarity, not a learned font embedding (scoped above).
