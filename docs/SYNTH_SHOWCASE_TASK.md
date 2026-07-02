# Task: "How we synthesize receipts" portfolio showcase (frontend only)

Build a polished portfolio section that demonstrates our **synthetic receipt
generation** — how a real receipt's OCR is turned into a pixel-faithful synthetic
one for training-data augmentation. This is a **frontend-only** task: the images
are already generated and committed; you display and explain them. **Do not run
or touch any Python / synthesis code, AWS, or glyph atlases.**

## What's already here

Pre-generated real-vs-synth proof images (committed) in
`portfolio/public/synthetic-receipts/showcase/`:

| Merchant | Real-vs-synth (side-by-side canvas) | Synth only |
|---|---|---|
| Sprouts Farmers Market | `sprouts_real_vs_synth.png` | `sprouts_synth.png` |
| The Home Depot | `homedepot_real_vs_synth.png` | `homedepot_synth.png` |
| Costco Wholesale | `costco_real_vs_synth.png` | `costco_synth.png` |

Each `_real_vs_synth.png` shows the REAL receipt crop (left) next to our SYNTHESIZED
render (right). Use whichever assets suit your layout (the side-by-side canvases are
the most convincing proof; the synth-only images are for a cleaner custom layout).

Prior art already on this branch: `portfolio/components/ui/Figures/SyntheticReceiptImages/`
(from PR #1017) — a React component for showing synthetic receipt images. **Build on
it or supersede it** with something cleaner; your call.

## The story to tell (real content for the copy)

1. **Real receipts → OCR.** Apple Vision OCR gives every word's text + bounding box
   (and, recently, barcodes/QR). That's the ground-truth layout of a real receipt.
2. **Per-merchant fonts.** Each merchant prints in a specific thermal font. We build
   a **per-merchant glyph atlas** — for data-rich merchants (Sprouts) from their own
   receipts; for others (Costco) from the real bitMatrix font chart. Logos/wordmarks
   are reconstructed too (Home Depot's tilted-square lockup, Sprouts' FARMERS MARKET).
3. **Grid render.** A fixed-cell renderer places each character in its real position
   using the merchant's font, pastes the logo, and composites thermal-paper texture —
   producing a synthetic receipt that matches the real one's layout but whose **content
   is controllable** (swap items, amounts, dates) for augmenting the training set.
4. **Result.** The synthesized receipts (right side of each pair) are visually
   indistinguishable in layout/typography from the real ones — that's the point.

## What to build

- A responsive showcase section (extend the receipt page, `portfolio/pages/receipt.tsx`,
  or a dedicated section/route — match the site's existing design system, dark/light, etc.).
- Per-merchant real-vs-synth presentation — a toggle/slider/tabs between merchants, and
  ideally a real↔synth compare interaction (side-by-side, or an overlay/slider).
- The explanation above, written for a technical-but-general portfolio audience.
- Accessible, performant (lazy-load images; they're large — optimize if needed).

## Constraints & done criteria

- **Frontend only.** Everything runs under `portfolio/` with Node. Setup: `cd portfolio && npm ci`.
- Don't modify Python packages, synthesis code, or CI for those.
- `cd portfolio && npm run lint && npm test` (and typecheck) pass; add a component test.
- Keep the bundle reasonable; don't ship 3 MB PNGs unoptimized.
- Open a PR when done; run the `@codex review` loop and address findings.

## Cloud environment (Fable session)

Start the session on this branch (`feat/synth-receipt-showcase`). Setup script:

```bash
#!/bin/bash
set -e
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"/portfolio
npm ci
```

No AWS key, no Python, Network = Trusted (npm registry). That's all it needs.
