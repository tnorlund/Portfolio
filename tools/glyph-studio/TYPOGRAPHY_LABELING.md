# Typography line labeling — packet + blind dual-judge protocol (Phase 0)

Phase 0 delivers the labeling *infrastructure* only: the per-line crop
packet generator, the immutable packet manifest, and the blind judge
schemas. **No labeling batch runs in Phase 0** — the full dual-judge run is
gated behind the #1178 triage/relabel chain and the #1175 final review.

Design authority: `ROW_SCHEMA.md` §7 (typography is **per-LINE, never
per-section**; 31 % of VALID sections are multi-style) and the merged pilot
(PR #1106, `TYPOGRAPHY_RUNS.md`). All measurement machinery is reused from
`glyphstudio.typography` / `glyphstudio.stylescan` — never re-vendored.

## 1. Packets

`py/typography_packets.py` (READ-ONLY against Dynamo + S3) emits, per
visual line of each vetted receipt (`ocr_overlap_score <= 2`, the M3 rule;
pixels prefer `cdn_s3_key` — raw keys often 404):

- `packets/<packet_id>/line.png` — grayscale line crop
- `packets/<packet_id>/letters/{seq:03d}_u{xxxx}.png` — raw per-letter crops
- a manifest packet record: identity (`image_id`, `receipt_id`,
  `line_index`, Dynamo `line_ids`), full line text, measured probe
  features (`cap_px`, `stroke_med`, `density_med`, `n_letters`,
  `contamination`, `underline`, `reverse_video`, `slant_deg`, `tier`), the
  receipt-relative context (`body_cap_px`, `body_stroke_px`), and SHA-256
  hashes of every crop file.

Tier assignment is computed over ALL measured lines of a receipt before
any `--max-lines` cap, so the receipt-relative body reference is never
biased by packet selection. Contaminated lines are included with their
contamination score (judges may abstain); nothing is silently dropped.

Image artifacts stay LOCAL under `.out/` (gitignored). Only JSON
manifests are committed.

## 2. Immutable manifest

`glyphstudio/packets.py` builds the manifest:

- **Byte-deterministic**: canonical JSON (sorted keys, compact
  separators, NaN rejected), all floats pre-rounded, no wall-clock, no
  usernames, no absolute paths. Two identical builds are byte-identical
  (tested).
- **Content-hashed**: `content_hash` = SHA-256 over the canonical
  serialization of the sorted packet identities + crop hashes.
- **Versioned, never mutated**: the manifest file is
  `manifest_<content_hash16>.json`. A changed packet set mints a new
  hash → new file; overwriting an existing manifest with different bytes
  raises. `MANIFEST` is a mutable one-line pointer to the current file.
  `manifest_version` (`tp-1`) plays the same role as `grouping_version`
  on ReceiptRow: packet-content/layout changes bump it.

`verify_manifest` re-hashes every referenced crop and recomputes the
content hash — run it before any judging batch.

## 3. Blind judge inputs

Schema: `schemas/typography_judge_input.schema.json` (`tj-in-1`).

A judge sees, per packet: the line crop, the per-letter crops, the line
text, and neutral geometry context (`cap_px`, `stroke_med`,
`receipt_body_cap_px`, `receipt_body_stroke_px` — required to make
receipt-relative size/weight judgeable at all).

Because tier is essentially the ratio of those provided geometry fields,
a judge's `tier` answer is arithmetic, not perception — it is therefore
classified as a DERIVED verdict attribute (owner review, 2026-07-18):
still reported (consistency check) but never counted as independent
agreement and excluded from acceptance metrics.

A judge NEVER sees (blindness lesson from the 2026-07-18 triage audit):

- merchant name/slug, section types, or any cross-receipt statistics;
- pilot probe verdicts (`tier`, `underline`, `reverse_video`,
  `slant_deg`, `contamination`) — judges must measure these
  independently or they'd just parrot the probes they're meant to check;
- atlas attribution scores or discovered typeface labels (T0..Tk) — the
  expected answer must not leak.

Enforcement is structural: `judge_input()` raises on any
`FORBIDDEN_JUDGE_KEYS` occurrence at any depth, and the input schema sets
`additionalProperties: false` at every level, so a leaked field cannot
validate.

## 4. Verdicts

Schema: `schemas/typography_judge_verdict.schema.json` (`tj-out-1`).

Per line: descriptive typeface classification (`family_class`,
`monospace`, `italic`, `weight`, free-text description), `tier`
(DERIVED — see §3), `underline`, `reverse_video`, `slant_deg`,
per-attribute confidences, and an explicit `abstain` + reason. Judges classify *descriptively*; they do
not name registry typefaces (they can't — they're blind to the merchant).
Mapping verdicts onto per-merchant registry entries (T0..Tk) happens in
adjudication, where merchant identity is restored.

Hardening from the Phase 0 schema review (grok):

- Every verdict carries `judge` (`codex`/`grok`) and
  `manifest_content_hash` — verdicts are pinned to the exact packet set
  they judged; stale-manifest verdicts fail validation.
- `abstain: true` forces all attribute fields to null (an abstaining
  judge cannot smuggle a partial verdict into adjudication);
  `abstain: false` REQUIRES every attribute (`typeface`, `tier`,
  `underline`, `reverse_video`, `slant_deg`, `confidence`) — null means
  "measured, can't tell", absence is invalid.
- Type mapping for agreement: the manifest probe `reverse_video` is the
  pilot's 0/1 int; the judge field is boolean (0 ↔ false, 1 ↔ true).
  Probe `tier` has no `unknown`; a judge `unknown` never *agrees* with a
  probe value — it routes to adjudication.
- Free-text fields (`typeface.description`, `notes`, `abstain_reason`)
  are quarantined: excluded from the other judge's context and from the
  adjudicator's prompt until after per-attribute agreement is computed.
- Follow-up (pre-Phase-1): an adjudication-outcome schema
  (`typography_adjudication.schema.json`) so the dual-judge result is
  itself validated; slant agreement threshold (3°) lives there.

## 5. Phase 1 batch one — Costco-only, ground-truth gated

Costco is the anchor merchant for the first labeling batch: it is the one
merchant with ground-truth letterforms (the committed specimen-chart
atlases — bitMatrix-C2 and C2-heavy — under `fonts/costco` +
`stylemap.json`) and the calibrated `shifted_iou` machinery in
`glyphstudio/typography.py` scores against them directly.

Batch one therefore runs Costco alone, and its verdicts are scored
against measured ground truth before any other merchant is labeled:

- **typeface (shape attributes)**: for each judged line, per-letter
  calibrated shifted-IoU against the Costco atlas (per-char norms,
  per-receipt centering — the pilot's two measured calibrations) decides
  whether the line IS the known body face. Judge `family_class`,
  `monospace`, and `italic` verdicts must match that ground truth (body
  lines → mono, upright), and `typeface.description` is graded for
  consistency with the bitMatrix-C2 letterforms (dot-matrix/mono
  character) on body lines.
- **tier is EXCLUDED from the gate** (owner review): judges receive the
  geometry that determines tier, so tier agreement is arithmetic, not
  perception. It is recorded as a derived consistency check only.
- **Acceptance gate**: >= 90 % agreement with ground truth on each SHAPE
  attribute (`family_class`, `monospace`, `italic`) on non-abstained
  lines, description-quality pass on review, and abstain rate <= 15 %,
  over the batch. Both judges must clear the gate independently. Fail →
  fix prompts/packets/schemas and re-run Costco; no other merchant is
  labeled until the gate passes.

## 6. Dual-judge + adjudication flow (documented, NOT run in Phase 0)

1. Generate packets + manifest; `verify_manifest` must return clean.
2. Judge A = codex, Judge B = grok. Each receives, per packet, the
   `judge_input` JSON plus the crop images, and returns one verdict JSON
   validating against `tj-out-1`. Invalid verdicts are re-prompted once,
   then recorded as abstains. Batches are keyed by
   `content_hash` — verdicts against a stale manifest are rejected.

   **Proven image-delivery mechanisms** (smoke-tested 2026-07-18 on the
   dry-run crop `01c0bc98…_1_L002/letters/000_u0048.png`, an 18×20 "H"
   from Costco's dot-matrix `Henderson #673` line):

   - **codex** — native image attachment, non-interactive:
     `codex exec --sandbox read-only -i <crop.png> -- "<prompt>"`
     → answered `H, sans-serif, regular.` (char correct).
   - **grok** — file-path vision in headless mode:
     `grok --output-format json -p "View the image file at <path> …"`
     (its file-viewing tool loads the image). Raw 18×20 crops are below
     its vision floor and its shell-based enlarge attempt is
     sandbox-cancelled, so **judge delivery must pre-upscale letter
     crops (×8 nearest-neighbor)**; with the ×8 crop it answered
     `ANSWER: H, sans, bold` (char correct; the weight split vs codex on
     a dot-matrix glyph is exactly what adjudication arbitrates).

   Pre-upscaling is a judge-delivery step (deterministic, nearest-
   neighbor — no resampling invention); packet crops on disk stay at
   native resolution and keep their manifest hashes.
3. Agreement rule, per attribute: both judges non-abstaining and equal
   (for `slant_deg`: within 3°) → accepted. One abstain → the other's
   verdict accepted only at confidence >= 0.8, else UNRESOLVED. Both
   abstain → UNRESOLVED.
4. Disagreements + UNRESOLVED go to a third adjudication pass (fresh
   context, both verdicts shown, crops re-attached), which may also mark
   the line `contaminated`.
5. Adjudicated per-line records are then mapped onto the per-merchant
   typeface registry (registry entries proposed from verdict clusters +
   pilot exemplars; `n_receipts >= 2` before a face is believed — pilot
   rule). Only after that mapping review would any Dynamo write be
   considered — outside Phase 0's scope entirely.

The exact gated command for the full run is recorded in
`~/section_label_kickoff/TYPOGRAPHY_PHASE0_RESULT.md`; it must not start
until the #1178 triage/relabel chain and the #1175 final review are
finished.

## 7. Proposed per-merchant TYPEFACE REGISTRY (minimal fields — spec only)

Per `ROW_SCHEMA.md` §7 the registry entity is still to be spec'd. Phase 0
proposes the minimal fields (NO Dynamo entity is created here):

| field | type | why |
|---|---|---|
| `merchant_slug` | str | registry is per-merchant (`fonts/<slug>` join) |
| `typeface_id` | str (`T0`..`Tk`) | stable id per-line records reference |
| `role` | enum `body\|display\|caption` | pilot's observed strata |
| `family_class` | enum (as `tj-out-1`) | descriptive class from adjudicated verdicts |
| `display_name` | str | human name (e.g. "serif FARMERS MARKET subtitle") |
| `exemplar_key` | str | S3/npz key of median-voted exemplar glyphs |
| `n_lines`, `n_receipts` | int | evidence; `n_receipts >= 2` to be CONFIRMED |
| `source_manifest_hash` | str | provenance: which packet manifest the evidence came from |
| `status` | enum `CANDIDATE\|CONFIRMED\|REFUTED` | Wild Fork's refuted second face needs a recordable outcome |

Per-line `typeface` values in the eventual typography map reference
`typeface_id` within the merchant's registry; runs stay pure derivations
and are never stored (ROW_SCHEMA §7 / pilot recommendation).
