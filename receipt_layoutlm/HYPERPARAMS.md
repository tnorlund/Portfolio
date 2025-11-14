## LayoutLM training: batch size and hyperparameter sweeps

### What batch size does here

- **Unit**: one training sample is a receipt line (tokens + bboxes + BIO tags).
- **Batch size**: number of lines per optimizer step.
- **Effects**:
  - **Throughput**: larger batch → more tokens/sec, fewer steps/epoch.
  - **Memory**: grows roughly linearly with batch size. Attention also scales with sequence length.
  - **Optimization**: very large batches can need a higher learning rate; huge batches can sometimes reduce generalization.
- **Effective batch**: per_device_batch_size × gradient_accumulation_steps × num_gpus.

### Suggested defaults (single A10G on g5.xlarge)

- Mixed precision: enabled (fp16 automatically when CUDA available).
- TF32: enabled for faster matmul on Ampere (safe for transformers).
- DataLoader: num_workers 4–8, pin_memory=True.
- Start batch size high enough to keep GPU-Util ~85–95% without OOM.

### Key hyperparameters to sweep

- **Learning rate (lr)**: 1e-5 → 5e-5 (log-uniform). Most important.
- **Weight decay**: 0.0 → 0.1.
- **Warmup ratio**: 0.0 → 0.2.
- **Effective batch size**: adjust batch_size and/or gradient_accumulation_steps.
- **Label smoothing**: 0.0 → 0.1. Can improve recall and stabilize early training.
- Optional: **max_grad_norm** (0.5–1.0), **early_stopping_patience** (2–4).

### Practical sweep recipe

1. Keep data and preprocessing fixed (BIO tagging, receipt-level split).
2. Limit epochs to 3–5 with early stopping on val_f1 to speed up search.
3. Run 10–20 trials varying: lr, weight_decay, warmup_ratio, batch_size/grad_accum, label_smoothing.
4. Select best by val_f1; retrain that config for full epochs (e.g., 10–12).

### Example commands

Run a single trial with tuned batch size and defaults:

```bash
export TORCH_ALLOW_TF32=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export CUDA_DEVICE_MAX_CONNECTIONS=1

JOB=receipts-$(date +%s); RUNS=~/runs/$JOB; mkdir -p "$RUNS"
layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 5 --batch-size 192 --lr 3e-5 2>&1 | tee "$RUNS/train.log"
```

Coarse manual sweep sketch (change just the numbers):

```bash
for lr in 1e-5 2e-5 3e-5 5e-5; do
  for wd in 0.0 0.01 0.05; do
    JOB="sweep-$(date +%s)-lr${lr}-wd${wd}"; RUNS=~/runs/$JOB; mkdir -p "$RUNS"
    layoutlm-cli train --job-name "$JOB" --dynamo-table "$DYNAMO_TABLE_NAME" \
      --epochs 4 --batch-size 160 --lr $lr 2>&1 | tee "$RUNS/train.log"
  done
done
```

### Interpreting metrics

- We report **seqeval** entity-level Precision/Recall/F1 (BIO), ignoring the O tag.
- Expect low F1 in early epochs, especially with O‑heavy data. It should rise as the head learns.
- Use `load_best_model_at_end=True` and `metric_for_best_model=f1` (already set) to keep the best checkpoint.

### Saving and promoting artifacts

Sync full run directory and best checkpoint to S3 after training:

```bash
/usr/bin/aws s3 sync /tmp/receipt_layoutlm/$JOB s3://$BUCKET/runs/$JOB/
if [ -f /tmp/receipt_layoutlm/$JOB/trainer_state.json ]; then
  BEST=$(python - <<'PY'
import json,sys
p=f"/tmp/receipt_layoutlm/{sys.argv[1]}/trainer_state.json"
print(json.load(open(p)).get("best_model_checkpoint",""))
PY
"$JOB")
  [ -n "$BEST" ] && /usr/bin/aws s3 sync "$BEST" s3://$BUCKET/runs/$JOB/best/
fi
```

### Common pitfalls and fixes

- F1 ~0 early on: model predicts mostly O after adding unlabeled tokens; let it train or filter all‑O lines for training only.
- OOM on big batches: reduce batch size; ensure `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` is set.
- Resume mismatch: changing label set invalidates old checkpoints; start a new run dir (already isolated per job).

### When larger batch is “better”

- Larger batch is primarily a performance lever. Accuracy doesn’t automatically improve. If you go much larger, consider modest LR scaling and monitor val_f1. Use the smallest batch that saturates the GPU and gives stable/improving metrics.
