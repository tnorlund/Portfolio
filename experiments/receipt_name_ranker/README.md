# Receipt name ranker: frozen CPU experiment, 2026-09-30

**Decision: keep experimental and off.** A small learned ranker finds a useful
shape signal, but its conservative gate makes no changes. Unrestricted ranking
improves aggregate names while choosing unsafe annotations and regressing one
previously correct name. This is not a production model or promotion proposal.
Nothing in ingestion imports this experiment; there are no new runtime flags,
services, cloud training jobs, API calls, or deployment changes.

## Reproduce

From the repository root, with the standard local Python packages installed:

```sh
PYTHONPATH=.:receipt_upload:receipt_dynamo python experiments/receipt_name_ranker.py \
  --output /tmp/receipt-name-ranker
PYTHONPATH=.:receipt_upload:receipt_dynamo pytest tests/test_receipt_name_ranker.py
```

No ML dependency, network call, paid training, or accelerator is needed. The
experiment finishes in a few seconds on a CPU. It writes `report.json` and a
small JSON coefficient artifact, never pickle. `report.json` includes source,
decoder, fixture and fold split SHA-256 hashes, fixed hyperparameters, all
receipt-level metrics, all merchant-level metrics, and override diagnostics.
The committed `model.json` is trained on **all 38 receipts** for future offline
stress tests; it is **not** the model used to report cross-validation results.
Held-out scores come from separately trained weights in each fold.

To compare another checkout without changing its files, point `PYTHONPATH` at
that checkout's `receipt_upload` and `receipt_dynamo` directories while invoking
this script and the same `--fixtures` directory. An optional `--stress-evidence`
argument accepts a locally authorized dev evidence export. Those receipts never
enter training or primary metrics; the adapter uses their stored sections and
summary and writes unscored `stress.json` diagnostics. Its current contract is
one receipt per image, as in the two-image export used here.

## Dataset and scope

- Fixtures: `receipt_upload/tests/fixtures/line_items_golden{,_ocr}.json`
- 38 receipts, 226 annotated items, 20 merchant spellings, 18 canonical groups
- All 212 non-discount items remain in evaluation, including unalignable or
  unrecoverable names. The other 14 annotated items are discounts and excluded
  from item-name metrics; signed predicted discounts remain in subtotal sums
- Seven non-discount labels carry `uncertain: true` (six Costco, one Home Depot)
  and remain included in this frozen experiment. Some names are faded/truncated
  or a price was reconstructed from subtotal; not all labels are unambiguous
  direct observations. The clean-label sensitivity below excludes those targets
- Only 25 receipts contribute usable ranking pairs: 168 pairs in all-data
  training. A target requires a unique manual item with matching amount and
  price-carrier line. If no exact normalized target candidate is available, it
  contributes no training pair. Such items are **not** removed from evaluation
- Inputs mostly contain preselected ITEMS zones, not complete receipt OCR.
  Thus this evaluates naming conditional on the existing section/block decoder,
  not full-pipeline extraction or section recovery
- Existing block-role priors are pseudo-labeled from non-golden receipts. They
  remain part of the frozen baseline; they are not new supervised targets here
- No local LayoutLM checkpoint was available, so no new standalone LayoutLM
  inference comparison was run. Existing pipeline decoder output is the control

These historical golden fixtures have already informed rule development. They
are development evidence, **not an untouched test set** or representative long-
tail sample. Merchant-held-out training applies to the new ranker, not to every
historical component of the baseline. Source instructions about low-information
refund/no-baseline receipts are unchanged.

## Frozen model and evaluation protocol

Twelve bounded, non-lexical features describe alphabetic/numeric/punctuation
fractions, token counts/shapes, maximum alphabetic run, length, leading number,
and whether the candidate is the existing decoder choice. There are no merchant
features. A pairwise logistic model learns positive-minus-negative feature
vectors using deterministic full-batch gradient descent: 400 epochs, learning
rate 0.5, L2 0.02, zero initialization, no fitted intercept.

Candidates are the baseline name and OCR line text from that baseline item's
own `line_ids`, with printed amounts removed, plus a leading long-SKU-stripped
variant. No candidate generator sees manual labels. Existing block contamination
can still include adjacent product names or annotations; staying within a
baseline block does **not** prove semantic eligibility.

Each fold holds out an entire canonical merchant group. Case/punctuation
variants of Trader Joe's and Wild Fork location/name variants are grouped.
No receipt appears in both sides of a fold. Gold names supervise training only;
held-out labels score predictions. No pseudo-labels train the new ranker.

The **predeclared 0.9 gate** is sigmoid(score difference from baseline), an
uncalibrated pairwise preference score, not a calibrated confidence probability.
The separate 0.5 unrestricted diagnostic selects the highest-scoring candidate.
Neither was tuned after looking at held-out outcomes. No second eligibility
experiment is included. The default-off gate's coverage is zero, so selective
accuracy among accepted overrides is undefined, not 100%.

Metrics use normalized multisets (case/punctuation-insensitive, preserving all
letters and numbers). Exact normalized name+price is the joint metric. Name-
only and price-only metrics are separately reported. Duplicate multiplicities
are respected; a price match by itself cannot establish correct item identity.
`missing_joint`/`spurious_joint` are unmatched joint records, **not independent
row-detection errors**: a renamed wrong item creates both. Price-only missing/
spurious counts can hide swaps at repeated prices. Subtotal exactness uses
signed predicted amounts and only directly available printed subtotals. No
total-minus-tax fallback or tolerance is used in this experiment.

## Results

Baseline main was `085c8d1`; comparison naming fix #1732 was `3ffc0a0`. Both give
identical predictions on this historical fixture. The fix's two fresh Best Buy
cases are not present in it. The experiment does not claim credit for #1732.

| Metric | Baseline | Gated ranker | Unrestricted diagnostic |
| --- | ---: | ---: | ---: |
| Predicted non-discount items | 210 | 210 | 210 |
| Exact normalized name+price matches / 212 | 127 | 127 | 138 |
| Joint precision | 60.48% | 60.48% | 65.71% |
| Joint recall | 59.91% | 59.91% | 65.09% |
| Name-only matches | 127 | 127 | 140 |
| Name-only precision / recall | 60.48% / 59.91% | unchanged | 66.67% / 66.04% |
| Price-only matches | 196 | 196 | 196 |
| Price-only precision / recall | 93.33% / 92.45% | unchanged | unchanged |
| Missing / spurious joint records | 85 / 83 | 85 / 83 | 74 / 72 |
| Missing / spurious price records | 16 / 14 | 16 / 14 | 16 / 14 |
| Exact printed-subtotal reconciliation | 13 / 23 | 13 / 23 | 13 / 23 |
| Changed names / non-discount decoded items | 0 / 210 | 0 / 210 | 39 / 210 |
| Override coverage | n/a | 0% | 18.57% |

There are 129 decoded items with multiple candidates. The 39 unrestricted
changes comprise 12 corrections, one regression, 23 still-wrong names, and three
without a unique manual price-carrier alignment. The aggregate joint gain is
11; the name-only gain is 13. Improved folds are Target (+7 joint matches),
Home Depot (+3 net), and Vons (+1). Other folds are unchanged in aggregate.
A merchant-cluster bootstrap (2,000 draws, fixed seed 20260930) gives a
95% percentile interval of **0 to +14.06 percentage points** for joint recall
delta. The interval includes zero and the dataset is small and curated; this
is descriptive uncertainty, not an independent confirmation of improvement.

Unsafe unrestricted selections include `MAX REFUND VALUE`, preferred-pricing
annotations, a QR-code footer, and a neighboring product name already present
in a contaminated baseline block. One correctly named steel-track item becomes
`METAL TRIM <A>`. Therefore aggregate gain is insufficient for promotion.

The two new receipts were excluded from training. In a separate unscored stress
check with stored sections and summary, unrestricted ranking fixes both Best
Buy names relative to main, while #1732 already fixes both. Gated ranking makes
no changes; neither ranker changes Chick-fil-A. Prices and counts remain fixed
(2 Best Buy items, 13 Chick-fil-A items). The ranker cannot recover the missing
Fries amount or repair stranded modifier sections.

## Clean-label sensitivity (same frozen ranker)

Run with `--exclude-uncertain` to remove all seven uncertain targets from
supervision and scoring. This also ignores five predicted rows whose printed
price-carrier line belongs to those uncertain annotations, independent of the
predicted name or amount. An explicit overlap check rejects a predicted carrier
that belongs to both uncertain and retained certain targets. Two uncertain
annotations have no excluded predicted carrier. All other predictions remain.
This is a **conditional clean-region diagnostic**, not a new untouched test set.
The full original predicted rows still drive subtotal reconciliation, so removing
scoring regions cannot artificially alter subtotal results.

The population becomes 205 certain non-discount targets and 205 predictions.
None of the uncertain targets contributed a usable positive ranking pair, so
training stays at 168 pairs and the learned coefficients are unchanged. Both
main and #1732 give the same sensitivity result:

| Metric | Baseline / gated | Unrestricted diagnostic |
| --- | ---: | ---: |
| Exact normalized name+price matches | 127 / 205 | 138 / 205 |
| Joint precision and recall | 61.95% | 67.32% |
| Name-only precision and recall | 61.95% | 68.29% |
| Price-only precision and recall | 93.66% | unchanged |
| Missing / spurious joint records | 78 / 78 | 67 / 67 |
| Missing / spurious price records | 13 / 13 | unchanged |
| Full-receipt exact subtotal | 13 / 23 | unchanged |

The gate still abstains everywhere and the unsafe candidate selections remain.
The clean-label artifact is `model-clean-labels.json`; the original artifact and
all-label comparison are preserved separately. No gate, feature, or candidate
rule was changed after observing either result.

## Next decision

Keep this frozen baseline as evidence. A future experiment should independently
validate candidate eligibility/provenance and calibrate abstention on training-
only or nested folds, then test on newly manually verified complete receipts.
Do not tune this gate on the same held-out labels and call the result a fresh
held-out improvement. No promotion is authorized or justified by these results.

## Review and validation

Nineteen focused tests passed (experiment invariants, golden regression, and
Swift parity fixture checks); changed Python files pass Black and isort.
Independent native code/methodology review reproduced the original and
clean-label results and confirmed the per-fold training-pair equality. External
Codex CLI diff review was not completed: the environment declined external diff
disclosure without additional authorization. This explicitly experimental draft
uses the native-review environmental exception; it is not merge-ready or a
production promotion. No Claude external review was performed.
