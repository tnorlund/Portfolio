# Codex: synthesize a prioritized receipt-realism roadmap

You have 25 per-receipt realism analyses (one per merchant x synthesis-operation) in
`/tmp/realism_findings.json`: each has realism_0_10 (mean 2.6, range 1-4), the most damaging tell, and a
ranked list of concrete fixes. Read it fully.

Goal: ONE prioritized roadmap to make our synthetic receipts photorealistic for a frontend demo.

Produce:
1. The 5 HIGHEST-LEVERAGE changes (the ones that, fixed, move the mean realism the most), each with: the
   concrete change, the LAYER (typography/layout/content/paper-texture/graphics), how many of the 25 it
   recurs in, and WHERE in the code it lives. Code map: render path = `scripts/render_synthetic_receipts.py`
   (`_render_cached_hybrid`) + `receipt_agent/.../rendering/receipt_renderer.py` (`render_receipt`, `_fit_font`,
   `_draw_text`); content/synthesis = `receipt_agent/.../merchant_synthesis.py` + `synthesis_reconcile.py`
   (+ the new `synthesis_text_clean.py`); the font profile = `rendering/font_profile.py`.
2. A grouped backlog (by layer) of the remaining fixes, deduped, each one line.
3. The single thing to do FIRST and why.
4. Any disagreement with the analysts (over/under-stated issues; things that won't matter for a demo).

Output a one-line headline verdict, then sections 1-4. Be concrete and ruthless about priority.
