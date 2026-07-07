# SynthesisPipeline figure — spec

One scroll-driven figure on `/receipt` REPLACING both `AugmentationShowcase`
and `SynthesisPrintReveal`. Story: **how a labeled receipt that never existed
gets made, end to end** — letterforms mined from real prints, style measured
from real receipts, content composed, printed, labeled.

A `merchant` toggle (Sprouts ⇄ Costco) switches EVERY act's assets: the
letterform journey, the font grid, the style treatments (Sprouts underlines
vs Costco reverse-video + heavy headings), and the final receipt. Same
machine, two stores — that's the generalization claim.

## Acts (scroll-advanced sections; one sticky canvas/stage)

1. **Raw material** — a fan of real receipt scan thumbnails (existing CDN
   images; 3-4 per merchant). Caption: every asset below is derived from
   these.
2. **One character** — zoom into a real `S` crop; then a fast flip-stack of
   ~30 real prints of the same character piling up; they average into the
   soft consensus cloud (pre-rendered PNG frames from the sample stacks).
3. **The pen path** — over the cloud, the stroke skeleton draws itself:
   anchor nodes + Bézier handles visible (animate SVG path from the
   committed glyph JSON — it IS cubic Béziers; y-up cap units, flip y,
   1000 units = cap height). Callout: "6 nodes. We didn't copy pixels —
   we learned the path the printhead travels."
4. **Thermal print + weight** — dots stamp along the path (canvas: circles
   at fixed arc-length steps, radius = dot.size/2 × weight). A weight
   slider (0.9–1.4, default 1.0) re-stamps live; at 1.33 label it "the
   measured BALANCE DUE weight" (Sprouts) / "the chart heavy face" (Costco).
5. **A whole font** — 94-glyph contact-sheet grid cascades in (pre-rendered
   per-glyph PNGs). One-line stat: "94 glyphs, ~6–12 nodes each, mined from
   N receipts."
6. **Measured style** — a real receipt column with style annotations lighting
   up per section (from the committed stylemaps): Sprouts → header underline
   41%, BALANCE DUE bold+taller, payment condensed; Costco → TOTAL
   reverse-video, display headings heavy+enlarged, date box. Values shown
   are the measured numbers, cited as such.
7. **Compose** — receipt content assembles: line items appear one by one
   (from the real composed candidate JSONs), a swap animation replaces one
   item with another (catalog), totals recompute with a tick.
8. **Print + labels** — the full synthetic receipt prints top-to-bottom
   (reuse the print-reveal mechanic: masked reveal of the final render),
   paper texture fades in, then ground-truth label boxes snap on with the
   family legend (reuse labelGeometry/labelStyles utilities). Counter:
   "Labeled training example. Zero manual labels."

## Assets (all generated from real data; land in
`portfolio/public/synthetic-receipts/pipeline/<merchant>/`)

- `char_prints/{i}.png` — ~30 individual sample crops of the hero char
  (Sprouts hero: `S`; Costco hero: `A`), upscaled NEAREST ×3
- `char_cloud.png` — soft consensus map
- `char_skeleton.json` — the glyph source JSON verbatim (nodes/handles)
- `dot_params.json` — {dotSize, refCap, weightDefault, weightBold}
- `font_grid/{cp}.png` — 94 rendered glyphs at cap 40 (tight crops)
- `style_annotated.json` — per-section measured values (subset of
  stylemap.json with display strings) + a real receipt image crop set
  showing each styled region (`style_crops/{section}.png`)
- `compose_steps.json` — the composed receipt's word list split into
  reveal groups (header/items/summary/footer) + the swap pair
- `final.webp` + `final.labels.json` — the printed synthetic receipt and
  its labels (same schema the existing figures consume)
- `real_thumbs/{i}.webp` — act-1 receipt scan thumbnails

Asset generation scripts live in the grid-discipline worktree (python,
reuse sample stacks + compiled fonts + renderer); the frontend consumes
only static files + JSON.

## Component

- `components/ui/Figures/SynthesisPipeline/` — same conventions as the
  existing figures (scroll progress via IntersectionObserver/sticky stage,
  prefers-reduced-motion fallback = static panels, jest tests co-located,
  no new deps).
- Remove `<AugmentationShowcase />` and `<SynthesisPrintReveal />` from the
  receipt page; keep their directories until the new figure ships green,
  then delete in the same PR.
- Mobile: the stage is a single column; the weight slider and merchant
  toggle are the only pointer controls; everything else scroll/tap-free.
- Perf: only PNG/WebP + one JSON per act in the viewport; dot-stamping is
  the single canvas animation (≤ 200 dots/frame).

## Copy anchors (tone: measured, first person plural, no hype)

- "No font files exist for these printers. So we mined the letterforms
  from the receipts themselves."
- "140 prints of one character, averaged. No single print is trustworthy;
  together they vote."
- "We didn't trace pixels. We learned the pen path — six nodes."
- "Bold isn't a second font. It's one parameter." (weight beat)
- "Style isn't designed. It's measured." (act 6)
- Final: "A receipt that never existed, labeled perfectly, for free."
