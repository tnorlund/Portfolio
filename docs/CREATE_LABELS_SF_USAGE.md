# Create Labels Step Function Usage Guide

## Overview

The `create-labels-dev-sf` Step Function creates/updates labels for all receipts with `validation_status="PENDING"`. This guide shows how to run it forwards (create labels) and backwards (delete labels).

---

## Running Forwards (Create Labels)

### Basic Usage

```bash
# Run for all receipts
python dev.run_create_labels_sf.py --forward

# Run with limit (process only first N receipts)
python dev.run_create_labels_sf.py --forward --limit 10
```

### What It Does

1. Lists all receipts from DynamoDB
2. For each receipt:
   - Runs LangGraph/Ollama workflow to generate labels
   - Sets all labels to `validation_status="PENDING"`
   - Sets `label_proposed_by="simple_receipt_analyzer"`
   - Saves labels to DynamoDB (adds new, updates existing)

### Check Status

After starting execution, you'll get an execution ARN. Check status with:

```bash
python dev.run_create_labels_sf.py --status <execution-arn>
```

### Example Output

```
🚀 Running create-labels-dev-sf FORWARDS (creating labels)...
✅ Step Function execution started
   Execution ARN: arn:aws:states:us-east-1:681647709217:execution:create-labels-dev-sf:create-labels-1234567890
   Execution Name: create-labels-1234567890
   View in AWS Console: https://console.aws.amazon.com/states/home?region=us-east-1#/executions/details/...
```

---

## Running Backwards (Delete Labels)

### Basic Usage

```bash
# Dry run (see what would be deleted, no actual deletion)
python dev.run_create_labels_sf.py --backward

# Actually delete labels
python dev.run_create_labels_sf.py --backward --no-dry-run
```

### What It Does

1. Lists all receipts from DynamoDB
2. For each receipt:
   - Finds labels with `label_proposed_by="simple_receipt_analyzer"`
   - Deletes those labels from DynamoDB

### Safety Features

- **Default is dry-run**: Must use `--no-dry-run` to actually delete
- **Shows sample**: Displays sample of labels that would be deleted
- **Batch deletion**: Deletes in batches of 25 (DynamoDB limit)

### Example Output (Dry Run)

```
🔄 Running create-labels-dev-sf BACKWARDS (deleting labels)...
   DRY RUN MODE - No labels will be deleted
   Listing all receipts...
   Found 150 receipts
   Found 1250 labels created by 'simple_receipt_analyzer'
   🔍 DRY RUN: Would delete 1250 labels
   Run with --no-dry-run to actually delete
   Sample labels that would be deleted:
      - abc123/1: GRAND_TOTAL (line 5, word 12)
      - abc123/1: TAX (line 4, word 8)
      - def456/1: SUBTOTAL (line 3, word 6)
      ... and 1247 more
```

### Example Output (Actual Deletion)

```
🔄 Running create-labels-dev-sf BACKWARDS (deleting labels)...
   Listing all receipts...
   Found 150 receipts
   Found 1250 labels created by 'simple_receipt_analyzer'
   🗑️  Deleting 1250 labels...
   Deleted batch 1: 25 labels (total: 25/1250)
   Deleted batch 2: 25 labels (total: 50/1250)
   ...
   ✅ Successfully deleted 1250 labels
```

---

## Advanced Options

### Filter by Different Label Source

If you want to delete labels from a different source:

```bash
python dev.run_create_labels_sf.py --backward --label-proposed-by "other_analyzer"
```

### Check Execution Status

```bash
python dev.run_create_labels_sf.py --status arn:aws:states:us-east-1:681647709217:execution:create-labels-dev-sf:create-labels-1234567890
```

Output:
```
📊 Checking status of execution: ...
   Status: RUNNING
   Started: 2025-01-18T10:00:00Z
   Latest events:
      - ExecutionStarted at 2025-01-18T10:00:00Z
      - TaskStateEntered at 2025-01-18T10:00:01Z
```

---

## Use Cases

### Use Case 1: Initial Label Creation

```bash
# Create labels for all receipts
python dev.run_create_labels_sf.py --forward

# Check status
python dev.run_create_labels_sf.py --status <execution-arn>

# After completion, validate labels
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:validate-pending-labels-dev-sf \
  --input '{}'
```

### Use Case 2: Test with Small Batch

```bash
# Create labels for first 5 receipts only
python dev.run_create_labels_sf.py --forward --limit 5

# Check results, then delete if needed
python dev.run_create_labels_sf.py --backward --no-dry-run
```

### Use Case 3: Clean Up and Re-run

```bash
# Delete existing labels
python dev.run_create_labels_sf.py --backward --no-dry-run

# Create fresh labels
python dev.run_create_labels_sf.py --forward
```

### Use Case 4: Selective Cleanup

```bash
# See what would be deleted
python dev.run_create_labels_sf.py --backward

# If looks good, actually delete
python dev.run_create_labels_sf.py --backward --no-dry-run
```

---

## Important Notes

### Labels Created by Step Function

Labels created by `create-labels-dev-sf` have:
- `label_proposed_by="simple_receipt_analyzer"`
- `validation_status="PENDING"` (initially)

The backward operation deletes labels based on `label_proposed_by`, not `validation_status`, because labels may have been validated since creation.

### Safety

- **Always dry-run first**: The backward operation is destructive
- **Check sample output**: Review the sample labels before deleting
- **Backup if needed**: Consider backing up labels before deletion

### Performance

- **Forwards**: Processes receipts in parallel (MaxConcurrency: 3)
- **Backwards**: Processes sequentially (safer for deletion)
- **Batch size**: 25 labels per batch (DynamoDB limit)

---

## Troubleshooting

### Error: "DYNAMODB_TABLE_NAME not found"

Make sure you have Pulumi configured or set the environment variable:

```bash
export DYNAMODB_TABLE_NAME="your-table-name"
```

Or ensure Pulumi stack is configured:

```bash
pulumi stack select dev
```

### Error: "Step Function execution failed"

Check the execution in AWS Console for detailed error messages:

```bash
# Get execution ARN from output, then:
aws stepfunctions describe-execution \
  --execution-arn <execution-arn> \
  --region us-east-1
```

### Labels Not Deleted

Make sure labels have `label_proposed_by="simple_receipt_analyzer"`. Check with:

```python
from receipt_dynamo import DynamoClient
client = DynamoClient("your-table-name")
labels, _ = client.list_receipt_word_labels_for_receipt(image_id, receipt_id)
for label in labels:
    print(f"{label.label_proposed_by}: {label.label}")
```

---

## Related Documentation

- `infra/create_labels_step_functions/README.md` - Step Function details
- `docs/VALIDATE_PENDING_LABELS_GUIDE.md` - How to validate created labels
- `docs/SELF_CORRECTING_VALIDATION_SYSTEM.md` - Advanced validation system

