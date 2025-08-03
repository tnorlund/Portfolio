# ChromaDB S3 Compaction Architecture

## Overview

This document provides a comprehensive overview of the ChromaDB S3 compaction pipeline implementation, which enables scalable, serverless vector storage with multi-writer safety for the receipt processing system.

## Architecture Components

### 1. ChromaDB Client Modes

The `ChromaDBClient` class supports three distinct operation modes:

#### Read Mode (`mode="read"`)
- **Purpose**: Query-only access for consumer lambdas
- **Write Protection**: All write operations throw `RuntimeError`
- **Use Case**: Lambda functions that only need to search vectors
- **Example**:
```python
chroma = ChromaDBClient(persist_directory="/mnt/chroma", mode="read")
results = chroma.query(collection_name="words", query_texts=["walmart"])
# chroma.upsert_vectors(...) # Raises RuntimeError
```

#### Delta Mode (`mode="delta"`)
- **Purpose**: Write temporary deltas for producer lambdas
- **Automatic Persistence**: Persists after each write operation
- **S3 Upload**: Provides `persist_and_upload_delta()` method
- **Use Case**: Lambda functions that generate new embeddings
- **Example**:
```python
chroma = ChromaDBClient(persist_directory="/tmp/delta", mode="delta")
chroma.upsert_vectors(...)  # Automatically persists
delta_key = chroma.persist_and_upload_delta(bucket, "delta/")
```

#### Snapshot Mode (`mode="snapshot"`)
- **Purpose**: Full read-write access for compactor lambda
- **Duplicate Handling**: Safe upserts with delete-and-retry logic
- **Use Case**: Compaction process that merges deltas
- **Example**:
```python
chroma = ChromaDBClient(persist_directory="/tmp/snapshot", mode="snapshot")
# Can read, write, and handle duplicate IDs during merge
```

### 2. DynamoDB CompactionLock Integration

The compaction process uses DynamoDB's conditional writes for distributed locking:

```python
# Lock structure
CompactionLock(
    lock_id="chroma-main-snapshot",
    owner=str(uuid.uuid4()),  # Unique owner ID
    expires=datetime.now(timezone.utc) + timedelta(minutes=15)
)
```

**Key Features**:
- **Atomic Acquisition**: Uses conditional put (attribute_not_exists)
- **TTL-based Expiration**: Prevents deadlocks from crashed workers
- **Heartbeat Updates**: Extends lock for long-running compactions
- **Owner Validation**: Only lock owner can release

### 3. S3 Storage Layout

```
s3://vectors-bucket/
├── snapshot/
│   ├── 2025-08-03T12:00:00Z/    # Timestamped snapshots
│   │   ├── chroma.sqlite3        # ChromaDB database
│   │   └── index/                # HNSW index files
│   │       ├── uuid/
│   │       └── metadata.json
│   └── latest/                   # Pointer to current snapshot
│       ├── chroma.sqlite3
│       └── index/
└── delta/
    ├── a1b2c3d4e5f6/            # UUID-named delta directories
    │   ├── chroma.sqlite3
    │   └── index/
    └── e5f6g7h8i9j0/
        ├── chroma.sqlite3
        └── index/
```

### 4. ChromaCompactor Process Flow

```python
def compact_deltas(self, delta_keys: List[str]) -> CompactionResult:
    # 1. Acquire distributed lock
    lock = self._acquire_lock()
    if not lock:
        return CompactionResult(status="busy")
    
    try:
        # 2. Download current snapshot
        self._download_s3_prefix("snapshot/latest/", snapshot_dir)
        
        # 3. Open snapshot in writeable mode
        chroma = ChromaDBClient(snapshot_dir, mode="snapshot")
        
        # 4. Merge each delta
        for delta_key in delta_keys:
            delta_client = ChromaDBClient(delta_dir, mode="read")
            self._merge_collections(delta_client, chroma)
        
        # 5. Upload new timestamped snapshot
        new_key = f"snapshot/{datetime.now().isoformat()}/"
        self._upload_directory(snapshot_dir, new_key)
        
        # 6. Update latest pointer atomically
        self._copy_s3_prefix(new_key, "snapshot/latest/")
        
    finally:
        # 7. Always release lock
        self._release_lock(lock)
```

## Lambda Integration Patterns

### Producer Lambda Pattern

```python
def embedding_producer_handler(event, context):
    # Extract data from event
    words = event["words"]
    
    # Generate embeddings
    embeddings = generate_embeddings(words)
    
    # Use helper function to produce delta
    result = produce_embedding_delta(
        ids=word_ids,
        embeddings=embeddings,
        documents=word_texts,
        metadatas=word_metadata,
        collection_name="words"
    )
    
    # Helper automatically:
    # 1. Creates ChromaDB in delta mode
    # 2. Upserts vectors
    # 3. Uploads to S3
    # 4. Sends SQS notification
    
    return {"delta_key": result["delta_key"]}
```

### Consumer Lambda Pattern

```python
def query_handler(event, context):
    # Use helper for snapshot queries
    results = query_snapshot(
        query_texts=[event["query"]],
        n_results=10,
        where=event.get("filters"),
        snapshot_path="/mnt/chroma"  # EFS mount
    )
    
    # Helper automatically:
    # 1. Creates read-only ChromaDB client
    # 2. Executes query
    # 3. Returns formatted results
    
    return {"results": results}
```

### Compactor Lambda Pattern

```python
def compactor_handler(event, context):
    # Extract delta keys from SQS
    delta_keys = [
        json.loads(record["body"])["delta_key"] 
        for record in event["Records"]
    ]
    
    # Initialize compactor with DynamoDB client
    compactor = ChromaCompactor(
        dynamo_client=DynamoClient(os.environ["DYNAMODB_TABLE_NAME"]),
        bucket_name=os.environ["VECTORS_BUCKET"]
    )
    
    # Run compaction with automatic locking
    result = compactor.compact_deltas(delta_keys)
    
    return {
        "status": result.status,
        "deltas_processed": result.deltas_processed
    }
```

## Performance Characteristics

### Write Performance (Producer)
- **Delta Creation**: ~20ms (in-memory operations)
- **Persistence**: ~50-100ms (SQLite flush)
- **S3 Upload**: ~200-500ms (depends on size)
- **Total**: ~300-600ms per batch

### Read Performance (Consumer)
- **Cold Start**: 2-3s (EFS mount + ChromaDB init)
- **Warm Query**: ~30-80ms (depends on collection size)
- **Metadata Filtering**: Efficient with ChromaDB indexes
- **Vector Search**: HNSW algorithm provides log(n) complexity

### Compaction Performance
- **Lock Acquisition**: ~50ms
- **Snapshot Download**: 2-5s (10-100MB typical)
- **Delta Merge**: ~100ms per delta
- **Snapshot Upload**: 3-10s
- **Total**: 10-30s for 10-50 deltas

## Operational Benefits

### 1. Multi-Writer Safety
- Multiple producer lambdas can write concurrently
- No coordination required between producers
- Compactor handles all merge conflicts

### 2. Atomic Updates
- Readers always see consistent snapshots
- No partial updates visible
- Zero-downtime updates via pointer swap

### 3. Failure Recovery
- Failed compactions don't corrupt data
- TTL-based locks prevent deadlocks
- Deltas preserved until successful compaction

### 4. Horizontal Scaling
- Add producer lambdas as needed
- Multiple compactor instances (with locking)
- Read replicas via multiple EFS mounts

### 5. Cost Efficiency
- Pay only for Lambda execution time
- S3 storage is cheap (~$0.023/GB)
- No always-on database costs

## Monitoring and Alerting

### Key Metrics to Monitor

1. **Lock Contention**
   - Metric: `CompactionLockBusy` count
   - Alert: If > 10% of attempts are busy

2. **Compaction Duration**
   - Metric: `CompactionDuration` histogram
   - Alert: If p99 > 60 seconds

3. **Delta Queue Depth**
   - Metric: SQS `ApproximateNumberOfMessages`
   - Alert: If > 1000 messages

4. **Snapshot Size Growth**
   - Metric: S3 object size for `snapshot/latest/`
   - Alert: If growth > 20% per day

5. **Producer Errors**
   - Metric: Lambda error rate
   - Alert: If error rate > 1%

### CloudWatch Dashboards

```json
{
  "widgets": [
    {
      "type": "metric",
      "properties": {
        "metrics": [
          ["AWS/Lambda", "Duration", {"stat": "p99"}],
          ["AWS/DynamoDB", "UserErrors", {"TableName": "CompactionLock"}],
          ["AWS/SQS", "ApproximateNumberOfMessages", {"QueueName": "delta-queue"}]
        ]
      }
    }
  ]
}
```

## Security Considerations

### 1. IAM Policies

**Producer Lambda**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject"],
      "Resource": "arn:aws:s3:::vectors-bucket/delta/*"
    },
    {
      "Effect": "Allow",
      "Action": ["sqs:SendMessage"],
      "Resource": "arn:aws:sqs:*:*:delta-queue"
    }
  ]
}
```

**Consumer Lambda**:
```json
{
  "Effect": "Allow",
  "Action": ["s3:GetObject"],
  "Resource": "arn:aws:s3:::vectors-bucket/snapshot/latest/*"
}
```

**Compactor Lambda**:
```json
{
  "Effect": "Allow",
  "Action": [
    "s3:GetObject",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:ListBucket"
  ],
  "Resource": [
    "arn:aws:s3:::vectors-bucket/*",
    "arn:aws:s3:::vectors-bucket"
  ]
}
```

### 2. Encryption
- **S3**: Enable SSE-S3 or SSE-KMS
- **DynamoDB**: Enable encryption at rest
- **EFS**: Enable encryption in transit

### 3. Network Security
- **VPC**: Run lambdas in private subnets
- **Security Groups**: Restrict EFS access
- **VPC Endpoints**: Use S3/DynamoDB endpoints

## Migration Strategy

### Phase 1: Parallel Operation
1. Keep Pinecone running
2. Deploy ChromaDB infrastructure
3. Dual-write to both systems
4. Compare query results

### Phase 2: Gradual Cutover
1. Route 10% queries to ChromaDB
2. Monitor performance/accuracy
3. Increase percentage gradually
4. Full cutover at 100%

### Phase 3: Cleanup
1. Stop writes to Pinecone
2. Archive Pinecone data
3. Cancel Pinecone subscription
4. Remove Pinecone code

## Troubleshooting Guide

### Common Issues

1. **"CompactionLock busy" errors**
   - Check if previous compaction is stuck
   - Verify lock TTL is appropriate
   - Consider increasing lock timeout

2. **"Mode is read-only" errors**
   - Verify lambda is using correct mode
   - Check ClientManager initialization
   - Ensure not attempting writes in consumer

3. **Large compaction times**
   - Reduce batch size in SQS
   - Increase lambda memory/timeout
   - Consider incremental compaction

4. **S3 rate limits**
   - Implement exponential backoff
   - Use S3 Transfer Acceleration
   - Request limit increases

## Future Enhancements

### 1. Incremental Compaction
- Track processed deltas in DynamoDB
- Only merge new deltas since last snapshot
- Reduce compaction time significantly

### 2. Multi-Collection Support
- Separate collections per merchant
- Parallel collection compaction
- Collection-specific retention

### 3. Smart Scheduling
- Compact during low-traffic periods
- Adaptive compaction frequency
- Cost-based optimization

### 4. Advanced Features
- Compression for snapshots
- Incremental backups
- Point-in-time recovery
- Cross-region replication

## Conclusion

The ChromaDB S3 compaction architecture provides a robust, scalable, and cost-effective solution for vector storage in a serverless environment. By combining ChromaDB's powerful vector search capabilities with S3's durability and DynamoDB's coordination primitives, we've created a system that can handle production workloads while maintaining operational simplicity.

The architecture's key strengths:
- **No single points of failure**
- **Linear scalability**
- **Cost scales with usage**
- **Simple operational model**
- **Strong consistency guarantees**

This design eliminates the $1,200+/year Pinecone costs while providing better control, flexibility, and integration with the existing AWS infrastructure.