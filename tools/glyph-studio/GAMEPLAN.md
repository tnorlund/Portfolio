# Game plan v2 — font epics after the density saga

**Audience:** the next implementing agents/workflows and Tyler. Read this +
`FONT_INTELLIGENCE_EPIC.md` + `M3_FINDINGS.md` + the `font-epics-state` memory
and you have the full picture. **v2 (2026-07-10)** — rewritten after three
discoveries invalidated parts of v1 of this plan: the pooling refutation, the
density-saga closure, and the glyph-quality loop. v1's text is in git history.

---

## 0. What changed since v1 of this plan

1. **The density crisis never existed.** The epic's acceptance test ("un-rail
   Wild Fork/Costco") was chasing a measurement artifact: contaminated OCR
   (duplicate-position word groups → the renderer overprints → density
   inflated) sat first in the merchant index and poisoned every first-N
   calibration sample, v1's included. On vetted receipts the fleet is
   healthy: **all pins stand** (WF median 0.94 — slightly *light*; Costco 0.75
   light/high-variance — the opposite of the "too dense" story). 6/10 merchant
   sample windows were contaminated (HD 17/18!). Consequences: single-receipt
   calibration is dead — vetted-distribution measurement is the standard;
   #1085 carries the vetting + findings corrections.
2. **Pixel-space cross-merchant pooling is refuted** (#1083): pooled atlases
   come out denser AND blurrier — printer offset is signal, not noise.
   Diagonals improve within-merchant with more samples, not across merchants.
   Standing harnesses: `m3_acceptance.py` (solo-vs-candidate, the exit test
   for any future mint) + `m3_crispness.py`.
3. **The glyph-quality loop is the real "fonts look better" engine, and it's
   proven.** Fleet-referenced identity gate (`glyph_gate.py`, #1088) ranks
   broken glyphs → an agent handcrafts skeleton fixes → gate re-verifies →
   render confirms. Ran on Home Depot: 23 glyphs fixed across two batches
   (including a bar-shaped 'A' and a '.'-as-hook that corrupted price
   decimals), gate findings 24→10 (residual = known FP noise floor), final
   render h 1.000 / density 0.930. Known gate FP classes: 1-hole box letters
   vs O; bar-glyph l/!/1/I confusion; slant-style divergence.
4. **Lines pipeline revived** (status-drift reset; #1086 ingest isolation):
   1,676 LINE_EMBEDDING batches at OpenAI, **not yet ingested** — which means
   the sequencing windfall is still winnable (next section).

## 1. P0 — protect the windfall (time-critical, strict order)

Lines have never been embedded. If the section wiring lands before ingest,
every line embedding carries `section_label` on first pass — no upsert sweep,
ever. All 1,676 batches are still PENDING at OpenAI as of this writing. **Do
not start `line-ingest-sf-dev` until steps 1–3 are done:**

```
1. Merge #1089 (M1a-2 ingest wiring)           ← open, review + land
2. Targeted dev deploy (embedding+compaction only — the 28-change recipe in
   font-epics-state; avoids the auto-mode blast-radius block and the full
   133-change deploy)
3. [Tyler gate] -v1 propagation write: run section_propagate over the words
   snapshot → per-line majority → ReceiptSection rows,
   model_source="section-propagate-v1", PENDING, additive (seeds cover only
   ~seeded lines; propagation makes line coverage dense BEFORE stamping)
4. Start line-ingest-sf-dev → lines snapshot populates WITH sections
5. Regenerate crop corpus (build_merchant_glyphs — also runs locally from
   receipt_letters + S3; not actually gated on the pipeline)
```

## 2. Merge queue

- **#1089** — P0 step 1 above.
- **#1085** — OCR vetting in `m3_acceptance.gather` + M3_FINDINGS correction
  (the "WF was never broken" record). Land it before anyone calibrates again.
- **#1088** — the glyph gate (the engine for W-A below).

## 3. Workstreams (post-P0), in value order

**W-A — fleet glyph-quality sweep (the new headline).** The proven loop has
run on ONE merchant. Run the gate across all 9, triage each merchant's queue
(respecting the documented FP classes), agent-handcraft the real breaks, gate
re-verify, render-confirm. Workflow-shaped: fan out per merchant, adversarial
verify = the gate itself + a render. Exit: every merchant's gate report at its
noise floor; before/after renders per fixed merchant. This — not density work —
is now the primary "fonts look better" deliverable.

**W-B — M4 multi-face rendering.** Unblocked (consumes the M2 map), unowned.
Renderer reads (merchant, section)→(family, face); per-word face stamping;
production scorecard gates. Note the M2 map is provisional (refined-font
families, outlier flip unresolved) — but M4's face switching doesn't depend on
family correctness, only on the face column, which came from stylemap
measurements.

**W-C — contamination detector productization (cross-epic).** The dup-OCR
vetting (same-row words with >30% x-interval overlap) currently lives in one
harness. It should gate every consumer of receipt samples: calibration gathers
(#1085 does this), QA sampling, scorecard review picks, and — highest stakes —
the **LayoutLM training-data export** (HD is 17/18 contaminated; training on
overprinted geometry teaches the model garbage). Investigate the HD anomaly
first: 17/18 may partly be the detector misreading HD's column layout.
Deliverable: a shared `vet_receipt_ocr()` helper + a per-merchant data-quality
report + adoption in the training exporter.

**W-D — seed QA + label audit (unchanged from v1, still undone).** Stratified
QA of the 3,128 v0 seeds (heavy in the 1,181-row 0.60 rule-only stratum +
SECTION_HEADER/TOTAL_LINE/BARCODE), confidence recalibration from measured
precision, then the #1066 label↔section audit. Unblocked today; pairs
naturally with -v1 rows once written.

**W-E — small wins & experiments.**
- WF mild recenter: derive thin on the vetted corpus (0.6→~0.3 candidate) —
  visual A/B before adoption, per the epic rule (and the A/B canvas rule:
  vet the receipt's OCR first, match canvas heights).
- Costco: light + high variance on vetted receipts — monitor, no change.
- SDF-consensus mint: promising, *unfairly tested* (raw stacks, no inlier
  filter) — rerun fairly; if it holds, it's the continuous-space v2 mint and
  may address diagonals + cross-merchant crispness where pooling failed.
- Height follow-ups: WF height 1.066 (base-cap floor, same mechanism as
  Vons/TJ) — separate knob work, low priority.

## 4. Tyler's gates

1. **-v1 propagation write** (P0 step 3) — additive, PENDING, dev.
2. W-D promote/flag batches (label-table NEEDS_REVIEW writes).
3. Any font/profile adoption (always render A/B on vetted receipts).
4. Eventually: prod line embed + sections promotion (rides the existing
   dev→prod mirror).

## 5. Measurement rules (learned the hard way — bind all future work)

1. **Vet OCR before measuring.** Dup-position words → overprint → junk
   density/layout numbers. `Counter((text, round(y,3)))>1` catches the crude
   case; the x-overlap detector is the real one. Never calibrate, A/B, or
   train on an unvetted receipt.
2. **Distributions, not single receipts.** Same font spans 0.94↔1.48
   density_ratio across receipts of one merchant. Any claim from n=1 is void.
3. **Match canvas heights in visual A/Bs** — zoom-crop scale mismatch reads
   as a rendering bug but is a viewing artifact.
4. **Cheap-measurer numbers are relative** (stable per-merchant factor):
   valid for solo-vs-candidate compares, never absolute targets.
5. **Run `m3_acceptance.py --min-coverage` + `m3_crispness.py` on every mint
   candidate** — they are the standing exit tests.
6. Landmines from v1 all still apply (pulumi lock/CI contention, chromadb
   1.5.x snapshot venv, JSON-only policy, anti-copy gate, labels-win,
   model_source versioning, case convention, no `s3 sync --delete`, Dynamo
   writes gated, coordinate across concurrent sessions).

## 6. Kickoff prompts

**P0 (critical path, one session):**
> Read GAMEPLAN.md §1. Review+merge #1089, run the targeted dev deploy
> (recipe in font-epics-state memory), ask Tyler to approve the -v1
> propagation write, run it, THEN start line-ingest-sf-dev and watch it
> populate the lines snapshot with section_label. Then regenerate the crop
> corpus locally. Do not start ingest before the wiring is deployed.

**W-A (glyph fleet sweep, workflow):**
> Read GAMEPLAN.md §3 W-A and PR #1088. For each of the 9 merchants: run
> glyph_gate, triage the queue against the documented FP classes, handcraft
> real breaks (skeleton JSON only, anti-copy gate), gate re-verify, render a
> vetted receipt before/after. Fan out per merchant; verify adversarially.
> HD is done — use it as the reference for what 'at noise floor' looks like.

**W-C (data quality, cross-epic):**
> Read GAMEPLAN.md §3 W-C and #1085. Extract the dup-OCR vetting into a
> shared helper, produce a per-merchant contamination report (investigate
> HD's 17/18 first — detector vs column layout), and wire vetting into the
> LayoutLM training-data export path. Coordinate with the LayoutLM leg.
