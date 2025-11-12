# LayoutLM Training Strategy Review

## What's Stored in DynamoDB?

Yes! All training runs are stored in DynamoDB. Here's what gets tracked:

### 1. **Job Entity** (Main Job Record)
- **Location**: `PK: JOB#{job_id}`, `SK: JOB`
- **Contains**:
  - Job ID, name, description
  - Status (pending, running, succeeded, failed, cancelled, interrupted)
  - Priority, created_at, created_by
  - **Full job_config** (all hyperparameters, data config, model settings)
  - Optional S3 storage metadata (bucket, prefixes for checkpoints/logs)

### 2. **JobMetric Entity** (Per-Evaluation Metrics)
- **Location**: `PK: JOB#{job_id}`, `SK: METRIC#{timestamp}`
- **Contains**:
  - F1, Precision, Recall (entity-level via seqeval)
  - Epoch number, global_step
  - Timestamp of evaluation

### 3. **JobLog Entity** (Logs and Summaries)
- **Location**: `PK: JOB#{job_id}`, `SK: LOG#{timestamp}`
- **Contains**:
  - Initial `run_config` (full config + dataset counts)
  - Final `run_summary` (best metrics, final status)
  - Other log messages during training

### 4. **Local Files** (on EC2)
- `/tmp/receipt_layoutlm/{job_name}/run.json` - Full training history
- `/tmp/receipt_layoutlm/{job_name}/checkpoint-*/` - Model checkpoints
- Can be synced to S3 manually or via script

## Previous Training Results (Sept 2025)

### Best Run: **0.32 F1** (run "b")
- Config: merged amounts, O:entity 2.0, batch 128, LR 6e-5
- Other runs: 0.14-0.24 F1

### Key Findings:
1. ✅ **Merging totals into AMOUNT helps** - boosted F1 from 0.14 to 0.32
2. ✅ **O:entity ratio 2.0 better than 1.5** - improved precision without hurting recall
3. ⚠️ **Still below target** - goal was >0.5 F1, achieved ~0.32
4. ⚠️ **Early stopping too aggressive** - patience=2 may have stopped too early

## Should You Start Over?

**Recommendation: Start fresh, but build on learnings**

### Why Start Fresh:
1. **Dataset may have grown** - You may have more labeled data now
2. **Code improvements** - The subtoken supervision fix (only first subtoken) wasn't implemented
3. **Better hyperparameters** - Previous runs suggest longer training needed

### What to Keep:
- ✅ **Merged amounts strategy** - This worked well
- ✅ **Label whitelist** - Keep simplified label set until F1 > 0.5
- ✅ **O:entity ratio 2.0** - This was the sweet spot

## Recommended Training Strategy

### Phase 1: Quick Validation Run (1-2 hours)
Test that everything works with a short run:

```bash
JOB=receipts-$(date +%F-%H%M)-validate
layoutlm-cli train \
  --job-name "$JOB" \
  --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 3 \
  --batch-size 64 \
  --lr 6e-5 \
  --warmup-ratio 0.2 \
  --label-smoothing 0.1 \
  --o-entity-ratio 2.0 \
  --merge-amounts \
  --allowed-label MERCHANT_NAME \
  --allowed-label PHONE_NUMBER \
  --allowed-label ADDRESS_LINE \
  --allowed-label DATE \
  --allowed-label TIME \
  --allowed-label PRODUCT_NAME \
  --allowed-label AMOUNT
```

**Goal**: Verify infrastructure works, check dataset size, see initial F1 trajectory

### Phase 2: Full Training Run (Based on Previous Best)
If validation looks good, run the full training:

```bash
JOB=receipts-$(date +%F-%H%M)-full
layoutlm-cli train \
  --job-name "$JOB" \
  --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 20 \
  --batch-size 128 \
  --lr 6e-5 \
  --warmup-ratio 0.2 \
  --label-smoothing 0.1 \
  --o-entity-ratio 2.0 \
  --merge-amounts \
  --early-stopping-patience 5 \
  --allowed-label MERCHANT_NAME \
  --allowed-label PHONE_NUMBER \
  --allowed-label ADDRESS_LINE \
  --allowed-label DATE \
  --allowed-label TIME \
  --allowed-label PRODUCT_NAME \
  --allowed-label AMOUNT
```

**Key Changes from Previous**:
- ✅ **Increased patience to 5** (was 2) - let it train longer
- ✅ **20 epochs** (was 12) - more time to converge
- ✅ **Batch 128** - matches previous best run

### Phase 3: Hyperparameter Tuning (If Needed)
If F1 still < 0.5, try these variations:

#### Option A: Lower Learning Rate
```bash
--lr 5e-5  # instead of 6e-5
```

#### Option B: Lower O:Entity Ratio (if recall is low)
```bash
--o-entity-ratio 1.8  # instead of 2.0
```

#### Option C: Longer Warmup
```bash
--warmup-ratio 0.1  # instead of 0.2
```

## Critical Issues to Address

### 1. **Subtoken Supervision** (Not Yet Fixed)
**Problem**: Currently all subtokens are supervised. Standard practice is to only supervise the first subtoken.

**Impact**: Boundary errors count multiple times, depressing F1 scores.

**Status**: The code at line 195-200 in `trainer.py` already implements this correctly:
```python
if wid != prev_word_id:
    lbl = example["ner_tags"][wid]
    labels.append(label2id.get(lbl, 0))
else:
    labels.append(-100)  # ✅ Already correct!
```

**Action**: ✅ Already fixed! No changes needed.

### 2. **Dataset Size**
**Previous Issue**: Insufficient labeled data was the main blocker.

**Action**: Check current dataset size:
```python
# On EC2, you can check dataset stats in run.json after first run
# Or query DynamoDB to count VALID labels
```

### 3. **Early Stopping**
**Previous Issue**: Patience=2 was too aggressive.

**Action**: ✅ Fixed in Phase 2 strategy (patience=5)

## Monitoring Training

### During Training:
1. **Check DynamoDB JobMetrics** - F1/precision/recall per epoch
2. **Check local run.json** - Full training history
3. **Watch for early stopping** - Should stop if F1 plateaus for 5 epochs

### After Training:
1. **Review best F1** - Check JobMetrics for peak performance
2. **Check run summary** - JobLog with final status
3. **Download best checkpoint** - From S3 or local `/tmp/receipt_layoutlm/{job}/best/`

## Expected Outcomes

### If Dataset Hasn't Grown Much:
- **Realistic F1**: 0.35-0.45 (slight improvement from 0.32)
- **Why**: Longer training + better patience should help

### If Dataset Has Grown:
- **Realistic F1**: 0.45-0.60 (significant improvement)
- **Why**: More data = better generalization

### If Still < 0.4 F1:
- Consider dataset quality improvements
- Review label consistency
- Check for class imbalance issues
- May need to collect more labeled data

## Next Steps

1. ✅ **Infrastructure deployed** - Ready to train
2. ⏭️ **Build and upload wheels** - See LAYOUTLM_GETTING_STARTED.md
3. ⏭️ **Launch EC2 instance** - Scale ASG to 1
4. ⏭️ **Run validation run** - Quick 3-epoch test
5. ⏭️ **Review results** - Check DynamoDB metrics
6. ⏭️ **Run full training** - If validation looks good

## Querying Previous Runs

To see your previous training runs in DynamoDB:

```python
from receipt_dynamo import DynamoClient

dynamo = DynamoClient(table_name="ReceiptsTable-dc5be22")
jobs = dynamo.list_jobs_by_status("succeeded")  # or "failed", "running", etc.

for job in jobs:
    print(f"{job.name}: {job.status}")
    print(f"  Config: {job.job_config}")

    # Get metrics
    metrics = dynamo.list_job_metrics(job.job_id)
    if metrics:
        best = max(metrics, key=lambda m: m.metric_value)
        print(f"  Best F1: {best.metric_value}")
```

Or use AWS CLI:
```bash
aws dynamodb query \
  --table-name ReceiptsTable-dc5be22 \
  --index-name GSI1 \
  --key-condition-expression "GSI1PK = :status" \
  --expression-attribute-values '{":status":{"S":"STATUS#succeeded"}}' \
  --region us-east-1
```



