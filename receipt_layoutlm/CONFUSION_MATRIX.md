# Confusion Matrix Feature

This document describes the confusion matrix recording feature added to LayoutLM training.

## Overview

A token-level confusion matrix is now recorded at each training epoch as a `JobMetric` in DynamoDB. This enables detailed analysis of model predictions across all entity types.

## Implementation

### Location
- **Computation**: `receipt_layoutlm/trainer.py` in `compute_metrics()` (lines 814-831)
- **Storage**: `receipt_layoutlm/trainer.py` in `_MetricLoggerCallback.on_evaluate()` (lines 507-520)

### Dependencies
- `scikit-learn>=1.3.0` added to `pyproject.toml`

### DynamoDB Record Structure

Each confusion matrix is stored as a `JobMetric`:

```python
JobMetric(
    job_id="<uuid>",
    metric_name="confusion_matrix",
    value={
        "labels": ["B-ADDRESS", "B-AMOUNT", "B-DATE", ...],
        "matrix": [[57, 0, 0, ...], [0, 162, 0, ...], ...]
    },
    timestamp="<iso-timestamp>",
    unit="matrix",
    epoch=<epoch_number>,
    step=<global_step>
)
```

### Keys
- `PK`: `JOB#<job_id>`
- `SK`: `METRIC#confusion_matrix#<timestamp>`

## Data Format

The confusion matrix uses BIO tagging format:
- `B-<LABEL>`: Beginning of entity
- `I-<LABEL>`: Inside/continuation of entity
- `O`: Outside (not an entity)

### Example Matrix (Epoch 3)

```
              B-ADDRESS B-AMOUNT B-DATE I-ADDRESS I-DATE      O
B-ADDRESS           57        0      0        20      0     70
B-AMOUNT             0      162      0         0      0     32
B-DATE               0        3     68         0      0     22
I-ADDRESS           20        0      0       114      0     58
I-DATE               0        4     12         0      0     19
O                   37      199      5        60      0   5358
```

## Querying Confusion Matrices

```python
from receipt_dynamo import DynamoClient

client = DynamoClient(table_name='ReceiptsTable-dc5be22', region='us-east-1')

# Get all confusion matrices for a job
metrics, _ = client.list_job_metrics(job_id, metric_name='confusion_matrix')

for m in sorted(metrics, key=lambda x: x.epoch):
    labels = m.value['labels']
    matrix = m.value['matrix']
    print(f"Epoch {m.epoch}: {len(labels)} labels")
```

## Known Issues

### 1. BIO Tags in Matrix (Design Decision)

The confusion matrix includes B- and I- tag variants separately. While this is verbose, it preserves full information. Collapsing to entity-level should be done in the API/frontend layer.

**Rationale**: Store verbose data, clean up in presentation.

### 2. Missing Labels in Training

During testing, MERCHANT_NAME was missing from the confusion matrix despite being in `allowed_labels`.

**Root Cause**: JSON parsing bug when passing `allowed_labels` to Lambda:
```python
# Broken (what was stored):
['["MERCHANT_NAME"', '"ADDRESS"', '"DATE"', '"AMOUNT"]']

# Correct (what should be):
['MERCHANT_NAME', 'ADDRESS', 'DATE', 'AMOUNT']
```

**Fix Required**: Ensure proper JSON serialization when invoking training Lambda.

### 3. I-DATE Never Predicted (0% Recall)

In test training job `confusion-matrix-test-1`:
- I-DATE had 35 actual instances
- Model predicted 0 correctly (all went to O, B-DATE, or B-AMOUNT)

**Possible Causes**:
- Dates in data may be single-token (no continuation)
- DATE/TIME merge may not be working as expected
- Class imbalance issue

### 4. Label Name Mapping

Raw DynamoDB labels vs training labels:

| Training Label | Raw DynamoDB Labels |
|----------------|---------------------|
| `ADDRESS` | `ADDRESS_LINE`, `PHONE_NUMBER` (merged) |
| `AMOUNT` | `LINE_TOTAL`, `SUBTOTAL`, `TAX`, `GRAND_TOTAL` (merged) |
| `DATE` | `DATE`, `TIME` (merged) |
| `MERCHANT_NAME` | `MERCHANT_NAME` (direct) |

## Metrics Interpretation

### Per-Label Analysis

For each label row in the confusion matrix:
- **Support**: Total actual instances (row sum)
- **Correct**: Diagonal value (true positives)
- **Recall**: Correct / Support
- **Main Errors**: Off-diagonal values show confusion patterns

### Common Patterns

1. **High O confusion**: Model is conservative, under-predicting entities
2. **B/I confusion**: Boundary detection issues (less critical)
3. **Cross-entity confusion**: Semantic similarity issues (more critical)

## Future Improvements

1. Add entity-level (collapsed) confusion matrix as separate metric
2. Add per-class precision/recall derived from matrix
3. Add confusion matrix visualization in frontend
4. Fix Lambda JSON parsing for `allowed_labels`
