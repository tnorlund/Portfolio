# Compaction Retry Mechanism

## Overview

When the enhanced compactor encounters a lock collision (another process is already holding the lock), it returns failed messages to SQS for retry. This document explains the retry timing and how to avoid collisions.

## SQS Retry Configuration

### Queue Settings
- **Visibility Timeout**: 1200 seconds (20 minutes)
  - How long a message is hidden after being received
  - Must be longer than Lambda timeout (14 minutes)
- **Max Receive Count**: 3
  - Messages are retried 3 times before going to Dead Letter Queue (DLQ)
- **Message Retention**: 345600 seconds (4 days)

### Lock Configuration
- **Lock Duration**: 1 minute (`LOCK_DURATION_MINUTES`)
- **Heartbeat Interval**: 30 seconds (`HEARTBEAT_INTERVAL_SECONDS`)
- **Max Heartbeat Failures**: 2 (`MAX_HEARTBEAT_FAILURES`)

## Retry Flow

### When Lock Collision Occurs

1. **Lambda receives SQS message** for compaction (e.g., CompactionRun INSERT or RECEIPT REMOVE)
2. **Lambda attempts to acquire lock** (`chroma-{collection}-update`)
3. **Lock is already held** by another Lambda invocation
4. **Lambda returns `batchItemFailures`** with the failed message receipt handles
5. **SQS makes message invisible** for visibility timeout (20 minutes)
6. **After 20 minutes**, message becomes visible again
7. **SQS retries the message** (up to 3 times total)
8. **After 3 failures**, message goes to DLQ

### Retry Timing

```
Time 0:00 - Message received, lock collision
Time 0:00 - Message becomes invisible (visibility timeout starts)
Time 20:00 - Message becomes visible again (first retry)
Time 20:00 - If collision again, message becomes invisible
Time 40:00 - Message becomes visible again (second retry)
Time 40:00 - If collision again, message becomes invisible
Time 60:00 - Message becomes visible again (third retry)
Time 60:00 - If collision again, message goes to DLQ
```

**Total retry time**: Up to 60 minutes (3 retries × 20 minutes)

## Common Collision Scenarios

### Scenario 1: Create + Delete in Quick Succession

**Problem**: Creating embeddings and immediately deleting the receipt causes collisions:

1. **Create embeddings** → CompactionRun INSERT → Stream processor → SQS messages (lines + words)
2. **Delete receipt** → RECEIPT REMOVE → Stream processor → SQS messages (lines + words)
3. **Both sets of messages** compete for the same lock (`chroma-words-update`, `chroma-lines-update`)
4. **First message** acquires lock and processes
5. **Second message** fails and retries after 20 minutes

**Solution**: Wait for compaction to complete before deleting:

```python
# Create embeddings
create_embeddings(...)

# Wait for compaction to complete
wait_for_compaction(...)

# Now safe to delete
delete_receipt(...)
```

### Scenario 2: Multiple Receipts in Same Image

**Problem**: Processing multiple receipts for the same image simultaneously:

1. **Receipt 2** → CompactionRun INSERT → SQS messages
2. **Receipt 3** → CompactionRun INSERT → SQS messages (same collection)
3. **Both compete** for the same lock
4. **One succeeds**, one retries

**Solution**: Process receipts sequentially or add delays between operations.

## Best Practices

### 1. Wait for Compaction Before Delete

Always wait for compaction to complete before deleting a receipt:

```python
from receipt_agent.lifecycle import create_receipt, delete_receipt, wait_for_compaction

# Create receipt with embeddings
receipt = create_receipt(
    client=client,
    receipt=receipt_entity,
    create_embeddings=True,
    wait_for_compaction=True,  # Wait for compaction to complete
)

# Now safe to delete (compaction is complete)
delete_receipt(client, receipt)
```

### 2. Add Delays Between Operations

If processing multiple receipts, add delays to avoid collisions:

```python
import time

for receipt in receipts:
    create_embeddings(...)
    time.sleep(30)  # Wait 30 seconds between operations
```

### 3. Monitor Compaction Status

Check compaction status before proceeding:

```python
from receipt_agent.lifecycle.compaction_manager import check_compaction_status

status = check_compaction_status(client, image_id, receipt_id, run_id)
if status["lines_state"] == "COMPLETED" and status["words_state"] == "COMPLETED":
    # Safe to proceed
    pass
```

### 4. Handle Retries Gracefully

The system automatically retries failed messages. Monitor CloudWatch metrics:
- `CompactionLockCollision`: Number of lock collisions
- `CompactionFailedMessages`: Number of failed messages
- `CompactionPartialBatchFailure`: Number of partial batch failures

## Monitoring

### CloudWatch Metrics

- **CompactionLockCollision**: Count of lock collisions (by phase and collection)
- **CompactionLockWaitTime**: Time spent waiting for lock (milliseconds)
- **CompactionFailedMessages**: Number of failed messages in a batch
- **CompactionPartialBatchFailure**: Number of partial batch failures

### CloudWatch Logs

Search for:
- `"Lock busy during validation"` - Phase 1 lock collision
- `"Lock busy during upload"` - Phase 3 lock collision
- `"Failed to acquire lock"` - Lock acquisition failure

### Dead Letter Queue

Check DLQ for messages that failed after 3 retries:
- Queue: `chromadb-dev-{collection}-dlq`
- Messages in DLQ indicate persistent failures (investigate)

## Troubleshooting

### Messages Stuck in Queue

If messages are stuck:
1. Check CloudWatch logs for lock collisions
2. Verify no Lambda is holding the lock indefinitely
3. Check DLQ for failed messages
4. Verify DynamoDB locks are not expired (check `CompactionLock` table)

### Long Retry Times

If retries are taking too long:
1. **Reduce visibility timeout** (not recommended - must be > Lambda timeout)
2. **Reduce lock duration** (already optimized at 1 minute)
3. **Add delays between operations** (recommended)
4. **Wait for compaction before delete** (recommended)

### Lock Collisions During Testing

When testing create + delete workflows:
1. **Always wait for compaction** before deleting
2. **Add explicit delays** between operations
3. **Process receipts sequentially** instead of in parallel
4. **Monitor CloudWatch metrics** to detect collisions

## Summary

- **Retry delay**: 20 minutes (visibility timeout)
- **Max retries**: 3 attempts before DLQ
- **Total retry time**: Up to 60 minutes
- **Best practice**: Wait for compaction to complete before deleting
- **Collision avoidance**: Add delays between operations or process sequentially

