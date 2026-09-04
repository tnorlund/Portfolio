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

**Always pass `--val-keys-s3`.** A run that omits it persists its split only
into `runs/<job>/run.json`, and that is not a durable location — see "Pin the
split, or lose the run" below.

Current splits (2026-09-04), built by
`scripts/build_layoutlm_val_splits.py` over 906 labelled receipts:

```text
s3://layoutlm-training-dev-68164770/config/val_keys_random_20260904.json
s3://layoutlm-training-dev-68164770/config/val_keys_template_20260904.json
```

| Split | Receipts | Hash | Measures |
|---|---|---|---|
| `random` | 163 (18.0%) | `4d2a0a60b6ba85e5` | in-distribution accuracy; 56 brands appear on both sides |
| `template` | 161 (17.8%) | `fad772f0d6946525` | generalization to unseen layouts; 0 brands shared |

Run both. The gap between them *is* the memorization measurement — a model that
scores well on `random` and poorly on `template` has learned merchant templates,
not receipt structure. They are deliberately near-identical in size so the two
numbers are directly readable against each other.

The template split holds out every receipt of `costco`, `vons`, `homedepot`,
`wildfork`, and `target`.

### Group by brand key, not by merchant name

A template holdout must group by *brand*, not by the stored `merchant_name`.
This corpus spells one chain several ways — `TRADER JOE'S`, `Trader Joe's`,
`Trader Joe's Store #0058` — so holding out one spelling while training on
another puts the same store's layout on both sides and inflates the score.
`brand_key()` in the split builder normalizes case and store decoration, then
unions any brand keys sharing a `place_id` (which is what merges
`Roast & Rice Asian Fusion` with `Roast and Rice Kitchen`).

Do **not** try to fix this by adopting Google Places canonical names. Several
`place_id`s here resolve to a street address (`'2716 N Green Valley Pkwy'` for a
Trader Joe's, `'791 Marks St'` for a Costco), so that path reintroduces the
address-as-merchant bug. `scripts/normalize_merchant_names.py` is the right
tool for the stored names: it fixes casing only, using mixed-case siblings under
the same `place_id` as evidence, and rejects any rewrite that changes more than
case.

### Pin the split, or lose the run

`layoutlm-v31-nonproduct-clean-20260729` — the active model — passed no
`--val-keys-s3`. Its split lived only in `runs/<job>/run.json`, which the
bucket lifecycle rule then deleted along with `best/` and
`output/model.tar.gz`. The seed-reconstruction fallback does not save you: it
re-derives the split from the *current* labelled corpus, which drifts. Rerunning
it today yields 91 receipts against a recorded 82, and the hash does not match.

So v31's `0.744` is unreproducible and unusable as a baseline, and its weights
are gone. The lifecycle rule now archives `runs/` instead of expiring it
(`infra/sagemaker_training/component.py`), but that only protects runs from here
on. Pin the split anyway.

Previous adversarial split:

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

Newer runs, recorded 2026-09-04 (this section previously stopped at v29):

- `layoutlm-v30-fullcore-clean-data-20260713-222017`: all 22 labels, no merges,
  163 val receipts (hash `5636de8b0c83b2e3`), best held-out F1 about `0.563` at
  epoch 29.
- `layoutlm-v31-nonproduct-clean-20260729`: **the active model.** Eight classes
  (`MERCHANT_NAME`, `DATE`, `TIME`, `AMOUNT`, `ADDRESS`, `WEBSITE`,
  `STORE_HOURS`, `PAYMENT_METHOD`) with `AMOUNT = LINE_TOTAL + SUBTOTAL + TAX +
  GRAND_TOTAL` and `ADDRESS = ADDRESS_LINE + PHONE_NUMBER`. 82 val receipts
  (hash `2b7c68b0568183c4`), best held-out F1 about `0.744` at epoch 53.

**v31's 0.744 is not a v30 improvement**, and the two must not be quoted
side by side. Three things changed at once: different held-out receipts (82 vs
163, different hash, different seed), 8 classes instead of 22, and the four
hardest-to-separate numeric fields merged into one. Merging away the dominant
confusion class and reporting a higher F1 measures an easier task. Neither
number is recoverable as a baseline — see "Pin the split, or lose the run".

A consequence worth stating plainly: because `AMOUNT` absorbs `GRAND_TOTAL`,
the deployed model cannot distinguish a grand total from tax or a subtotal.
Ground-truth work on `GRAND_TOTAL` is still correct and still needed by any
unmerged head, but it will not move this model's metric.

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

- Template coverage: context-unseen merchants/templates should score worse than
  context-seen merchants/templates, and nearest-template distance should
  correlate negatively with product F1. Treat this as training coverage only
  when the diagnostic context comes from persisted train receipt keys; otherwise
  it is current-corpus coverage.
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
context-seen merchants averaged product F1 about `0.472`, context-unseen
merchants about `0.375`, and nearest-template distance correlated with product
F1 at about `-0.315`. These v28/v29 diagnostics used current VALID corpus
context excluding the full frozen validation split, not a persisted historical
train snapshot. Receipts with `20-39` item rows averaged product F1 about
`0.125`, and receipts without a `LINE_TOTAL` column averaged about `0.108`. The
token logs also show label/eval tension: v28 had `527` high-confidence product
false positive tokens and many `O -> PRODUCT_NAME` product confusions.

Those product false positives are not one failure mode. The current heuristic
diagnostic buckets split the `527` high-confidence product false-positive tokens
in the v28 artifacts into:

- `233` tokens classified as likely unlabeled product text pending manual
  audit;
- `97` numeric amount overpredictions;
- `96` numeric quantity overpredictions;
- `55` product-name numeric/code boundary cases;
- `21` refund, fee, discount, tax, or deposit terms;
- `25` other product-name false positives.

The v29 weighted run was similar: `268` of `556` high-confidence product false
positives landed in the same heuristic likely-product-text bucket. This does
not mean the labels are bad or that the metric should be relaxed. It means
strict product F1 must be read alongside a product false-positive review queue
before we claim the model is hallucinating product names.

## Product Label/Eval Contract

Strict evaluation stays strict: only VALID gold spans count as correct. A token
that looks like an item but is labeled `O` is still an `O -> PRODUCT_NAME` false
positive for F1.

Diagnostics now add a second, explanatory contract for high-confidence product
false positives:

- `likely_unlabeled_product_text`: a heuristic audit queue for examples that
  may need more real/synthetic coverage, but do not auto-credit the prediction.
- `product_name_numeric_or_code`: inspect SKU/code boundaries separately from
  product-word boundaries.
- `numeric_quantity_overprediction` and `numeric_amount_overprediction`: treat
  as column-structure errors unless the gold contract explicitly labels that
  numeric role.
- `adjustment_or_fee_term` and `receipt_meta_term`: keep outside
  `PRODUCT_NAME` unless the business contract changes.

This gives us two truths at once: strict F1 remains comparable across jobs, and
the review queue explains whether false positives point to contract ambiguity,
missing template coverage, or actual model weakness.

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
