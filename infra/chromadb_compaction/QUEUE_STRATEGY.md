# SQS Queue Strategy for ChromaDB Compaction

## Problem Statement

The ChromaDB compaction Lambda processes DynamoDB stream events to sync label updates, place metadata, and deletions to ChromaDB. The queue strategy affects:

1. **Throughput** - How many messages can be processed per Lambda invocation
2. **Ordering** - Whether updates are applied in the correct sequence
3. **Data consistency** - Avoiding updates to deleted records

## Current Architecture (FIFO Queue)

### Why FIFO Was Chosen

FIFO queues were selected to prevent a race condition:
- If a record is deleted, then an older update arrives, we don't want to resurrect the deleted record
- FIFO ordering ensures deletes are processed after any pending updates

### Configuration

```
Queue Type: FIFO
MessageGroupId: "compaction:{collection}" (single group per collection)
Batch Size: 10 (AWS maximum for FIFO)
Visibility Timeout: 600 seconds (10 minutes)
Lambda Concurrency: 1
```

### Limitations Discovered

1. **AWS Hard Limit**: FIFO queues have a maximum batch size of 10 messages per `receive_message` call

2. **Message Group Locking**: When Lambda receives messages from a FIFO queue:
   - The messages become "in-flight"
   - The message group is locked until those messages are processed
   - Additional `receive_message` calls cannot fetch more from the locked group
   - This defeats "Phase 2 batching" (fetching additional messages within Lambda)

3. **Throughput Impact**:
   - ~10 messages per invocation
   - ~25 seconds per invocation (snapshot download/upload overhead)
   - Effective throughput: ~0.4 messages/second
   - 10,000 updates takes ~7 hours

## Alternative Strategies

### Option 1: FIFO with Current Settings (Status Quo)

| Aspect | Value |
|--------|-------|
| Batch Size | 10 messages |
| Ordering | Strict FIFO |
| Data Safety | Guaranteed |
| Throughput | ~0.4 msg/sec |

**Pros**: Simple, safe, no code changes needed
**Cons**: Very slow for bulk operations

### Option 2: Standard Queue + Graceful Failure Handling

Switch to standard (non-FIFO) queues and handle out-of-order updates at the application level.

| Aspect | Value |
|--------|-------|
| Batch Size | Up to 10,000 messages |
| Ordering | None (best-effort) |
| Data Safety | Via error handling |
| Throughput | ~50-100x improvement |

**How it works**:
1. Process updates in any order
2. When updating a record that was deleted:
   - ChromaDB's `update()` returns "ID not found"
   - Catch the error, log it, skip the message
   - No orphaned data created

**Pros**: Massive throughput improvement, Phase 2 batching works
**Cons**: Requires error handling code, some harmless error logs

### Option 3: Standard Queue + Existence Check

Before applying an update, check if the record still exists.

```python
if chroma_client.get(id=record_id):
    chroma_client.update(id=record_id, ...)
else:
    logger.info(f"Skipping update for deleted record: {record_id}")
```

**Pros**: Explicit handling, clear intent
**Cons**: Extra read operation, small race window between check and update

### Option 4: Timestamp-Based Conflict Resolution

Each message carries a timestamp. Only apply updates if the message timestamp is newer than the record's last-modified timestamp.

```python
current_record = chroma_client.get(id=record_id)
if message.timestamp > current_record.metadata.get("last_modified"):
    chroma_client.update(id=record_id, ...)
```

**Pros**: True idempotency, handles all race conditions
**Cons**: Requires timestamp tracking in ChromaDB metadata, more complex

### Option 5: Multiple Message Groups with Higher Concurrency

Use per-image MessageGroupIds with Lambda concurrency > 1.

```
MessageGroupId: "{entity_type}:{image_id}:{collection}"
Lambda Concurrency: 5-10
```

**Pros**: Parallel processing of different images
**Cons**: Returns to original locking issues, complex failure handling

## Recommendation

**Short-term**: Keep FIFO queues for safety, accept slower throughput for bulk operations.

**Long-term**: Migrate to **Standard Queue + Graceful Failure Handling** (Option 2):

1. ChromaDB operations are already somewhat idempotent
2. "ID not found" errors on update are harmless and expected
3. 50-100x throughput improvement
4. Phase 2 batching becomes effective
5. Simpler mental model (no FIFO complexity)

### Migration Path

1. Add error handling for "ID not found" in compaction handler
2. Test with a shadow standard queue
3. Verify no orphaned records are created
4. Switch production traffic
5. Monitor for unexpected error patterns

## Performance Comparison

| Scenario | FIFO (Current) | Standard (Proposed) |
|----------|----------------|---------------------|
| 10K updates | ~7 hours | ~10 minutes |
| Cost per 10K | ~$20 | ~$0.40 |
| Messages/invocation | 10 | Up to 500 |
| Recovery from failure | 10 min wait | Immediate retry |

## Related Configuration

### Current Settings (as of 2025-12-22)

- **Lambda Memory**: 10,240 MB (10GB) - increased due to OOM with 70K+ embeddings
- **Lambda Timeout**: 900 seconds (15 minutes)
- **Visibility Timeout**: 600 seconds (10 minutes) - reduced from 1200s for faster failure recovery
- **Reserved Concurrency**: 1 - prevents race conditions on snapshot updates
- **gc.collect()**: Added to handler for memory cleanup between warm starts

### Files

- Queue configuration: `components/sqs_queues.py`
- Lambda handler: `lambdas/enhanced_compaction_handler.py`
- Lambda configuration: `components/lambda_functions.py`
