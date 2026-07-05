# Glyph Studio

Local, Illustrator-like design tool for parametric **stroke-skeleton receipt
fonts**. Glyph sources are per-glyph JSON (centerline strokes in cap units,
committed under `fonts/<merchant>/`); the compiler stamps thermal dots along
the strokes and emits the exact `.glyphs.npz` contract the receipt renderer's
`BitmapFont` consumes. The tracer seeds skeletons from the real-letterform
corpus (`*.samples.npz`) so a merchant font starts faithful and gets
hand-polished in the editor.

## Run

```bash
cd tools/glyph-studio
npm install          # once
npm run dev          # server :5177 + vite app
```

Python side uses `~/Portfolio/.venv/bin/python` (numpy + PIL only — no new
deps by design; scipy/skimage are deliberately absent from the venv).

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

## MCP server

`server/mcp.mjs` is a stdio Model Context Protocol server — a sibling entry
point to the HTTP server over the same core (`server/lib.mjs`). It gives an
agent the trace/render/compile/review loop as tools, with rendered PNGs coming
back as image blocks the model can see. Run it standalone with `npm run mcp`.

Tools: `list_glyphs`, `get_glyph`, `render_glyph` (fast inner loop — renders an
unsaved candidate), `view_samples`, `set_glyph` (validate-then-write, refuses to
clobber a hand-`edited` glyph without `force`), `compile_font`, `review_font`,
`simplify_glyphs`, `font_audit`.

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
