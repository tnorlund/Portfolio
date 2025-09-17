# DynamoDB Stream Processor for ChromaDB Metadata Sync

## Overview

The DynamoDB Stream Processor enables real-time synchronization of metadata changes between DynamoDB entities and ChromaDB vector databases. It processes stream events for receipt metadata and word labels, triggering ChromaDB updates through the existing compaction infrastructure.

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐    ┌──────────────────────┐
│   DynamoDB      │    │  Stream Lambda   │    │   SQS Queue     │    │  Compaction Lambda   │
│                 │───▶│  (Lightweight    │───▶│  (Existing)     │───▶│  (Enhanced)          │
│ Receipt Entities│    │   Trigger)       │    │                 │    │  + CompactionLock    │
└─────────────────┘    └──────────────────┘    └─────────────────┘    └──────────────────────┘
```

## Supported Entities

### 1. `RECEIPT_METADATA` - Merchant Information

- **Key Pattern**: `PK: IMAGE#{uuid}`, `SK: RECEIPT#{id:05d}#METADATA`
- **ChromaDB Impact**: Updates merchant info across ALL embeddings for that receipt
- **Monitored Fields**:
  - `canonical_merchant_name`
  - `merchant_name`
  - `merchant_category`
  - `address`
  - `phone_number`
  - `place_id`

### 2. `RECEIPT_WORD_LABEL` - Word Classifications

- **Key Pattern**: `PK: IMAGE#{uuid}`, `SK: RECEIPT#{id:05d}#LINE#{id:05d}#WORD#{id:05d}#LABEL#{label}`
- **ChromaDB Impact**: Updates label metadata for SPECIFIC word embeddings
- **Monitored Fields**:
  - `label`
  - `reasoning`
  - `validation_status`
  - `label_proposed_by`
  - `label_consolidated_from`

## Supported Operations

### MODIFY Events

- **Metadata Changes**: Merchant name corrections, address updates
- **Label Changes**: Label updates, validation status changes, reasoning updates

### REMOVE Events

- **Metadata Removal**: When receipt metadata is deleted → update all related embeddings
- **Label Removal**: When labels are deleted → remove label metadata from word embeddings

## Components

### 1. Stream Processor Lambda (`stream_processor.py`)

- **Runtime**: Python 3.12
- **Memory**: 256 MB
- **Timeout**: 5 minutes
- **Trigger**: DynamoDB Stream events
- **Output**: SQS messages to existing compaction queue

**Key Functions**:

- `parse_receipt_entity_key()` - Extracts entity type and IDs from DynamoDB keys
- `get_chromadb_relevant_changes()` - Identifies fields that affect ChromaDB metadata
- `send_messages_to_sqs()` - Batches and sends messages to compaction queue (FIFO)

### 2. Infrastructure Components (`stream_processor_infra.py`)

- **StreamProcessorLambda** - Lambda function with IAM roles and policies
- **DynamoDBStreamEventSourceMapping** - Connects stream to Lambda
- **Factory function** - `create_stream_processor()` for easy setup

### 3. Unit Tests (`test_stream_processor.py`)

- 26 comprehensive test cases
- 100% test coverage
- Mocked AWS services for isolated testing

## Message Format

Messages sent to the SQS queue have this structure:

```json
{
  "source": "dynamodb_stream",
  "entity_type": "RECEIPT_METADATA|RECEIPT_WORD_LABEL",
  "entity_data": {
    "image_id": "550e8400-e29b-41d4-a716-446655440000",
    "receipt_id": 1,
    "line_id": 2, // Only for RECEIPT_WORD_LABEL
    "word_id": 3, // Only for RECEIPT_WORD_LABEL
    "label": "TOTAL" // Only for RECEIPT_WORD_LABEL
  },
  "changes": {
    "canonical_merchant_name": {
      "old": "Old Merchant",
      "new": "New Merchant"
    }
  },
  "event_name": "MODIFY|REMOVE",
  "timestamp": "2025-01-15T10:30:00.000Z",
  "stream_record_id": "dynamodb-event-id",
  "aws_region": "us-east-1",
  "record_snapshot": {
    "...": "Full entity at this event, produced via dict()"
  }
}
```

### FIFO Ordering and Idempotency

- SQS queues are FIFO with content-based deduplication.
- MessageGroupId derives from entity identifiers for ordering:
  - Lines: `IMAGE#{image_id}#RECEIPT#{receipt_id}`
  - Words: `IMAGE#{image_id}#RECEIPT#{receipt_id}#LINE#{line_id}#WORD#{word_id}`
- MessageDeduplicationId uses the stream record id when available.

## Integration Example

```python
import pulumi_aws as aws
from chromadb_compaction.sqs_queues import create_chromadb_queues
from chromadb_compaction.stream_processor_infra import create_stream_processor

# Create existing SQS infrastructure
chromadb_queues = create_chromadb_queues("chromadb")

# Get existing DynamoDB table
table = aws.dynamodb.get_table(name="receipt-entities-prod")

# Enable DynamoDB Streams on existing table (if not already enabled)
# This would typically be done during table creation or via AWS Console
stream_specification = aws.dynamodb.TableStreamSpecificationArgs(
    stream_enabled=True,
    stream_view_type="NEW_AND_OLD_IMAGES"  # Required for change detection
)

# Create stream processor
lambda_processor, event_mapping = create_stream_processor(
    name="chromadb-stream-processor",
    chromadb_queues=chromadb_queues,
    dynamodb_table_arn=table.arn,
    dynamodb_stream_arn=table.stream_arn
)

# Export important values
pulumi.export("stream_processor_arn", lambda_processor.function_arn)
pulumi.export("event_mapping_uuid", event_mapping.mapping_uuid)
```

## Deployment Steps

### 1. Prerequisites

- Existing DynamoDB table with streams enabled
- Existing ChromaDB compaction SQS queue
- Existing compaction Lambda function

### 2. Enable DynamoDB Streams

```bash
# Via AWS CLI (replace with your table name)
aws dynamodb update-table \
  --table-name receipt-entities-prod \
  --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES
```

### 3. Deploy Infrastructure

```bash
cd infra/
pulumi up
```

### 4. Verify Deployment

- Check Lambda function is created
- Verify event source mapping is active
- Monitor CloudWatch logs for stream events

## Monitoring

### CloudWatch Metrics

- **Lambda Invocations**: Monitor processing frequency
- **Lambda Errors**: Track processing failures
- **Lambda Duration**: Ensure under timeout limits
- **SQS Messages Sent**: Verify downstream messaging

### CloudWatch Logs

- **Log Group**: `/aws/lambda/chromadb-stream-processor-{stack}`
- **Retention**: 14 days
- **Log Level**: INFO (configurable via `LOG_LEVEL` env var)

### Key Log Messages

```
INFO: Processing 5 DynamoDB stream records
INFO: Sent 3 messages to compaction queue
ERROR: Error processing stream record abc123: Invalid format
```

## Error Handling

### Stream Processing Errors

- Individual record failures don't stop batch processing
- Failed records are logged with details
- Lambda continues processing remaining records

### SQS Send Failures

- Failed messages are logged with error details
- Partial batch successes are counted correctly
- Dead letter queue captures persistently failed messages

### DynamoDB Stream Resilience

- **Retry Logic**: 3 automatic retries for failed batches
- **Batch Splitting**: Splits batches on function errors
- **Age Limit**: Discards records older than 1 hour
- **Starting Position**: LATEST (only new changes)

## Performance Characteristics

### Throughput

- **Batch Size**: Up to 100 records per invocation
- **Batching Window**: Max 5 seconds
- **Parallelization**: Single shard (can be increased)
- **Processing Time**: ~50ms per batch (lightweight processing)

### Costs (Estimated)

- **Lambda Invocations**: $0.02/month (100K invocations)
- **Lambda Compute**: $0.03/month (256MB, 50ms avg)
- **CloudWatch Logs**: $0.01/month (14 day retention)
- **Total**: ~$0.06/month additional cost

## Testing

### Unit Tests

```bash
cd infra/chromadb_compaction/
python -m pytest test_stream_processor.py -v
```

### Integration Testing

1. Deploy to test environment
2. Make changes to receipt metadata or labels
3. Verify SQS messages are created
4. Check CloudWatch logs for processing

### Load Testing

- Stream processor handles ~1000 records/second
- SQS batching reduces downstream load
- Auto-scaling through Lambda concurrency

## Troubleshooting

### Common Issues

#### No messages in SQS queue

- Check DynamoDB streams are enabled
- Verify event source mapping is active
- Ensure changes are to monitored entity types
- Check Lambda function logs for errors

#### Lambda timeout errors

- Reduce batch size in event source mapping
- Check for network issues with SQS
- Monitor Lambda duration metrics

#### Permission errors

- Verify Lambda role has DynamoDB streams permissions
- Check SQS queue access policies
- Ensure event source mapping permissions

### Debug Commands

```bash
# Check event source mapping status
aws lambda get-event-source-mapping --uuid {mapping-uuid}

# View recent Lambda logs
aws logs filter-log-events \
  --log-group-name /aws/lambda/chromadb-stream-processor-prod \
  --start-time $(date -d '1 hour ago' +%s)000

# Check SQS queue depth
aws sqs get-queue-attributes \
  --queue-url {queue-url} \
  --attribute-names ApproximateNumberOfMessages
```

## Future Enhancements

### Performance Optimizations

- Increase parallelization factor for high-volume streams
- Implement message deduplication for exactly-once processing
- Add custom retry logic with exponential backoff

### Monitoring Improvements

- Custom CloudWatch metrics for business logic
- SNS notifications for critical errors
- Detailed tracing with AWS X-Ray

### Feature Extensions

- Support for additional entity types (RECEIPT, RECEIPT_LINE, RECEIPT_WORD)
- Configurable field monitoring via environment variables
- Batch processing optimizations for bulk updates
