## LayoutLM training session notes (2025-09-19)

### Goals

- Train a LayoutLM model for receipt token classification.
- Make training faster/reliable on EC2; prep for inference on Fargate/CPU and locally on Mac.
- Improve accuracy with better labels, class handling, and hyperparameters.

### Key fixes and changes today

- EC2/Pulumi bootstrap

  - Escaped shell vars in user-data to avoid string.Template collisions (e.g., `$${LD_LIBRARY_PATH:-}`).
  - Pinned to system AWS CLI path to avoid conda/awscli mismatch: use `/usr/bin/aws`.
  - Ensured `/opt/wheels` is writable by `ec2-user` (install dir + chown/chmod).
  - Instance type: moved to `g5.xlarge` for faster training; batch size guidance below.

- Data pipeline (`receipt_layoutlm/receipt_layoutlm/data_loader.py`)

  - BIO tagging per line, receipt-level train/val split to prevent leakage.
  - Include all words for receipts containing any VALID labels; unlabeled → `O`.
  - Downsample all-`O` training lines to target O:entity ratio via env `LAYOUTLM_O_TO_ENTITY_RATIO`.
  - Label normalization: map synonyms, drop noise to `O`:
    - BUSINESS_NAME→MERCHANT_NAME, PHONE→PHONE_NUMBER, ITEM_TOTAL→LINE_TOTAL, ITEM_NAME→PRODUCT_NAME, ITEM_PRICE→UNIT_PRICE, TOTAL→GRAND_TOTAL; NONE/NO_LABEL/OTHER/AUTH_CODE/CHANGE→`O`.
  - New options:
    - Allowed-label whitelist (others → `O`).
    - Optional merge of amount-like labels (LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL) into `AMOUNT`.

- Trainer (`receipt_layoutlm/receipt_layoutlm/trainer.py`)

  - Seqeval metrics (F1/Precision/Recall), load-best-model-at-end, save_total_limit=1.
  - Early stopping enabled (patience configurable in code; default 2).
  - Performance: TF32, cuDNN autotune, parallel map, dataloader workers, group_by_length, gradient checkpointing, optional torch.compile, FP16 eval.
  - Safer checkpoint resume logic (label-set compatibility handled implicitly by fresh job dirs).

- CLI (`receipt_layoutlm/receipt_layoutlm/cli.py`)
  - New flags:
    - `--warmup-ratio FLOAT`
    - `--allowed-label LABEL` (repeatable)
    - `--merge-amounts`
  - Existing: `--label-smoothing`, `--o-entity-ratio`.

### Practical commands

- Verify CLI version/flags on EC2:

```bash
layoutlm-cli train --help | egrep -e 'warmup-ratio|allowed-label|merge-amounts'
python -c "import importlib.metadata as m, receipt_layoutlm as r; print(m.version('receipt-layoutlm')); print(r.__file__)"
```

- Typical training (normalized + merged amounts + whitelist):

```bash
layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 20 --batch-size 128 --lr 6e-5 --warmup-ratio 0.2 \
  --label-smoothing 0.1 --o-entity-ratio 2.0 --merge-amounts \
  --allowed-label MERCHANT_NAME --allowed-label PHONE_NUMBER \
  --allowed-label ADDRESS_LINE --allowed-label DATE --allowed-label TIME \
  --allowed-label PRODUCT_NAME --allowed-label AMOUNT
```

#### Dataset snapshots (skip DynamoDB + preprocessing)

- Save a tokenized dataset once, then reuse:

```bash
SNAP=/opt/hf_cache/datasets/receipt_layoutlm_snapshot
layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 1 --batch-size 128 --lr 6e-5 --merge-amounts \
  --allowed-label MERCHANT_NAME --allowed-label PHONE_NUMBER \
  --allowed-label ADDRESS_LINE --allowed-label DATE --allowed-label TIME \
  --allowed-label PRODUCT_NAME --allowed-label AMOUNT \
  --dataset-snapshot-save "$SNAP"

# Subsequent runs (much faster startup):
layoutlm-cli train --job-name "$JOB2" --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 20 --batch-size 128 --lr 6e-5 \
  --dataset-snapshot-load "$SNAP"
```

- Copy artifacts to S3 (use system AWS CLI):

```bash
# model outputs (Trainer):
JOB=receipts-<id>
/usr/bin/aws s3 sync \
  "/tmp/receipt_layoutlm/$JOB" \
  "s3://<bucket>/models/$JOB" --only-show-errors

# training logs (if kept under ~/runs):
/usr/bin/aws s3 sync \
  "$HOME/runs/$JOB" \
  "s3://<bucket>/runs/$JOB" --only-show-errors
```

### Label strategy takeaways

- Core set (`receipt_label/receipt_label/constants.py`) is good; performance was hurt by rare/noisy synonyms and catch-alls.
- Normalization improved precision and stability.
- When accuracy is the goal, merging totals into `AMOUNT` often boosts early F1 by reducing class confusion. Include `AMOUNT` in whitelist when merging.
- If needed later, retrain without `--merge-amounts` to regain granularity among totals.

### Hyperparameter guidance

- Warmup: 0.1–0.2 typically stable.
- LR: 5e-5 to 8e-5 for batch 128; 5e-5 to 6e-5 for batch 64.
- Label smoothing: 0.05–0.1 can help noisy labels.
- O:entity ratio: lower value → fewer negatives kept → higher recall. Start 2.0; try 1.5 if recall lags.
- Epochs: up to 20 with early stopping; expect best around 6–12 on this dataset.

### Expected F1 trajectory (rough)

- Epochs 1–3: 0.05–0.25
- Epochs 4–8: 0.30–0.5+
- Epochs 8–12: 0.5–0.7 if labels are reasonably clean
  Notes: Multi-token entities (ADDRESS_LINE, PRODUCT_NAME) and overlapping totals slow early gains. Normalization/merging helps.

### Instance and batch size

- `g5.xlarge` (A10G 24GB) runs batch 128 comfortably with current settings; faster $/epoch.
- `g4dn.xlarge` (T4 16GB) can run batch 64; if OOM, use 32 with grad accumulation 2 (effective 64). Training slower; consider Spot.

### Troubleshooting

- Conda AWS CLI crash (`KeyError: opsworkscm`): call system CLI as `/usr/bin/aws`.
- TorchVision image extension warning is benign (not using torchvision.io).
- Checkpoint head mismatch after label changes: start a new `--job-name` to avoid resuming incompatible checkpoints.

### Evaluation and analysis

- `dev.label_counts.py`: reports per-split counts, token totals, O:entity ratio; use `--ratio` to simulate downsampling.
- `dev.layout_lm_inference.py`: pulls a random receipt, compares predictions vs VALID labels, prints per-label precision/recall/F1 and sample mismatches.

### Next steps

- Optionally expose `--gradient-accumulation-steps` and `--early-stopping-patience` via CLI.
- Consider EC2 Golden AMI or Image Builder for faster cold starts.
- Prepare an inference Docker for Fargate (CPU) with model auto-download from S3.

Biggest factors:

- Label noise and overlap: many INVALID/NEEDS_REVIEW and ambiguous totals (now mitigated by merging to AMOUNT).
- Heavy negative bias: validation O:entity ~5.4 and training kept many all-O lines; model became conservative (precision > recall).
- Strict entity-level metric (seqeval): any boundary error on multi-token entities (ADDRESS_LINE, PRODUCT_NAME) zeros that span’s F1.
- Subtoken supervision: you’re labeling every subtoken; standard practice is to supervise only the first subtoken and ignore the rest (-100). Labeling all subtokens makes boundary errors count multiple times and depresses F1.
- Early stopping likely too aggressive with noisy data; training didn’t get enough epochs to climb past ~0.3.
  What will most likely fix it:
- Supervise only the first subtoken; set non-first subtoken labels to -100 during alignment.
- Lower negatives further: set --o-entity-ratio to 1.2–1.5 to boost recall.
- Increase patience (e.g., 4–5) and let it train to ~15–20 epochs.
- Keep the simplified label set (your whitelist + AMOUNT) until F1 > 0.5; re-introduce harder labels later.
- Use label_smoothing ~0.1 and warmup_ratio 0.2; keep batch 64–128; LR 5e-5 to 6e-5.
- Validate with your inference script per label; prioritize fixing boundary issues on ADDRESS_LINE and PRODUCT_NAME.

# Runs

JOB=receipts-$(date +%F-%H%M)-a
layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
 --epochs 12 --batch-size 128 --lr 6e-5 --warmup-ratio 0.2 \
 --label-smoothing 0.1 --o-entity-ratio 1.5 --merge-amounts \
 --gradient-accumulation-steps 1 \
 --allowed-label MERCHANT_NAME --allowed-label PHONE_NUMBER \
 --allowed-label ADDRESS_LINE --allowed-label DATE --allowed-label TIME \
 --allowed-label PRODUCT_NAME --allowed-label AMOUNT

### Results so far (2025-09-20)

- a (merged, O:entity 1.5): last F1 ≈ 0.21
- b (merged, O:entity 2.0): last F1 ≈ 0.32 ← best so far
- c (no merge): last F1 ≈ 0.14
- d (merged, batch 64 + GA=2): last F1 ≈ 0.24

Notes

- Merging totals into `AMOUNT` materially helps early learning. Unmerged totals underperform with current data.
- A slightly higher O:entity ratio (2.0 vs 1.5) improved precision without collapsing recall.
- Gradient accumulation changed optimization dynamics; same effective batch did not match the non-accumulated run.

Artifacts

- Local: `/tmp/receipt_layoutlm/$JOB/run.json` (configs, dataset_counts, epoch_metrics)
- S3: `s3://<models-bucket>/$JOB/run.json` (synced from instance)
- Dynamo: JobMetrics per eval (F1/precision/recall), JobLogs for `run_config` and final `run_summary`.

### Can we do better than ~0.31?

Yes. Recommended next passes (keep merged totals + whitelist):

1. Longer training + higher patience

```bash
JOB=receipts-$(date +%F-%H%M)-b-long
layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 20 --batch-size 128 --lr 6e-5 --warmup-ratio 0.2 \
  --label-smoothing 0.1 --o-entity-ratio 2.0 --merge-amounts \
  --early-stopping-patience 5 \
  --allowed-label MERCHANT_NAME --allowed-label PHONE_NUMBER \
  --allowed-label ADDRESS_LINE --allowed-label DATE --allowed-label TIME \
  --allowed-label PRODUCT_NAME --allowed-label AMOUNT
```

2. LR/warmup sweep around best setup

```bash
# Lower LR
JOB=receipts-$(date +%F-%H%M)-b-lr5e5
layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 20 --batch-size 128 --lr 5e-5 --warmup-ratio 0.2 \
  --label-smoothing 0.1 --o-entity-ratio 2.0 --merge-amounts \
  --early-stopping-patience 5 \
  --allowed-label MERCHANT_NAME --allowed-label PHONE_NUMBER \
  --allowed-label ADDRESS_LINE --allowed-label DATE --allowed-label TIME \
  --allowed-label PRODUCT_NAME --allowed-label AMOUNT

# Lower warmup
JOB=receipts-$(date +%F-%H%M)-b-warm01
layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 20 --batch-size 128 --lr 6e-5 --warmup-ratio 0.1 \
  --label-smoothing 0.1 --o-entity-ratio 2.0 --merge-amounts \
  --early-stopping-patience 5 \
  --allowed-label MERCHANT_NAME --allowed-label PHONE_NUMBER \
  --allowed-label ADDRESS_LINE --allowed-label DATE --allowed-label TIME \
  --allowed-label PRODUCT_NAME --allowed-label AMOUNT
```

3. If recall lags, reduce negatives slightly

```bash
JOB=receipts-$(date +%F-%H%M)-b-oe18
layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 20 --batch-size 128 --lr 6e-5 --warmup-ratio 0.2 \
  --label-smoothing 0.1 --o-entity-ratio 1.8 --merge-amounts \
  --early-stopping-patience 5 \
  --allowed-label MERCHANT_NAME --allowed-label PHONE_NUMBER \
  --allowed-label ADDRESS_LINE --allowed-label DATE --allowed-label TIME \
  --allowed-label PRODUCT_NAME --allowed-label AMOUNT
```

Evaluation tips

- Track best F1 (Dynamo JobMetrics) and learning curves (`epoch_metrics` in run.json).
- Prioritize boundary quality for `ADDRESS_LINE` and `PRODUCT_NAME` in spot checks.

JOB=receipts-$(date +%F-%H%M)-b
layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
 --epochs 12 --batch-size 128 --lr 6e-5 --warmup-ratio 0.2 \
 --label-smoothing 0.1 --o-entity-ratio 2.0 --merge-amounts \
 --gradient-accumulation-steps 1 \
 --allowed-label MERCHANT_NAME --allowed-label PHONE_NUMBER \
 --allowed-label ADDRESS_LINE --allowed-label DATE --allowed-label TIME \
 --allowed-label PRODUCT_NAME --allowed-label AMOUNT

JOB=receipts-$(date +%F-%H%M)-c
layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
 --epochs 12 --batch-size 128 --lr 6e-5 --warmup-ratio 0.2 \
 --label-smoothing 0.1 --o-entity-ratio 1.5 \
 --gradient-accumulation-steps 1 \
 --allowed-label MERCHANT_NAME --allowed-label PHONE_NUMBER \
 --allowed-label ADDRESS_LINE --allowed-label DATE --allowed-label TIME \
 --allowed-label PRODUCT_NAME \
 --allowed-label LINE_TOTAL --allowed-label SUBTOTAL \
 --allowed-label TAX --allowed-label GRAND_TOTAL

JOB=receipts-$(date +%F-%H%M)-d
layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
 --epochs 12 --batch-size 64 --lr 6e-5 --warmup-ratio 0.2 \
 --label-smoothing 0.1 --o-entity-ratio 1.5 --merge-amounts \
 --gradient-accumulation-steps 2 \
 --allowed-label MERCHANT_NAME --allowed-label PHONE_NUMBER \
 --allowed-label ADDRESS_LINE --allowed-label DATE --allowed-label TIME \
 --allowed-label PRODUCT_NAME --allowed-label AMOUNT
