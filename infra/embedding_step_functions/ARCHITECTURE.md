# Line Embedding Architecture

## Overview
The line embedding system processes receipt lines through OpenAI's Batch API to generate embeddings, then stores them in ChromaDB for vector search capabilities. The system uses two main Step Functions and a hybrid Lambda architecture.

## Step Functions

### 1. Create Embedding Batches (`create-batches-sf`)
**Purpose**: Find items without embeddings and submit them to OpenAI for processing.

```
FindUnembedded → SubmitBatches (Map State)
```

**Flow**:
1. **FindUnembedded**: Searches DynamoDB for lines without embeddings
2. **SubmitBatches**: Parallel processing of batches (up to 10 concurrent)
   - Each batch is submitted to OpenAI's Batch API
   - Returns batch IDs for tracking

### 2. Poll and Store Embeddings (`poll-store-sf`)
**Purpose**: Poll OpenAI for completed batches and store the embeddings in ChromaDB.

```
ListPendingBatches → CheckPendingBatches → PollBatches (Map) → CompactDeltas
                                         ↓
                                   NoPendingBatches
```

**Flow**:
1. **ListPendingBatches**: Queries DynamoDB for batches submitted to OpenAI
2. **CheckPendingBatches**: Choice state - proceeds if batches exist
3. **PollBatches**: Parallel polling of up to 10 batches
4. **CompactDeltas**: Merges all ChromaDB delta files into main storage

## Lambda Functions

### Zip-Based Lambdas (Fast, Lightweight)

#### 1. `list-pending` (512 MB, 900s timeout)
- **Type**: Zip deployment
- **Purpose**: List pending OpenAI batches from DynamoDB
- **Input**: None
- **Output**: Array of `{batch_id, openai_batch_id}`
- **Dependencies**: DynamoDB access only
- **Cold Start**: <1 second

#### 2. `find-unembedded` (1024 MB, 900s timeout)
- **Type**: Zip deployment
- **Purpose**: Find lines without embeddings and prepare batches
- **Process**:
  1. Query DynamoDB for lines with `embedding_status != SUCCESS`
  2. Chunk lines into batches (optimal size for OpenAI)
  3. Serialize and upload batches to S3
- **Output**: `{batches: [{s3_bucket, s3_key, line_count}], total_lines, batch_count}`
- **Dependencies**: DynamoDB, S3

#### 3. `submit-openai` (1024 MB, 900s timeout)
- **Type**: Zip deployment
- **Purpose**: Submit a batch to OpenAI's Batch API
- **Process**:
  1. Download serialized lines from S3
  2. Format as NDJSON for OpenAI
  3. Upload to OpenAI Files API
  4. Submit batch job
  5. Save batch metadata to DynamoDB
- **Output**: `{batch_id}`
- **Dependencies**: S3, OpenAI API, DynamoDB

### Container-Based Lambdas (ChromaDB Operations)

#### 4. `line-polling` (3008 MB, 900s timeout, 3GB ephemeral storage)
- **Type**: Container deployment (342 MB image)
- **Purpose**: Poll OpenAI batch status and save embeddings
- **Process**:
  1. Check batch status with OpenAI API
  2. If complete, download results
  3. Save embeddings as ChromaDB delta to S3
  4. Update DynamoDB with embedding records
  5. Mark lines as embedded
- **Key Feature**: `skip_sqs_notification: true` prevents individual compaction triggers
- **Output**: `{batch_id, status, delta_id, embedding_count}`
- **Dependencies**: OpenAI API, S3 (ChromaDB deltas), DynamoDB

#### 5. `word-polling` (3008 MB, 900s timeout, 3GB ephemeral storage)
- **Type**: Container deployment
- **Purpose**: Similar to line-polling but for word embeddings
- **Process**: Same as line-polling but for word-level embeddings

#### 6. `compaction` (4096 MB, 900s timeout, 5GB ephemeral storage)
- **Type**: Container deployment
- **Purpose**: Merge ChromaDB delta files into main database
- **Process**:
  1. Download delta files from S3
  2. Load into ChromaDB PersistentClient
  3. Merge deltas into main ChromaDB instance
  4. Upload compacted database back to S3
  5. Clean up processed deltas
- **Critical**: Only function that directly uses ChromaDB libraries

## Data Flow

### Phase 1: Batch Creation
```
DynamoDB (unembedded lines) 
    → find-unembedded 
    → S3 (serialized batches)
    → submit-openai 
    → OpenAI Batch API
    → DynamoDB (batch tracking)
```

### Phase 2: Polling & Storage
```
DynamoDB (pending batches)
    → list-pending
    → line-polling (parallel)
    → OpenAI API (get results)
    → S3 (ChromaDB deltas)
    → compaction
    → S3 (main ChromaDB)
```

## Storage Locations

### S3 Buckets
1. **Batch Bucket** (`embedding-infra-batch-bucket-{stack}`)
   - Stores serialized line batches (temporary)
   - NDJSON files for OpenAI submission

2. **ChromaDB Bucket** (`chromadb-test-chromadb-buckets-{stack}`)
   - `/deltas/`: Individual embedding deltas from polling
   - `/snapshots/`: Compacted ChromaDB databases
   - `/chunks/`: Intermediate compaction files

### DynamoDB Tables
- **Main Table** (`ReceiptsTable-{hash}`)
  - Stores line items with embedding status
  - Tracks batch metadata and OpenAI batch IDs
  - Records embedding completion

## Environment Variables

### Common to All Lambdas
- `DYNAMODB_TABLE_NAME`: Main DynamoDB table
- `OPENAI_API_KEY`: OpenAI API authentication
- `S3_BUCKET`: Batch storage bucket

### ChromaDB Lambdas Only
- `CHROMADB_BUCKET`: ChromaDB storage bucket
- `CHROMA_PERSIST_DIRECTORY`: `/tmp/chroma`
- `COMPACTION_QUEUE_URL`: SQS queue for compaction triggers

### Compaction Specific
- `CHUNK_SIZE`: 10 (deltas per chunk)
- `HEARTBEAT_INTERVAL_SECONDS`: 60
- `LOCK_DURATION_MINUTES`: 5
- `DELETE_PROCESSED_DELTAS`: false
- `DELETE_INTERMEDIATE_CHUNKS`: true

## Performance Characteristics

### Cold Start Times
- **Zip Lambdas**: 200-800ms
- **Container Lambdas**: 5-10 seconds

### Memory Usage
- **Simple operations**: 512-1024 MB
- **Polling operations**: 3008 MB (processing embeddings)
- **Compaction**: 4096 MB (ChromaDB operations)

### Typical Processing Times
- **Finding unembedded**: 5-30 seconds (depends on count)
- **Submitting batch**: 10-20 seconds
- **Polling batch**: 30-60 seconds (downloading results)
- **Compaction**: 60-180 seconds (merging deltas)

## Scaling Considerations

### Concurrency Limits
- **Map States**: MaxConcurrency: 10
  - Prevents overwhelming OpenAI API
  - Controls S3 throughput
  - Manages Lambda concurrency

### Batch Sizes
- **Lines per batch**: Optimized for OpenAI limits
- **Pending batches**: Limited to 5 (temporary)
- **Delta accumulation**: Compacted after each polling cycle

### Cost Optimization
- **Zip Lambdas**: Minimal storage, fast execution
- **Container reuse**: Warm containers serve multiple requests
- **Batch processing**: Reduces API calls to OpenAI
- **Delta compression**: Minimizes S3 storage

## Error Handling

### Retry Logic
- **OpenAI API**: Exponential backoff built into SDK
- **DynamoDB**: AWS SDK automatic retries
- **S3 Operations**: Automatic retries with jitter

### Failure States
- **Expired batches**: Partial results processed, failed items marked for retry
- **Failed batches**: Items marked for retry in next cycle
- **Compaction failures**: Deltas preserved for next attempt

## Monitoring Points

### Key Metrics
1. **Batch creation rate**: Lines processed per hour
2. **OpenAI batch completion time**: Average time to complete
3. **Delta accumulation**: Number of uncompacted deltas
4. **Cold start frequency**: Container vs zip performance
5. **Error rates**: By Lambda function and error type

### CloudWatch Logs
- Each Lambda logs to `/aws/lambda/{function-name}`
- Step Functions log to state machine execution history
- Structured logging with correlation IDs

## Future Improvements

1. **Remove batch limit**: Implement proper pagination for pending batches
2. **Incremental compaction**: Compact deltas in smaller chunks
3. **Caching layer**: Add Redis for frequently accessed embeddings
4. **Batch optimization**: Dynamic batch sizing based on line complexity
5. **Cost tracking**: Per-receipt embedding cost allocation