# LayoutLM Metrics

Last updated: 2026-07-09 UTC.

Metrics are recorded in three places:

- SageMaker training job metadata;
- S3 run artifacts under `s3://layoutlm-training-dev-68164770/runs/<job>/`;
- DynamoDB `Job`, `JobMetric`, and `JobLog` records when the trainer can write
  them.

For current model decisions, prefer the S3 run artifacts because they contain
the live held-out curve and frozen run lineage.

## Key Artifacts

Each serious run should write:

- `run.json`: config, label set, split metadata, dataset counts, and training
  summary.
- `epochs.json`: live or post-hoc checkpoint evaluation on the frozen held-out
  receipts.
- `checkpoints/checkpoint-*/`: saved model checkpoints.
- `best/`: best checkpoint copied for downstream inference/export.
- `receipts/`: optional showcase receipt predictions from checkpoint eval.

Current v25 live metrics:

```text
s3://layoutlm-training-dev-68164770/runs/layoutlm-v25-adversarial-real-20260708-022719/epochs.json
```

Current product-detail comparison artifacts:

```text
s3://layoutlm-training-dev-68164770/runs/layoutlm-v28-control-prodmetric-20260708-063942/epochs.json
s3://layoutlm-training-dev-68164770/runs/layoutlm-v28-item-window-prodmetric-20260708-063942/epochs.json
```

Completed v29 jobs wrote the same artifacts under:

```text
s3://layoutlm-training-dev-68164770/runs/layoutlm-v29-item-window-prodweight-20260708-145418/epochs.json
s3://layoutlm-training-dev-68164770/runs/layoutlm-v29-product-only-item-window-20260708-145418/epochs.json
```

Current diagnostic artifacts:

```text
s3://layoutlm-training-dev-68164770/diagnostics/layoutlm-diag-v28-item-window-v2-cpu-20260709174453/
s3://layoutlm-training-dev-68164770/diagnostics/layoutlm-diag-v29-weighted-v2-cpu-20260709174453/
```

## Metrics To Report

Always report these together:

- entity-level F1, precision, and recall from `seqeval`;
- token accuracy;
- per-label F1;
- number of validation receipts;
- validation key file;
- label set;
- best epoch;
- latest epoch;
- whether the run is real-only or synthetic-augmented.

Token accuracy is not enough. Receipts are O-heavy, so a model can achieve high
token accuracy while missing the labels that matter.

## Per-Label Metrics

Per-label F1 is the main diagnostic for whether the aggregate score is useful.

Labels that usually validate the model is learning receipt structure:

- `DATE`
- `TIME`
- `PAYMENT_METHOD`
- `MERCHANT_NAME`
- `TAX`
- `SUBTOTAL`
- `GRAND_TOTAL`
- `LINE_TOTAL`

Labels that usually expose the hard part:

- `PRODUCT_NAME`
- `LOYALTY_ID`
- `UNIT_PRICE`
- `DISCOUNT`
- `REFUND`
- `TIP`
- `QUANTITY`

Do not hide weak labels behind aggregate F1. Product search and loyalty/refund
questions depend on these fields.

## Memorization Signals

Track these gaps:

- train-sample F1 versus held-out F1;
- context-seen merchant F1 versus context-unseen merchant F1, or
  training-seen versus training-unseen when the context is a persisted train
  split;
- canonical random split F1 versus adversarial split F1;
- recent-upload F1 when recent uploads are held out versus when they were in
  training;
- synthetic-augmented F1 versus real-only F1 on the exact same validation file.

Large gaps are not automatically bad. Merchant templates are a real production
signal. The risk is claiming generalization when the split only measures
template familiarity.

## Live Curve

The trainer can run windowed held-out evaluation after each saved checkpoint.
That produces `epochs.json` during training. The useful fields are:

- `best_epoch_heldout`
- `num_val_receipts`
- per-epoch `heldout_f1`
- per-epoch `heldout_precision`
- per-epoch `heldout_recall`
- per-epoch `token_accuracy`
- per-epoch `per_label_f1`
- per-epoch `per_label_precision`, `per_label_recall`, and
  `per_label_support`
- per-epoch `product_detail_macro_f1`
- per-epoch `entity_prediction_rate` and `gold_entity_rate`
- `checkpoint`

If F1 plateaus for many epochs while training loss continues to fall, treat the
extra epochs as overfitting risk rather than progress.

Best-checkpoint selection is configurable with `--checkpoint-metric`. The
default remains aggregate entity F1 (`f1`). For first-pass product experiments,
use `--checkpoint-metric product_detail_macro_f1` so `best/` follows
`PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`, and `LINE_TOTAL` instead of easy
non-product fields.

Product-detail loss pressure is configurable with
`--product-detail-loss-weight`. Compare precision and recall separately when
using it; an apparent product macro-F1 gain is not useful if it comes mostly
from low-precision over-prediction.

For v29, judge the jobs this way:

- full weighted head wins only if product-detail macro F1 improves without a
  large precision collapse and without major aggregate held-out F1 regression;
- product-only head wins if it materially improves `PRODUCT_NAME` and
  `UNIT_PRICE`, even if it is not directly promotable as the only production
  model;
- neither wins if the curves remain in the v28 band, in which case the next
  metric work should segment by merchant/template and synthetic source rather
  than launch another same-shape long run.

## Explanation Metrics

New runs also write `diagnostics` inside each `epochs.json` epoch entry. These
fields are meant to explain why the model is or is not improving:

- `token_counts`: total, correct, gold/predicted `O`, and gold/predicted entity
  tokens.
- `entity_counts`: gold versus predicted entity spans, plus predicted/gold
  ratio.
- `rates`: gold entity token rate, predicted entity token rate, `O` token
  accuracy, and entity token accuracy.
- `product_detail`: macro F1 and prediction balance for `PRODUCT_NAME`,
  `QUANTITY`, `UNIT_PRICE`, and `LINE_TOTAL`.
- `per_label_token_counts`: gold versus predicted token counts per label.
- `per_label_entity_counts`: gold versus predicted entity counts per label.
- `top_token_confusions`: the most common token-level label confusions.

Use these to tell whether a run is under-predicting product details, spraying
rare labels everywhere, improving recall at the cost of precision, or merely
getting better at easy non-product fields.

## Diagnostic Artifacts

`epochs.json` explains how a run moved during training. `diagnose-run` explains
why one selected checkpoint behaves the way it does on frozen validation.

Run diagnostics when aggregate/product curves are not enough:

```bash
layoutlm-cli diagnose-run \
  --run-s3-uri s3://layoutlm-training-dev-68164770/runs/<job-name>/ \
  --dynamo-table ReceiptsTable-dc5be22 \
  --output-dir /tmp/<job-name>-diagnostics \
  --output-s3-uri s3://layoutlm-training-dev-68164770/diagnostics/<job-name>/
```

Interpret the diagnostic files this way:

- `summary.json`: top-level evidence for template coverage, line-item
  structure, label/eval mismatch, and model weakness.
- `report.md`: human-readable version of the same evidence.
- `per_receipt.csv`: quick spreadsheet view for sorting by merchant, product
  F1, nearest-template distance, and high-confidence product false positives.
- `groups.json`: grouped merchant/place/template/shape slices.
- `token_errors.jsonl`: the inner loop for debugging confidence, boundary
  errors, false positives, misses, and wrong-label predictions.

Do not treat high-confidence false positives as automatic bad labels. They are
review prompts: either the label/eval contract is too strict, the receipt has
unlabeled valid entities, or the model is confidently wrong.

For product details, diagnostics now split high-confidence product false
positives into review buckets while leaving strict F1 unchanged:

- `likely_unlabeled_product_text`;
- `product_name_numeric_or_code`;
- `numeric_quantity_overprediction`;
- `numeric_amount_overprediction`;
- `adjustment_or_fee_term`;
- `receipt_meta_term`;
- other inspection buckets.

Use those buckets to decide whether the next action is label-contract review,
real receipt collection, synthetic structural coverage, or model architecture
work. The same diagnostic run also emits `data_targeting` in `summary.json` so
missing merchant templates, long item tables, and no-line-total layouts are
visible as data targets rather than buried in token errors.

## DynamoDB Records

When enabled, training writes:

- `Job`: run metadata, status, config, and final results.
- `JobMetric`: per-epoch scalar metrics and dataset statistics.
- `JobLog`: structured config and summary payloads.

`Job.results` keeps both `best_f1`/`best_f1_epoch` and the checkpoint-selection
fields `checkpoint_metric`, `best_checkpoint_metric_value`, and
`best_checkpoint_metric_epoch`. Check the latter before promoting or exporting
from `best/`.

Dataset metrics to keep:

- number of train examples;
- number of validation examples;
- train O:entity ratio before downsampling;
- train O:entity ratio after downsampling;
- validation O:entity ratio;
- target O:entity ratio;
- label merge configuration;
- allowed-label list;
- validation split URI.

Live held-out metrics to keep:

- `heldout_windowed_f1`, precision, recall, and token accuracy;
- `heldout_windowed_product_detail_macro_f1`;
- `heldout_windowed_entity_prediction_rate`;
- `heldout_windowed_f1_delta_vs_training_reported`;
- `heldout_label_{NAME}_{f1,precision,recall,support}` for **every** class in
  the model's own label map.

## Per-Class Held-Out Metrics

Every epoch writes four `JobMetric` rows per class:

| Metric | Unit |
|---|---|
| `heldout_label_{NAME}_f1` | `ratio` |
| `heldout_label_{NAME}_precision` | `ratio` |
| `heldout_label_{NAME}_recall` | `ratio` |
| `heldout_label_{NAME}_support` | `count` |

`{NAME}` is the base label with its BIO prefix stripped, so `B-DATE`/`I-DATE`
both report as `DATE`. The class list comes from the checkpoint's label map,
not from what a given epoch happened to predict — a class the model can emit
but that scored nothing records **support 0 with zeroed scores** rather than
disappearing. A missing row and a zero score are different facts, and only one
of them is evidence.

The class list was previously hardcoded to the four product-detail labels
(`PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`, `LINE_TOTAL`). Runs whose label
vocabulary excluded those four therefore recorded **zero** per-class rows, and
could only be compared on aggregates that do not survive a change of label set.
The names and units above match what the wide 22-class runs already emitted, so
runs from either side of this change compare directly.

## Comparability

Two runs' metrics only mean the same thing if they held out the same receipts.
A run with no pinned canonical split scores itself on a split nobody else used,
yet its `val_f1` looks identical in shape to a frozen-split `val_f1`.

Training therefore refuses to start unless one of the following is true:

- `--val-keys-s3 <s3://.../val_keys.json>` pins the shared frozen split; or
- `--no-frozen-val` explicitly accepts a non-comparable run (legitimate for a
  first-ever run on a new label vocabulary, where no frozen split applies).

Whichever path a run takes is recorded, not inferred:

| Surface | Field |
|---|---|
| `run.json` | `comparability` (`comparable`, `val_keys_s3`, `val_split_source`, `reason`) |
| `Job.job_config` | `metrics_comparable`, `comparability_reason`, `val_split_source` |
| `Job.results` | `metrics_comparable`, `comparability_reason`, `val_keys_s3`, `val_split_source` |
| `JobMetric` | `run_metrics_comparable` (`1.0` / `0.0`, unit `flag`) |

On SageMaker, pass `val_keys_s3` or `no_frozen_val: "true"` as a
hyperparameter.

## Model Identity

`Job.results` also records what the run produced, so a deployed artifact traces
back to it without reading S3 object timestamps:

- `training_job_name`, `training_job_id`, `num_labels`, `label_list`;
- after CoreML export: `coreml_export_id`, `coreml_bundle_s3_uri`,
  `coreml_canonical_bundle_s3_uri`, `coreml_canonical_bundle_etag`,
  `coreml_model_size_bytes`, `coreml_exported_at`.

The link runs both ways: the export worker writes `model_identity.json` into
the CoreML bundle (`export_id`, `training_job_id`,
`source_checkpoint_s3_uri`, `quantize`, `exported_at`), so a bundle found on a
device names its own training run.

## Quick Checks

Check SageMaker status:

```bash
aws sagemaker describe-training-job \
  --region us-east-1 \
  --training-job-name <job-name> \
  --query '{Status:TrainingJobStatus,SecondaryStatus:SecondaryStatus,TrainingEndTime:TrainingEndTime,FailureReason:FailureReason,ModelArtifacts:ModelArtifacts.S3ModelArtifacts}' \
  --output json
```

Inspect live held-out metrics:

```bash
aws s3 cp \
  s3://layoutlm-training-dev-68164770/runs/<job-name>/epochs.json \
  /tmp/<job-name>-epochs.json \
  --region us-east-1

jq '{generated_at,best_epoch_heldout,num_val_receipts,latest:.epochs[-1]}' \
  /tmp/<job-name>-epochs.json
```

Re-score all saved checkpoints:

```bash
layoutlm-cli eval-checkpoints \
  --run-s3-uri s3://layoutlm-training-dev-68164770/runs/<job-name>/ \
  --dynamo-table ReceiptsTable-dc5be22 \
  --output-dir /tmp/<job-name>-eval
```
