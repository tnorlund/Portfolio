# receipt_layoutlm

`receipt_layoutlm` trains and serves the receipt token classifier used by the
Portfolio receipt pipeline. The production target is still LayoutLM v1 because
it uses OCR text plus bounding boxes and can be exported to CoreML. Rendered
synthetic images are useful for QA, but they are not training inputs for the
current runtime.

## Current State

Last updated: 2026-07-09 UTC.

- Active deployed model: `layoutlm-v23-qty-pinned`.
- Active model held-out F1: about `0.719` on its original canonical split.
- Completed full-core adversarial retrain:
  `layoutlm-v25-adversarial-real-20260708-022719`.
- Current adversarial split: validation with all recent uploads plus full
  merchant-template holdouts.
- v25 full-core result: best held-out F1 about `0.532` at epoch 68.
- Completed reduced-label ablation:
  `layoutlm-v25-adversarial-core-real-20260708-032219`. Trainer validation F1
  peaked at `0.626` at epoch 46; this run intentionally excludes product-detail
  labels.
- Completed v27 first-pass comparison:
  `layoutlm-v27-control-adv-20260708-041606` beat the item-window run on
  aggregate held-out F1 (`0.536` vs `0.531`), while
  `layoutlm-v27-item-window-adv-20260708-041259` had the better early
  product-detail macro F1 (`0.397` vs `0.348`).
- Completed v28 product-metric comparison:
  `layoutlm-v28-item-window-prodmetric-20260708-063942` beat the v28 control on
  held-out product-detail macro F1 (`0.390` vs `0.363`), while the control
  remained slightly better on aggregate held-out F1 (`0.528` vs `0.525`).
- Completed v29 improvement ablations:
  `layoutlm-v29-item-window-prodweight-20260708-145418` and
  `layoutlm-v29-product-only-item-window-20260708-145418`. Neither beat the v28
  product-selected item-window checkpoint on first-pass product-detail macro F1
  (`0.354` weighted full-head, `0.332` product-only, versus `0.390` for v28).
- Completed proof-oriented diagnostics for v28 and v29 weighted full-head under
  `s3://layoutlm-training-dev-68164770/diagnostics/`. The current evidence
  points more toward merchant/template coverage, line-item structure, and
  label/eval contract issues than toward "just train longer."
- High-confidence product false positives are now treated as a review queue in
  diagnostics. Current v28 artifacts had `527` high-confidence product
  false-positive tokens; a heuristic bucket classified `233` as likely item
  text pending manual audit, while the rest split across numeric detail,
  SKU/code, adjustment/fee, and other model-error buckets.
- Product-detail first-pass hooks: `--item-window-augment` appends train-only
  line-item-band windows; `--checkpoint-metric product_detail_macro_f1` selects
  the product checkpoint; `--product-detail-loss-weight` increases loss pressure
  on `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`, and `LINE_TOTAL`.
- Main-branch synthetic training hook: not wired in yet. The synthetic loader
  exists in `.worktrees/synth-images-frontend` and must be ported before a fair
  synthetic-augmented SageMaker run.

The v25 number is lower than the active model number by design. It is measured
on a harder split that holds out recent receipts and entire merchant layouts,
not just random receipts.

## Product Details In The First Pass

Historical runs show why this is hard. The scoped line-item model
`layoutlm-v25-line-items-scoped` reached about `0.61` `PRODUCT_NAME` F1, but it
trained and evaluated on cropped line-item bands with only five validation
receipts. The full first-pass `layoutlm-v26-qty-unitprice` reached about `0.36`
`PRODUCT_NAME`, `0.38` `QUANTITY`, and `0.32` `UNIT_PRICE` F1 at its best heldout
epoch.

The v27 item-window experiment did not win overall, but it showed useful
product-detail signal early. The v28 product-metric run confirmed that
selecting by `product_detail_macro_f1` preserves the better product checkpoint:
the item-window run reached held-out product-detail macro F1 `0.390` at epoch
12, compared with the control's `0.363` at epoch 20.

The v29 result answered the immediate ablation question: neither a weighted
full head nor a product-only head beat v28. The next useful work is not another
long same-shape epoch run. It is to prove which failure mode dominates:
merchant-template coverage, line-item structure, label/eval mismatch, or actual
model weakness. Use `diagnose-run` to produce that evidence before choosing
between synthetic template augmentation, line-item structural features, label
contract cleanup, or a model architecture change.

The immediate data targets are missing merchant-template variants, long item
tables, and layouts without explicit line totals. For the current adversarial
split, synthetic data should use structural analogs rather than exact held-out
merchant names/templates; exact held-out merchant coverage should come with a
new honest split.

## What The Model Learns

The model works because receipts are semi-structured documents. Labels such as
`DATE`, `TIME`, `MERCHANT_NAME`, `PAYMENT_METHOD`, `TAX`, and `GRAND_TOTAL`
usually appear in predictable regions and near predictable words. Merchant
templates amplify that signal because repeated receipts from the same merchant
reuse column positions, headers, totals blocks, loyalty sections, and item rows.

The model is not just memorizing exact receipts, but it is partly learning
merchant-specific layout templates. That is useful in production and dangerous
for evaluation. Any claim about generalization must separate seen-merchant,
unseen-merchant, recent-upload, and full-template holdout slices.

## Main Commands

Install from the repo root:

```bash
pip install -e receipt_dynamo
pip install -e receipt_layoutlm
```

Train locally against DynamoDB:

```bash
export DYNAMO_TABLE_NAME=ReceiptsTable-dc5be22
layoutlm-cli train \
  --job-name local-layoutlm-smoke \
  --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 5 \
  --batch-size 8 \
  --lr 1e-5
```

Run the current SageMaker path through the training Lambda rather than using
local shell commands for long jobs. Keep the validation split pinned when
comparing runs:

```text
s3://layoutlm-training-dev-68164770/config/adversarial_val_keys_v2_20260708.json
```

Re-score a run's checkpoints on its frozen validation set:

```bash
layoutlm-cli eval-checkpoints \
  --run-s3-uri s3://layoutlm-training-dev-68164770/runs/<job-name>/ \
  --dynamo-table ReceiptsTable-dc5be22 \
  --output-dir /tmp/<job-name>-eval
```

Diagnose one selected checkpoint receipt-by-receipt:

```bash
layoutlm-cli diagnose-run \
  --run-s3-uri s3://layoutlm-training-dev-68164770/runs/<job-name>/ \
  --dynamo-table ReceiptsTable-dc5be22 \
  --output-dir /tmp/<job-name>-diagnostics \
  --output-s3-uri s3://layoutlm-training-dev-68164770/diagnostics/<job-name>/
```

The diagnostic artifacts include `summary.json`, `report.md`,
`per_receipt.csv`, `per_receipt.jsonl`, `groups.json`, and
`token_errors.jsonl`.

## Related Docs

- [EVALUATION.md](./EVALUATION.md): validation contracts, current scorecard,
  and devil's advocate checks.
- [HYPERPARAMS.md](./HYPERPARAMS.md): current training strategy and parallel
  experiment lanes.
- [METRICS.md](./METRICS.md): metrics artifacts, live `epochs.json`, and how to
  interpret the curves.
