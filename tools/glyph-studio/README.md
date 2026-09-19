# Glyph Studio

Local, Illustrator-like design tool for parametric **stroke-skeleton receipt
fonts**. Glyph sources are per-glyph JSON (centerline strokes in cap units,
committed under `fonts/<merchant>/`); the compiler stamps thermal dots along
the strokes and emits the exact `.glyphs.npz` contract the receipt renderer's
`BitmapFont` consumes. The tracer seeds skeletons from the real-letterform
corpus (`*.samples.npz`) so a merchant font starts faithful and gets
hand-polished in the editor.

## Run

The interactive GUI is gone; the tool now drives from the MCP server (agent
loop) or the Python CLIs. Start a server with:

```bash
cd tools/glyph-studio
npm install          # once
npm run mcp          # stdio MCP server (agent tools) — see "MCP server" below
npm run server       # or the HTTP server on :5177 over the same core
```

Python side uses `~/Portfolio/.venv/bin/python` (numpy + PIL only — no new
deps by design; scipy/skimage are deliberately absent from the venv). See the
CLIs below for the trace/compile/test loop, and `ADD_MERCHANT.md` for
onboarding a new merchant font.

## CLIs

```bash
PY=~/Portfolio/.venv/bin/python
cd tools/glyph-studio/py

# seed/refresh skeletons from the corpus (hand-edited glyphs divert to _traced/)
$PY -m glyphstudio.trace /tmp/gridfix/sprouts_font2/sprouts.samples.npz ../fonts/sprouts

# compile to npz + self-check against the REAL BitmapFont + contact sheet
$PY -m glyphstudio.compile ../fonts/sprouts ../.out/sprouts-studio.glyphs.npz \
    --sheet ../.out/sprouts-studio.sheet.png

# regenerate the WYSIWYG parity fixture (after bitmap_font.py changes)
$PY -m glyphstudio.cellmath --emit ../fixtures/cellmath_cases.json

# tests
$PY -m pytest tests -q
```

## Publish / review

Review runs use a **BITMATRIX_DIR overlay** (`.out/bitmatrix-overlay/`:
symlinks + our npz copied over `sprouts.glyphs.npz`) so nothing global
changes. To publish for real, back up and copy the npz into `$BITMATRIX_DIR`
(default `/tmp/bitmatrix`) under the profile's filename.

## Portfolio figure assets (SynthesisPipeline)

`py/export_pipeline_assets.py` rebuilds one merchant's tree under
`portfolio/public/synthetic-receipts/pipeline/<slug>/` (the static files the
`/receipt` figure plays back) from committed tooling: the production render
path for `final.webp` + render-true `final.labels.json`, the CDN scan for
`real.webp`, the vault logo, and for hero merchants the letterform corpus
(`char_prints/`, `char_cloud.png`), the glyph JSON (`char_skeleton.json`,
`dot_params.json`), the renderer's own `BitmapFont` masks (`font_grid/`,
`font_metrics.json`), the stylemap (`style_annotated.json`, `style_crops/`)
and act-1 `real_thumbs/`. Source receipts per slug live in
`fixtures/pipeline_merchants.json`.

```bash
# dev AWS reads: DynamoDB (words, labels, ReceiptPlace, dims), S3 (scan,
# fonts/logo via the ACTIVE truth bundle, merchant_fonts/<font>/corpus.npz)
export DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1
export RECEIPT_PAPER_STRENGTH=0.3 BITMATRIX_DIR=/tmp/bitmatrix
$PY py/export_pipeline_assets.py sprouts costco --out-dir /tmp/pipeline
$PY py/export_pipeline_assets.py --all --finale-only --out-dir /tmp/pipeline
```

Review `/tmp/pipeline/<slug>/` and copy it over `portfolio/public/...`.
The figure's merchant tables (`Merchant`, `MERCHANTS`, `MERCHANT_LABELS`,
`RECEIPT_DIMS`, `BOLD_WEIGHT_CALLOUT`) are **generated** from
`fixtures/pipeline_merchants.json` into
`portfolio/components/ui/Figures/SynthesisPipeline/merchants.generated.ts`
by `python -m glyphstudio.portfolio_wiring` (`--check` in tests); write the
printed dims into the manifest's `dims` and regenerate rather than editing
`pipelineData.ts`. `py/new_vendor.py export <slug>` does all of that in one
step. Offline logic is in `glyphstudio/pipeline_assets.py` (tests:
`tests/test_pipeline_assets.py`). Merchants without a vault corpus or a
bundle logo get finale files plus `font_grid/` only; `--corpus` / `--logo`
supply local inputs.

## New vendor in one command per stage (`py/new_vendor.py`)

`new_vendor.py` chains the mint loop for a vendor described by
`fonts/<slug>/vendor.json` (`ADD_MERCHANT.md` "The short way"):
`census` / `init` / `corpus` / `font` (`glyphstudio.mint`: trace, simplify,
handcraft, `--donor` fill, normalize, compile, specimen + strips) / `pitch` /
`style` (stylescan + styleagg into a `stylemap.json` whose `rules` list is
shared by the renderer) / `profile` / `fixture`
(`migrate_merchant_truth_v1.py --fixture-out`, so an unminted vendor renders
in `MERCHANT_TRUTH_MODE=fixture`) / `calibrate` (solves
`ocr_cap_height_ratio` from the first scorecard) / `export` / `wire`. Dev
reads only; publish, mint and flip stay owner-only. Glyphs adopted from a
sibling face carry `"donor": "<font>"`; `publish_merchant_font.py` refuses a
font with more than a quarter of them unless `--allow-donor-glyphs`.

## Conventions

- Cap units: y-up, baseline y=0, cap ink line y=1000; 1 px @ REF_CAP 60 =
  16.67 units. **Stroke coords are centerlines** — ink extends dot/2 past
  them (cap stems top out at `1000 − dot/2`; the tracer does this
  automatically, the editor shows inset guides).
- Renderer spacing is fixed-grid monospace: per-glyph advance is IGNORED;
  pitch = `advance_ratio × cap_px × condense` where `advance_ratio` derives
  from the compiled widths of `MWHNUABDOR`. Tracking = scale glyph widths.
- Deliver solid strokes; ink density is applied downstream (`bitmap_thin`,
  auto-derived). Generated npz/PNGs live in `.out/` (gitignored); the JSON
  sources are the committable truth.

## Layout variant clustering (W-G)

`scripts/build_variant_layout.py` (repo root) measures a merchant's SCAN
receipts with `glyphstudio.stylescan`, clusters the per-receipt layout
signatures BEFORE pooling (`glyphstudio.variant_cluster`), then pools each
cluster through `layout_template.build_layout_template`. Persisted-artifact
convention (artifacts gitignored via `.out/`; this layout is the contract):

```
tools/glyph-studio/.out/stylescan/<merchant_slug>/
    <image_id>_<receipt_id>.json   # one stylescan.measure record
    manifest.json                  # sha256 per record + run provenance
tools/glyph-studio/.out/variant_layout/
    <merchant_slug>_variant_layout.json  # template + verdict + provenance
```

The emitted `template` keeps `version: 1` (dominant cluster at the top
level, other clusters in `template.variants[]`) and must pass
`layout_template.validate_layout_template` unchanged. A committed sample of
the real Costco run lives at `fixtures/costco_variant_layout.sample.json`.

## MCP server

`server/mcp.mjs` is a stdio Model Context Protocol server — a sibling entry
point to the HTTP server over the same core (`server/lib.mjs`). It gives an
agent the trace/render/compile/review loop as tools, with rendered PNGs coming
back as image blocks the model can see. Run it standalone with `npm run mcp`.

Tools: `list_glyphs`, `get_glyph`, `render_glyph` (fast inner loop — renders an
unsaved candidate), `view_samples` (corpus modes: `median`/`binary`/`index`/`grid`
— `grid` montages the first up-to-9 real prints 3x3), `measure_glyph` (numeric
consensus geometry: ink bbox, spans, crossbars, stems, stroke width, holes),
`compare_glyph` (batch `[soft consensus | compiled | overlay]` strip — the
confirmation arbiter view), `set_glyph` (validate-then-write, refuses to clobber a
hand-`edited` glyph without `force`), `compile_font`, `review_font`,
`simplify_glyphs`, `font_audit`, `publish_font` (compile + full-coverage gate,
timestamped backup, symlink-safe copy into `$BITMATRIX_DIR`, and *inkthin*
render-cache clear/seed).

Register with Claude Code (user scope, since this repo differs from most
sessions):

```bash
claude mcp add --scope user --transport stdio glyph-studio \
  -- node /Users/tnorlund/Portfolio_grid_discipline/tools/glyph-studio/server/mcp.mjs
```

Or project-scope `.mcp.json` at the repo root (needs one-time interactive
approval; `compile`/`review` exceed the default tool timeout, so raise it):

```json
{
  "mcpServers": {
    "glyph-studio": {
      "type": "stdio",
      "command": "node",
      "args": ["${CLAUDE_PROJECT_DIR:-.}/tools/glyph-studio/server/mcp.mjs"],
      "env": {},
      "timeout": 600000
    }
  }
}
```

Tools surface as `mcp__glyph-studio__<tool>`. Notes: never write to stdout from
`mcp.mjs` (it is the JSON-RPC channel — diagnostics go to stderr). Smoke test:
`node test/mcp-smoke.mjs`.
