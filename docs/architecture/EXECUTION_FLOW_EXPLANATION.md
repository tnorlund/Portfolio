# Execution Flow Explanation

## Current Flow (Sequential, Not Parallel)

```
1. Embedding Processing (~15-30s)
   ├─ Merchant resolution
   ├─ ReceiptMetadata created
   ├─ Embeddings generated
   ├─ Deltas uploaded to S3
   └─ COMPACTION_RUN created
   ↓
2. Lambda returns immediately ✅ (async task starts)
   ↓
3. Compaction Lambda starts (async via DynamoDB Streams)
   ├─ Downloads deltas from S3
   ├─ Merges into ChromaDB snapshot
   └─ Takes ~30-60 seconds
   ↓
4. Validation Task (runs in background async task)
   ├─ POLLS compaction state every 2 seconds
   ├─ WAITS for lines_state + words_state = COMPLETED
   └─ Takes ~0-60 seconds (waiting)
   ↓
5. Compaction completes (lines + words → COMPLETED)
   ↓
6. Validation proceeds (LangGraph runs)
   ├─ Extracts labels from receipt
   ├─ Creates ReceiptWordLabels
   └─ Takes ~20-30 seconds
```

## Key Points

### Lambda Returns Fast (Sequential, but Non-blocking)
- ✅ Lambda returns ~15-30 seconds after embedding
- ✅ Compaction runs independently (via DynamoDB Streams)
- ✅ Validation starts as background task
- ❌ Validation WAITS for compaction to finish
- ❌ LangGraph work does NOT run in parallel with compaction

### Why We Wait
We wait because:
1. Compaction reads from DynamoDB (receipt_words, receipt_lines)
2. Validation writes to DynamoDB (ReceiptWordLabels)
3. If both happen simultaneously, we could have inconsistent state

### Timing
- Embedding: 15-30 seconds
- Lambda Return: After embedding (doesn't wait for compaction or validation)
- Compaction: 30-60 seconds (async, via streams)
- Validation Wait: 0-60 seconds (polls state)
- LangGraph Work: 20-30 seconds

**Total End-to-End**: ~65-120 seconds (but Lambda only takes 15-30 seconds)

## The Answer

> "Does it run the langgraph part in parallel with the compaction run?"

**NO.** The LangGraph validation waits for compaction to finish first.

### What's Actually Parallel
- ✅ Compaction runs independently (via DynamoDB Streams)
- ✅ Validation task starts independently (asyncio.create_task)
- ❌ But validation WAITS for compaction before running LangGraph

### Why We Can't Run in Parallel
If we ran LangGraph in parallel with compaction:
- Compaction reads from DynamoDB (to get word IDs for ChromaDB)
- LangGraph writes ReceiptWordLabels to DynamoDB
- Could create race condition or inconsistent state

## Alternative Approach

If we want true parallelism:

1. **Skip Waiting**: Run LangGraph immediately, don't wait
   - ❌ Risk: Race condition with compaction
   - ⚠️ Labels might not be in consistent state

2. **Create Labels After Compaction**: Move label creation to a separate Lambda
   - ✅ Ensures compaction completes first
   - ⚠️ More complex architecture

3. **Current Approach (Best)**: Wait in async task
   - ✅ Lambda returns quickly (non-blocking)
   - ✅ Ensures consistent state
   - ✅ Resilient with timeout protection

## Recommendation

The current approach is correct. The "parallelism" happens at the Lambda level (compaction and validation both run as separate async processes), but the validation work itself waits for compaction to complete to ensure data consistency.

