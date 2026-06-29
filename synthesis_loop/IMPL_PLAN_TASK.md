# Codex: turn the realism research into a concrete IMPLEMENTATION PLAN

Inputs to read fully:
- `/tmp/realism_research.json` — 5 research briefs (typography, layout, content, graphics, paper-texture),
  each with key_findings (named libs/fonts/specs + URLs), a recommended_approach, libraries, pitfalls.
- `synthesis_loop/REALISM_ROADMAP.md` — the 5 ranked roadmap items + root cause + code map.
- The actual renderer/synthesis code in this worktree.

For EACH of the 5 items, make DECISIONS (not a survey):
1. The ONE library/font to adopt, resolving the license tradeoffs for an ML-training + frontend-demo pipeline
   (e.g. typography: thermal-sans-mono is GPL-2.0 vs VileR CC-BY-SA vs VT323 OFL — pick one and justify).
2. The exact code changes, function by function (which functions in receipt_renderer.py / render_synthetic_
   receipts.py / merchant_synthesis.py / font_profile.py change, and how), plus any NEW modules to add.
3. pip/system dependencies to install and any offline/runtime risks (e.g. treepoem needs Ghostscript;
   augraphy needs opencv).
4. The implementation ORDER and what can be parallelized.

Then:
- Give a precise FIRST-PR scope for item #1 (grid typography): the minimal change to replace per-token
  _fit_font with a fixed-grid, single-size-per-line monospace renderer (fontmode='1', per-glyph placement at
  col*cell_w, anchor='ls', integer NEAREST upscale, then degrade) — including how to derive cell_w/cell_h from
  the existing merchant font profile, and how to A/B measure the realism lift.
- Flag any place the research is wrong, over-engineered for a demo, or conflicts with our constraints.

Output: a one-line headline, then per-item decisions (1-4), then the first-PR scope, then disagreements.
Be concrete and ruthless.
