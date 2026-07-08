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

### 4.2 Sections as labels — the `SECTION_*` namespace

Persist per-word sections as `ReceiptWordLabel` rows with a `SECTION_` prefix
(e.g. `SECTION_TOTAL_LINE`), exactly like word labels. Verified:

- `create_word_label_impl` does **not** validate against `CORE_LABELS` (it
  uppercases and writes), so the namespace is writable today.
- Tools that iterate `for label in CORE_LABELS` ignore the new namespace — no
  breakage, it is parallel.
- We inherit the entire validation-status workflow (`PENDING → VALID /
  INVALID`), MCP QA tooling (`list_words_by_label`,
  `label_validation_summary`, `update_word_label`), and label-distribution
  reporting for free.
- **One small write-path change needed:** the MCP create path hardcodes
  `validation_status="VALID"`. Machine-propagated sections must land as
  `PENDING` (promoted by QA), so add a `validation_status` parameter or write
  propagated rows via `add_receipt_word_label` directly. Dynamo writes require
  explicit approval per repo policy.
- The section labels are **shared ground truth** — the LayoutLM leg consumes
  the same rows. Labeling sections once serves both epics.

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
- `create_word_label` / `update_word_label` — write/correct `SECTION_*` rows.
- `list_words_by_label` / `label_validation_summary` — QA sampling and
  coverage tracking per section.
- Section QA becomes the same human-in-the-loop review motion already used for
  word labels.

## 5. Pipeline (five stages)

```
S0 seed        CORE_LABELS → section projection (GRAND_TOTAL ⇒ total_line, PRODUCT_NAME ⇒ items, …)
               + stylescan rules where they exist (Costco/Vons/TJ)
S1 propagate   word-context Chroma NN → section posterior for every word
               → write SECTION_* labels as PENDING → MCP QA sample → VALID
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

- **M0 — Section-label infrastructure.** `SECTION_*` write path (with
  `PENDING` support), seed projection from CORE_LABELS, stylescan rules
  extended to all 9 merchants. *Exit:* every labeled word has a section row;
  seed coverage/accuracy report per merchant.
- **M1 — Propagation + QA.** Word-context Chroma collection; NN propagation;
  QA loop via MCP. *Exit:* ≥95 % of words carry a section with measured
  accuracy ≥90 % on a held-out hand-checked sample; per-section
  distribution sane on all 9 merchants.
- **M2 — Family/face discovery.** Glyph-crop collection; clustering. *Exit:*
  the `(merchant, section) → (family, face)` map with confidence scores;
  cluster count and IoU-style separation reported; Costco isolates as its own
  family (known-answer check).
- **M3 — Pooled family font (Font A pilot).** Hierarchical skeleton fit for
  the largest family; compile regular + bold faces. *Exit:* diagonal-glyph A/B
  beats every constituent merchant's current atlas; passes the anti-copy gate
  and the 94/94 coverage gate.
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

## 7. Risks & mitigations

- **Section propagation accuracy.** Wrong sections poison face grouping.
  → Labels land `PENDING` until QA'd; M1 has a hard measured-accuracy gate;
  clustering (M2) weights by section confidence.
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
- **Dynamo write volume.** Dense `SECTION_*` rows are a large additive write.
  → Batch, additive-only, explicit approval before the first write; dev first,
  promotion via the existing dev→prod mirror.

## 8. Decision log (structure locked here)

| decision | choice | why |
|---|---|---|
| Unit of analysis | `(merchant, section) → (family, face)` | section is the strongest face prior; fixes multi-face rendering by construction |
| Section vocabulary | stylescan's 10 sections | receipt-native, rules already exist for 3 merchants |
| Section persistence | `SECTION_*` ReceiptWordLabel rows | zero schema change (verified), full QA workflow free, shared with LayoutLM leg |
| Propagation | Chroma NN from labeled seeds | infra proven by validate_word_similarity; turns sparse seed into dense coverage |
| Crop embedding | deterministic pixel+geometry features first | no new API deps, reproducible; upgrade only if clusters demand |
| Font model | family skeleton + face transform + merchant offset | stroke-space pooling is the diagonal fix; offsets keep printer character |
| Calibration | v1 `calibrate_merchant` unchanged, downstream | v1 composes; railed-pin disappearance is the epic's acceptance test |
