# LayoutLM Training Strategy

Last updated: 2026-07-09 UTC.

The current receipt model should be improved with controlled parallel
experiments, not by waiting for one 100-epoch job and guessing from the final
aggregate F1.

## Current Baseline Recipe

The current v25 adversarial real-only baseline uses:

- pretrained model: `microsoft/layoutlm-base-uncased`
- model version: `v1`
- epochs: `100`
- batch size: `8`
- learning rate: `1e-5`
- warmup ratio: `0.1`
- gradient accumulation: `1`
- label smoothing: `0.0`
- early stopping patience: `15`
- validation split: `adversarial_val_keys_v2_20260708.json`
- labels: all 22 current core labels
- live held-out eval: enabled, writes `epochs.json`

This recipe is intentionally conservative. The low learning rate and 22-label
head can take many epochs to settle, especially with receipt windows that are
mostly `O` tokens and with sparse labels such as `LOYALTY_ID`, `DISCOUNT`,
`REFUND`, `TIP`, and `UNIT_PRICE`.

## Why So Many Epochs

Many epochs are needed here because the run is doing several hard things at
once:

- adapting a generic LayoutLM base model to receipt-specific OCR geometry;
- learning a new 22-label BIO head;
- separating many visually similar numeric fields;
- learning sparse labels with little support;
- handling long receipts through sliding windows;
- training with a low learning rate.

More epochs are not a guarantee of better generalization. If held-out F1
plateaus while training loss keeps falling, more epochs mostly increase
template memorization and confidence in wrong predictions.

## Labels

The full 22-label model is useful for product search and receipt intelligence,
but it is harder than the deployed v23 head. Do not compare v23 aggregate F1
directly to full-core v24/v25 without calling out the label-set difference.

Current weak labels to watch:

- `PRODUCT_NAME`
- `LOYALTY_ID`
- `UNIT_PRICE`
- `DISCOUNT`
- `REFUND`
- `TIP`
- `QUANTITY`

Useful label ablations can run in parallel:

- full-core control: all 22 labels, real receipts only;
- operational-core model: merchant, date/time, payment, totals, address/phone;
- line-item model: `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`, `LINE_TOTAL`, plus
  enough anchors to crop and reconcile;
- merged-amount model: merge `LINE_TOTAL`, `SUBTOTAL`, `TAX`, and `GRAND_TOTAL`
  only if the product need is "find any amount" rather than exact field type.

Use `--allowed-label` and `--label-merges` for these ablations. Keep the same
validation split so the comparison is honest.

## Parallel Experiment Lanes

We can train multiple jobs at once. The useful parallel lanes are:

1. Real-only adversarial control.
   v25 is complete. The fresh-image v27 control completed with best live
   held-out F1 about `0.536`.

2. Short hyperparameter sweep.
   Run 3-5 epoch jobs on the same adversarial split to test learning rate,
   label smoothing, and O-token downsampling. Pick winners for full retrain.

3. Label-scope ablation.
   Train a smaller label head to test whether the full 22-label task is hurting
   high-value extraction.

4. Synthetic-loader port.
   Port the synthetic training hook from `.worktrees/synth-images-frontend` into
   main before launching a synthetic SageMaker job.

5. First-pass item-window augmentation.
   Use `--item-window-augment` on the full 22-label head. This appends cropped
   line-item-band windows to the training split only, while validation and
   inference stay full-receipt/windowed. v27 did not beat the control on
   aggregate F1, but it had the stronger early product-detail checkpoint. v28
   confirmed that `--checkpoint-metric product_detail_macro_f1` preserves that
   better product checkpoint: item-window held-out product-detail macro F1 was
   about `0.390`, compared with the control's `0.363`.

6. Synthetic-augmented run.
   Use the exact same adversarial validation split, add synthetic rows to the
   training split only, and exclude heldout merchants from synthetic generation.

Avoid running multiple long jobs that differ only by random seed. That can be
useful later, but it does not answer the current product question as quickly as
label, data, and synthesis ablations.

Current concurrency note: as of this update, the account limit for
`ml.g5.xlarge` training usage is 2 instances. A third concurrent `g5.xlarge`
job will be rejected unless one run finishes or the quota is increased. Use the
two slots for meaningfully different experiments.

## Current Improvement Result

The v29 jobs were intentionally different:

- `layoutlm-v29-item-window-prodweight-20260708-145418`: full 22-label head,
  item-window augmentation, product checkpointing, and
  `--product-detail-loss-weight 1.5`.
- `layoutlm-v29-product-only-item-window-20260708-145418`: product-only head
  with `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`, and `LINE_TOTAL`, plus
  item-window augmentation.

Both completed below the v28 product-selected item-window checkpoint:

- v28 item-window product-selected: product-detail macro F1 about `0.390`.
- v29 weighted full-head: product-detail macro F1 about `0.354`.
- v29 product-only head: product-detail macro F1 about `0.332`.

This rules out two easy fixes for now: simply increasing product-label loss
weight and simply narrowing the head to product labels. Do not spend more long
runs on epoch count alone. Move to proof-driven data changes: merchant-template
slices, synthetic product-row augmentation, label/eval contract review, and
product-line structural priors.

Merchant layout repetition is useful but easy to fool ourselves with. Report
context-seen and held-out-template metrics separately before deciding that a
change generalizes; call it training-seen only when the diagnostic context comes
from persisted train receipt keys.

Use `layoutlm-cli diagnose-run` before launching the next training lane. The
current v28/v29 diagnostics support these directions:

- Template coverage: context-unseen merchants scored worse than context-seen
  merchants, and product F1 fell as nearest-template distance increased.
- Line-item structure: missing `LINE_TOTAL` columns and very long item tables
  are materially weaker slices.
- Label/eval mismatch: high-confidence product false positives need review
  before we decide whether they are model errors or unlabeled-but-useful tokens.
- Model weakness: low-confidence product errors remain, but they are not the
  only failure mode, so an architecture change should wait until coverage,
  structure, and labels have been isolated.

## Next Product-Detail Data Lane

The next useful data change is targeted, not broad. Current diagnostics point at
three data targets:

- merchant-template variants for `THE HOME DEPOT`, `COSTCO WHOLESALE`,
  `WILD FORK`, and `VONS` style layouts;
- long item tables, especially `20-39` item rows, where v28 averaged product
  F1 about `0.125`;
- no-line-total layouts, where v28 averaged product F1 about `0.108` versus
  about `0.470` when a line-total column was present.

For the current adversarial split, do not synthesize exact held-out merchant
names or copied held-out templates. Use real uploads for those merchants when
the goal is production coverage, then create a new honest split. Use synthetic
receipts for structural analogs: warehouse-club layouts without line totals,
hardware-store SKU/code rows, grocery/butcher long item tables, and item rows
with discounts/refunds/fees that are explicitly not product names.

The next train job should be launched only after one of these inputs changes:

- a reviewed real-receipt batch is added for the missing merchant/template
  shapes;
- a synthetic training bundle is wired into main and excludes held-out
  merchants/templates;
- or the product label/eval contract changes and is reflected in both training
  labels and diagnostics.

## Sweep Knobs

Good first sweep values:

- learning rate: `5e-6`, `1e-5`, `2e-5`, `3e-5`
- label smoothing: `0.0`, `0.03`, `0.05`
- O:entity ratio target: unset, `2.0`, `3.0`
- early stopping patience for sweeps: `3` to `5`
- epochs for sweeps: `3` to `5`

Keep these fixed during sweeps:

- validation key file;
- label set for the experiment lane;
- receipt window size and stride;
- real-only versus synthetic augmentation status.
- first-pass item-window augmentation status.

## Command Shape

Local command shape:

```bash
layoutlm-cli train \
  --job-name <job-name> \
  --dynamo-table ReceiptsTable-dc5be22 \
  --epochs 5 \
  --batch-size 8 \
  --lr 1e-5 \
  --warmup-ratio 0.1 \
  --early-stopping-patience 4 \
  --val-keys-s3 s3://layoutlm-training-dev-68164770/config/adversarial_val_keys_v2_20260708.json
```

First-pass product-detail experiment:

```bash
layoutlm-cli train \
  --job-name <job-name> \
  --dynamo-table ReceiptsTable-dc5be22 \
  --epochs 100 \
  --batch-size 8 \
  --lr 1e-5 \
  --warmup-ratio 0.1 \
  --early-stopping-patience 15 \
  --checkpoint-metric product_detail_macro_f1 \
  --product-detail-loss-weight 1.5 \
  --val-keys-s3 s3://layoutlm-training-dev-68164770/config/adversarial_val_keys_v2_20260708.json \
  --item-window-augment
```

Use `--product-detail-loss-weight` conservatively. Start with `1.5`; values much
larger than that may improve product recall by spraying product labels into
non-product regions.

For SageMaker, pass the same settings as Lambda hyperparameters so the training
container builds the equivalent `layoutlm-cli train` command.

## Promotion Rules

Do not promote from aggregate F1 alone. A model is promotable only if:

- it beats the current candidate on the same pinned validation split;
- the improvement is visible in the slices we care about;
- weak-label behavior is understood, not hidden by easy-label gains;
- recent-upload and merchant-template holdout results are reported separately;
- synthetic training, if used, did not include heldout merchants or templates;
- CoreML export and inference validation pass for the target runtime.
