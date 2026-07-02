# Task: "Growing the training set" — synthetic receipt augmentation showcase (frontend only)

Build a polished, interactive portfolio piece that demonstrates how we **expand the
receipt training dataset** by generating new, *perfectly-labeled* receipts from real
ones. **Frontend-only:** all images are pre-generated and committed. Do NOT run or
touch Python/synthesis code, AWS, or glyph atlases.

## The one-line story

> A receipt-labeling model needs lots of *labeled* receipts. We take one real
> receipt, mutate its contents with a grammar learned from the merchant's other
> receipts (add/remove items, recompute the math), and render a photorealistic
> **new** receipt — and because we generate from labeled positions, **every one is
> already perfectly labeled.** One real receipt → many labeled training examples,
> covering baskets the real data never had.

## Assets (committed) — `portfolio/public/synthetic-receipts/showcase/`

Four Sprouts variants, each with a photorealistic render + a ground-truth label
overlay (WebP), plus `<variant>.labels.json` (the exact `tokens`/`bboxes`/`ner_tags`)
and `manifest.json` (operation, old→new total, caption, token counts):

| Variant | `.webp` (render) | `.labeled.webp` (label overlay) | Operation |
|---|---|---|---|
| `base` | real Sprouts base receipt | boxes + legend | — |
| `add_line_item_1` | **Added LIMES (PRODUCE) → total 29.08 → 30.58** | | add item |
| `remove_line_item_2` | **Removed SOUR CREAM (DAIRY) → 10.78 → 7.99** | | remove item |
| `remove_line_item_5` | **Removed CAGE → 23.78 → 17.99** | | remove item |

The renders are the real photorealistic thermal output (glyph atlas + logo + paper
texture); the injected/removed line and the recomputed totals are visible in the
receipt body and BALANCE DUE. The `.labeled` overlays color boxes per label family
(MERCHANT_NAME, ADDRESS_LINE, DATE, TIME, PRODUCT_NAME, QUANTITY, UNIT_PRICE,
LINE_TOTAL, GRAND_TOTAL, PAYMENT_METHOD, …) with a legend.

Prior art on this branch: `portfolio/components/ui/Figures/SyntheticReceiptImages/`
(from PR #1017) — reuse or supersede.

## What to build (the "cool visualization")

- A single receipt front-and-center (start on `base`).
- **Operation controls** — buttons/tabs: *Add item · Remove item · Reveal labels*
  (and stepping between the remove variants). Clicking swaps to that variant's image.
- Each operation shows a **caption** ("Added LIMES · total 29.08 → 30.58") and, ideally,
  a subtle highlight of the changed line + the changed total.
- **Reveal labels** toggle → cross-fades to the `.labeled` overlay (or draws boxes from
  `<variant>.labels.json` yourself for crisper, themable boxes — your call).
- A **running counter**: "labeled training examples generated: N", and a headline like
  *"1 real receipt → N labeled examples."*
- Responsive, accessible, lazy-loaded, matches the site's design system (dark/light).

## Constraints & done criteria

- **Frontend only** — everything under `portfolio/`, Node. Setup: `cd portfolio && npm ci`.
- Don't modify Python packages / synthesis / their CI.
- `cd portfolio && npm run lint && npm test` + typecheck pass; add a component test.
- Open a PR when done; run the `@codex review` loop and address findings.

## Cloud environment (Fable session)

Start the session on this branch (`feat/synth-receipt-showcase`). Setup script:

```bash
#!/bin/bash
set -e
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"/portfolio
npm ci
```

No AWS, no Python, Network = Trusted (npm registry). That's all it needs.
