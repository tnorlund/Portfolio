# HANDOFF — per-merchant font discovery (parallelize across merchants)

**Goal:** extend the data-driven font pipeline (proven on Costco) to every other
merchant — one branch per merchant — so each renders in its real (or closest-free)
typeface. This doc is the full context for an agent doing ONE merchant.

## TL;DR of the Costco result (the template to follow)
Costco's real receipt font is **bitMatrix-C2** (body) + **bitMatrix-C2-heavy**
(emphasis) — confirmed both by our data pipeline AND by https://www.receiptfont.com
(it sells these exact fonts; their per-merchant pages NAME the font). We did NOT buy
it: we extracted clean glyph atlases from the **font chart preview PNGs** on
receiptfont.com and render in them. Costco now renders in its actual font (body
bitMatrix-C2, TOTALS bitMatrix-C2-heavy), 21 renderer tests green.

Two valid outcomes per merchant, in priority order:
1. **Chart available** (a bitMatrix-style font chart for the merchant from
   receiptfont.com, downloaded by the user to ~/Downloads): extract a glyph atlas
   with `extract_bitmatrix.py` and render in the real font (best).
2. **No chart**: identify the closest FREE font with the prototype/font_match
   tools, apply measured condense + per-zone weight. (~0.80 shape-match ceiling.)

## reference: https://www.receiptfont.com  (cite this)
- Sells per-merchant thermal fonts; product/template pages NAME each merchant's
  font (e.g. Costco -> bitMatrix-C2 / -heavy). Use it to IDENTIFY the font name.
- We do NOT pay. If the user provides the merchant's chart PNG (the glyph map
  preview), we extract our own atlas from it. Otherwise use the free-font path.
- "Most Common Fonts for Receipt Printing Across Different Brands" lists merchant
  fonts — a good starting research page.

## The pipeline (all in synthesis_loop/, numpy+PIL only, no scipy/cv2)
- `glyph_segment.py` — Sauvola adaptive binarization + projection segmentation
  (constrained to the OCR char count) + `auto_polarity` (reverse-video invert).
- `font_diff.py <merchant_dir> [font]` — per-letter aspect/ink-weight bias of a
  font vs the real receipt. ">1 aspect = our glyph too wide -> condense ~1/aspect".
- `section_cluster.py <merchant_dir> [k]` — cluster VISUAL lines (not OCR line_id)
  by size x weight -> typographic zones (logo / emphasis / body / footer).
- `glyph_prototype.py` / `glyph_prototype_dynamo.py <Merchant> <out> [N]` — average
  every real glyph per char (across N receipts via ReceiptLetter+S3) -> canonical
  prototypes per zone; match against the font library. Reverse-video + emphasis
  sub-clustering + emboldened candidates built in.
- `font_match.py <out_dir> [worst_n]` — rank ~23 fonts (in /tmp/fonts_lib + system)
  incl. bold variants against cached prototypes; report WORST chars + WHY (localized
  real-vs-font ink) + per-char best font. Off-the-shelf caps ~0.80.
- `extract_bitmatrix.py <chart.png> <out> <name>` — parse a 17-col ASCII font chart
  (`!`..`~`): black-mask glyphs, strip cell borders, per-glyph baseline offsets ->
  `<name>.glyphs.npz` (kept LOCAL, gitignored; paid-font derived).
- `bitmap_font_demo.py` / `proto_font_demo.py` — render a sample in an atlas.
- Font library: `/tmp/fonts_lib` (17 OFL monospace fonts downloaded from Google
  Fonts; see the curl list in the conversation). Re-download if missing.

## Renderer integration (already built, on feat/grid-discipline)
- `receipt_agent/.../rendering/bitmap_font.py` — `BitmapFont(atlas_npz)`: glyph
  atlas font with condensed monospace advance (derived from glyph widths) + per-
  glyph baseline offsets.
- `RenderConfig` gains: `condense` (per-glyph H-compress), `stroke` (double-strike),
  `section_scale`/`section_font` (per-zone size/font), `bitmap_font` ({regular,heavy}).
- `scripts/render_synthetic_receipts.py::merchant_typography(merchant)` -> the
  per-merchant config dict. **This is where you register a merchant.** Costco entry:
  `{"bitmap_font": {"regular": <C2 atlas>, "heavy": <C2-heavy atlas>}}`.
  Free-font entry shape: `{"font_path": <ttf>, "condense": 0.88, "stroke": 0}`.
- `section_scale_for_merchant(merchant)` -> header-size map (measured: Amazon 0.78,
  Target/Smiths/Gelsons 0.80, Costco/Vons/Sprouts uniform, default 0.80).
- Render: `synthesis_loop/render_matrix.py <bundle.json> <receipt_dir> <Merchant> <out>`
  (renders one hybrid per op; reads merchant_typography automatically).

## Per-merchant playbook (do this on your branch)
1. `section_cluster.py /tmp/gridfix/<m>` — see the merchant's zones (size/weight).
2. `glyph_prototype_dynamo.py "<Merchant>" /tmp/gridfix/<m>` — body+emphasis
   prototypes across all its receipts; note the font_match ranking.
3. `font_match.py /tmp/gridfix/<m>` — best free font + per-char WHY + condense hint.
4. `font_diff.py /tmp/gridfix/<m> <best_font.ttf>` — measured aspect/ink -> condense.
5. Research receiptfont.com to NAME the real font (document it).
6a. If the user supplied a chart PNG: `extract_bitmatrix.py <chart> /tmp/bitmatrix <name>`
    then register `{"bitmap_font": {...}}` in `merchant_typography`.
6b. Else register `{"font_path": <best free ttf>, "condense": <1/aspect>, "stroke": 0}`.
7. `render_matrix.py` the merchant; crop body vs its `original.jpg`; iterate the
   condense/advance until the letterforms + width match.
8. Commit on your branch `font/<merchant>`; keep `.glyphs.npz` OUT of git.

## Env
```
W=~/Portfolio_grid_discipline   # (mini: sync this branch first)
export PYTHONPATH="$W/receipt_agent:$W/receipt_dynamo:$W/receipt_upload"
export DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 PORTFOLIO_ENV=dev
export FONT_LIB=/tmp/fonts_lib BITMATRIX_DIR=/tmp/bitmatrix
PY=~/Portfolio/.venv/bin/python   # (mini: confirm its venv/python)
```
Render cache (`/tmp/render_cache`, atlas/profile) makes re-renders ~instant after
the first build per merchant.

## Merchants (each its own branch font/<m>)
amazon_fresh (Amazon Fresh) · target (Target) · vons (Vons) · smiths (Smith's) ·
sprouts_farmers_market (Sprouts Farmers Market) · gelsons_westlake_village (Gelson's).
Per-merchant data dirs already exist under /tmp/gridfix/<m> (bundle.json, receipt_dir/,
original.jpg).

## Key findings from the originating conversation
- Realism arc: layout+font+per-section+content fixes took the Opus realism re-score
  2.71 -> 5.08 (+87%); content + the photographic/paper layer are the remaining gaps.
- Font: off-the-shelf monospace caps ~0.80 shape-match vs real thermal print
  (degradation + unique printer face). The lever is **condense** ("font too wide"
  is the dominant per-char tell) + per-zone weight, NOT hunting more fonts.
- Sections are typographic (Font A/B): header smaller; emphasis (headings/totals)
  larger+heavy; reverse-video amounts in black boxes (a render treatment, TODO).
- Prototypes averaged from real receipts ghost (alignment); the receiptfont.com
  CHART extraction is far cleaner — that's the win for any merchant with a chart.
- Costco refinement still open: heavy weight should target the large HEADING lines
  (SELF-CHECKOUT) not the TOTALS zone; add the reverse-video black-box TOTAL.
