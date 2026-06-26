# Synthetic Receipt Rendering & Font Geometry

> Branch: `feat/receipt-font-render`. Builds on PR #994's font analysis and the
> PR #1003 per-merchant tax gate. Everything here produces or consumes
> **geometry data**; none of it relaxes a deterministic safety gate.

This documents the four pieces added for synthesized-receipt typography and
rendering, all under
`receipt_agent/receipt_agent/agents/label_evaluator/rendering/`.

## 1. Per-merchant font profile (`font_profile.py`)

A `MerchantFontProfile` captures a merchant's receipt typography in
resolution-independent **normalized 0-1** units (bottom-left origin, y high-is-
top — the OCR convention):

| field | meaning |
|---|---|
| `font_height` | median glyph height |
| `char_width` | median character advance (`width / visible_chars`) |
| `char_aspect` | `char_width / font_height` |
| `line_pitch` | median center-to-center row spacing (None if < 2 rows) |
| `price_column_x` | center-x of the line-total column (None if unproven) |
| `dominant_style_label` | label of the largest #994 font cluster |

Built on PR #994's `analyze_receipt_fonts`: the dominant font cluster supplies
the typography; `line_pitch` / `price_column_x` are measured from word/line
positions. `price_column_x` clusters price tokens by right edge and takes the
**rightmost well-aligned column**, so it never lands between the quantity / unit-
price / line-total columns, and returns `None` when no column has enough aligned
evidence (we never guess).

```python
from receipt_agent.agents.label_evaluator.rendering import (
    extract_receipt_font_profile, build_merchant_font_profile,
    build_merchant_font_profile_from_dynamo,
)

# Local (no AWS): per-receipt OCR words/lines/letters -> profile.
profile = build_merchant_font_profile("Vons", [
    extract_receipt_font_profile(words, lines, letters=letters)
    for words, lines, letters in receipts
])

# Real data: resolve receipts via ReceiptPlace, run #994 per receipt, aggregate.
profile = build_merchant_font_profile_from_dynamo(
    "ReceiptsTable-dc5be22", "Vons", region="us-east-1", max_receipts=8,
)
```

`profile.to_geometry_params()` scales the profile into the synthesis pixel space
(`char_width_px`, `font_height_px`, `line_step_px`, `price_column_x_px` — fields
that could not be observed are `None`).

> **`ReceiptMetadata` is deprecated** — resolve merchant receipts via
> `get_receipt_places_by_merchant`. The Dynamo path skips receipts that fail to
> load so one bad image never sinks the merchant profile.

## 2. Renderer (`receipt_renderer.py`)

`render_receipt(receipt, profile=..., config=...)` draws the **exact word boxes**
a synthesizer produced to a PNG — a faithful picture of the geometry the LayoutLM
gates will see, not a re-imagined layout.

- Input boxes are `[x0,y0,x1,y1]`, **y high-is-top**, in either normalized 0-1 or
  0-1000 (auto-detected). The renderer flips y so the receipt header paints at
  the top.
- Per-word glyph size comes from each box; the profile supplies the typeface
  (monospace — exact typeface is unrecoverable from OCR, per the #994 pilot) and
  a `char_aspect`-driven condensation.
- `color_by_label=True` colors PRODUCT_NAME / totals / etc. (BIO prefixes
  stripped); `draw_price_column=True` overlays the profile's price column.

`render_real_vs_synthetic(real, synthetic, ...)` produces the side-by-side
visual-QA diff. `scripts/render_synthetic_receipts.py` renders accepted bundle
candidates beside their real base receipts.

## 3. Font metrics → synthesis spacing (`merchant_synthesis.py`)

`generate_merchant_synthesis_candidates(..., font_geometry=...)` accepts a
profile's `to_geometry_params()` dict. It is stamped onto each working receipt
and used **only as a fallback** for scaffolds too sparse to measure their own
geometry:

- `_template_fill_geometry` falls back to the profile's char width / glyph
  height / price column when a scaffold's item region can't be measured.
- `_line_step` falls back to the profile's row pitch **only at row-generation
  call sites** (`allow_font_geometry_fallback=True`). Structure **scoring** and
  signature paths stay on measured geometry + the flat constant, so the
  structure-similarity gate and emitted evidence are never influenced by the
  profile.

Real measured geometry always wins. Verified end-to-end: threading
`font_geometry` through the local Vons pipeline leaves the accepted high-fidelity
candidate set and operation mix unchanged.

## 4. Training-image path — out of scope

LayoutLMv3's visual modality does **not** export to CoreML (the target runtime),
so we do not train on rendered images. The structured receipt (tokens + boxes +
labels) is the only training artifact. Rendering exists purely to **show /
v3-training contract was removed; it remains in git history on
`feat/receipt-font-render` if v3 ever becomes viable.

## Tests

```bash
python3.12 -m pytest \
  receipt_agent/tests/test_font_profile.py \
  receipt_agent/tests/test_receipt_renderer.py \
  receipt_agent/tests/test_merchant_synthesis_font_geometry.py \

# Font module (run from the package root):
(cd receipt_upload && python3.12 -m pytest \
   tests/test_font_analysis.py tests/test_font_letter_analysis.py -q)
```
