# Queue Consumer Conflict - CRITICAL ISSUE

## Problem: Two Consumers, One Queue ⚠️

The `EMBED_NDJSON_QUEUE` currently has **TWO** consumers trying to process the same messages:

### 1. Lambda Consumer (ACTIVE)

**Location:** `infra/upload_images/infra.py` lines 629-637

```python
aws.lambda_.EventSourceMapping(
    f"{name}-embed-ndjson-direct-mapping",
    event_source_arn=self.embed_ndjson_queue.arn,
    function_name=embed_from_ndjson_lambda.name,
    batch_size=10,
    maximum_batching_window_in_seconds=5,
    enabled=True,  # ← ACTIVE
    opts=ResourceOptions(parent=self),
)
```

### 2. ECS Worker Consumer (DISABLED)

**Location:** `infra/__main__.py` line 310

```python
ChromaCompactionWorker(
    ...
    embed_ndjson_queue_url=upload_images.embed_ndjson_queue.url,
    embed_ndjson_queue_arn=upload_images.embed_ndjson_queue.arn,
    desired_count=0,  # ← DISABLED (but would poll the same queue if enabled)
)
```

## What Happens When Both Are Active?

If you enable the ECS worker (`desired_count=1`), you'll get:

```
SQS Queue (EMBED_NDJSON_QUEUE)
       │
       ├──────────────────────────┬─────────────────────────┐
       │                          │                         │
       ▼                          ▼                         ▼
  Message 1              Message 2                  Message 3
       │                          │                         │
       ▼                          ▼                         ▼
    Lambda              ← RACE CONDITION! →         ECS Worker
```

**Result:**

- ❌ Some messages go to Lambda
- ❌ Some messages go to ECS Worker
- ❌ Unpredictable behavior
- ❌ Duplicate processing possible
- ❌ Load not properly distributed

## Solution: Choose ONE Consumer

### Option 1: Use Lambda (Current State)

**Best for:** Lower cost, simpler ops, auto-scaling

Keep:

```python
# infra/upload_images/infra.py
aws.lambda_.EventSourceMapping(
    f"{name}-embed-ndjson-direct-mapping",
    enabled=True,  # ← Keep enabled
    ...
)

# infra/__main__.py
ChromaCompactionWorker(
    desired_count=0,  # ← Keep disabled
)
```

✅ Lambda automatically scales  
✅ Pay per invocation  
✅ Built-in retry/DLQ support  
❌ 15-minute timeout limit  
❌ Limited memory (up to 10GB)

### Option 2: Use ECS Worker (Recommended for Large Jobs)

**Best for:** Long-running jobs, more control, custom resources

Disable Lambda EventSourceMapping:

```python
# infra/upload_images/infra.py
aws.lambda_.EventSourceMapping(
    f"{name}-embed-ndjson-direct-mapping",
    enabled=False,  # ← DISABLE THIS
    ...
)

# OR comment it out entirely:
# aws.lambda_.EventSourceMapping(
#     f"{name}-embed-ndjson-direct-mapping",
#     ...
# )
```

Enable ECS Worker:

```python
# infra/__main__.py
ChromaCompactionWorker(
    desired_count=1,  # ← ENABLE THIS
)
```

✅ No timeout limits  
✅ More memory/CPU available  
✅ Better for EFS access patterns  
✅ Can run multiple workers  
❌ Fixed cost (even if idle)  
❌ Manual scaling

### Option 3: Hybrid (Different Queues)

**Best for:** Different workloads

This would require creating separate queues:

- `EMBED_NDJSON_QUEUE_LAMBDA` → Lambda for small/fast jobs
- `EMBED_NDJSON_QUEUE_ECS` → ECS for large/slow jobs

Not recommended unless you have a specific need.

## Recommendation

Based on your worker code using EFS extensively for snapshot restoration and delta creation, I recommend:

### **Use ECS Worker Only**

**Why:**

1. ✅ Worker already mounts EFS for ChromaDB access
2. ✅ No 15-minute timeout constraint
3. ✅ Better for merchant resolution (can take time)
4. ✅ More memory for large receipts
5. ✅ Your worker code is already optimized for this

**How to switch:**

1. **Disable the Lambda EventSourceMapping:**

   ```python
   # In infra/upload_images/infra.py line 629
   aws.lambda_.EventSourceMapping(
       f"{name}-embed-ndjson-direct-mapping",
       event_source_arn=self.embed_ndjson_queue.arn,
       function_name=embed_from_ndjson_lambda.name,
       batch_size=10,
       maximum_batching_window_in_seconds=5,
       enabled=False,  # ← Change to False
       opts=ResourceOptions(parent=self),
   )
   ```

2. **Enable the ECS Worker:**

   ```python
   # In infra/__main__.py line 310
   desired_count=1,  # ← Change from 0 to 1
   ```

3. **Deploy:**

   ```bash
   pulumi up
   ```

4. **Verify:**
   - Lambda no longer processes messages
   - ECS worker CloudWatch logs show activity
   - Check ECS task is running: `aws ecs list-tasks --cluster <cluster-name>`

## Current Architecture (After Switching to ECS)

```
┌──────────────────────────────┐
│ process_ocr_results.py       │
│ Exports NDJSON → S3          │
│ Enqueues to EMBED_NDJSON     │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ EMBED_NDJSON_QUEUE           │
│ (SQS FIFO Queue)             │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ ECS Worker                   │ ← Only consumer
│ - Polls queue                │
│ - Creates embeddings         │
│ - Uploads deltas to S3       │
│ - Writes CompactionRun       │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ DynamoDB Stream              │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ LINES_QUEUE & WORDS_QUEUE    │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ enhanced_compaction_handler  │
│ Merges deltas to EFS         │
└──────────────────────────────┘
```

## Testing After Switch

1. **Upload a receipt** through your app
2. **Check SQS metrics:** Messages should decrease as worker processes them
3. **Check ECS CloudWatch Logs:** Should see embedding activity
4. **Check Lambda is idle:** No invocations for `embed_from_ndjson_lambda`
5. **Verify deltas in S3:** At `s3://CHROMADB_BUCKET/lines/delta/{run_id}/`
6. **Check DynamoDB:** CompactionRun records being created

## Rollback Plan

If issues occur with ECS worker:

```python
# infra/upload_images/infra.py
enabled=True,  # Switch back to Lambda

# infra/__main__.py
desired_count=0,  # Disable ECS worker
```

Then `pulumi up` to revert.
