# Guide: Validating PENDING Labels After create-labels-dev-sf

## Overview

After running `create-labels-dev-sf` to label all receipts, you'll have many labels with `validation_status="PENDING"`. This guide explains which step functions to run to validate these PENDING labels.

---

## Step Function: `validate-pending-labels-dev-sf` ✅

**This is the main step function you need to run** to validate PENDING word labels.

### What It Does

The `validate-pending-labels-dev-sf` step function validates PENDING `ReceiptWordLabel` entities using a **three-tier validation approach**:

1. **Tier 0: Rule-based validation** (fast, cheap)
   - Validates structured formats: DATE, TIME, PHONE_NUMBER
   - Uses regex/pattern matching
   - Updates labels to VALID or INVALID immediately

2. **Tier 1: ChromaDB Similarity Search** (fast, cheap)
   - Queries similar words with VALID labels from ChromaDB
   - Uses similarity scores and conflict detection
   - Updates labels to VALID or INVALID based on similarity matches

3. **Tier 2: LangGraph + CoVe Fallback** (accurate, slower)
   - Runs full LangGraph workflow with Chain of Verification (CoVe)
   - Re-generates labels and verifies them using LLM
   - Updates remaining PENDING labels to VALID, INVALID, or NEEDS_REVIEW

### Workflow

```
ListPendingLabels (Lambda)
  ↓
CheckReceipts (Choice)
  ├─ total_receipts > 0 → ProcessReceipts (Map)
  └─ total_receipts = 0 → NoReceipts (End)
      ↓
ProcessReceipts (Map, MaxConcurrency: 10)
  └─ ValidateReceipt (Lambda) - per receipt
```

### How to Run

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:validate-pending-labels-dev-sf \
  --input '{}'
```

**Note**: The input can be empty `{}` or you can optionally pass:
```json
{
  "limit": 100  // Optional: limit number of receipts to process
}
```

### Expected Results

- **Most PENDING labels** (60-80%) will be validated via ChromaDB similarity search
- **Remaining labels** will be validated via LangGraph + CoVe
- Labels will be updated to:
  - `VALID` - Label is correct
  - `INVALID` - Label is incorrect
  - `NEEDS_REVIEW` - Ambiguous or conflicting validation results
  - `PENDING` - Could not be validated (rare)

### Monitoring

After starting the execution, you can monitor it:

1. **AWS Console**: Go to Step Functions → `validate-pending-labels-dev-sf` → View execution
2. **CloudWatch Logs**: Check Lambda logs for detailed validation results
3. **DynamoDB**: Query labels to see updated `validation_status` values

### Performance

- **Max Concurrency**: 10 receipts processed in parallel
- **Timeout**: 900 seconds (15 minutes) per receipt
- **Duration**: ~10 minutes for 100 receipts (with 10 parallel)

---

## Optional: `validate-metadata-dev-sf`

**This step function is separate** and validates `ReceiptMetadata` entities, not word labels.

### What It Does

- Validates `ReceiptMetadata` entities (merchant name, address, etc.) against receipt text
- Uses LangGraph + CoVe to ensure metadata accuracy
- Can update metadata if mismatches are found

### When to Run

- **After** validating word labels (optional)
- If you want to ensure receipt metadata is accurate
- If you've made changes to metadata extraction logic

### How to Run

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:validate-metadata-dev-sf \
  --input '{}'
```

---

## Summary: What to Run

### ✅ Required: Validate PENDING Word Labels

```bash
# Run this to validate all PENDING word labels
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:validate-pending-labels-dev-sf \
  --input '{}'
```

### ⚪ Optional: Validate Receipt Metadata

```bash
# Run this if you also want to validate ReceiptMetadata entities
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:validate-metadata-dev-sf \
  --input '{}'
```

---

## Workflow Order

1. ✅ **create-labels-dev-sf** - Creates labels for all receipts (you've already done this)
2. ✅ **validate-pending-labels-dev-sf** - Validates PENDING word labels ← **Run this next**
3. ⚪ **validate-metadata-dev-sf** - Validates ReceiptMetadata (optional)

---

## Related Documentation

- `docs/STEP_FUNCTION_VALIDATION_REVIEW.md` - Detailed validation review
- `docs/VALIDATE_PENDING_LABELS_WORKFLOW_REVIEW.md` - Workflow details
- `docs/VALIDATE_PENDING_LABELS_STEP_FUNCTION_ANALYSIS.md` - Architecture analysis


