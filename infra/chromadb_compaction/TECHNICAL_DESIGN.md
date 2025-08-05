# ChromaDB Compaction Technical Design

## Problem Statement

ChromaDB is designed as a single-writer database. In a serverless environment with multiple Lambda functions producing embeddings concurrently, we need a way to handle multiple writers without conflicts.

## Solution: Delta-Compaction Pattern

We implement a delta-compaction pattern similar to LSM (Log-Structured Merge) trees:

1. **Multiple producers** write small delta files to S3
2. **Single compactor** periodically merges deltas into the main snapshot
3. **DynamoDB mutex** ensures only one compactor runs at a time
4. **Readers** always see a consistent snapshot

## Detailed Component Design

### 1. Producer Lambda Pattern

```python
def produce_embeddings(event, context):
    # 1. Extract data from event
    words = event['words']
    receipt_id = event['receipt_id']
    
    # 2. Generate embeddings
    embeddings = generate_embeddings(words)
    
    # 3. Create temporary ChromaDB instance
    temp_dir = f"/tmp/delta_{uuid.uuid4()}"
    chroma = ChromaDBClient(
        persist_directory=temp_dir,
        mode="delta"
    )
    
    # 4. Write to local ChromaDB
    collection = chroma.get_collection("words")
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas
    )
    
    # 5. Upload delta to S3
    delta_key = f"delta/{uuid.uuid4()}/"
    chroma.persist_and_upload_delta(BUCKET, delta_key)
    
    # 6. Notify compactor via SQS
    sqs.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=json.dumps({
            'delta_key': delta_key,
            'item_count': len(ids),
            'timestamp': datetime.utcnow().isoformat()
        })
    )
    
    # 7. Cleanup temp directory
    shutil.rmtree(temp_dir)
    
    return {'delta_key': delta_key, 'count': len(ids)}
```

### 2. Compactor Lambda Pattern

```python
def compact_deltas(event, context):
    compactor = ChromaCompactor(
        s3_bucket=BUCKET,
        dynamo_client=dynamo_client,
        sqs_queue_url=QUEUE_URL
    )
    
    # 1. Acquire distributed lock
    lock = CompactionLock(
        lock_id="chroma-main-snapshot",
        owner=context.request_id,
        expires=datetime.now(timezone.utc) + timedelta(minutes=15)
    )
    
    try:
        dynamo_client.acquire_compaction_lock(lock)
    except EntityAlreadyExistsError:
        # Another compactor is running
        return {'status': 'skipped', 'reason': 'lock_held'}
    
    try:
        # 2. Process SQS messages to get delta keys
        messages = sqs.receive_messages(MaxNumberOfMessages=10)
        delta_keys = [
            json.loads(m.body)['delta_key'] 
            for m in messages
        ]
        
        if not delta_keys:
            return {'status': 'skipped', 'reason': 'no_deltas'}
        
        # 3. Download current snapshot
        local_snapshot = "/tmp/snapshot"
        s3_download_directory(BUCKET, "snapshot/latest/", local_snapshot)
        
        # 4. Load snapshot ChromaDB
        chroma = ChromaDBClient(
            persist_directory=local_snapshot,
            mode="snapshot"
        )
        collection = chroma.get_collection("words")
        
        # 5. Download and merge each delta
        merged_count = 0
        for delta_key in delta_keys:
            delta_dir = f"/tmp/delta_{uuid.uuid4()}"
            s3_download_directory(BUCKET, delta_key, delta_dir)
            
            # Load delta ChromaDB
            delta_chroma = ChromaDBClient(
                persist_directory=delta_dir,
                mode="read"
            )
            delta_collection = delta_chroma.get_collection("words")
            
            # Extract and merge data
            data = delta_collection.get()
            if data['ids']:
                collection.upsert(
                    ids=data['ids'],
                    embeddings=data['embeddings'],
                    documents=data['documents'],
                    metadatas=data['metadatas']
                )
                merged_count += len(data['ids'])
            
            # Cleanup delta
            shutil.rmtree(delta_dir)
            s3_delete_directory(BUCKET, delta_key)
        
        # 6. Persist merged snapshot
        chroma.persist()
        
        # 7. Upload new snapshot with timestamp
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        new_snapshot_key = f"snapshot/{timestamp}/"
        s3_upload_directory(local_snapshot, BUCKET, new_snapshot_key)
        
        # 8. Update latest pointer atomically
        s3.put_object(
            Bucket=BUCKET,
            Key="snapshot/latest/pointer.txt",
            Body=new_snapshot_key.encode('utf-8')
        )
        
        # 9. Delete processed messages from SQS
        for message in messages:
            message.delete()
        
        # 10. Update heartbeat periodically
        if merged_count > 1000:
            dynamo_client.update_lock_heartbeat(lock.lock_id, lock.owner)
        
        return {
            'status': 'success',
            'merged_count': merged_count,
            'snapshot_key': new_snapshot_key
        }
        
    finally:
        # Always release lock
        try:
            dynamo_client.release_compaction_lock(lock.lock_id, lock.owner)
        except:
            pass  # Lock may have expired
```

### 3. Query Lambda Pattern

```python
def query_vectors(event, context):
    # 1. Download latest snapshot pointer
    response = s3.get_object(
        Bucket=BUCKET,
        Key="snapshot/latest/pointer.txt"
    )
    latest_snapshot = response['Body'].read().decode('utf-8')
    
    # 2. Check if cached locally (warm start optimization)
    local_snapshot = "/tmp/snapshot_cache"
    cache_marker = f"{local_snapshot}/.version"
    
    if os.path.exists(cache_marker):
        with open(cache_marker, 'r') as f:
            cached_version = f.read().strip()
        if cached_version == latest_snapshot:
            # Use cached snapshot
            chroma = ChromaDBClient(
                persist_directory=local_snapshot,
                mode="read"
            )
        else:
            # Download new version
            shutil.rmtree(local_snapshot)
            s3_download_directory(BUCKET, latest_snapshot, local_snapshot)
            with open(cache_marker, 'w') as f:
                f.write(latest_snapshot)
            chroma = ChromaDBClient(
                persist_directory=local_snapshot,
                mode="read"
            )
    else:
        # First run - download snapshot
        s3_download_directory(BUCKET, latest_snapshot, local_snapshot)
        with open(cache_marker, 'w') as f:
            f.write(latest_snapshot)
        chroma = ChromaDBClient(
            persist_directory=local_snapshot,
            mode="read"
        )
    
    # 3. Perform query
    collection = chroma.get_collection("words")
    results = collection.query(
        query_texts=[event['query_text']],
        n_results=event.get('n_results', 10),
        where=event.get('metadata_filter')
    )
    
    return {
        'results': results,
        'snapshot_version': latest_snapshot
    }
```

## Lock Management Design

### DynamoDB Schema

```python
# CompactionLock entity
{
    "PK": "LOCK#chroma-main-snapshot",
    "SK": "LOCK#chroma-main-snapshot",
    "TYPE": "CompactionLock",
    "lock_id": "chroma-main-snapshot",
    "owner": "lambda-request-id-123",
    "expires": "2025-08-03T12:45:00Z",  # TTL
    "heartbeat": "2025-08-03T12:30:00Z",
    "created": "2025-08-03T12:30:00Z"
}
```

### Lock Operations

```python
# Acquire with condition
condition_expression = """
    attribute_not_exists(PK) OR expires < :now
"""

# Heartbeat update
update_expression = """
    SET heartbeat = :now, expires = :new_expires
"""
condition_expression = """
    owner = :owner AND expires > :now
"""

# Release with validation
condition_expression = """
    owner = :owner
"""
```

## S3 Storage Design

### Directory Structure

```
chromadb-vectors-{stack}-{account}/
├── snapshot/
│   ├── 20250803_120000/          # Timestamped snapshots
│   │   ├── chroma.sqlite3        # Main database
│   │   ├── index/                # Vector indices
│   │   │   ├── id_to_uuid.pkl
│   │   │   ├── index_metadata.pkl
│   │   │   └── vectors/
│   │   └── metadata.json         # Snapshot metadata
│   ├── 20250803_130000/
│   └── latest/
│       └── pointer.txt           # Points to active snapshot
└── delta/
    ├── a1b2c3d4-e5f6-7890-abcd-ef1234567890/
    │   ├── chroma.sqlite3
    │   └── index/
    └── b2c3d4e5-f6a7-8901-bcde-f23456789012/
```

### Lifecycle Policies

```python
# Delta cleanup after 7 days
{
    "Rules": [{
        "Id": "DeleteOldDeltas",
        "Status": "Enabled",
        "Prefix": "delta/",
        "Expiration": {
            "Days": 7
        }
    }]
}

# Snapshot retention (keep last 10)
{
    "Rules": [{
        "Id": "RetainRecentSnapshots",
        "Status": "Enabled",
        "Prefix": "snapshot/",
        "NoncurrentVersionExpiration": {
            "NoncurrentDays": 30
        }
    }]
}
```

## Performance Optimization

### 1. Warm Start Optimization

Query lambdas cache the snapshot locally:
- Check if cached version matches latest
- Skip download if already cached
- ~10x faster queries on warm starts

### 2. Batch Processing

Compactor processes multiple deltas per run:
- Reduces lock contention
- Amortizes snapshot download cost
- Better S3 operation efficiency

### 3. Compression

All data is compressed:
- Deltas: gzip compression
- Snapshots: ChromaDB native compression
- Network: S3 transfer acceleration

## Monitoring and Alerting

### Key Metrics

```python
# 1. Lock contention rate
custom_metric("LockAcquisitionFailed", 1, unit="Count")

# 2. Delta accumulation
custom_metric("DeltaQueueDepth", queue_depth, unit="Count")

# 3. Compaction duration
custom_metric("CompactionDuration", duration_seconds, unit="Seconds")

# 4. Snapshot size
custom_metric("SnapshotSize", size_bytes, unit="Bytes")
```

### CloudWatch Alarms

```python
# High lock contention
alarm_name = "ChromaDB-HighLockContention"
metric_name = "LockAcquisitionFailed"
threshold = 5  # failures in 5 minutes

# Delta accumulation
alarm_name = "ChromaDB-DeltaAccumulation"
metric_name = "DeltaQueueDepth"
threshold = 1000  # deltas pending

# Compaction failure
alarm_name = "ChromaDB-CompactionFailure"
metric_name = "CompactionErrors"
threshold = 1  # any error
```

## Failure Recovery

### 1. Producer Failure
- Delta partially written: S3 multipart handles atomicity
- SQS send fails: Delta orphaned but cleaned up after 7 days

### 2. Compactor Failure
- Lock expires after timeout
- Deltas remain in queue for retry
- Snapshot unchanged (atomic update)

### 3. Query Failure
- Fallback to previous snapshot
- Cache corruption detected and cleared
- Retry with fresh download

## Security Model

### 1. IAM Roles

```python
# Producer role
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:PutObjectAcl"
            ],
            "Resource": "arn:aws:s3:::chromadb-vectors-*/delta/*"
        },
        {
            "Effect": "Allow",
            "Action": "sqs:SendMessage",
            "Resource": "arn:aws:sqs:*:*:chromadb-delta-queue"
        }
    ]
}

# Compactor role (includes DynamoDB lock)
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "s3:*",
            "Resource": [
                "arn:aws:s3:::chromadb-vectors-*/*",
                "arn:aws:s3:::chromadb-vectors-*"
            ]
        },
        {
            "Effect": "Allow",
            "Action": [
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
                "dynamodb:GetItem"
            ],
            "Resource": "arn:aws:dynamodb:*:*:table/portfolio-*",
            "Condition": {
                "ForAllValues:StringEquals": {
                    "dynamodb:LeadingKeys": ["LOCK#chroma-main-snapshot"]
                }
            }
        }
    ]
}
```

### 2. Encryption

- S3: SSE-S3 encryption at rest
- Transit: TLS for all API calls
- ChromaDB: Transparent encryption via S3

## Cost Analysis

### Storage Costs (Monthly)
- Snapshots: 10GB × $0.023 = $0.23
- Deltas: 1GB × $0.023 = $0.02
- Total: ~$0.25

### Compute Costs (Monthly)
- Producer Lambdas: 10K invocations × 1s × 512MB = ~$2
- Compactor Lambda: 2K invocations × 10s × 2GB = ~$8
- Query Lambdas: 50K invocations × 0.5s × 1GB = ~$10
- Total: ~$20

### Data Transfer
- S3 to Lambda (same region): Free
- Between AZs: Minimal

### Total Monthly Cost: ~$20-25

## Migration Strategy

### Phase 1: Parallel Running
1. Deploy infrastructure
2. Producers write to both Pinecone and ChromaDB
3. Queries still use Pinecone

### Phase 2: Read Migration
1. Switch read traffic to ChromaDB
2. Monitor query performance
3. Keep Pinecone as fallback

### Phase 3: Write Migration
1. Stop writing to Pinecone
2. Run full data migration script
3. Validate data integrity

### Phase 4: Cleanup
1. Remove Pinecone dependencies
2. Cancel Pinecone subscription
3. Archive migration code