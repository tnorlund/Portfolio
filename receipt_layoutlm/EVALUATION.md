# LayoutLM Evaluation

Last updated: 2026-07-09 UTC.

Evaluation is now built around one rule: do not trust a single random validation
score for receipt understanding. Random receipt splits can put the same merchant
template in train and validation, which makes the model look better than it is
at handling new uploads or new merchant layouts.

## Current Validation Contract

Use pinned receipt-key files in S3 for all serious comparisons. A run is only
comparable to another run when both use the same validation key file, same label
set, and same inference windowing.

Current adversarial split:

```text
s3://layoutlm-training-dev-68164770/config/adversarial_val_keys_v2_20260708.json
```

This split intentionally holds out:

- all post-active-model uploads available when the split was created;
- selected full merchant templates;
- the same receipt keys for real-only and synthetic-augmented comparisons.

The full merchant-template holdouts are:

- `COSTCO WHOLESALE`
- `VONS`
- `THE HOME DEPOT`
- `WILD FORK`
- `TARGET GROCERY`

Do not train on synthetic examples derived from those merchants when reporting
generalization against this split. That would turn the experiment into a
template-leakage test.

## Current Scorecard

Active deployed model:

- job: `layoutlm-v23-qty-pinned`
- best checkpoint: `s3://layoutlm-training-dev-68164770/runs/layoutlm-v23-qty-pinned/best/`
- original held-out F1: about `0.719`
- important limitation: the label head does not include `PRODUCT_NAME` or
  `LOYALTY_ID`

Recent full-core real-only retrain:

- job: `layoutlm-v24-fullcore-real-20260708-004112`
- trained on real receipts only
- label set: 22 core labels, including `PRODUCT_NAME` and `LOYALTY_ID`
- original canonical held-out F1: about `0.665`
- weak labels: `PRODUCT_NAME`, `LOYALTY_ID`, `UNIT_PRICE`, `DISCOUNT`, `REFUND`
- contaminated for recent-upload evaluation because the new uploads were in the
  training split

Current adversarial real-only baseline:

- job: `layoutlm-v25-adversarial-real-20260708-022719`
- validation file: `adversarial_val_keys_v2_20260708.json`
- status at doc update: SageMaker completed
- best live held-out F1: about `0.532` at epoch 68
- latest reviewed epoch: 83
- validation receipts: 168

The v25 score is lower because the split is harder. Treat it as a better
generalization estimate, not as a failed run by itself.

Parallel reduced-label ablation:

- job: `layoutlm-v25-adversarial-core-real-20260708-032219`
- validation file: `adversarial_val_keys_v2_20260708.json`
- labels: merchant/contact/date/time/payment/totals core labels
- purpose: test whether the full 22-label head is hurting core extraction
- status at doc update: SageMaker completed
- best trainer validation F1: about `0.626` at epoch 46
- final trainer validation F1: about `0.625` at epoch 56
- important limitation: excludes `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`,
  `LOYALTY_ID`, `COUPON`, `DISCOUNT`, and `REFUND`

Product-detail reference runs:

- `layoutlm-v25-line-items-scoped`: cropped line-item-band distribution,
  `PRODUCT_NAME` F1 about `0.61`, `LINE_TOTAL` about `0.79`, but validation was
  only five scoped receipts and does not represent first-pass inference.
- `layoutlm-v26-qty-unitprice`: full first-pass distribution, best held-out F1
  about `0.681`, with `PRODUCT_NAME` about `0.36`, `QUANTITY` about `0.38`, and
  `UNIT_PRICE` about `0.32`.
- New first-pass experiment hook: `--item-window-augment`, which adds cropped
  line-item-band windows to train only and keeps validation/inference on full
  receipts.

Completed v27 dev comparison:

- `layoutlm-v27-control-adv-20260708-041606`: fresh full-core control on the
  newly deployed dev image. Best live held-out F1 was about `0.536` at epoch
  65. Best held-out product-detail macro F1 was about `0.348` at epoch 21.
- `layoutlm-v27-item-window-adv-20260708-041259`: same recipe plus
  `--item-window-augment`, `--item-window-size 200`, and
  `--item-window-stride 150`. Best live held-out F1 was about `0.531` at epoch
  38. Best held-out product-detail macro F1 was about `0.397` at epoch 12.

Interpretation: item-window augmentation is not enough to improve the overall
first-pass model as-is, but it helps product labels early.

Completed v28 product-metric comparison:

- `layoutlm-v28-control-prodmetric-20260708-063942`: selected checkpoints by
  `--checkpoint-metric product_detail_macro_f1`; best held-out product-detail
  macro F1 was about `0.363` at epoch 20; best aggregate held-out F1 was about
  `0.528` at epoch 25.
- `layoutlm-v28-item-window-prodmetric-20260708-063942`: same checkpoint metric
  plus `--item-window-augment`; best held-out product-detail macro F1 was about
  `0.390` at epoch 12; best aggregate held-out F1 was about `0.525` at epoch
  15.

Interpretation: selecting `best/` by product-detail macro F1 works, and the
item-window recipe is the better product-detail checkpoint. It still does not
solve `PRODUCT_NAME`: the best v28 item-window checkpoint had `PRODUCT_NAME` F1
around `0.25` at the product-selected epoch, while `UNIT_PRICE` and
`LINE_TOTAL` benefited more from the item-window signal.

Completed v29 improvement ablations:

- `layoutlm-v29-item-window-prodweight-20260708-145418`: full 22-label head,
  item-window augmentation, product checkpointing, and
  `--product-detail-loss-weight 1.5`.
- `layoutlm-v29-product-only-item-window-20260708-145418`: item-window
  augmentation with only `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`, and
  `LINE_TOTAL` allowed.

Both v29 SageMaker jobs completed. The weighted full-head run reached best
product-detail macro F1 about `0.354`; the product-only run reached about
`0.332`. Both were below the v28 item-window product-selected checkpoint's
`0.390`. This argues against "just weight product labels more" or "just shrink
the head to product labels" as the next main direction.

Completed checkpoint diagnostics:

- v28 diagnostic job:
  `layoutlm-diag-v28-item-window-v2-cpu-20260709174453`
- v29 weighted diagnostic job:
  `layoutlm-diag-v29-weighted-v2-cpu-20260709174453`
- output prefix:
  `s3://layoutlm-training-dev-68164770/diagnostics/`

The v28 diagnostic report found held-out F1 `0.5199` and product-detail macro
F1 `0.3900`. The v29 weighted diagnostic report found held-out F1 `0.5022` and
product-detail macro F1 `0.3536`.

## Required Slices

Every serious report should include these slices:

- all adversarial validation receipts;
- recent-upload holdout;
- full merchant-template holdout;
- seen-merchant validation receipts;
- unseen-merchant validation receipts;
- product-heavy receipts;
- receipts containing any weak label;
- per-merchant results for merchants with enough support;
- in-sample train sample, clearly labeled as contaminated.

The in-sample number is useful only as a memorization/template-fitting warning.
For v24, a train sample scored about `0.913` while canonical heldout was about
`0.665`. That gap shows real learning plus substantial dependence on repeated
formats.

## Failure-Mode Diagnostics

Use `layoutlm-cli diagnose-run` when deciding why a checkpoint failed. It scores
one selected checkpoint on the frozen validation receipts and writes:

- `summary.json`: aggregate scores plus hypothesis evidence.
- `report.md`: readable scorecard for the same evidence.
- `per_receipt.csv` and `per_receipt.jsonl`: one row per validation receipt.
- `groups.json`: merchant, place, template, line-item, and distance slices.
- `token_errors.jsonl`: every incorrect token with gold label, predicted label,
  confidence, top probabilities, and error kind.

The four hypotheses should be separated this way:

- Template coverage: unseen merchants/templates should score worse than seen
  merchants/templates, and nearest-template distance should correlate
  negatively with product F1.
- Line-item structure: repeated item-count or column-presence buckets should
  show consistent weak slices, such as no `LINE_TOTAL` column or very long item
  tables.
- Label/eval mismatch: high-confidence false positives, especially product-like
  tokens labeled `O`, suggest the model may be finding plausible product fields
  that the current label/eval contract does not accept.
- Model weakness: many low-confidence product errors across both seen and
  unseen templates suggest capacity, architecture, or input representation is
  the blocker.

Current v28/v29 diagnostics support template and structure effects. For v28,
seen merchants averaged product F1 about `0.472`, unseen merchants about
`0.375`, and nearest-template distance correlated with product F1 at about
`-0.315`. Receipts with `20-39` item rows averaged product F1 about `0.125`,
and receipts without a `LINE_TOTAL` column averaged about `0.108`. The token
logs also show label/eval tension: v28 had `527` high-confidence product false
positive tokens and many `O -> PRODUCT_NAME` product confusions.

## How To Interpret Scores

High `DATE`, `TIME`, `PAYMENT_METHOD`, `MERCHANT_NAME`, and totals performance
usually means the model has learned stable receipt geometry and local text
patterns.

Weak `PRODUCT_NAME`, `LOYALTY_ID`, `UNIT_PRICE`, `DISCOUNT`, and `REFUND`
performance usually means at least one of these is true:

- the label is sparse;
- the label boundary is ambiguous at word level;
- the label appears in many merchant-specific formats;
- the model sees many similar numeric/text tokens with different meanings;
- we are asking one global head to solve a field that may need post-processing
  or merchant-aware rules.

## Devil's Advocate Checks

Before promoting a model, answer these questions:

- Did the validation split include the same merchant templates as training?
- Did recent uploads appear in training?
- Did synthetic examples use validation merchants or validation receipt-derived
  structure?
- Did F1 improve only because the model over-predicted common labels?
- Did weak-label F1 improve, or did the aggregate move because easy labels got
  slightly better?
- Are we reporting entity-level `seqeval` F1, token accuracy, and per-label F1
  separately?
- Are we comparing against the active model on a compatible label set?
- If product-detail F1 improves, did precision stay usable, or did the model
  merely spray product labels into non-product regions?
- If a product-only run beats the full-head run, are we willing to use a
  product-specialized model/head instead of forcing one flat label head to do
  everything?
- If synthetic data improves seen merchants but not held-out merchant
  templates, are we measuring template imitation instead of generalization?

## Synthetic Evaluation Contract

A synthetic-augmented run is fair only when:

- it uses the exact same validation key file as the real-only baseline;
- synthetic rows are added to training only;
- validation stays real receipts only;
- held-out merchants and held-out receipt-derived templates are excluded from
  synthetic generation;
- the synthetic bundle passes its quality gates and mix-balance checks;
- results are reported by slice, not only aggregate F1.

The first useful synthetic question is not "can synthetic beat the active
model?" The useful question is "does synthetic improve v25's adversarial slices
without leaking the heldout merchant templates?"
