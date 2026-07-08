# Epic v2: Learned receipt structure (stop reverse-engineering each merchant by hand)

**Thesis:** A thermal receipt is a generative process — transaction CONTENT → POS LAYOUT → thermal RENDER. Today we reverse-engineer each merchant's (template + printer) by hand. v2 learns the generative structure from the already-labeled corpus so a new merchant is *inferred few-shot, not configured*.

## Why now

v1 (PR #1057) already makes minting cheap and correct: it calibrates the render knobs (weight, cap-ratio, bitmap_thin) so a synthesized receipt looks like the merchant's printer. The other agent's leg measures *training value* — whether synthetic receipts actually move the label model. This leg is the third leg: it makes synthesis **scalable** and **novel**.

The scaling wall is concrete and measured. The corpus is 222 merchants; **9 are done**. Every pillar below found the same shape of liability — O(merchants) hand-tuning that fits the done fleet and silently mis-serves the other 213:

- **stylescan.py**: 7 bespoke `_<SLUG>_RULES` regex blocks, 55 `re.compile()` patterns, ~259 lines of hand-written section-classification config.
- **receipt_grid.py / receipt_stylemap.py**: a dozen hand-guessed layout constants plus per-merchant `_INNOUT_RULES`/`_RULES` regexes.
- **merchant_synthesis.py**: mined-median catalogs, hand-typed `online_catalogs/*.json` for 5 of 222 merchants, hand-coded `merchant_tax_config.py`.
- **merchant_profiles.json**: 12 hand-authored blobs, ~20 style knobs each, "one focused session per merchant" (`ADD_MERCHANT.md`).

This is exactly the corner-cutting surface the lead flagged: an agent onboarding merchant #40 copies a regex block and moves on. The non-obvious point: **we hand-write regexes to re-derive structure the receipt LABELS already contain.** The 22-type `CORE_LABELS` taxonomy (`receipt_dynamo/constants.py:165-195`: `MERCHANT_NAME`, `ADDRESS_LINE`, `PRODUCT_NAME`, `LINE_TOTAL`, `SUBTOTAL`, `TAX`, `GRAND_TOTAL`, `PAYMENT_METHOD`, …) is curated per word by the labeling leg and is the single source of truth. It should be the shared interface between the labeling leg and the synthesis leg.

## What we hard-coded, mined, or guessed

| Category | Where | What it is | Example values |
|---|---|---|---|
| **MINED — keep** | `receipt_agent/.../font_profile.py` `_price_column_x` | Fitted per-merchant price column: clusters LINE_TOTAL right-edges, median center-x, `align_tolerance=0.04`, `min_support=2` | Robust statistics, already computed — **then discarded by receipt_grid.py** |
| **MINED — keep** | `font_profile.py` medians | char_width / font_height / line_pitch as robust medians | The good part; extend with variance |
| **MINED — keep** | `styleagg.py` → `stylemap.json` `sections` block | Per-section cap/stroke/underline pixel stats that drive synthesis | costco: 13 section keys; vons: 9 |
| MINED-but-point-estimate | `merchant_synthesis.py:4673` `_build_item_catalog` | Item catalog grouped by (category, product_text, taxable), price = `_median_money` | Single median price → every synthetic "AVOCADO" same price |
| **GUESSED** section roles | `stylescan.py:55-313` | 7 `_<SLUG>_RULES` regex blocks + `SECTION_TOKENS` (L39-54) + `_BARCODE_RE` | `total_line`: `^Total:` (sprouts) vs `^\**\s*TOTAL\b` (costco) vs `^\**\s*BALANCE\b\|^TOTAL\b` (vons) — same concept, 3 hand-tuned regexes |
| **GUESSED** payment | `stylescan.py` CVS block | Card-network spellings | `AID:\|TVR\|TSI\|CVM:\|TC:\|REF#\|MASTERCARD\|VISA\|DEBIT\|PIN VERIFIED` |
| **GUESSED** layout constants | `receipt_grid.py` | Global grid knobs fit to 9 merchants | `_AMOUNT_LANE_TOL_CELLS=4`, `_AMOUNT_LANE_SNAP_CELLS=2`, `_ROW_OVERLAP_FRAC=0.35`, `_RIGHT_SEGMENT_GAP_CELLS=4.0`, line-pitch fudge `font_px*1.05` / `*1.35` |
| **GUESSED** section regexes | `receipt_stylemap.py` | Per-merchant `_INNOUT_RULES`, `_RULES`, `_MERCHANT_RULES` | `BLVD\|AVE\|,\s*CA\s+\d{5}`, `MON-SUN\|7AM-10PM`, `sprouts\.com` |
| **GUESSED** section keywords | `render_synthetic_receipts.py:800-874` `_text_section()` | Merchant-specific token dumps | footer: `SPROUTSFEEDBACK`,`EMAILATSPROUTS`,`WEEKLYAD`; price via `_PRICE_TOKEN_RE` (L208), *not* the LINE_TOTAL label |
| **HAND-CODED** tax | `merchant_tax_config.py` | Per-merchant rates + flags | Vons/Sprouts/Amazon Fresh `validated_rate=0.0725` flag `T`; Target NV 8.375% / CA ~9.5%; Costco/Home Depot flag `A`, `can_support_taxable_edits=False` |
| **HAND-CODED** content | `online_catalogs/*.json` + `_MERCHANT_ONLINE_CATALOG` | Typed items | `SPROUTS CAGE FREE EGGS 12CT` $4.19 UPC 00646670513046; Home Depot `FEIT 100W ST19 AMBER LED 4PK` $28.97 — **5 of 222 merchants** |
| **HAND-CODED** categories | `sprouts_parameterization.py` | `SPROUTS_CATEGORY_HEADINGS` | BAKERY/BEER/BULK/DAIRY/PRODUCE… |
| **HAND-CODED** merchant identity | `scripts/merchant_profiles.json` | 12 blobs, ~20 knobs each | condense (In-N-Out 0.7 … Trader Joe's 1.033), pitch_ratio (TJ 0.499 … CVS 0.551), cap ratios, section_scale, logo_anchor phrases, barcode symbology |
| **HAND-DECLARED** POS family | `build_merchant_glyphs.py:445,340` | Font pooling via `;`-split string | `Target;Smith's;Vons -> bitMatrix-C1`; human must already know the family |

The pattern is uniform: the **MINED** rows (pitch, stylemap, `price_column_x`) are the good part — real statistics fitted from data. The **GUESSED / HAND-CODED** rows all re-derive, per merchant, structure the labels already encode.

## The reframe: model the generative process

Model the three layers explicitly, each with the tool that fits it. The label taxonomy is the shared interface across all of them.

**Layer 1 — CONTENT ← ML / generative.** `P(transaction | merchant, category)` sampled from labeled `PRODUCT_NAME`/`UNIT_PRICE`/`LINE_TOTAL`/`QUANTITY`/tax-flag words. Item-name grammar via char-n-gram/embedding clusters over `PRODUCT_NAME` (brand prefixes `G&G`/`SPROUTS ORG`, size suffixes `12CT`/`1GAL`/`8OZ`); price/quantity as fitted distributions, not medians; totals kept as **exact arithmetic constraints** (`subtotal=Σline_total`, `tax=rate·taxable_subtotal`, `total=subtotal+tax`) — this is what makes labels correct *by construction*. A small merchant-conditioned generative model earns its keep only on genuinely novel names and structural edge cases (doubled-scan, coupons, weighted PLU rows).

**Layer 2 — LAYOUT ← statistics.** A per-merchant `LayoutPrior` fitted by plain statistics over labeled OCR boxes: k price-column centers (mean+stddev from clustered `LINE_TOTAL`/`UNIT_PRICE`/`SUBTOTAL` right-edges), inter-word gap distribution, section y-bands from label positions, glyph geometry with variance. Wire it into `build_grid_spec`/`plan_grid_line` so `grid_left` and the amount lane come from the fitted `price_column_x` (already computed, currently thrown away) and tolerances come from fitted stddev — replacing `_AMOUNT_LANE_TOL_CELLS=4` / `_SNAP_CELLS=2`.

**Layer 3 — STRUCTURE ← labels.** Section role for a visual line is a line-level aggregation of its words' `CORE_LABELS`: `GRAND_TOTAL→total_line`; `SUBTOTAL|TAX|CHANGE|CASH_BACK→summary`; `PAYMENT_METHOD→payment`; `PRODUCT_NAME|QUANTITY|UNIT_PRICE|LINE_TOTAL→item`; `MERCHANT_NAME|ADDRESS_LINE|PHONE_NUMBER|WEBSITE|STORE_HOURS→header`; `DISCOUNT|COUPON|LOYALTY_ID→savings`. ~8 of ~11 roles recover directly from labels; only footer/survey/separator/barcode_caption stay as 2-3 *shared, merchant-invariant* geometric heuristics. One shared POS-family grammar (`header → [section_header? items]* → totals → payment → footer`) fits all merchants because the role table is label-driven and merchant-invariant.

**Merchant ← embedding.** Replace the 12 blobs with a low-dim style embedding inferred few-shot: typography (font-family id, condense, pitch_ratio, cap_ratio, stroke), layout (header shrink, price-column-present, display-heading scale), graphics (barcode footer + symbology, logo anchor). Most dims are directly measurable from labels (header shrink = median cap-height of `MERCHANT_NAME`/`ADDRESS` lines vs body; price-column-right = median `LINE_TOTAL` right edge). Learn a POS-family latent (Kroger: Smith's/Kroger/Fry's; Safeway/Albertsons: Vons/Safeway; restaurant Toast/Square/Clover/NCR) so a thin-corpus merchant shrinks toward its cluster centroid — generalizing the `;`-pooling from the font dimension to all style dimensions.

## The flywheel

The label taxonomy is the shared interface between the two legs:

**better labels → better structure → novel synthesis → better labels.**

The labeling leg curates `CORE_LABELS` per word (`ReceiptWordLabel` with `validation_status`/`label_proposed_by`; `label_evaluator` validates against the taxonomy at `graph.py:633`). The synthesis leg consumes those labels to recover structure, sample content, and render supervised boxes — then emits novel, correctly-labeled receipts that feed the label model (the other agent's leg measures how much that helps). Role/layout/content accuracy is *bounded by* label quality: lines with unlabeled words fall through. That bound is the flywheel, not a bug — improving labels is how synthesis improves, and vice versa.

## Goals

- Delete the per-merchant section regex families (`stylescan.py` `_<SLUG>_RULES`, `receipt_stylemap.py` `_INNOUT_RULES`/`_RULES`, `_text_section()` keyword lists) in favor of one label-driven role aggregation + one shared grammar.
- Turn discarded fitted priors (`price_column_x`) into the source of truth for layout; replace hand-guessed constants with fitted distributions.
- Turn content synthesis from transplant/re-skin into sampling from learned `P(transaction | merchant)` with totals correct by construction.
- Replace the 12 hand-authored merchant blobs with a few-shot style embedding + learned POS-family clusters; new merchant onboarded from ~5 labeled receipts, zero hand config.

## Non-goals

- **Render-knob calibration (v1 / PR #1057):** weight, cap-ratio, `bitmap_thin=0.0`, `bitmap_cap_ratio=0.66`. Do not re-derive.
- **Glyph tracing / per-glyph atlas work** (glyph-studio font pipeline).
- **Training-value measurement** — the other agent owns whether synthetic receipts move the label model.
- The legitimate render mechanics stay: fixed-pitch monospace snap (thermal printers *are* fixed-pitch), de-glue cursor advance, truncation-to-fit, reverse-video boxes.

## Milestones (cheapest-highest-leverage first)

**M1 — Kill the per-merchant section regexes (biggest hand-coding liability, cleanest win).** Add `_classify_from_labels(line_words)` beside `_classify` in `stylescan.py` using the ~20-line shared `CORE_LABELS`→role table. Run it on the 7 already-scanned merchants (sprouts 173, costco 38, vons 30, tj 20, cvs 19, innout 15, target N) and diff against the existing regex `section` per line — a free labeled-vs-regex agreement audit, no new data. Where labels win or tie, delete the merchant's `_<SLUG>_RULES` block and the `ADD_MERCHANT.md` step-6 regex instruction; keep only footer/separator/barcode as shared geometric fallbacks. Formalize the near-invariant skeleton as one grammar (extend `SectionType`, `constants.py:82-88`) and have `styleagg` pool per-role across merchants keyed by POS-family.

**M2 — Statistical layout.** Stop discarding `MerchantFontProfile.price_column_x` — feed it into `amount_lane` and `grid_left` so the lane is a merchant prior, not a per-receipt recompute. Replace `_AMOUNT_LANE_TOL_CELLS`/`_SNAP_CELLS` and row-overlap constants with per-merchant fitted stddev + gap distribution (extend `font_profile.py` to emit variance). Derive section y-bands from labels, deleting `receipt_stylemap`'s `_INNOUT_RULES`/`_RULES` and `SECTION_LABELS` positional hacks. Regression gate: `layout_score.py` `amount_col_cv` / `token_overlap_count`.

**M3 — Generative content.** Swap `_median_money` in `_build_item_catalog` for sampled price/quantity distributions + a per-category name-cluster sampler — turning `compose()` from transplant into sample, reusing totals-constraint and clone-geometry plumbing unchanged. Learn category-sequence + item-count (reuse `_summarize_categories` heading_counts) to sample scaffolds structurally instead of `deepcopy`-ing one real receipt (kills the re-skin). Auto-derive per-merchant tax flags/rate and category vocab from labels, replacing hand-coded `merchant_tax_config.py` + `SPROUTS_CATEGORY_HEADINGS`. Finally, a small merchant-conditioned generative name/format model for the long tail and deliberate edge cases (doubled-scan, coupon, weighted rows), rendered on the grid with render-true boxes.

**M4 — Merchant embedding + long-tail scale.** Auto-measure the continuous knobs (condense, pitch_ratio, cap/height ratios, section_scale, price-column-right) directly from each merchant's labeled receipts to auto-fill the profile. Cluster style vectors into POS families and back off thin-corpus merchants to the cluster centroid — generalizing `build_merchant_glyphs`' `;`-pooling from font-only to all style dims. New-merchant path: ~5 labeled receipts → nearest-cluster prior + light few-shot fit, no `ADD_MERCHANT` session. Scale across the 222-merchant tail.

## Risks & mitigations

- **Un-labeled roles.** Footer boilerplate, survey/marketing, separators, barcode captions (~3-4 of ~11 roles) carry no word label. Mitigation: small *shared* geometric heuristics (position-in-receipt, separator glyph run, numeric-only barcode) — merchant-invariant, not per-merchant. Some merchant-invented roles (`self_checkout`, `member`, `items_sold`, `points`, `date_box`) have no `CORE_LABEL` and collapse into header/summary unless the taxonomy is extended. M1 audit quantifies agreement *before* any regex block is deleted.
- **Sparse/noisy labels on thin-corpus merchants** destabilize measured dims. Mitigation: category → corpus backoff (mirrors the existing `min_support=2` / lone-price fallback) and cluster-centroid shrinkage — but shrinkage assumes correct POS-family assignment; mis-clustering a Kroger banner as Safeway imports wrong priors, so clusters must be *learned and validated*, not hand-declared.
- **Multi-column receipts** (weight+unit+total) need k-column fitting, not a single lane.
- **Hallucinated content.** Learned name sampling can emit non-existent SKUs; constrain to learned token grammars and validate against catalog groundings. Auto-derived tax flags can mis-class where per-item flag OCR is sparse (the exact reason Costco/Home Depot are gated `can_support_taxable_edits=False`) — keep the receipt-level rate-reconciliation guard.
- **Do not relax arithmetic/structure gates.** Totals stay exact by construction; a wrong learned pitch reintroduces the "item after SUBTOTAL" defect class the ordered `compose()` currently prevents. `font_profile` is DATA only.
- **Thin basis.** Only 9 labeled merchants exist — a thin basis for a 222-way cluster structure. Keep the embedding low-dim to avoid overfitting the seen fleet.

## Success criteria

- **No per-merchant regex rule-sets:** `stylescan.py` `_<SLUG>_RULES`, `receipt_stylemap.py` per-merchant regexes, and `_text_section()` keyword lists are deleted; section role comes from labels + one shared grammar.
- **New merchant inferred few-shot, not configured:** ~5 labeled receipts → layout prior + style embedding + nearest-cluster backoff, with zero hand-written regex, zero hand-authored profile blob, zero `ADD_MERCHANT` session.
- **Synthesis produces novel, correctly-labeled receipts:** items/prices/formats not present in any single real scaffold (not transplanted rows, not 12-item hand JSON), with totals and labels correct by construction.
- **Path to the 222-merchant long tail:** POS-family clusters let a thin-corpus merchant borrow neighbors' priors; onboarding cost is O(1) labeled receipts, not O(1) engineering session — the 9→222 gap closes by labeling, not by hand-tuning.
