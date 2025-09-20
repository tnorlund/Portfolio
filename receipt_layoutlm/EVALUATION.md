## LayoutLM receipts: training and evaluation notes

This document summarizes how we train and evaluate the LayoutLM model in this repo, and how to run a quick local evaluation against ground-truth Dynamo labels.

### What’s implemented

- BIO tagging per line (B-/I-/O) generated from raw word labels.
- Train/validation split at receipt level: lines from the same `(image_id, receipt_id)` never mix across splits.
- Entity-level evaluation via `seqeval` (F1/precision/recall). Best model is selected by F1 and loaded at the end. Early stopping is enabled (patience=2).
- Stable checkpointing (safetensors, limited retention) to reduce disk pressure.

### Quick local evaluation (against Dynamo VALID labels)

Use `dev.layout_lm_inference.py`:

1. Model resolution and download

- The script uses Pulumi env (`layoutlm_training_bucket`) to auto-resolve the latest `runs/<job>/best/` (fallback to the newest checkpoint) and sync it to `~/model` if `MODEL_S3_URI` is not set.

2. Inference

- Picks a random receipt via `DynamoClient`, groups words by line, normalizes per-receipt bounding boxes to 0..1000, runs the model per line with `padding='longest'`.

3. Comparison vs ground truth

- Fetches only `VALID` `ReceiptWordLabel`s, reduces BIO to base labels, and computes per-word precision/recall/F1 by label (excluding `O`). Prints micro-averages and sample mismatches.

### Interpreting results

- If recall is high and precision is low, the model is over-tagging; common causes are sparse or inconsistent word-level labels for multi-token entities. Ensure contiguous words for an entity are labeled consistently (BIO) during annotation.
- Consider entity-level (span) scoring for better correlation with extraction utility. `seqeval` F1 already measures entity-level performance during training; the local script prints token-level comparisons to surface concrete errors quickly.

### Improving accuracy

- Data quality: label full spans for entities, harmonize taxonomy (see `receipt_label/constants.py`).
- Model recipe: keep warmup (0.1), weight decay (0.01), gradient clipping (1.0), optional label smoothing; run a small LR sweep (1e-5…5e-5). For visual gains, migrate to LayoutLMv2 if images are available.
- Post-processing: merge consecutive `ADDRESS_LINE` tokens; apply simple validators (e.g., phone/date regex) as a final filter.

### Reproducing training

```bash
layoutlm-cli train \
  --job-name "receipts-$(date +%s)" \
  --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 12 --batch-size 4 --lr 3e-5
```

Artifacts are written to `/tmp/receipt_layoutlm` (synced to S3 under `runs/<job-id>/`).
