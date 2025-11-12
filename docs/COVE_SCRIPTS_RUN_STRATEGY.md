# CoVe Scripts: Run Strategy and Frequency

## Overview

This document outlines the CoVe-enabled scripts for receipt labeling and validation, their purposes, and recommended run frequencies.

## Scripts Overview

### 1. `dev.process_all_receipts_cove.py`
**Purpose**: Process all receipts end-to-end with CoVe validation

**What it does**:
- Runs LangGraph workflow with CoVe for all receipts
- Creates/updates `ReceiptWordLabels` (with `validation_status` based on CoVe)
- Validates and updates `ReceiptMetadata` (with Google Places API if needed)
- Uses ChromaDB validation node (if provided)

**When to run**:
- **Initial setup**: Once to process all existing receipts
- **After adding new receipts**: When new receipt images are uploaded
- **After major changes**: When CoVe prompts or validation logic changes
- **Periodic refresh**: Monthly to catch any missed receipts

**Frequency**:
- **One-time**: Initial processing of all receipts
- **On-demand**: After adding new receipts
- **Monthly**: Periodic refresh (optional)

**Cost**: High (LLM API calls for every receipt)
**Duration**: ~2-3 hours for 400 receipts

---

### 2. `dev.validate_pending_labels_chromadb.py`
**Purpose**: Validate PENDING labels using LangGraph + CoVe

**What it does**:
- Queries all PENDING labels from DynamoDB
- Groups labels by receipt (image_id, receipt_id)
- Re-runs LangGraph workflow with CoVe for each receipt
- Updates `validation_status` to VALID/INVALID based on CoVe verification

**When to run**:
- **After initial processing**: To validate labels that were marked PENDING
- **After new receipts processed**: To catch PENDING labels from new uploads
- **Periodic validation**: To re-validate labels that couldn't be verified initially

**Frequency**:
- **Daily**: After new receipts are uploaded (if uploads are frequent)
- **Weekly**: If uploads are less frequent
- **After `process_all_receipts_cove.py`**: Run once after initial processing completes

**Cost**: Medium-High (LLM API calls, but only for receipts with PENDING labels)
**Duration**: Depends on number of PENDING labels (typically 30-60 minutes for 100 receipts)

---

### 3. `dev.reverify_word_labels_cove.py`
**Purpose**: Re-verify existing ReceiptWordLabels using CoVe

**What it does**:
- Lists all receipts that have ReceiptWordLabels
- Re-runs LangGraph workflow with CoVe
- Compares existing labels vs new CoVe-verified labels
- Updates `validation_status` on existing labels based on CoVe verification
- Adds new CoVe-verified labels that don't exist yet

**When to run**:
- **After CoVe improvements**: When CoVe prompts or logic are improved
- **After model updates**: When LLM models are updated
- **Quality assurance**: To ensure existing labels are still accurate

**Frequency**:
- **Quarterly**: Every 3 months to re-verify existing labels
- **After major changes**: When CoVe implementation changes significantly
- **On-demand**: When you suspect label quality issues

**Cost**: High (LLM API calls for all receipts with labels)
**Duration**: Similar to `process_all_receipts_cove.py` (2-3 hours for 400 receipts)

---

### 4. `dev.validate_receipt_metadata_cove.py`
**Purpose**: Validate ReceiptMetadata using CoVe

**What it does**:
- Lists all ReceiptMetadata entities
- Validates merchant name against receipt text using CoVe
- Updates ReceiptMetadata via Google Places API if validation finds issues

**When to run**:
- **After metadata creation**: To validate newly created metadata
- **After metadata updates**: To ensure updates are correct
- **Quality assurance**: To catch metadata errors

**Frequency**:
- **Weekly**: To catch metadata issues early
- **After metadata creation changes**: When metadata creation logic changes
- **On-demand**: When you suspect metadata quality issues

**Cost**: Medium (LLM API calls + Google Places API calls)
**Duration**: ~1-2 hours for 400 receipts

---

## Recommended Run Schedule

### Initial Setup (One-Time)
1. **Run `dev.process_all_receipts_cove.py`** (once)
   - Processes all existing receipts
   - Creates labels and validates metadata
   - Duration: ~2-3 hours

2. **Run `dev.validate_pending_labels_chromadb.py`** (once, after step 1)
   - Validates PENDING labels from initial processing
   - Duration: ~30-60 minutes

### Ongoing Operations

#### Daily (Automated)
- **Upload Lambda**: Automatically processes new receipts with CoVe
  - Creates metadata (with CoVe) ✅
  - Creates labels (with CoVe) ✅
  - Validates metadata (with CoVe) ✅

#### Weekly (Scheduled)
- **`dev.validate_pending_labels_chromadb.py`**
  - Validates any PENDING labels from the week
  - Catches labels that couldn't be verified initially
  - **Schedule**: Every Sunday at 2 AM

- **`dev.validate_receipt_metadata_cove.py`** (optional)
  - Validates metadata quality
  - Catches metadata errors
  - **Schedule**: Every Wednesday at 2 AM

#### Monthly (Scheduled)
- **`dev.process_all_receipts_cove.py`** (optional)
  - Periodic refresh to catch any missed receipts
  - Re-processes receipts with latest CoVe improvements
  - **Schedule**: First Sunday of the month at 2 AM

#### Quarterly (Scheduled)
- **`dev.reverify_word_labels_cove.py`**
  - Re-verifies all existing labels
  - Ensures label quality over time
  - **Schedule**: First Sunday of each quarter at 2 AM

---

## Script Dependencies

```
Upload Lambda (automatic)
  ↓
  Creates metadata (with CoVe) ✅
  Creates labels (with CoVe) ✅
  ↓
  Some labels marked as PENDING
  ↓
dev.validate_pending_labels_chromadb.py (weekly)
  ↓
  Validates PENDING labels
  ↓
  Most labels become VALID
  ↓
  Remaining PENDING labels
  ↓
dev.reverify_word_labels_cove.py (quarterly)
  ↓
  Re-verifies all labels
```

---

## Cost Considerations

### High Cost Scripts
- **`dev.process_all_receipts_cove.py`**: ~$X per 100 receipts (LLM API calls)
- **`dev.reverify_word_labels_cove.py`**: Similar cost (processes all receipts)

### Medium Cost Scripts
- **`dev.validate_pending_labels_chromadb.py`**: ~$Y per 100 receipts (only processes receipts with PENDING labels)
- **`dev.validate_receipt_metadata_cove.py`**: ~$Z per 100 receipts (metadata validation only)

### Cost Optimization
1. **Run `validate_pending_labels_chromadb.py` more frequently** than `process_all_receipts_cove.py`
   - Only processes receipts with PENDING labels
   - More cost-effective than re-processing all receipts

2. **Use ChromaDB pre-filtering** (future enhancement)
   - Quick wins for high-confidence cases
   - Reduces LLM API calls

3. **Batch processing**
   - Process receipts in batches
   - Monitor costs per batch

---

## Monitoring Recommendations

### Key Metrics to Track

1. **PENDING Label Rate**
   - Target: < 20% of all labels
   - Alert if > 40%
   - **Action**: Run `validate_pending_labels_chromadb.py` more frequently

2. **Validation Success Rate**
   - Percentage of PENDING labels that become VALID after validation
   - Target: > 50%
   - **Action**: If low, investigate CoVe prompts or LLM model

3. **Metadata Validation Rate**
   - Percentage of metadata that passes CoVe validation
   - Target: > 90%
   - **Action**: If low, investigate metadata creation logic

4. **Script Execution Time**
   - Monitor duration of each script run
   - **Action**: Optimize if duration increases significantly

### Alerts

Set up alerts for:
- PENDING label rate > 40%
- Validation success rate < 30%
- Script execution failures
- Cost per receipt > threshold

---

## Decision Tree: Which Script to Run?

```
Do you have new receipts to process?
├─ Yes → Run dev.process_all_receipts_cove.py
│         ↓
│         Some labels marked as PENDING
│         ↓
│         Run dev.validate_pending_labels_chromadb.py
│
├─ No, but have PENDING labels?
│  └─ Yes → Run dev.validate_pending_labels_chromadb.py
│
├─ No, but want to re-verify existing labels?
│  └─ Yes → Run dev.reverify_word_labels_cove.py
│
└─ No, but want to validate metadata?
   └─ Yes → Run dev.validate_receipt_metadata_cove.py
```

---

## Automation Recommendations

### Option 1: AWS EventBridge (Recommended)
Schedule scripts as Lambda functions or ECS tasks:

```yaml
Weekly:
  - validate_pending_labels_chromadb.py: Sunday 2 AM
  - validate_receipt_metadata_cove.py: Wednesday 2 AM

Monthly:
  - process_all_receipts_cove.py: First Sunday 2 AM

Quarterly:
  - reverify_word_labels_cove.py: First Sunday of quarter 2 AM
```

### Option 2: Cron Jobs (Local/EC2)
Set up cron jobs on a server:

```bash
# Weekly - Validate PENDING labels
0 2 * * 0 cd /path/to/portfolio && python dev.validate_pending_labels_chromadb.py --stack dev

# Weekly - Validate metadata
0 2 * * 3 cd /path/to/portfolio && python dev.validate_receipt_metadata_cove.py --stack dev

# Monthly - Process all receipts
0 2 1 * * cd /path/to/portfolio && python dev.process_all_receipts_cove.py --stack dev

# Quarterly - Re-verify labels
0 2 1 */3 * cd /path/to/portfolio && python dev.reverify_word_labels_cove.py --stack dev
```

### Option 3: Manual (Development)
Run scripts manually when needed:
- After adding new receipts
- After CoVe improvements
- When investigating quality issues

---

## Best Practices

1. **Always run `validate_pending_labels_chromadb.py` after `process_all_receipts_cove.py`**
   - Catches PENDING labels from initial processing
   - More cost-effective than re-processing all receipts

2. **Monitor PENDING label rate**
   - If rate is high, run validation scripts more frequently
   - If rate is low, you can reduce frequency

3. **Use dry-run mode for testing**
   - Test script changes with `--dry-run` flag
   - Verify behavior before running on production data

4. **Save results to JSON files**
   - Use `--output` flag to save results
   - Track validation success rates over time

5. **Check logs for errors**
   - Monitor script execution logs
   - Investigate errors or warnings

---

## Summary Table

| Script | Purpose | Frequency | Cost | Duration |
|--------|---------|-----------|------|----------|
| `process_all_receipts_cove.py` | Process all receipts | Monthly / On-demand | High | 2-3 hours |
| `validate_pending_labels_chromadb.py` | Validate PENDING labels | Weekly | Medium | 30-60 min |
| `reverify_word_labels_cove.py` | Re-verify all labels | Quarterly | High | 2-3 hours |
| `validate_receipt_metadata_cove.py` | Validate metadata | Weekly (optional) | Medium | 1-2 hours |

---

## Next Steps

1. **Set up monitoring** for PENDING label rate
2. **Schedule weekly runs** of `validate_pending_labels_chromadb.py`
3. **Monitor costs** and adjust frequency as needed
4. **Create alerts** for high PENDING rates or validation failures
5. **Document results** in JSON files for analysis

