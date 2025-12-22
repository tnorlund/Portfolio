# ChromaDB Compaction Strategy

## Overview

This document outlines the architecture, current issues, and optimization strategy for the self-hosted ChromaDB compaction system.

## Architecture

```
DynamoDB Stream → SQS FIFO → Lambda → Lock → Download Snapshot → Apply Updates → Upload Snapshot
                                ↓
                         (Lock contention)
                         Multiple Lambdas fight for lock
                         Losers fail fast, messages return to queue
```

### Key Components

| Component | Configuration |
|-----------|---------------|
| Lambda Memory | 4096 MB |
| Lambda Timeout | 900s (15 min) |
| Ephemeral Storage | 10 GB |
| Lock Duration | 5 min |
| SQS Message Retention | 4 days (main), 14 days (DLQ) |
| Max Receive Count | 3 (then → DLQ) |

### Compaction Cycle Timing

| Phase | Duration |
|-------|----------|
| Snapshot download | ~5s |
| In-memory updates | ~1.6s |
| Snapshot upload + validation | ~12s |
| **Total** | **~19s** |

## Current Issues

### 1. Race Condition (Lost Updates)

There's a window where updates can be lost:

```
Time    Lambda A                          Lambda B
────    ────────                          ────────
T1      Acquire lock
T2      Download snapshot V1
T3      Apply updates
T4      Upload versioned/050000/
T5      Validate lock ✓
T6      ─── Lock expires ───
T7                                        Acquire lock
T8                                        Download V1 (pointer not updated yet!)
T9      Write pointer → 050000
T10                                       Apply updates (on V1, not V2!)
T11                                       Upload versioned/050001/
T12                                       Write pointer → 050001

Result: Lambda A's updates are LOST. V3 was built from V1, not V2.
```

### 2. Lock Contention (Wasted Resources)

Multiple Lambdas are invoked concurrently, but only one can hold the lock:
- Winners: ~19s execution, successful compaction
- Losers: ~70ms execution, wasted invocation, messages return to queue

### 3. Throughput Bottleneck

- SQS FIFO batch size: 10 messages max
- Compaction cycle: ~19s
- Effective throughput: ~30 messages/min
- S3 transfers are the bottleneck (17s of 19s total)

## Cost Analysis

### Current Monthly Cost (~$130-150)

| Component | Monthly Cost |
|-----------|--------------|
| Lambda (compactions) | ~$86 |
| Lambda (lock failures) | ~$4 |
| Lambda (cold starts) | ~$1 |
| S3 Storage (5.4 GB) | ~$0.12 |
| S3 Requests | ~$20 |
| SQS FIFO | ~$10-20 |
| DynamoDB (locks) | ~$5 |

### Why EFS Was Expensive

EFS throughput charges killed the budget:
- Storage: 5GB × $0.30 = $1.50/mo
- Throughput: 5GB × 3 compactions/min × 43K min/mo × $0.03 = **~$19,000/mo**

### Chroma Cloud Comparison

| Scenario | Self-Hosted | Chroma Cloud |
|----------|-------------|--------------|
| Low query volume (<1M/mo) | ~$130/mo | ~$5-10/mo |
| Medium query volume (~10M/mo) | ~$130/mo | ~$50-100/mo |
| High query volume (~100M/mo) | ~$130/mo | ~$500-1000/mo |

Self-hosted has flat costs regardless of query volume since queries run in-memory after snapshot download.

## Optimization Strategy

### Phase 1: Simple Fix (Recommended First)

```python
# In Pulumi infrastructure
lambda_function.reserved_concurrent_executions = 1

# In SQS event source mapping
event_source_mapping.maximum_batching_window_in_seconds = 30
```

**Benefits:**
- Eliminates race condition (no concurrent Lambdas)
- Eliminates wasted invocations (no lock contention)
- Better batching (accumulates messages during compaction)
- FIFO ordering fully preserved

**Throughput:** ~30 messages/min

### Phase 2: In-Lambda Batching (If More Throughput Needed)

Since SQS FIFO limits batch size to 10, fetch more messages within the handler:

```python
def handler(event, context):
    messages = parse_sqs_messages(event)  # Initial 10

    # Greedily fetch more from queue
    while len(messages) < 100:
        additional = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            VisibilityTimeout=1200,
        )
        if not additional.get('Messages'):
            break
        messages.extend(parse_messages(additional['Messages']))

    # Process all messages in ONE compaction cycle
    process_compaction(messages)

    # Delete messages manually
    for msg in messages:
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg.receipt_handle)
```

**Throughput:** ~150-300 messages/min

### Phase 3: Sharding (For Horizontal Scale)

If throughput still insufficient, shard the collection:

```
words → hash(word_id) % 4 → words-0, words-1, words-2, words-3
```

Each shard has independent:
- MessageGroupId (parallel SQS processing)
- Lock (parallel compaction)
- S3 snapshot (smaller, faster transfers)

**Throughput:** N shards × base throughput

**Trade-off:** Queries must search all shards and merge results.

## Data Durability

### Safe Scenarios

- **Compaction failure mid-upload**: Pointer unchanged, old snapshot remains valid
- **Lock contention**: Messages return to queue, retry later

### Risk Scenarios

- **DLQ expiration**: Messages in DLQ for >14 days are deleted
- **Prolonged outage**: Main queue messages expire after 4 days

### Mitigations

1. Set up CloudWatch alarm when DLQ `ApproximateNumberOfMessages > 0`
2. Periodically redrive or purge DLQ
3. Consider increasing main queue retention to 14 days

## Queue Management Commands

```bash
# Check queue depths
aws sqs get-queue-attributes \
  --queue-url "https://sqs.us-east-1.amazonaws.com/ACCOUNT/chromadb-dev-queues-words-queue-XXXX.fifo" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible

# Purge main queue
aws sqs purge-queue --queue-url "https://sqs.us-east-1.amazonaws.com/ACCOUNT/chromadb-dev-queues-words-queue-XXXX.fifo"

# Purge DLQ
aws sqs purge-queue --queue-url "https://sqs.us-east-1.amazonaws.com/ACCOUNT/chromadb-dev-queues-words-dlq-XXXX.fifo"

# Redrive DLQ to main queue
aws sqs start-message-move-task \
  --source-arn "arn:aws:sqs:us-east-1:ACCOUNT:chromadb-dev-queues-words-dlq-XXXX.fifo" \
  --destination-arn "arn:aws:sqs:us-east-1:ACCOUNT:chromadb-dev-queues-words-queue-XXXX.fifo"
```

## Reset Procedure

To fully reset the ChromaDB state and re-ingest:

1. Purge SQS queues (main + DLQ)
2. Clear DynamoDB locks
3. Delete S3 snapshots
4. Reset batch summaries
5. Run ingest step function

See `scripts/reset_chromadb.py` for automation.
