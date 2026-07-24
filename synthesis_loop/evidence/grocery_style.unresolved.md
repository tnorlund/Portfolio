# Grocery style reds not changed

The focused renderer change fixes the independently identifiable Trader Joe's
transaction-heading defect. Vons and Gelson's remain byte-identical to their
baselines because their active v1 truth cannot honestly select a different
face for the red rows.

## Vons

The red is receipt-relative fade, not an address-face omission:

- real/synthetic `Main: (805) 497-1921` stroke-per-height: `0.1264 / 0.1264`;
- real/synthetic `WESTLAKE CA 91360`: `0.1404 / 0.1404`;
- body median: real `0.0948`, synthetic `0.1336`.

Thus the address ink already matches in absolute terms. Selecting the heavy
face would overprint the address while concealing the actual mismatch: the
golden receipt's body is lighter than the consensus atlas. The v1 stylemap also
explicitly says address/phone rows are body-identical and records
`store_header`, `footer`, and `item` as normal weight
(`tools/glyph-studio/fonts/vons/stylemap.json:10`,
`:59`, and `:66`). An honest fix needs receipt-level measured row faces/fade or
a reminted truth rule, not an unconditional address bold.

## Gelson's Westlake Village

The active stylemap has only one face rule, `section_header`; its source note
explicitly says payment/summary/footer are body-normal
(`tools/glyph-studio/fonts/gelsons/stylemap.json:3-16`). The golden receipt,
however, measures address `3/3` and footer `2/2` bold relative to its unusually
light body. No active rule distinguishes those rows, so `row_style` correctly
falls back to the regular face when a section is absent
(`receipt_agent/receipt_agent/agents/label_evaluator/rendering/receipt_stylemap.py:266-286`).

Adding a merchant-name conditional would make this receipt pass by bypassing
truth. Editing the local JSON would not change production either: online
rendering consumes only the active bundle's inline, hash-verified document
(`scripts/render_synthetic_receipts.py:1566-1577`,
`:1620-1628`). Since this lane is read-only against dev DynamoDB, the remaining
Gelson's red requires a new measured stylemap/truth version (or receipt-level
`row_faces`) and remint.
