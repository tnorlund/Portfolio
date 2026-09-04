# tools/glyph-studio/ (stroke-skeleton receipt fonts)

Deltas to the root `AGENTS.md`.

- Python side uses the repo `.venv` with numpy and PIL only. scipy and skimage
  are deliberately absent; do not add dependencies to make an algorithm easier.
- Run: `npm run mcp` (stdio MCP server for agent loops) or `npm run server`
  (HTTP on :5177); `npm run test` and `npm run typecheck` for the TypeScript
  core. Python CLIs run from `py/` as `python -m glyphstudio.<cmd>`; see
  `README.md` for the trace → compile → check loop and `ADD_MERCHANT.md` for
  onboarding a merchant font.
- Glyph JSON under `fonts/<merchant>/` is the committable truth; compiled
  `.glyphs.npz`, sheets, and PNGs go to `.out/` (gitignored). Hand-edited
  glyphs are never overwritten by the tracer; re-traces divert to `_traced/`.
- Coordinates are cap units (y-up, baseline 0, cap 1000) and stroke coordinates
  are centerlines. Spacing is fixed-grid monospace; per-glyph advance is
  ignored, so adjust tracking by scaling glyph widths.
- Deliver solid strokes; ink density is applied downstream by the renderer.
