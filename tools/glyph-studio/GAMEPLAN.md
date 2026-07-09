# Game plan — font epics: where we are, what's proven, what's next

**Audience:** the next implementing agents/workflows and Tyler. This is the
handoff artifact: read this + `FONT_INTELLIGENCE_EPIC.md` + the
`font-epics-state` memory and you have the full picture. Written 2026-07-09,
immediately after M0–M2 merged and while the dev deploy was in flight.

---

## 1. Scoreboard

### Epic 1 — measured font calibration (DERIVATION_EPIC.md): **COMPLETE**

Every render knob (`weight`, `ocr_cap_height_ratio`, `bitmap_thin`) is now
derived, not eyeballed; `bitmap_thin` is pinned for all bitmap-font merchants
(CI-locked); the render-time re-derive (10-min cold renders) is dead. Artifacts:
VALIDATION.md, RETROFIT.md, `glyphstudio/{calibrate,validate}.py`,
`test_merchant_profiles_contract.py`.

### Epic 2 — font intelligence (FONT_INTELLIGENCE_EPIC.md): **front half done**

| milestone | state | evidence |
|---|---|---|
| M0 section infra + seeds | ✅ merged, executed | 3,128 `section-seed-v0` rows / 381 receipts in dev; labels-win policy; invariants verified (label-only merchants have zero rule-only rows) |
| M1a-1 payload plumbing | ✅ merged | section_label flows into row/line metadata when a map is passed; inert until callers wired |
| M1a-2 caller wiring | 📋 spec'd, not shipped | exact 4-path threading map in `font-epics-state` memory (orchestration A, line_polling B, embedding_processor C, ndjson D) |
| M1b propagation | ✅ merged, validated | 89.1% cross-receipt / 96.8%@conf≥0.8 on 97,658 real word embeddings; per-receipt-callable |
| M1b `-v1` write + QA + #1066 audit | ⏳ gated (approval + sequencing) | — |
| M2 family + face map | ✅ merged, **provisional** | cvs~vons reproduced (IoU 0.620); 67-entry (merchant,section)→(family,face) map; built on refined FONTS, not crops |
| M3 pooled family fit | 🚧 blocked on crop corpus | refined skeletons unpoolable (3/36 shared topology) — must re-trace from pooled crops |
| M4 multi-face render, M5 retrofit | 🚧 blocked on M3 + rendered receipts | — |

**Infra state:** prod carries #1070+#1073; dev deploy in flight (Tyler).
**Line embeddings have never run in either env** (snapshots empty post-reset);
words are complete (dev 97,658 / prod done). Crop corpus regenerates only after
line-submit/ingest runs.

## 2. Proven vs. assumed — the honesty ledger

**Proven (evidence-backed, safe to build on):**
- Erosion saturates at thin **0.40** on every real atlas; cap height linear in
  cap_px (VALIDATION.md).
- The cheap measurer tracks the production scorecard by a stable per-merchant
  factor (Sprouts 1.967, CV 0.37%, Spearman 1.0) and found a *better* thin than
  the render bisection.
- Vons/TJ are floor-bound (ratio knob inert); WildFork/Costco density-railed —
  atlas-quality ceilings, the epic-2 acceptance test.
- Seed invariants in dev (single source, PENDING, labels-win, guard whitelist).
- Propagation faithfully extends the seed signal cross-receipt (89.1%).
- CVS+Vons are one typeface family (holds in both atlas and refined-font space).

**Assumed / pending its gate (do NOT treat as truth yet):**
- *Propagated sections are correct.* 89.1% measures reproducibility of the
  seed function, not human truth. Gate: stratified human/agent QA (heavy in
  the 1,181-row 0.60 rule-only stratum + SECTION_HEADER/TOTAL_LINE/BARCODE).
- *The family map.* Built on refined fonts as an interim. The outlier flip
  (atlases: Costco isolates; refined fonts: HomeDepot isolates, Costco
  converged) may be studio over-regularization. Gate: re-derive families from
  the raw crop corpus (epic S2) before ANY pooling; crops arbitrate Costco.
- *Face priors.* Validated only against stylemap.json — itself rule-derived.
  Gate: M4's production scorecard on real multi-face renders.
- *Epic-1 derived corrections* (Sprouts ratio 0.691, WildFork 0.736, HD 0.881,
  Sprouts thin ~0.14). Gate: render A/B per RETROFIT.md before adoption.

## 3. The sequencing windfall — read this before running anything

Lines were **never embedded** after the reset. That turns the deploy delay into
an opportunity: if the section machinery lands **before** the line pipeline
runs, 100% of line embeddings carry dense `section_label` metadata on their
FIRST pass — no metadata-upsert sweep, ever. The strict order is:

```
1. dev `pulumi up`                      (Tyler — in flight)
2. M1a-2 caller wiring                  (now testable; merge it)
3. M1b -v1 ReceiptSection write         (Tyler approves; PENDING rows, dense
                                         line coverage from word propagation)
4. line-submit-sf-dev → line-ingest-sf-dev   (lines embed WITH sections)
5. crop corpus regen (build_merchant_glyphs) (M3 unblocks)
```

Running step 4 before 2–3 forfeits the windfall and buys a 100k-record
metadata-upsert task later. Don't.

## 4. Workstreams (what can run in parallel, by whom)

**W1 — critical path (one Opus session, serial):** steps 2–5 above, then M3
(pooled family fit from crops, starting with the crop-based family
re-derivation), M4 (renderer consumes the map), M5 (retrofit; acceptance =
railed/floor-bound merchants come out fixed, measured by `calibrate_merchant` +
production scorecard).

**W2 — QA + audit (parallelizable NOW, good Workflow-tool fan-out):** doesn't
need the pipeline. Stratified QA of the 3,128 v0 seeds via MCP label tools +
the words snapshot: sample per (merchant × section × confidence-stratum),
verify against receipt images, promote PENDING→VALID / flag INVALID; measure
per-source precision (labels vs rules) and recalibrate the confidence formula;
run the #1066 label↔section audit on seeded lines (its output = NEEDS_REVIEW
flags on CORE labels — the only permitted label-table writes). A multi-agent
fan-out (one agent per merchant, adversarial verify pass, aggregate) fits the
Workflow tool exactly.

**W3 — epic-1 A/B adoption (parallelizable NOW, local renders only):** the
RETROFIT.md derived corrections each need one render A/B on the merchant's
review receipt. Local render path works today (no deploy dependency).
Outcome: adopt or reject each correction with evidence; update
merchant_profiles.json only on wins.

**W4 — M4 interface prototyping (optional, after W1 step 2):** the renderer's
(merchant, section)→(family, face) consumption can be built against the
provisional 67-entry map behind a flag, so M4 is wiring-ready when M3's real
fonts land.

## 5. Tyler's decision gates (in order of arrival)

1. ✅ dev `pulumi up` (in flight).
2. **-v1 propagation write** to dev ReceiptSection (additive, PENDING,
   `model_source=section-propagate-v1`) — approve before W1 step 3.
3. **W2 promote/flag batches** — QA writes VALID/INVALID statuses.
4. **W3 adoptions** — each RETROFIT correction, on A/B evidence.
5. Eventually: sections dev→prod (rides the existing reconcile mirror — do NOT
   invent a new path), and prod line re-embed with sections.

## 6. Landmines (every one has bitten or nearly bitten)

- **Pulumi:** individual account = no concurrent updates; CI auto-deploys prod
  on merge to main and holds the lock. Never `pulumi cancel` prod. Targeted
  dev deploys (`--target embedding+chromadb --target-dependents`) clear the
  auto-mode blast-radius classifier; full 133-change deploys don't.
- **Chroma snapshots** need chromadb 1.5.x (`/private/tmp/chroma_venv`); the
  main venv's 1.3.6 cannot open them. Deltas don't open standalone.
- **JSON-only policy:** never commit `.npz`/samples/crops. Skeleton JSON is
  source of truth.
- **Anti-copy gate** (`_reject_copied_letterforms`) is mandatory in every
  publish; family fonts are fitted skeletons, never crop copies.
- **Labels win lines** (the seed-writer policy after the inversion bug); keep
  it in any re-write.
- **Confidence provenance:** bump `model_source` whenever the confidence
  formula changes; cap machine confidence below 1.0 (69 rows at 1.0 exist in
  v0 — recalibrate in W2).
- **Case convention:** Chroma metadata carries UPPERCASE SectionType values;
  glyphstudio canonical vocab is lowercase. Convert at boundaries; don't mix.
- **Never** `aws s3 sync --delete`; Dynamo deletes/bulk writes need Tyler's
  approval; dev before prod always.
- Multiple concurrent sessions edit this plan — check `font-epics-state`
  memory and open PRs before starting work.

## 7. Kickoff prompts

**W1 (critical path):**
> Read tools/glyph-studio/GAMEPLAN.md and FONT_INTELLIGENCE_EPIC.md. Dev is
> deployed. Execute the sequencing in GAMEPLAN §3 strictly: implement M1a-2
> using the 4-path threading map in the font-epics-state memory, test against
> dev, merge; then ask Tyler to approve the -v1 propagation write; then run
> line-submit-sf-dev → line-ingest-sf-dev; then regenerate the crop corpus.
> Then M3: re-derive families from crops FIRST (Costco arbitration), then the
> pooled skeleton fit for the largest family. Commit/push frequently, codex
> review each part, small PRs.

**W2 (QA workflow):**
> Read GAMEPLAN.md §4-W2. Run a stratified QA of the 3,128 section-seed-v0
> rows in dev: sample per (merchant × section × confidence stratum), heavy in
> the 0.60 rule-only stratum and SECTION_HEADER/TOTAL_LINE/BARCODE. Verify
> each sample against the receipt image, promote/flag via MCP label tools
> (Tyler approves write batches). Measure per-source precision, recalibrate
> the confidence formula, and run the #1066 label↔section audit on seeded
> lines. Use a multi-agent fan-out with an adversarial verify pass.

**W3 (A/B adoption):**
> Read tools/glyph-studio/RETROFIT.md. For each derived correction (Sprouts
> ratio 0.691, Wild Fork 0.736, Home Depot 0.881, Sprouts thin ~0.14), run a
> local render A/B against the merchant's review receipt (production render
> path, RECEIPT_PAPER_STRENGTH=0.3, cold cache). Adopt into
> merchant_profiles.json only on a scorecard win; report each verdict with
> the scorecard evidence. Vons/Trader Joe's are floor-bound — skip them
> (epic-2 fixes those).
