# Font Intelligence Epic — learned font families, section-conditioned faces

**Status:** proposed (structure locked; implementation not started)
**Depends on:** font-calibration v1 (PR #1059/#1060/#1061 — composes with it, does not replace it)
**Siblings:** SYNTHESIS_V2_EPIC (receipt *structure*; this epic is the *fonts* leg — they share the section-label infrastructure defined here)

---

## 1. Problem — three failures of the per-merchant median-vote atlas

Today every merchant gets one bitmap atlas, built by pixel-median-voting that
merchant's own letter crops (`synthesis_loop/build_merchant_glyphs.py`). This
fails in three structural ways that no amount of calibration can fix:

1. **Mushy diagonals — the fonts don't look good.** Pixel-averaging jittery
   thermal prints shreds thin diagonal strokes (`H K M N V W X Y`, `v w x y`)
   and complex lowercase (`g h i p q`). You cannot average your way to a crisp
   1-px diagonal, and each merchant has only its own noisy crops as evidence.
2. **One atlas per receipt full of faces — the renderer struggles with
   different fonts within a receipt.** A real receipt is never one font: a
   regular Font-A body, **bold** totals, sometimes a condensed Font-B header,
   double-height amounts, reverse-video blocks. A single monolithic atlas
   smears all of these into one face, then the renderer guesses.
3. **No cross-merchant knowledge.** Each merchant re-learns the same underlying
   thermal typeface from scratch. Evidence is fragmented ~9 ways when most of
   it describes the *same* letterforms.

The calibration epic (v1) made the render *knobs* derived-not-eyeballed, and in
doing so exposed the ceiling: **Wild Fork rails at bitmap_thin 0.6 and still
renders ~48 % too dense; Costco rails at the 0.0 floor.** Those are atlas
quality problems. v1 finds the best settings for a given atlas; this epic makes
the atlases themselves good.

## 2. Evidence (measured, not asserted)

- **Merchants share fonts.** Shape-normalized glyph IoU across the six M0
  atlases (31 shared letterforms, size-invariant):
  CVS↔Vons **0.73**, Vons↔Trader Joe's **0.71**, Sprouts↔Wild Fork **0.70**,
  Trader Joe's↔Wild Fork **0.70** — versus Costco at 0.53–0.59 to everyone
  (genuinely different: the chart-derived bitMatrix face). Same-font pairs sit
  ~0.7; different-font pairs ~0.55. The corpus is printing **~2–4 typeface
  families, not 9+ unique fonts.**
- **The measurement infrastructure is proven.** v1's render-free measurer
  tracks the production scorecard's density by a stable per-merchant factor
  (Sprouts: 1.967 ± 0.007, CV 0.37 %, Spearman 1.0) and recovered the
  scorecard's density optimum *better* than the render-time bisection
  (VALIDATION.md). Fast, trustworthy glyph-space measurement exists.
- **Faces are already detectable.** The atlas pipeline already separates
  body/bold by ink density (`test_atlas_separates_body_and_bold_weight`), and
  `stylescan.py` already classifies receipt sections by rule for
  Costco/Vons/TJ. Both are crude versions of what this epic generalizes.

## 3. The core reframe

**Change the unit of analysis from `merchant → font` to
`(merchant, section) → (family, face)`.**

Section position is a strong prior on face: header → display/large; body
line-items → regular workhorse; totals → bold / double-height; footer → small.
"Where the word sits" is not extra context bolted onto the font model — it is
the index the font model should be organized around. The generative model:

```
glyph_crop = family_skeleton(char)          # shared across merchants — the win
           ∘ face_transform                 # regular | bold | condensed | scale
           + merchant_printer_offset        # small per-merchant deformation
           + thermal_noise                  # what median-voting was fighting
```

Fitting `family_skeleton` on **pooled crops across every merchant and section
in a family** is the statistical fix for mushy diagonals: averaging happens in
*stroke space* (Glyph Studio's parametric skeletons), not pixel space, with
~10× the evidence per glyph.

## 4. Data model & infrastructure (verified against the codebase)

### 4.1 Section vocabulary — adopt stylescan's

`storefront / address / items / section_header / summary / total_line /
payment / survey / footer / barcode` (stylescan.py). Receipt-native, already
has hand-written rules for 3 merchants; richer than the CORE_LABELS grouping.

### 4.2 Sections as `ReceiptSection` rows

Persist sections as **`ReceiptSection`** rows — line-level, one row per
`(receipt, section)` keyed `SK = RECEIPT#{id:05d}#SECTION#{TYPE}` holding that
section's `line_ids`. Words inherit their line's section. This keeps the
`ReceiptWordLabel` table a **clean training corpus** — sections are receipt
*structure*, not word labels. (Earlier draft persisted `SECTION_*`
`ReceiptWordLabel` rows; rejected — it pollutes the label table.)

- `SectionType` carries the 10 canonical sections. The 4-value 2025-05
  classifier experiment vocab (`HEADER/FOOTER/ITEMS_VALUE/ITEMS_DESCRIPTION`)
  is deprecated; its stale 818-row batch (219 receipts, `model_source` unset)
  was deleted from dev.
- Rows carry `confidence`, `model_source` (versions writes: `section-seed-v0`
  seeds → `-v1` propagation), and `validation_status` (`PENDING` → QA). CRUD
  is fully plumbed (add/update/delete/get/list).
- Machine-seeded rows land `PENDING`; QA promotes them. Writes are additive
  (conditional add skips existing `(receipt, section)` rows) and require
  explicit approval per repo policy; dev first.
- **Shared ground truth for Chroma — but the wiring is currently inert.** The
  embedding pipeline already calls `get_receipt_sections_from_receipt` and
  `records.py` has a `section_label` line-metadata hook, but nothing maps
  `section.line_ids` onto `line.section_label` (and `ReceiptLine` has no such
  field), so it always emits `None`. Completing that map is a **deferred M1
  task** so section rows enrich line embeddings. The LayoutLM leg also
  consumes sections — writing them once serves both epics.

### 4.3 ChromaDB — two collections

Existing infra (verified): `receipt_chroma` has words/lines collections,
OpenAI-embedding pipeline, delta/compaction machinery, and this pattern already
powers `validate_word_similarity` / `search_product_lines`.

1. **Word-context collection (section propagation).** Embed each word's
   context (text + geometry: y-position percentile, x-extent, line neighbors,
   char-class profile). Labeled seeds → nearest-neighbor propagation gives
   every word a section posterior, within and across receipts. This is what
   turns a sparse labeled seed into dense per-word coverage.
2. **Glyph-crop collection (family/face clustering).** Embeddings of
   letter crops keyed `(merchant, section, char, crop_id)`. NN queries here
   answer "find this same 'A' across merchants" as a live query. Crop
   joinability is verified: `receipt_letters` entities carry
   `line_id`/`word_id`, so every crop inherits its word's section.

   *Embedding choice:* start with cheap deterministic features (normalized
   pixel grid + stroke-width/aspect/density geometry) — no new API dependency,
   fully reproducible. Upgrade to a learned embedding only if cluster quality
   demands it.

### 4.4 MCP tools in the loop

- `get_receipt_words` / `get_receipt_image_url` — corpus + pixels.
- `get_receipt_sections_from_receipt` / `add|update|delete_receipt_section` —
  read/write section rows (`update_word_label` is still used, but only by the
  M1 consistency audit to flag inconsistent CORE labels `NEEDS_REVIEW`).
- Section QA reuses the same `validation_status` (`PENDING → VALID`) motion as
  word labels; `list_receipt_sections` samples coverage per section.

## 5. Pipeline (five stages)

```
S0 seed        CORE_LABELS → section projection (GRAND_TOTAL ⇒ total_line, PRODUCT_NAME ⇒ items, …)
               + stylescan rules where they exist (Costco/Vons/TJ)
S1 propagate   word-context Chroma NN → section posterior for every word
               → write ReceiptSection rows as PENDING → QA sample → VALID
S1' audit      cross-tab CORE label × consensus section → flag inconsistent
               word labels NEEDS_REVIEW (update_word_label); report per-label
               violation rate
S2 cluster     glyph-crop Chroma collection → family/face clustering
               → the (merchant, section) → (family, face) map, with confidence
S3 fit         pooled stroke-skeleton fit per (family, face):
               family mean skeleton + per-merchant offset (hierarchical)
               → compile per-face .glyphs.npz via existing Glyph Studio toolchain
S4 render      renderer consumes the map: per-word face selection
               (section → face, geometry override for in-section emphasis)
               → v1 calibrate_merchant re-derives knobs on the new fonts
```

v1 is the last stage's tool, not a casualty: family fonts still need
per-merchant `weight/thin/cap-ratio`, and `calibrate_merchant` +
`validate_atlas` are exactly the instruments (with the anti-copy publish gate
unchanged — family fonts are *fitted skeletons*, never copied crops).

## 6. Milestones

- **M0 — Section infrastructure.** `ReceiptSection` write path (canonical
  `SectionType` + `confidence`/`model_source`/`validation_status`, `PENDING`
  support), seed projection from CORE_LABELS, stylescan rules extended to all
  9 merchants. *Exit:* every labeled word has a section row;
  seed coverage/accuracy report per merchant.
- **M1 — Propagation + QA.** Word-context Chroma collection; NN propagation;
  QA loop via MCP. *Exit:* ≥95 % of words carry a section with measured
  accuracy ≥90 % on a held-out hand-checked sample; per-section
  distribution sane on all 9 merchants.
  - **M1 deliverable — label-section consistency audit (label cleanup).**
    Sections audit the labels back: after propagation, cross-tab every
    labeled word's CORE label against its consensus section; violations of
    the label's expected section footprint (LINE_TOTAL in `payment`,
    PRODUCT_NAME in `footer`, GRAND_TOTAL outside `total_line`/`summary`,
    the CHANGE/CASH_BACK-as-LINE_TOTAL class) are marked `NEEDS_REVIEW` via
    `update_word_label`. Not circular: a word's consensus section comes from
    its neighbors' geometry/context, not its own label, so a mislabeled word
    disagrees with its surroundings — that disagreement is the flag.
    *Exit adds:* per-label violation rates reported; flagged words land in
    the standard `NEEDS_REVIEW` QA queue. (Also cleans the LayoutLM leg's
    training labels — shared payoff.)
- **M2 — Family/face discovery.** Glyph-crop collection; clustering. *Exit:*
  the `(merchant, section) → (family, face)` map with confidence scores;
  cluster count and IoU-style separation reported; Costco isolates as its own
  family (known-answer check).
- **M3 — Pooled family font (Font A pilot). ⚠ AMENDED — see M3_FINDINGS.md.**
  Measured 2026-07-09: pixel-space cross-merchant pooling **fails both epic
  criteria** — the pooled railed-family atlas is *denser* than the solo
  atlases (WF 0.335→0.404, Costco 0.308→0.402 vs targets 0.135/0.187; both
  still ceiling-railed) and every hard diagonal's consensus *blurs* (W −0.163
  … M −0.043) despite 2–17× the samples. Root cause: the merchant printer
  offset is signal, not noise — median-voting across letterforms adds edge
  ink. Pooling survives only for **coverage** (+5 glyphs, rare-glyph recovery,
  M6 cold start). Revised M3: (a) **density-calibrated minting per merchant**
  (derive the mint's vote/erosion against the measured real density with the
  v1 cheap measurer — "re-mint lighter", derived instead of eyeballed);
  (b) diagonals via **family-level handcraft** (one skeleton set per family,
  reused across members). *Exit (unchanged in spirit):* the railed merchants
  come off the rails on the standing harness `py/m3_acceptance.py`; anti-copy
  + coverage gates hold. **Validated same day** (M3_FINDINGS.md addendum):
  skeleton-protected stroke normalization un-rails BOTH railed merchants (WF
  interior thin 0.333, density 0.137 vs 0.135; Costco interior 0.333, 0.200
  vs 0.187; full coverage), and the derived parametric weight (WF `1.4 →
  0.60`, closed-form from stylescan stroke/cap) lands 0.99× target in one
  step. Root causes differ (WF stroke-width, Costco shape-driven) — the
  density-targeted bisection is the general form. Visual A/B still required
  before adoption.
- **M4 — Multi-face rendering.** Renderer reads the map; per-word face
  stamping. *Exit:* a Costco receipt renders bold totals + regular body from
  family fonts and the production scorecard is in-gate (h/wpc/density within
  v1 thresholds).
- **M5 — Retrofit + regression lock.** All 9 merchants moved to family fonts;
  `calibrate_merchant` re-derives all knobs; the railed merchants (Wild Fork,
  Costco) verified un-railed (this is the epic's acceptance test — v1's
  clamp-railing disappears when the atlas is right). Regression test asserts
  every merchant's scorecard stays in-gate.
- **M6 (stretch) — New-merchant cold start.** A new merchant = classify its
  sections, match its crops to existing families, derive knobs. *Exit:* a new
  merchant reaches an in-gate scorecard with no hand-drawn glyphs and no
  hand-tuned constants.

## 6a. Downstream consumers of the section layer

The section layer is pipeline infrastructure, not a font-only artifact. Build
constraint that follows: **the M1 propagator must be callable per-receipt at
runtime (a function taking one receipt's words → section posteriors), not only
as a batch backfill script** — every consumer below except training calls it
at upload/interaction time.

Surveyed hooks (verified against the code where noted):

| consumer | today's pain | section hook | unlocked by |
|---|---|---|---|
| Upload labeling (LayoutLM + LLM refine) | product labels confused by look-alikes anywhere on the page (footer promos as PRODUCT_NAME, payment amounts as LINE_TOTAL) | sections first, then label within section — items-only candidates for PRODUCT_NAME/QTY/UNIT_PRICE/LINE_TOTAL | M1 |
| Label cleanup | historical mislabels invisible until they bite training | the M1 label-section consistency audit (already a deliverable) | M1 |
| Product memoization | every upload relabels "ORG BAN 4011" from scratch | items-section words NN-match prior VALIDATED PRODUCT_NAME + MerchantCatalogItem for that merchant; novel items only → review | M1 |
| `search_product_lines` (MCP) | *(verified)* queries the whole `lines` collection + regex price fallback — header/footer lines pollute spending analysis | section metadata on the collections → filter to `items` at query time | M1 |
| Re-OCR targeting (`compute_reocr_region`) | *(verified)* caller must already know which line_ids are garbled | "items-section lines with low label confidence" becomes the principled region selector | M1 |
| Merchant resolution backstop | Places failures → null-merchant uploads | storefront-section words NN-match against known merchants' storefront text | M1 |
| Dedupe content grouping | (merchant, date, total) key depends on fragile field extraction | totals/date pulled from their known sections → reliable content keys; items-section fingerprint as a secondary signal | M1 |
| Row grammar / item catalog ingest | name+price on different line_ids matched by y-row, poisoned by header/summary rows | grammar fenced to the items section's rows | M1 |
| LayoutLM training | position is raw x/y only | section as input feature or auxiliary target; plus section-conditioned synthetic data with correct per-section faces | M1 + M4 |
| Barcode handling | symbology varies by merchant, position unused | `barcode` section = prior on where barcodes live per merchant | M1 |
| Scorecard / render QA | one density/height number per receipt | per-section metrics (totals-row density vs body) — sharper gates for multi-face rendering | M4 |
| Synthesis v2 epic (#1058) | layout structure hand-coded | dense section labels are the training signal for learned layout/content generation | M1 (shared) |

Ranked by leverage: upload labeling, product memoization, and the label audit
are the near-term wins (all M1); per-section scorecards land with M4; the
synthesis-v2 handoff is a data dependency, not code.

## 7. Risks & mitigations

- **Section propagation accuracy.** Wrong sections poison face grouping.
  → Labels land `PENDING` until QA'd; M1 has a hard measured-accuracy gate;
  clustering (M2) weights by section confidence. The M1 label↔section
  consistency audit is the reverse check — it uses the propagated sections to
  surface mislabeled words (flagged `NEEDS_REVIEW`), and the per-label
  violation rate is a standing quality metric for both the labels and the
  sections.
- **Families don't separate cleanly** (continuum instead of clusters).
  → The IoU probe already shows separation at atlas level; if crops are
  messier, fall back to fewer/coarser families — even 2 families beat 9
  fragmented atlases. Costco-isolates is the canary.
- **Pooled fit loses merchant character.** Some "noise" is real printer
  identity. → The hierarchical merchant-offset term preserves it; A/B review
  per merchant before adoption (M5), same visual gate as v1.
- **Anti-copy gate.** Family fonts must remain fitted skeletons, never crop
  copies — the existing `_reject_copied_letterforms` gate stays mandatory in
  the publish path.
- **Dynamo write volume.** `ReceiptSection` rows are a large additive write
  (~10 per receipt). → Additive-only (conditional add skips existing), explicit
  approval before the first write; dev first, promotion via the existing
  dev→prod mirror.

## 8. Decision log (structure locked here)

| decision | choice | why |
|---|---|---|
| Unit of analysis | `(merchant, section) → (family, face)` | section is the strongest face prior; fixes multi-face rendering by construction |
| Section vocabulary | stylescan's 10 sections | receipt-native, rules already exist for 3 merchants |
| Section persistence | line-level `ReceiptSection` rows (`confidence`/`model_source`/`validation_status`) | keeps the label table a clean training corpus; purpose-built line-level entity; CRUD plumbed; shared with LayoutLM + Chroma legs |
| Propagation | Chroma NN from labeled seeds | infra proven by validate_word_similarity; turns sparse seed into dense coverage |
| Crop embedding | deterministic pixel+geometry features first | no new API deps, reproducible; upgrade only if clusters demand |
| Font model | family skeleton + face transform + merchant offset | stroke-space pooling is the diagonal fix; offsets keep printer character |
| Calibration | v1 `calibrate_merchant` unchanged, downstream | v1 composes; railed-pin disappearance is the epic's acceptance test |
| Label↔section co-QA | M1 consistency audit flags inconsistent labels `NEEDS_REVIEW`; per-label violation rate is an exit metric | sections and labels are shared ground truth — each QAs the other; propagated sections catch mislabels the original pass missed |
