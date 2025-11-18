# DynamoDB TransactionConflict in Line Embedding Polling

## Problem

The line ingestion step function is experiencing `TransactionCanceledException` errors with `TransactionConflict` cancellation reasons when multiple batches are processed concurrently. This occurs in the `PollBatch` step when updating line embedding statuses to `SUCCESS`.

## Error Details

```
DynamoDB error during update_entities: TransactionCanceledException - Transaction cancelled,
please refer cancellation reasons for specific reasons [TransactionConflict, TransactionConflict, ...]
```

**Error Location**: `receipt_label/receipt_label/embedding/line/poll.py::update_line_embedding_status_to_success`

**Step Function**: `line-ingest-sf-dev-1554303`

## Root Cause Analysis

### Expected Behavior
According to the batch creation logic:
- `chunk_into_line_embedding_batches()` groups lines by `(image_id, receipt_id)`
- `serialize_receipt_lines()` creates **one file per `(image_id, receipt_id)` pair**
- Each file becomes **one batch** submitted to OpenAI
- **Each batch should only contain lines from ONE receipt**

### Actual Behavior (CONTRADICTS EXPECTATION)
The `TransactionConflict` errors **prove** that:
- Multiple concurrent Lambda invocations (up to 50 with `MaxConcurrency: 50`) are attempting to update lines from the **same receipt** simultaneously
- This **contradicts** the expected behavior that each batch should be unique per receipt
- **The same receipt IS appearing in multiple batches**, despite the batch creation logic suggesting it shouldn't

### Possible Causes

1. **Batch Re-submission**: If batches are re-submitted (e.g., after a reset with `dev.full_reset_and_reembed.py`), the same receipt could appear in multiple batches from different submission runs
2. **Race Condition in Batch Creation**: Multiple submission runs could create overlapping batches for the same receipt
3. **Batch Creation Bug**: There may be a bug in batch creation that allows the same receipt to appear in multiple batches despite the grouping logic
4. **Concurrent Processing of Old + New Batches**: If old batches weren't cleaned up and new batches are created, the same receipt could appear in both old and new batches

## Current Implementation

The current code groups lines by receipt and updates them in a transaction:

```python
# Update lines in DynamoDB by receipt
for image_id, receipt_dict in lines_by_receipt.items():
    for receipt_id, lines in receipt_dict.items():
        if lines:
            client_manager.dynamo.update_receipt_lines(lines)  # Transactional update
```

This causes conflicts when multiple batches (even if they're supposed to be unique per receipt) try to update the same receipt's lines concurrently.

## Proposed Solution

Update lines individually instead of grouping by receipt:

```python
# Update each line individually using PutItem (non-transactional)
# This avoids TransactionConflict errors in concurrent scenarios
for line in all_lines:
    client_manager.dynamo.update_receipt_line(line)
```

**Benefits**:
- Eliminates transaction conflicts
- Still deduplicates lines (using hash-based deduplication)
- Handles concurrency gracefully

## Investigation Needed

### Critical Question: Why does the same receipt appear in multiple batches?

The TransactionConflict errors **prove** that multiple batches contain lines from the same receipt, contradicting the expected behavior. We need to investigate:

1. **Check BatchSummary records**:
   ```python
   # Query all LINE_EMBEDDING batch summaries and check receipt_refs
   # Look for receipts that appear in multiple batches
   ```

2. **Verify batch creation logic**:
   - Does `serialize_receipt_lines()` always create one file per receipt?
   - Can the same receipt appear in multiple files if `chunk_into_line_embedding_batches()` is called multiple times?

3. **Check for batch re-submission**:
   - If `dev.full_reset_and_reembed.py` is run, are old batches cleaned up before new ones are created?
   - Can multiple submission step function runs create overlapping batches?

4. **Verify batch uniqueness**:
   - Query DynamoDB to find receipts that appear in multiple `BatchSummary` records
   - Check if `receipt_refs` in `BatchSummary` can contain multiple receipts (it shouldn't based on the logic)

## Related Code

- `receipt_label/receipt_label/embedding/line/submit.py::chunk_into_line_embedding_batches()`
- `receipt_label/receipt_label/embedding/line/submit.py::serialize_receipt_lines()`
- `receipt_label/receipt_label/embedding/line/poll.py::update_line_embedding_status_to_success()`
- `infra/embedding_step_functions/components/line_workflow.py` (MaxConcurrency: 50)

## Impact

- **Severity**: High - Prevents line embedding ingestion from completing
- **Frequency**: Occurs consistently when processing multiple batches concurrently
- **Workaround**: Update lines individually instead of using transactions (implemented in PR)

## Testing

To verify the fix:
1. Deploy the individual update fix
2. Run the line ingestion step function with multiple batches
3. Monitor for `TransactionConflict` errors
4. Verify that all lines are updated successfully

