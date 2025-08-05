# ChromaDB Compaction Infrastructure

This directory contains the infrastructure components for the ChromaDB S3 compaction pipeline with DynamoDB mutex protection.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Producer Lambdas                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                │
│  │  Embedding  │  │  Embedding  │  │  Embedding  │                │
│  │  Producer 1 │  │  Producer 2 │  │  Producer N │                │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                │
│         │                 │                 │                        │
│         └─────────────────┴─────────────────┘                       │
│                           │                                          │
│                           ▼                                          │
│                    ┌─────────────┐                                  │
│                    │  S3 Deltas  │                                  │
│                    │ /delta/UUID │                                  │
│                    └──────┬──────┘                                  │
│                           │                                          │
│                           ▼                                          │
│                    ┌─────────────┐                                  │
│                    │  SQS Queue  │                                  │
│                    └──────┬──────┘                                  │
│                           │                                          │
│                           ▼                                          │
│         ┌─────────────────────────────────────┐                    │
│         │        Compactor Lambda              │                    │
│         │  1. Acquire DynamoDB Lock            │                    │
│         │  2. Download Current Snapshot        │                    │
│         │  3. Merge All Deltas                 │                    │
│         │  4. Upload New Snapshot              │                    │
│         │  5. Release Lock                     │                    │
│         └─────────────────────────────────────┘                    │
│                           │                                          │
│                           ▼                                          │
│                    ┌─────────────┐                                  │
│                    │ S3 Snapshot │                                  │
│                    │/snapshot/   │                                  │
│                    └─────────────┘                                  │
└─────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. S3 Buckets (`s3_buckets.py`)
- **chromadb-vectors**: Stores snapshots and delta files
- Lifecycle policies for delta cleanup
- Versioning enabled for snapshots

### 2. SQS Queue (`sqs_queue.py`)
- **chromadb-delta-queue**: Notifies compactor of new deltas
- Dead letter queue for failed messages
- Message retention: 4 days

### 3. DynamoDB Table (`dynamodb_table.py`)
- Uses existing portfolio table
- CompactionLock entity for distributed locking
- TTL on lock entries to prevent deadlocks

### 4. Lambda Functions

#### Producer Lambda (`producer_lambda.py`)
- Processes embedding requests
- Creates delta files in S3
- Sends SQS notification

#### Compactor Lambda (`compactor_lambda.py`)
- Triggered by EventBridge (scheduled)
- Processes SQS messages
- Performs atomic compaction with lock

#### Query Lambda (`query_lambda.py`)
- Read-only access to snapshots
- Serves vector similarity queries

### 5. EventBridge Rules (`eventbridge_rules.py`)
- Scheduled trigger for compactor (every 15 minutes)
- Can be adjusted based on delta accumulation rate

### 6. IAM Roles (`iam_roles.py`)
- Producer role: Write to S3 deltas, send SQS
- Compactor role: Full S3 access, DynamoDB lock operations
- Query role: Read-only S3 snapshot access

## Configuration

### Environment Variables

```bash
# S3 Configuration
CHROMADB_BUCKET=chromadb-vectors-{stack}-{account_id}
DELTA_PREFIX=delta/
SNAPSHOT_PREFIX=snapshot/

# SQS Configuration
DELTA_QUEUE_URL=https://sqs.{region}.amazonaws.com/{account_id}/chromadb-delta-queue

# DynamoDB Configuration
DYNAMODB_TABLE=portfolio-{stack}
LOCK_ID=chroma-main-snapshot

# Compaction Settings
LOCK_TIMEOUT_MINUTES=15
COMPACTION_BATCH_SIZE=100
```

### Pulumi Configuration

```bash
# Set the stack
pulumi stack select dev

# Configure AWS region
pulumi config set aws:region us-east-1

# Configure ChromaDB settings
pulumi config set chromadb:enableCompaction true
pulumi config set chromadb:compactionSchedule "rate(15 minutes)"
```

## Deployment

```bash
# Deploy all ChromaDB infrastructure
cd infra/chromadb_compaction
pulumi up

# Deploy individual components
pulumi up -t chromadb-vectors-bucket
pulumi up -t chromadb-compactor-lambda
```

## Operations

### Monitoring

Key metrics to monitor:
- **Lock contention**: Failed lock acquisitions
- **Delta accumulation**: Queue depth and age
- **Compaction duration**: Time to process deltas
- **Snapshot size**: Growth over time

### Manual Compaction

```bash
# Trigger compaction manually
aws lambda invoke \
  --function-name chromadb-compactor-{stack} \
  --invocation-type Event \
  response.json
```

### Lock Management

```bash
# View current lock status
aws dynamodb get-item \
  --table-name portfolio-{stack} \
  --key '{"PK": {"S": "LOCK#chroma-main-snapshot"}, "SK": {"S": "LOCK#chroma-main-snapshot"}}'

# Force release lock (emergency only)
aws dynamodb delete-item \
  --table-name portfolio-{stack} \
  --key '{"PK": {"S": "LOCK#chroma-main-snapshot"}, "SK": {"S": "LOCK#chroma-main-snapshot"}}'
```

## Performance Tuning

### Delta Size Optimization
- Target: 1-5MB per delta file
- Batch embeddings to reach optimal size
- Compress with gzip for network efficiency

### Compaction Frequency
- Default: Every 15 minutes
- Adjust based on:
  - Delta production rate
  - Query latency requirements
  - Cost considerations

### Lock Timeout
- Default: 15 minutes
- Increase if compaction takes longer
- Add heartbeat updates for long operations

## Cost Optimization

### Estimated Monthly Costs
- S3 Storage: ~$5-10 (depends on snapshot size)
- Lambda Compute: ~$10-20 (depends on volume)
- SQS: ~$1-2
- DynamoDB: Minimal (single lock record)
- **Total**: ~$16-32/month

### Cost Reduction Strategies
1. Adjust compaction frequency based on usage
2. Enable S3 Intelligent-Tiering for old snapshots
3. Use Lambda ARM architecture for better price/performance
4. Implement delta batching to reduce S3 operations

## Troubleshooting

### Common Issues

1. **Lock Timeout**
   - Symptom: Compactor fails with lock acquisition error
   - Solution: Increase timeout or optimize compaction

2. **Delta Accumulation**
   - Symptom: SQS queue depth growing
   - Solution: Increase compaction frequency or parallelize

3. **Snapshot Corruption**
   - Symptom: Query lambdas fail to load snapshot
   - Solution: Revert to previous snapshot via S3 versioning

### Debug Commands

```bash
# Check SQS queue depth
aws sqs get-queue-attributes \
  --queue-url $DELTA_QUEUE_URL \
  --attribute-names ApproximateNumberOfMessages

# List recent deltas
aws s3 ls s3://$CHROMADB_BUCKET/delta/ --recursive

# View compactor logs
aws logs tail /aws/lambda/chromadb-compactor-{stack} --follow
```

## Security Considerations

1. **Encryption**: Enable S3 encryption at rest
2. **Access Control**: Least privilege IAM roles
3. **Network**: VPC endpoints for S3/DynamoDB access
4. **Audit**: CloudTrail logging for all operations

## Future Enhancements

1. **Multi-Region Replication**: Cross-region snapshot backups
2. **Incremental Compaction**: Process only new deltas
3. **Parallel Collections**: Separate locks per collection
4. **Auto-Scaling**: Dynamic compaction frequency based on load