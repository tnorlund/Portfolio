# Receipt synthesis: the unified plan (2026-09-23)

One plan replacing two overlapping epics:

- **#1188 "Render-system refactor: layout-as-data"** (2026-07-19): measured columns and
  section sequences stored as merchant data, one engine that snaps to them, a
  full-fidelity metric layer, and a versioned merchant-truth bundle.
- **`tools/glyph-studio/SYNTHESIS_V2_EPIC.md` "Learned receipt structure"** (2026-07-18,
  #1058): section roles aggregated from word labels instead of per-merchant regexes,
  fitted layout priors, sampled content, and few-shot merchant onboarding.

They attack the same liability (per-merchant hand-coding that fits the done fleet and
mis-serves the other 200+ merchants) with different answers. This document decides
where each answer applies, orders the remaining work, and makes evaluation the gate
between every stage. It supersedes the phased plan in #1188 and the milestone list in
the v2 epic; those documents stay as design rationale.

## 1. Where we actually are

Much of #1188 landed in July. The v2 epic has one pilot PR. Nothing else has started.

| Work item | Origin | Status | Evidence |
|---|---|---|---|
| Full-fidelity metric layer (7 metrics, validated) | #1188 P1 | **Done** | #1192, `synthesis_loop/full_fidelity_eval.py`, `METRIC_VALIDATION.md` |
| Layout-template measurement (columnscan riders, profile writer) | #1188 P1b + P2 | **Done** | #1192, `layout_template` in 18 files |
| Merchant truth bundle in DynamoDB (versioned, hashed, ACTIVE flip, gate records) | #1188 P4 | **Done** | #1193–#1219 series, `MerchantTruthLoader`, `evidence/ACTIVE_FLEET.json` (v1 for the active fleet) |
| Engine adoption: `resolve_columns` + fabrication guard, generic composer, dispatch registry, rules→data, literal purge | #1188 P3 | **Open** | no `resolve_columns` on main; only Speedway and Whole Foods carry `stylemap.json` rules, the other 9 merchant rule sets plus the Sprouts default are still in-code `_<SLUG>_RULES` blocks in `stylescan.py` (the rules→data move launched as the first S2 cloud job on 2026-09-23) |
| Fleet re-baseline (Costco, Gelson's, then the rest) | #1188 P5 | **Partial** | two evidence campaigns under `evidence/`; no fleet-wide scorecard committed |
| Label-driven section roles + regex agreement audit | v2 M1 | **Pilot** | #1721: classifier + audit CLI over 15 gold receipts, 731 lines; no regex deleted |
| Statistical layout priors | v2 M2 | Not started | |
| Generative content | v2 M3 | Not started | |
| Merchant style embedding, few-shot onboarding | v2 M4 | Not started | |
| Costco merchant-truth v2 | truth pipeline | Minted, never sealed or activated | dormant |

Gates that run in CI today: glyph-studio tests, `corpus_regression_gate.py check`
(freezes the seven metric verdicts over committed fixtures, zero AWS), and
`verify_evidence_stamps.py`. Gates that need AWS or macOS and run by hand:
`full_fidelity_eval.py run` / `real-real`, `render_regression_guard.py compare`
(byte-identical re-render), `receipt_line_scorecard.py`.

## 2. The three disagreements, decided

**2a. What is the source of receipt structure: labels or measurement?**
The v2 epic makes word labels the single interface for section roles, layout, and
content. #1188 makes measured geometry (columns, separators, section sequence) the
truth and stores it as data. The M1 pilot settled this empirically: 37% of lines on the
gold receipts carry no role-bearing label, and the largest disagreement clusters were
label defects (Costco tax flags labeled `PAYMENT_METHOD`, CRV deposits labeled
`COUPON`, `LOYALTY_ID` misfiling CVS ExtraCare lines). Labels cannot be the sole
interface for exactly the thin-corpus merchants the plan is meant to reach.

*Decision.* Measured geometry from the truth bundle's `layout_template` is layout
truth. Labels supply **section roles** where they exist. The shared geometric
fallbacks (footer, separator, barcode caption, position-in-receipt) cover the rest and
are merchant-invariant. Per-merchant regex rule blocks are deleted **one merchant at a
time**, only when the label-role classifier plus fallbacks match adjudicated truth at
least as well as that merchant's regexes on that merchant's full corpus (stage S3).

**2b. What is the unit of merchant knowledge?**
#1188 built the versioned merchant-truth bundle. The v2 epic proposes a low-dimensional
style embedding inferred few-shot, replacing the hand-authored profile blobs.

*Decision.* The bundle is the unit. It already carries measured typography, layout
template, stylemap, assets, catalog snapshot, and provenance. The v2 embedding becomes
a **derived view over bundles**: cluster the measured dims of sealed bundles into POS
families, and let a thin-corpus merchant borrow its cluster's priors as a *proposed*
bundle that the S0–S6 measurement pipeline then confirms or rejects. Nothing hand-authored
enters truth; that rule from #1188 stands.

**2c. How is content produced?**
#1188's P3 wants a generic composer driven by the template's emit sequence plus a
lifted OCR-repair library. The v2 epic's M3 wants sampled content from learned
per-merchant distributions with exact arithmetic constraints.

*Decision.* Generic composer first (S2). Sampling plugs into it afterwards (S5) as the
content source, reusing the composer's totals-constraint and clone-geometry plumbing.
Arithmetic identities stay exact by construction throughout; the `arithmetic` metric
is the gate.

## 3. Evaluation doctrine

The owner's instruction for this plan: evaluate as we develop. These rules apply to
every stage below and to every PR under this plan.

1. **No fix without a failing metric first.** (Standing rule from #1188, kept.) A
   fidelity change ships with a before/after evidence pair under `evidence/<name>/`,
   stamped with the git SHA, atlas hash, inputs hash, and bundle tuple, and checked by
   `verify_evidence_stamps.py`.
2. **A metric counts only after it has failed its historical defect and passed the
   real-vs-real null.** New metrics append a row to `METRIC_VALIDATION.md` before they
   gate anything.
3. **Renderer changes prove byte-identity on untouched merchants.**
   `render_regression_guard.py compare`, MAD quoted in the PR. Intentional drift gets a
   re-captured baseline and says so.
4. **Eval logic is frozen by the corpus gate.** `corpus_regression_gate.py` verdicts do
   not change without an explicit, named acceptance in the PR.
5. **Classifier changes are measured against adjudicated truth, not self-scored.**
   The M1 audit's `label-wins` / `regex-wins` verdict is triage only. Stage S1 creates a
   committed, hand-adjudicated truth file; from then on agreement is reported against
   it, per merchant, and the regex-deletion gate reads that number.
6. **Every stage ends with the fleet scorecard re-published.** One committed table
   (`evidence/FLEET_SCORECARD.md`, produced by `fleet_status.py` plus the seven metric
   verdicts per active merchant) so regressions across merchants are visible without
   re-running anything. A stage is not done until the scorecard shows no merchant
   worse than before.
7. **Owner-gated actions stay owner-gated.** Minting, sealing, activating, and
   promoting merchant truth are never run by agents. A FAIL from a truth gate is
   authoritative.

**Where work runs.** Cloud sessions can do anything that is repo-only: classifier and
audit code, the generic composer against committed fixtures, layout-prior fitting over
exported snapshots, tests, and the corpus gate. They cannot reach DynamoDB (no
credentials by design), Apple Vision OCR, CoreML, or `pulumi`. So every stage lists an
**export step** that a local session runs first to put the needed data in the repo or
in a committed fixture directory, and the cloud does the rest.

## 4. Stages

Ordered by dependency. Each stage names its goal, deliverable, gate, where it runs, and
the backlog items it absorbs. Estimates are working sessions, not calendar days.

### S0. Baseline fleet scorecard (1 local session)

*Goal.* Know the fleet's fidelity before changing anything, so every later stage has a
"before".
*Deliverable.* `evidence/FLEET_SCORECARD.md`: for each active merchant, the seven
`full_fidelity_eval` verdicts on its gold receipt, the `layout_score` numbers, and the
`render_regression_guard` hash state. Generated by a script, committed with stamps.
*Gate.* Real-vs-real null passes for every merchant with two or more receipts.
*Runs.* Local (needs the dev table and S3).
*Absorbs.* Nothing; this is the instrument.

### S1. Corpus-scale label-role audit and adjudicated truth (1 local + 1 cloud session)

*Goal.* Turn the M1 pilot into the measurement the v2 epic actually asked for: the
full scanned corpora, not one receipt per merchant.
*Export step (local).* Extend the snapshot format to a per-merchant corpus export
(every labeled receipt for the 18 profiled merchants, words with text, bbox, line id,
labels). Commit under `tools/glyph-studio/fixtures/corpus_snapshots/` if size allows;
otherwise pin by hash in S3 as the similarity fixtures are.
*Cloud step.* Run `label_role_audit` over the corpus. Hand-adjudicate the top
disagreement patterns per merchant into `fixtures/section_role_truth/<slug>.jsonl`
(line text, adjudicated role, note). Report per-merchant agreement of **both**
classifiers against that truth.
*Gate.* Truth file covers at least the three largest disagreement patterns per merchant.
The label-defect list found along the way (tax flags, CRV, LOYALTY_ID) is filed as
label-quality issues, not worked around in the classifier.
*Runs.* Export local; audit and adjudication cloud.
*Absorbs.* #1214 (the savings rule) is settled by the corpus, one way or the other.

### S2. Engine adoption, #1188 P3 (3–4 cloud sessions, 1 local)

*Goal.* One engine, measured columns, no merchant literals in generic code.
*Deliverables.*
- `resolve_columns()` in the grid: faithful mode snaps token edges to the bundle's
  measured columns within tolerance, with the fabrication guard (receipt lane deviating
  more than 0.03 paper-width from the profile falls back to receipt-local).
- Stylemap rules move from `_<SLUG>_RULES` in `stylescan.py` into each merchant's
  `stylemap.json` `rules`, read through `rules_for_font` (the path Speedway and Whole
  Foods already use). This is a *move*, not a deletion; behavior is byte-identical.
- Price-token and y-band dedupe are **already done** for the render path (#1188 P1b:
  `price_tokens.py` declares each historical pattern once with tests pinning them;
  `row_bands.py` consolidates the greedy groupers and documents why the two remaining
  variants stay distinct). The other price regexes on main live in `receipt_upload`,
  `receipt_dynamo`, `receipt_embeddings`, and `scripts`, accept different token
  languages, and sit behind the layering rule that `receipt_dynamo` imports no
  sibling; they are out of scope for S2.
- Generic composer with a dispatch registry; Dollar Tree becomes a registry entry and
  its OCR-repair reconcilers become a library parameterized by template geometry.
*Gate.* `render_regression_guard compare` byte-identical (MAD 0.0000) for every pinned
merchant after each PR. The `columns` metric passes on Gelson's with zero human
eyeballs; that receipt is the acceptance test for the whole refactor. Fleet scorecard
re-published, no merchant worse.
*Runs.* Code in cloud against committed fixtures; the render guard and Gelson's eval
locally before merge.
*Absorbs.* #1155, #1176, #1217.

### S3. Regex retirement, one merchant at a time (v2 M1 completion; 1–2 cloud sessions)

*Goal.* Delete a merchant's regex rule set when the label-role classifier plus shared
geometric fallbacks match adjudicated truth at least as well as the regexes on that
merchant's corpus.
*Deliverable.* Per merchant: a PR that removes its `rules` and points classification
at `_classify_from_labels` with fallbacks, carrying the S1 agreement numbers in the
description.
*Gate.* Agreement against `section_role_truth` at or above the regex baseline for that
merchant; `corpus_regression_gate` verdicts unchanged; fleet scorecard unchanged for
that merchant.
*Runs.* Cloud.
*Absorbs.* The v2 epic's "no per-merchant regex rule-sets" success criterion.

### S4. Layout priors from labeled boxes, v2 M2 (2 cloud sessions, 1 local)

*Goal.* Stop discarding fitted priors. `price_column_x` and its variance, inter-word
gap distribution, and section y-bands are fitted per merchant from the corpus
snapshots and written into the truth bundle's layout template as a **proposed** version.
*Gate.* `layout_score` `amount_col_cv` and `token_overlap_count` do not regress on any
merchant sharing the template; the `columns` metric passes on every merchant that
passed before. The owner seals and activates the new bundle versions (Costco v2 first,
which is already minted).
*Runs.* Fitting in cloud over S1's exports; eval and activation local.

### S5. Sampled content, v2 M3 (2–3 cloud sessions)

*Goal.* Turn composition from transplant into sampling: per-category name grammar,
fitted price and quantity distributions, learned category sequence and item count,
tax flags and rates derived from labels instead of `merchant_tax_config.py`.
*Gate.* `arithmetic` metric passes on every synthetic receipt by construction; `tokens`
precision does not drop (no fabricated content classes); a new **novelty** check
(no synthetic receipt's item sequence equals any real scaffold) validated per rule 2.
*Runs.* Cloud.

### S6. Few-shot onboarding, v2 M4 (2 cloud sessions, 1 local)

*Goal.* A new merchant from a handful of labeled receipts with zero hand config: cluster
sealed bundles into POS families, propose a bundle for the new merchant from its
cluster's priors plus a light fit, and let the measurement pipeline confirm it.
*Acceptance test.* Roast & Rice (15 receipts across two name variants, the largest
merchant with no profile) onboarded this way and passing the S0 scorecard at parity
with the hand-onboarded merchants. Then one merchant with fewer than six receipts.
*Runs.* Clustering and proposal in cloud; measurement, mint, seal, activate local and
owner-gated.

### S7. Fleet re-baseline, #1188 P5 (1–2 local sessions)

*Goal.* Every active merchant re-evaluated on the finished engine; Moody last for the
quantization diagnosis. Target from #1188 stands: zero owner-spotted defects on the
merchants onboarded after July.

## 5. Backlog mapping

| Issue | Stage |
|---|---|
| #1155 render-quality calibration (Vons, Costco, Sprouts) | S2 |
| #1176 GeometryTests failures | S2 |
| #1214 stylescan savings rule swallowing item rows | S1 decides, S3 retires the rule |
| #1217 section scaling vs layout geometry | S2 |
| #1721 label classifier + audit (open PR) | S1 input; merge as tooling |
| Costco truth v2 dormant | S4 (seal and activate with the fitted priors) |

## 6. Decisions the owner still holds

1. Whether corpus snapshots are committed to git or pinned in S3 (size decides; the
   similarity fixtures set the S3 precedent).
2. The agreement threshold for S3. Proposed: label-plus-fallback agreement at or above
   the regex agreement on the same adjudicated truth, per merchant, with no role
   worse by more than five lines.
3. Whether `LOYALTY_ID → savings` stays in the role table. The pilot says it is the
   weakest row; a `loyalty` role folded into footer for rendering is the alternative.
4. Cloud AWS access. Every stage above is designed so the cloud never needs it. If
   that becomes too slow, the only acceptable form is a dev-only IAM user scoped to
   read the dev table and dev buckets, supplied as environment variables.

## 7. Housekeeping

- #1188 is the tracking issue for this plan. Its "Phased plan" section is superseded by
  §4; comment there linking here.
- `SYNTHESIS_V2_EPIC.md` gets a one-line pointer at the top. Its thesis, liability
  table, risks, and success criteria remain the rationale for S1, S3, S4, S5, S6.
- Each stage opens as a checklist issue under #1188 when it starts, carrying its gate
  verbatim from this document.
