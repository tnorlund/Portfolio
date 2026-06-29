# Plan: per-merchant item-line GRAMMAR for synthetic receipts (content lane)

## Problem (from a 24-receipt opus realism re-score, v2)
After grid typography + totals/item-count reconciliation, mean realism is still 2.62/10 and **content is now the
#1 fix layer (61 mentions)**. The dominant content tell is item-line GRAMMAR: synthesized add/compose item rows
are bare `<name> <price>` with NO `F`/`T` tax flag, no `now`/markdown marker, no `SALE 1@ $x, WAS: $y each`
sub-line, and a trailing comma instead of `...` truncation — nothing like the merchant's real line format.

## Root cause (in code)
`_build_line_item_line` (merchant_synthesis.py ~L5588) emits exactly:
```python
_build_line(entry.product_tokens + [price], ["PRODUCT_NAME"]*n + ["LINE_TOTAL"], ...)
```
A lowest-common-denominator line. It ignores the merchant's grammar even though we already have the data.

## What we ALREADY built (review these, don't duplicate)
- `merchant_intelligence/*.json` (M1–M8): per-merchant `catalog` (real products: name/price/taxable),
  `tax` (e.g. amazon `nontaxable_flags:['F']`, `taxable_flag:'T'`, jurisdiction), `details`, `sources`, `review`.
- M6–M8 STRUCTURE work: structural fingerprint + archetype clustering + a "structure block in artifacts" +
  the cross-merchant structural prior ("borrow structure, never content"). See `merchant_research/research.py`,
  the intelligence schema/loader (commits c63c1f423 M1, 6361c4499 M6, f6644535d M7a, 3d6ac417f M8).
- The catalog/online-catalog system (`MerchantCatalogEntry`/`OnlineCatalogEntry`, `online_catalogs/` dir).
- NOTE: amazon_fresh.json has NO per-line token template — the structure work is layout-archetype level, not
  an item-line TOKEN grammar. That extraction was never done; the line builder falls back to bare name+price.

## Proposed implementation (extend the existing pipeline; "borrow structure, never content")
1. **Item-line-grammar LEARNER** (new): for each merchant, derive an `item_line_template` from REAL labeled
   receipts (we have PRODUCT_NAME / LINE_TOTAL / tax-flag / marker labels). Learn:
   - tax flag: present? which chars (F/T/FF/FT…)? column/position relative to price.
   - markdown markers: a `now`/sale marker left of price? a second `SALE 1@ $x, WAS: $y each` sub-line? its pattern.
   - name truncation style (`...` vs none) and max width.
   - column x-positions for name / price / flag (we already use `_label_x_p50`).
   Store as a new `item_line_template` block in `merchant_intelligence/*.json` (extend schema + loader).
2. **Render the template** in `_build_line_item_line`: fill name (truncated to style), price (in column),
   `F`/`T` from `entry.taxable` + the merchant's flag chars, and emit the `now` + `SALE/WAS` sub-line ONLY for
   merchants whose template has them. Apply to `add_line_item` and `compose_online_catalog`.

## Questions for the reviewer — decide these
1. Should `item_line_template` live IN `merchant_intelligence/*.json` (extend the schema/loader) or in a
   SEPARATE grammar module? Does it overlap/duplicate the existing M6–M8 structure/archetype block — should it
   reuse or extend that instead of a new block?
2. Where should the LEARNER live — inside `merchant_research/research.py` (the intelligence pipeline that
   already mines receipts) so it regenerates with the other intelligence, or a standalone extractor?
3. A 2-line item (name + SALE sub-line) changes per-item GEOMETRY/bboxes. How does that interact with the
   layout-integrity gates (`build_layout_integrity_evidence`, the overlap/collision checks) and the new grid
   renderer? Is emitting a sub-line safe, or does it need vertical reflow (the #2 layout lane) FIRST?
4. The tax flag is the cheapest, highest-value sub-fix (we already have F/T + `entry.taxable`). Should we ship
   JUST the tax-flag + truncation in a first PR, and defer the SALE sub-line until #2 layout reflow lands?
5. Any cleaner approach given the existing code (e.g. derive the template lazily from the base receipt's own
   item rows at synth time, instead of a precomputed per-merchant block)?

Output: a one-line verdict, decisions on 1–5, and a concrete FIRST-PR scope (smallest change that moves the
content score) with the files/functions to touch.
