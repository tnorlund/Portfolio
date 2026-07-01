# Generalizing the synthetic-receipt renderer to arbitrary merchants

The synthetic-receipt renderer (`scripts/render_synthetic_receipts.py` +
`receipt_agent/.../rendering/`) grew up around Costco. Adding a new merchant
today means editing code: per-merchant config dicts, hardcoded trigger phrases
in `receipt_renderer.py`, Sprouts-only header branches, and ~7 scattered
USD/date regexes. A workflow catalogued **115 coupling points** across the
pipeline; this doc is the agreed plan to remove that coupling so onboarding a
merchant becomes "add one data row + stage its font/logo assets."

## Guiding principle: no output regression

Every PR below must leave the rendered pixels **unchanged** for the merchants we
already support (Costco first, then Sprouts as the second-most-special case).
The guard is a two-level regression check, run before/after each PR:

1. **Config-layer snapshot** — `merchant_typography()`, `section_scale_for_merchant()`,
   and `_merchant_logo()` (image bytes hash) for every merchant must be equal.
   Baseline: `/tmp/gridfix/pr1/baseline_config.json`.
2. **Pixel-identical render** — the same-content Costco render
   (`ed28a4ce`, 760×true-aspect) hashed to the byte. Baseline hash captured per
   PR. A deliberate-change PR (canvas/geometry) records a new baseline with a
   before/after visual diff in the PR description.

When a default is promoted from a hardcoded literal to config, the default value
must be the **exact current literal** so behavior is byte-identical until a
merchant explicitly overrides it.

## Target architecture (verdict from the design workflow)

A **data-first merchant registry** (`scripts/merchant_profiles.json`), keyed by
exact `merchant_name`, every field optional (absent ⇒ generic default ⇒ current
behavior). The existing `font_profile.py` geometry fitter stays and *feeds* the
registry (condense/stroke/price-lane); we do **not** build a full auto-fitting
pipeline until a second bitmap merchant exists. Lookup helpers keep their
signatures and read the registry. Pure typographic ratios stay as named module
constants; paper/thermal realism stays shared (device physics, not merchant).

The one thing that cannot be derived from data and stays an authored asset: a
distinctive brand **typeface** (Costco's bitMatrix atlas) and **logo** PNG —
these are data files + a pointer, not a code branch.

## The PRs

Each is independently shippable and gated by the regression check above.

### PR-1 — Registry loader, zero behavior change *(this PR)*
Introduce `scripts/merchant_profiles.json` + `load_merchant_profiles()` /
`get_merchant_profile()`. Move `_SECTION_SCALE_BY_MERCHANT` / `_DEFAULT_SECTION_SCALE`,
`_MERCHANT_TYPOGRAPHY`, and `_MERCHANT_LOGO` into it **verbatim** (Costco = row 1).
Route `section_scale_for_merchant`, `merchant_typography`, `_merchant_logo`
through the loader and delete the module dicts in the same commit (no split
brain). Font tokens (`PTMONO`/`VT323`/`B612`) and asset filenames resolve via the
existing path constants so the produced paths are byte-identical. Keep the
graceful drop-missing-asset fallback.
**Guard:** config snapshot + Costco pixel hash unchanged.

### PR-2 — Phrase anchors → config, defaults byte-identical
Add `anchors.*` to `RenderConfig`, defaulting to the exact current literals:
- `total_include_tokens=["TOTAL"]`, `total_exclude_tokens=["SUBTOTAL","NUMBER"]`
  (replaces `_is_final_total`, `receipt_renderer.py`)
- `heading_bleed_phrase` (Costco `"ITEMS SOLD:"`)
- `reverse_date_anchor` (Costco `"NUMBER OF ITEMS SOLD"`)
- `dash_after_amount_date` (Costco `True`)
- `reverse_box_lane_cells` (Costco `4`, `receipt_grid.py`)

Swap the ~6 literals for `config.field`. Costco golden image must be unchanged.

### PR-3 — Graphics / in-body-barcode profile
Route in-body symbology through `graphics.inbody_barcode` (default `code128`,
max 2, gap 34px, bar 30px — current Costco values) and move `_UPCA_MERCHANTS`
into `graphics.barcode_kind` (substring default retained). No output change.

### PR-4 — Locale regex factory
Build one price matcher + one date matcher from a `number_format` block (US
default). **Unit-test that the generated US pattern string equals the current
compiled pattern** for all ~7 sites before switching. Consolidate
`_DATE_LED`/`_DATE_TOKEN`/`_DATE_MDY` and the duplicated BIO-strip helper.

### PR-5 — Sprouts branches → header config
Replace the `SPROUTS` / `PRODUCE|DAIRY|GROCERY` / baked-phone-and-hours gates
with `header.header_markers` / `body_section_anchors` / `reflow_sections` /
`header_dedup`; brand token derived from `merchant_name`. Seed Sprouts' row with
its current literals so its render holds.

### PR-6 — Derive geometry from the font profile *(highest drift)*
Replace `_PRICE_COLUMN_RIGHT=905`, the 16/20 pitch quanta, and the
`_cached_output_size` 560×1280 / 576×1176 constants with values from
`font_profile.py` / example metadata. Gate hard behind the golden images; allow
a per-field pin if a value drifts.

### PR-7 — *(deferred)* Fitter auto-populates rows
Only once a second bitmap merchant exists, extend `font_profile.py` to emit
`section_scale` / `display_headings` / `anchors` as *suggested* row values a
human confirms. Same registry, no renderer change — additive, not a rewrite.

## The merchant profile (target schema)

```jsonc
{
  "<merchant_name>": {
    "section_scale":  { "HEADER": 0.80 },              // default {"HEADER":0.80}
    "typography": {
      "font": "PTMONO|VT323|B612",                     // token -> path constant
      "bitmap_font": { "regular": "<file>.npz", "heavy": "<file>.npz" },
      "condense": 1.0, "stroke": 0,                     // from font_profile
      "display_headings": { "PHRASE": 1.7 },            // {} default
      "reverse_total": false, "reverse_date_after_items": false,
      "dashed_separators": false
    },
    "logo": "<file>.png",                              // under $BITMATRIX_DIR; absent = text header
    "anchors":  { /* PR-2 */ },
    "graphics": { /* PR-3 */ },
    "header":   { /* PR-5 */ },
    "number_format": { /* PR-4, non-US only */ }
  }
}
```

## Add-a-new-merchant recipe (target, post-migration)

1. Ensure the merchant has OCR'd receipts in Dynamo; run `cached_font_profile` /
   `cached_glyph_atlas` for it (produces condense/stroke/price-lane; no code edit).
2. Add one row to `merchant_profiles.json` keyed by exact `merchant_name`; fill
   only what differs from generic.
3. Set `typography.font` (or point `bitmap_font` at a staged atlas) + condense/
   stroke from step 1; set `section_scale.HEADER` (or omit for the 0.80 default).
4. Drop `<slug>_logo.png` into `$BITMATRIX_DIR`, or omit for a text-only header.
5. Set `graphics.barcode_kind` / `inbody_barcode` only if it prints codes.
6. For thermal quirks (reverse-video total, boxed date, dashed rules, big
   headings): set the `features`/`anchors` with **that merchant's wording**.
7. For a dense grocer: set `header.*` from its own place/OCR record.
8. Non-US only: add `number_format`; else the US default applies.
9. Render — verify against the real receipt; tune data values.

**The one unavoidable authored bit:** a novel typeface still needs a staged
atlas asset, and a printed wordmark needs a logo PNG. Both are data, not code.

## What stays merchant-specific / open risks

- **Typeface & logo** cannot be derived from geometry — authored assets, by design.
- **Reverse-video / dashed detection** keys off phrases + labels, not pixels — a
  new merchant *authors* its anchor phrases (step 6) rather than getting them free.
  Pixel-fitting is deferred to PR-7 to avoid fragile small-sample detection.
- **`header_markers`** assume clean place data; seed from current literals and
  fall back to the repeated top-of-receipt block when place fields are missing.
- **Content repair** (`content_clean.py`): the Costco `*`→`=` footer repair moves
  to `content_repair.footer_sep`; EMV AID / PIN / OCR-confusion tables stay
  generic (payments-standard). Non-English TAX lookback / 3-decimal heuristics
  stay US-shaped until a `number_format` rate block is added — deferred, default
  matches all current merchants.
- **Main hazard is silent default drift** — mitigated by byte-identical defaults,
  the pixel-hash gate on every PR, and the generated-regex == current-regex test.
