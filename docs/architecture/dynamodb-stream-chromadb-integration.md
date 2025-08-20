# DynamoDB Stream Integration with ChromaDB Compaction

## Overview

This document outlines the implementation plan for integrating DynamoDB Streams with the existing ChromaDB compaction process to provide real-time metadata synchronization between DynamoDB entities and ChromaDB vector database.

## Architecture Summary

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐    ┌──────────────────────┐
│   DynamoDB      │    │  Stream Lambda   │    │   SQS Queue     │    │  Compaction Lambda   │
│                 │───▶│  (Lightweight    │───▶│  (Existing)     │───▶│  (Enhanced)          │
│ Receipt Entities│    │   Trigger)       │    │                 │    │  + CompactionLock    │
└─────────────────┘    └──────────────────┘    └─────────────────┘    └──────────────────────┘
        │                        │                        │                        │
        ▼                        ▼                        ▼                        ▼
   Stream Events          Message Creation          Queue Storage            Delta Processing
   - RECEIPT_LINE         - Parse entity data       - Batch messages         - Acquire mutex lock
   - RECEIPT_WORD         - Identify changes        - Dead letter queue      - Process updates
   - RECEIPT_METADATA     - Create SQS messages     - Retry logic           - Update ChromaDB
                                                                             - Release lock
```

## Current State Analysis

### Existing ChromaDB Metadata Structure

#### Line Embeddings
- **Collection**: `receipt_lines`
- **Database**: `lines`
- **ID Format**: `IMAGE#uuid#RECEIPT#00001#LINE#00001`
- **DynamoDB Keys**:
  - **PK**: `IMAGE#{image_id}`
  - **SK**: `RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}`
  - **GSI1**: `EMBEDDING_STATUS#{status}` (tracks embedding progress)

#### Word Embeddings  
- **Collection**: `receipt_words`
- **Database**: `words`
- **ID Format**: `IMAGE#uuid#RECEIPT#00001#LINE#00001#WORD#00001`
- **DynamoDB Keys**:
  - **PK**: `IMAGE#{image_id}`
  - **SK**: `RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}`

#### Metadata Sources
Current ChromaDB metadata is built from:
- **Receipt Entity**: Basic receipt info (dimensions, timestamps, S3 locations)
- **ReceiptLine Entity**: Text, bounding box, confidence, geometric data  
- **ReceiptWord Entity**: Text, bounding box, confidence, geometric data
- **ReceiptMetadata Entity**: Merchant info (canonical_merchant_name, address, place_id)
- **Computed Context**: Previous/next line text, spatial relationships

### Existing Infrastructure

#### CompactionLock Entity
Located at `receipt_dynamo/receipt_dynamo/entities/compaction_lock.py`:
- Single-row mutex using DynamoDB conditional puts
- TTL-based automatic lock expiration
- Key format: `LOCK#{lock_id}` / `LOCK`
- Owner identification via UUID
- Heartbeat updates for long-running operations

#### SQS Queue Infrastructure  
Located at `infra/chromadb_compaction/sqs_queues.py`:
- Main queue: `chromadb-delta-queue-{stack}`
- Dead letter queue with 3 retry attempts
- 15-minute visibility timeout (matches compaction timeout)
- Long polling for efficiency
- Message retention: 4 days

## Implementation Plan

### Phase 1: Stream Processing Lambda

#### 1.1 Stream Lambda Handler

Create `infra/dynamodb_stream_processor/handler.py`:

```python
import json
import os
import boto3
from datetime import datetime
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    DynamoDB Stream handler - identifies ChromaDB-relevant changes 
    and queues compaction work.
    
    This function is lightweight and focuses only on:
    1. Parsing stream records
    2. Identifying relevant entity changes  
    3. Creating SQS messages for the compaction Lambda
    
    Heavy processing is delegated to the compaction Lambda.
    """
    logger.info(f"Processing {len(event['Records'])} DynamoDB stream records")
    
    messages_to_send = []
    processed_records = 0
    
    for record in event['Records']:
        try:
            # Parse entity information from DynamoDB keys
            entity_data = parse_receipt_entity_key(
                record['dynamodb']['Keys']['PK']['S'],
                record['dynamodb']['Keys']['SK']['S']
            )
            
            if not entity_data:
                continue  # Not a receipt entity we care about
                
            # Only process INSERT, MODIFY events (not REMOVE for now)
            if record['eventName'] in ['INSERT', 'MODIFY']:
                old_image = record['dynamodb'].get('OldImage', {})
                new_image = record['dynamodb'].get('NewImage', {})
                
                # Check if any ChromaDB-relevant fields changed
                changes = get_chromadb_relevant_changes(entity_data, old_image, new_image)
                
                if changes:
                    # Create SQS message for the compaction Lambda
                    message = {
                        "source": "dynamodb_stream",
                        "entity_type": entity_data['entity_type'],
                        "entity_data": entity_data,
                        "changes": changes,
                        "event_name": record['eventName'],
                        "timestamp": datetime.utcnow().isoformat(),
                        "stream_record_id": record.get('eventID', 'unknown'),
                        "aws_region": record.get('awsRegion', 'unknown')
                    }
                    messages_to_send.append(message)
                    processed_records += 1
                    
        except Exception as e:
            logger.error(f"Error processing stream record {record.get('eventID', 'unknown')}: {e}")
            # Continue processing other records
    
    # Send all messages to existing SQS queue in batches
    if messages_to_send:
        sent_count = send_messages_to_sqs(messages_to_send)
        logger.info(f"Sent {sent_count} messages to compaction queue")
    
    return {
        "statusCode": 200,
        "processed_records": processed_records,
        "queued_messages": len(messages_to_send)
    }

def parse_receipt_entity_key(pk: str, sk: str) -> Optional[Dict[str, Any]]:
    """
    Parse DynamoDB keys to identify entity type and extract IDs.
    
    Args:
        pk: Primary key (e.g., "IMAGE#uuid")
        sk: Sort key (e.g., "RECEIPT#00001#LINE#00001")
    
    Returns:
        Dictionary with entity info or None if not relevant
    """
    if not pk.startswith('IMAGE#'):
        return None
        
    image_id = pk.split('#')[1] if '#' in pk else None
    if not image_id:
        return None
    
    if '#METADATA' in sk:
        # RECEIPT#00001#METADATA
        parts = sk.split('#')
        if len(parts) >= 2:
            receipt_id = int(parts[1])
            return {
                'entity_type': 'RECEIPT_METADATA',
                'image_id': image_id,
                'receipt_id': receipt_id
            }
    elif sk.count('#') == 3 and '#LINE#' in sk and '#WORD#' not in sk:
        # RECEIPT#00001#LINE#00001
        parts = sk.split('#')
        if len(parts) == 4:
            return {
                'entity_type': 'RECEIPT_LINE',
                'image_id': image_id,
                'receipt_id': int(parts[1]),
                'line_id': int(parts[3])
            }
    elif sk.count('#') == 5 and '#WORD#' in sk:
        # RECEIPT#00001#LINE#00001#WORD#00001
        parts = sk.split('#')
        if len(parts) == 6:
            return {
                'entity_type': 'RECEIPT_WORD',
                'image_id': image_id,
                'receipt_id': int(parts[1]),
                'line_id': int(parts[3]),
                'word_id': int(parts[5])
            }
    
    return None  # Not a receipt entity we care about

def get_chromadb_relevant_changes(entity_data: Dict[str, Any], old_image: Dict[str, Any], new_image: Dict[str, Any]) -> Dict[str, Any]:
    """
    Identify changes to fields that affect ChromaDB metadata.
    
    Args:
        entity_data: Parsed entity information
        old_image: Previous DynamoDB item state
        new_image: Current DynamoDB item state
        
    Returns:
        Dictionary of changed fields with old/new values
    """
    # Define ChromaDB-relevant fields for each entity type
    relevant_fields = {
        'RECEIPT_METADATA': [
            'canonical_merchant_name', 'merchant_name', 'merchant_category', 
            'address', 'phone_number', 'place_id'
        ],
        'RECEIPT_LINE': [
            'text', 'confidence', 'bounding_box', 'embedding_status', 'is_noise'
        ],  
        'RECEIPT_WORD': [
            'text', 'confidence', 'bounding_box', 'embedding_status', 'is_noise'
        ]
    }
    
    fields_to_check = relevant_fields.get(entity_data['entity_type'], [])
    changes = {}
    
    for field in fields_to_check:
        old_val = old_image.get(field, {})
        new_val = new_image.get(field, {})
        
        # Handle different DynamoDB types
        old_value = extract_dynamodb_value(old_val)
        new_value = extract_dynamodb_value(new_val)
        
        if old_value != new_value:
            changes[field] = {'old': old_value, 'new': new_value}
    
    return changes

def extract_dynamodb_value(dynamo_value: Dict[str, Any]) -> Any:
    """
    Extract the actual value from DynamoDB attribute format.
    
    Args:
        dynamo_value: DynamoDB attribute (e.g., {'S': 'string'}, {'N': '123'})
        
    Returns:
        The actual value
    """
    if not dynamo_value:
        return None
        
    if 'S' in dynamo_value:
        return dynamo_value['S']
    elif 'N' in dynamo_value:
        return float(dynamo_value['N'])
    elif 'BOOL' in dynamo_value:
        return dynamo_value['BOOL']
    elif 'M' in dynamo_value:
        # Map type - convert to dict
        return {k: extract_dynamodb_value(v) for k, v in dynamo_value['M'].items()}
    elif 'L' in dynamo_value:
        # List type - convert to list
        return [extract_dynamodb_value(item) for item in dynamo_value['L']]
    elif 'NULL' in dynamo_value:
        return None
    else:
        logger.warning(f"Unknown DynamoDB type: {dynamo_value}")
        return dynamo_value

def send_messages_to_sqs(messages: List[Dict[str, Any]]) -> int:
    """
    Send messages to the existing compaction SQS queue.
    
    Args:
        messages: List of message dictionaries to send
        
    Returns:
        Number of messages successfully sent
    """
    sqs = boto3.client('sqs')
    queue_url = os.environ['COMPACTION_QUEUE_URL']
    sent_count = 0
    
    # Send in batches of 10 (SQS batch limit)
    for i in range(0, len(messages), 10):
        batch = messages[i:i+10]
        
        entries = []
        for j, message in enumerate(batch):
            entries.append({
                'Id': str(j),
                'MessageBody': json.dumps(message, default=str),
                'MessageAttributes': {
                    'source': {
                        'StringValue': 'dynamodb_stream', 
                        'DataType': 'String'
                    },
                    'entity_type': {
                        'StringValue': message['entity_type'], 
                        'DataType': 'String'
                    },
                    'timestamp': {
                        'StringValue': message['timestamp'],
                        'DataType': 'String'  
                    }
                }
            })
        
        try:
            response = sqs.send_message_batch(
                QueueUrl=queue_url,
                Entries=entries
            )
            
            # Check for partial failures
            if 'Failed' in response and response['Failed']:
                logger.error(f"Failed to send {len(response['Failed'])} messages: {response['Failed']}")
                
            successful = len(response.get('Successful', []))
            sent_count += successful
            logger.info(f"Successfully sent {successful} messages in batch {i//10 + 1}")
            
        except Exception as e:
            logger.error(f"Failed to send message batch {i//10 + 1}: {e}")
            # Continue with next batch
    
    return sent_count
```

#### 1.2 Stream Lambda Requirements

Create `infra/dynamodb_stream_processor/requirements.txt`:
```
boto3>=1.26.0
```

#### 1.3 Stream Lambda Configuration

Create `infra/dynamodb_stream_processor/config.py`:
```python
"""
Configuration for DynamoDB Stream Processor Lambda.
"""

import os

# SQS Configuration
COMPACTION_QUEUE_URL = os.environ.get('COMPACTION_QUEUE_URL')
if not COMPACTION_QUEUE_URL:
    raise ValueError("COMPACTION_QUEUE_URL environment variable is required")

# DynamoDB Configuration  
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE')
if not DYNAMODB_TABLE:
    raise ValueError("DYNAMODB_TABLE environment variable is required")

# Batch processing limits
MAX_SQS_BATCH_SIZE = 10  # SQS limit
MAX_MESSAGES_PER_INVOCATION = 100  # Reasonable limit to prevent timeouts

# Logging configuration
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

# Feature flags
ENABLE_METADATA_UPDATES = os.environ.get('ENABLE_METADATA_UPDATES', 'true').lower() == 'true'
ENABLE_LINE_UPDATES = os.environ.get('ENABLE_LINE_UPDATES', 'true').lower() == 'true'  
ENABLE_WORD_UPDATES = os.environ.get('ENABLE_WORD_UPDATES', 'true').lower() == 'true'
```

### Phase 2: Enhanced Compaction Lambda

#### 2.1 Enhanced Compaction Handler

Create `infra/chromadb_compaction/enhanced_compaction_handler.py`:

```python
import json
import os
import boto3
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from botocore.exceptions import ClientError
import logging

# Import existing compaction lock entity
from receipt_dynamo.entities.compaction_lock import CompactionLock

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def enhanced_compaction_handler(event, context):
    """
    Enhanced compactor using existing CompactionLock and SQS processing.
    
    Handles both:
    1. Traditional embedding deltas (from step functions)
    2. Stream-triggered metadata updates (from DynamoDB streams)
    
    Uses existing CompactionLock for mutual exclusion.
    """
    # Generate unique owner ID for this Lambda invocation
    owner_id = str(uuid.uuid4())
    lock_id = os.environ.get('COMPACTION_LOCK_ID', 'chroma-main-snapshot')
    
    logger.info(f"Starting compaction with owner {owner_id}")
    
    try:
        # Try to acquire the existing CompactionLock
        lock = CompactionLock(
            lock_id=lock_id,
            owner=owner_id,
            expires=datetime.utcnow() + timedelta(minutes=15),  # 15-minute timeout
            heartbeat=datetime.utcnow()
        )
        
        # Conditional put - only succeed if lock is available
        acquire_success = try_acquire_lock(lock)
        
        if not acquire_success:
            logger.info("Compaction lock held by another process, skipping")
            return {
                "statusCode": 200,
                "status": "skipped", 
                "reason": "lock_held"
            }
            
        logger.info(f"Acquired compaction lock with owner {owner_id}")
        
        # Process messages from SQS (mix of embedding deltas and stream updates)
        processed_count = process_compaction_queue(owner_id, lock_id)
        
        logger.info(f"Compaction completed successfully, processed {processed_count} items")
        
        return {
            "statusCode": 200,
            "status": "completed", 
            "processed_deltas": processed_count,
            "lock_owner": owner_id
        }
        
    except Exception as e:
        logger.error(f"Compaction failed: {e}")
        return {
            "statusCode": 500,
            "status": "failed", 
            "error": str(e),
            "lock_owner": owner_id
        }
        
    finally:
        # Always release the lock
        try:
            release_compaction_lock(lock_id, owner_id)
            logger.info("Released compaction lock")
        except Exception as e:
            logger.warning(f"Failed to release lock: {e}")

def try_acquire_lock(lock: CompactionLock) -> bool:
    """
    Try to acquire compaction lock using existing DynamoDB conditional put.
    
    Args:
        lock: CompactionLock entity to acquire
        
    Returns:
        True if lock acquired successfully, False if held by another process
    """
    dynamodb = boto3.client('dynamodb')
    table_name = os.environ['DYNAMODB_TABLE']
    
    try:
        # Conditional put - only if lock doesn't exist or is expired
        now_iso = datetime.utcnow().isoformat()
        
        dynamodb.put_item(
            TableName=table_name,
            Item=lock.to_item(),
            ConditionExpression="attribute_not_exists(PK) OR expires < :now",
            ExpressionAttributeValues={
                ':now': {'S': now_iso}
            }
        )
        return True
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False  # Lock is held
        raise  # Other error

def release_compaction_lock(lock_id: str, owner_id: str):
    """
    Release the compaction lock.
    
    Args:
        lock_id: The lock identifier
        owner_id: The owner UUID (for safety check)
    """
    dynamodb = boto3.client('dynamodb')
    table_name = os.environ['DYNAMODB_TABLE']
    
    try:
        # Only delete if we own the lock (safety measure)
        dynamodb.delete_item(
            TableName=table_name,
            Key={
                "PK": {"S": f"LOCK#{lock_id}"},
                "SK": {"S": "LOCK"}
            },
            ConditionExpression="owner = :owner",
            ExpressionAttributeValues={
                ':owner': {'S': owner_id}
            }
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            logger.warning("Lock was not owned by this process, may have expired")
        else:
            raise

def update_lock_heartbeat(lock_id: str, owner_id: str):
    """
    Update the heartbeat timestamp for a held lock.
    
    Args:
        lock_id: The lock identifier
        owner_id: The owner UUID
    """
    dynamodb = boto3.client('dynamodb')
    table_name = os.environ['DYNAMODB_TABLE']
    
    try:
        now_iso = datetime.utcnow().isoformat()
        
        dynamodb.update_item(
            TableName=table_name,
            Key={
                "PK": {"S": f"LOCK#{lock_id}"},
                "SK": {"S": "LOCK"}
            },
            UpdateExpression="SET heartbeat = :heartbeat",
            ConditionExpression="owner = :owner",
            ExpressionAttributeValues={
                ':heartbeat': {'S': now_iso},
                ':owner': {'S': owner_id}
            }
        )
    except ClientError as e:
        logger.warning(f"Failed to update heartbeat: {e}")

def process_compaction_queue(owner_id: str, lock_id: str) -> int:
    """
    Process SQS messages (both embedding deltas and stream updates).
    
    Args:
        owner_id: The lock owner UUID
        lock_id: The lock identifier
        
    Returns:
        Number of items processed
    """
    sqs = boto3.client('sqs')
    queue_url = os.environ['COMPACTION_QUEUE_URL']
    
    processed = 0
    consecutive_empty_polls = 0
    max_empty_polls = 3  # Stop after 3 consecutive empty polls
    
    while consecutive_empty_polls < max_empty_polls:
        # Update heartbeat while processing
        update_lock_heartbeat(lock_id, owner_id)
        
        # Poll SQS for messages
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=5,  # Short wait to prevent timeout
            MessageAttributeNames=['All']
        )
        
        messages = response.get('Messages', [])
        if not messages:
            consecutive_empty_polls += 1
            logger.info(f"No messages found, consecutive empty polls: {consecutive_empty_polls}")
            continue
        
        consecutive_empty_polls = 0  # Reset counter
        logger.info(f"Processing {len(messages)} messages from SQS")
        
        for message in messages:
            try:
                # Determine message type from attributes
                attributes = message.get('MessageAttributes', {})
                source = attributes.get('source', {}).get('StringValue', 'unknown')
                
                if source == 'dynamodb_stream':
                    # Process stream-triggered metadata update
                    process_stream_metadata_update(json.loads(message['Body']))
                else:
                    # Process traditional embedding delta (existing logic)
                    process_embedding_delta(message['Body'])
                
                # Delete processed message
                sqs.delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=message['ReceiptHandle']
                )
                processed += 1
                
                logger.info(f"Successfully processed message from {source}")
                
            except Exception as e:
                logger.error(f"Failed to process message: {e}")
                # Message will be retried or go to DLQ based on redrive policy
    
    logger.info(f"Finished processing queue, total processed: {processed}")
    return processed

def process_stream_metadata_update(message_data: Dict[str, Any]):
    """
    Process metadata updates from DynamoDB streams.
    
    Args:
        message_data: Message body from stream Lambda
    """
    entity_type = message_data['entity_type']
    entity_data = message_data['entity_data']
    changes = message_data['changes']
    
    logger.info(f"Processing {entity_type} metadata update with {len(changes)} changes")
    
    if entity_type == 'RECEIPT_METADATA':
        # Update merchant info for all ChromaDB records for this receipt
        update_merchant_metadata_in_chromadb(entity_data, changes)
        
    elif entity_type == 'RECEIPT_LINE':
        # Update specific line record in ChromaDB
        update_line_metadata_in_chromadb(entity_data, changes)
        
    elif entity_type == 'RECEIPT_WORD':
        # Update specific word record in ChromaDB
        update_word_metadata_in_chromadb(entity_data, changes)
    
    else:
        logger.warning(f"Unknown entity type: {entity_type}")

def update_merchant_metadata_in_chromadb(entity_data: Dict[str, Any], changes: Dict[str, Any]):
    """
    Update merchant metadata for all ChromaDB records for a specific receipt.
    
    This is called when ReceiptMetadata entity changes affect ChromaDB metadata.
    
    Args:
        entity_data: Entity identification data
        changes: Dictionary of changed fields
    """
    logger.info(f"Updating merchant metadata for receipt {entity_data['receipt_id']} in image {entity_data['image_id']}")
    
    # TODO: Implement ChromaDB client interaction
    # This would:
    # 1. Query ChromaDB for all records matching image_id + receipt_id
    # 2. Update metadata for each record
    # 3. Create delta file with updated metadata
    # 4. Trigger compaction
    
    # Placeholder implementation
    logger.info(f"Would update merchant metadata: {changes}")

def update_line_metadata_in_chromadb(entity_data: Dict[str, Any], changes: Dict[str, Any]):
    """
    Update metadata for a specific line in ChromaDB.
    
    Args:
        entity_data: Entity identification data  
        changes: Dictionary of changed fields
    """
    line_id = f"IMAGE#{entity_data['image_id']}#RECEIPT#{entity_data['receipt_id']:05d}#LINE#{entity_data['line_id']:05d}"
    
    logger.info(f"Updating line metadata for {line_id}")
    
    # TODO: Implement ChromaDB client interaction
    # This would:
    # 1. Query ChromaDB for the specific line record
    # 2. Update its metadata with changed fields
    # 3. Create delta file
    # 4. Trigger compaction
    
    # Placeholder implementation
    logger.info(f"Would update line metadata: {changes}")

def update_word_metadata_in_chromadb(entity_data: Dict[str, Any], changes: Dict[str, Any]):
    """
    Update metadata for a specific word in ChromaDB.
    
    Args:
        entity_data: Entity identification data
        changes: Dictionary of changed fields  
    """
    word_id = f"IMAGE#{entity_data['image_id']}#RECEIPT#{entity_data['receipt_id']:05d}#LINE#{entity_data['line_id']:05d}#WORD#{entity_data['word_id']:05d}"
    
    logger.info(f"Updating word metadata for {word_id}")
    
    # TODO: Implement ChromaDB client interaction
    # This would:
    # 1. Query ChromaDB for the specific word record  
    # 2. Update its metadata with changed fields
    # 3. Create delta file
    # 4. Trigger compaction
    
    # Placeholder implementation
    logger.info(f"Would update word metadata: {changes}")

def process_embedding_delta(message_body: str):
    """
    Process traditional embedding delta (existing logic).
    
    This maintains compatibility with existing embedding step functions.
    
    Args:
        message_body: SQS message body
    """
    logger.info("Processing traditional embedding delta")
    
    # TODO: Implement existing embedding delta logic
    # This should call existing compaction functions
    
    # Placeholder implementation
    logger.info(f"Would process embedding delta: {message_body[:100]}...")
```

### Phase 3: Infrastructure Integration

#### 3.1 Enhanced Infrastructure Component

Update `infra/embedding_step_functions/infrastructure.py` to include stream processing:

```python
"""Enhanced infrastructure with DynamoDB Stream integration."""

# Add to existing infrastructure.py

class EmbeddingInfrastructure(ComponentResource):
    def __init__(
        self,
        name: str,
        base_images=None,
        portfolio_table_arn: Optional[Output[str]] = None,  # Add this parameter
        opts: Optional[ResourceOptions] = None,
    ):
        # ... existing initialization code ...
        
        # Add stream processor Lambda
        self.stream_processor = self._create_stream_processor(name)
        
        # Add stream mapping if table ARN provided
        if portfolio_table_arn:
            self.stream_mapping = self._create_stream_mapping(name, portfolio_table_arn)
        
    def _create_stream_processor(self, name: str):
        """Create the DynamoDB Stream processor Lambda."""
        
        # Create IAM role for stream processor
        stream_role = aws.iam.Role(
            f"{name}-stream-processor-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Action": "sts:AssumeRole",
                        "Effect": "Allow",
                        "Principal": {
                            "Service": "lambda.amazonaws.com"
                        },
                    }
                ],
            }),
            opts=ResourceOptions(parent=self),
        )
        
        # Attach basic Lambda execution policy
        aws.iam.RolePolicyAttachment(
            f"{name}-stream-processor-basic",
            role=stream_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )
        
        # Attach DynamoDB stream permissions
        stream_policy = aws.iam.RolePolicy(
            f"{name}-stream-processor-policy",
            role=stream_role.id,
            policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:DescribeStream",
                            "dynamodb:GetRecords",
                            "dynamodb:GetShardIterator",
                            "dynamodb:ListStreams"
                        ],
                        "Resource": "*"  # Will be restricted in production
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "sqs:SendMessage",
                            "sqs:SendMessageBatch"
                        ],
                        "Resource": self.chromadb_queues.delta_queue_arn
                    }
                ],
            }),
            opts=ResourceOptions(parent=self),
        )
        
        # Create the Lambda function
        stream_processor = aws.lambda_.Function(
            f"{name}-stream-processor",
            role=stream_role.arn,
            code=aws.lambda_.AssetArchive({
                ".": pulumi.FileArchive("./dynamodb_stream_processor")
            }),
            runtime="python3.12",
            handler="handler.lambda_handler",
            environment={
                "COMPACTION_QUEUE_URL": self.chromadb_queues.delta_queue_url,
                "DYNAMODB_TABLE": pulumi.get_stack(),  # Or get from config
                "LOG_LEVEL": "INFO",
                "ENABLE_METADATA_UPDATES": "true",
                "ENABLE_LINE_UPDATES": "true", 
                "ENABLE_WORD_UPDATES": "true"
            },
            timeout=300,  # 5 minutes max
            memory_size=256,  # Lightweight processing
            description="Processes DynamoDB streams for ChromaDB metadata updates",
            tags={
                "Project": "ChromaDB",
                "Component": "StreamProcessor",
                "Environment": pulumi.get_stack(),
            },
            opts=ResourceOptions(parent=self, depends_on=[stream_policy]),
        )
        
        return stream_processor
    
    def _create_stream_mapping(self, name: str, table_stream_arn: Output[str]):
        """Create event source mapping for DynamoDB stream."""
        
        return aws.lambda_.EventSourceMapping(
            f"{name}-stream-mapping",
            event_source_arn=table_stream_arn,
            function_name=self.stream_processor.arn,
            starting_position="TRIM_HORIZON",  # Process all existing records
            batch_size=10,  # Process up to 10 records per invocation
            maximum_batching_window_in_seconds=5,  # Wait max 5 seconds for batch
            parallelization_factor=1,  # Process one shard at a time initially
            maximum_record_age_in_seconds=3600,  # Skip records older than 1 hour
            bisect_batch_on_function_error=True,  # Split batches on error
            maximum_retry_attempts=3,  # Retry failed batches 3 times
            opts=ResourceOptions(parent=self),
        )
```

#### 3.2 DynamoDB Table Stream Configuration

Update your DynamoDB table configuration to enable streams:

```python
# In your existing dynamo_db.py file

portfolio_table = aws.dynamodb.Table(
    "portfolio-table",
    # ... existing configuration ...
    stream_specification=aws.dynamodb.TableStreamSpecificationArgs(
        stream_enabled=True,
        stream_view_type="NEW_AND_OLD_IMAGES"  # Need both for change detection
    ),
    # ... rest of existing configuration ...
)

# Pass the stream ARN to the embedding infrastructure
embedding_infrastructure = EmbeddingInfrastructure(
    "embedding-infra",
    portfolio_table_arn=portfolio_table.stream_arn,  # Add this
    # ... other parameters ...
)
```

### Phase 4: Testing and Validation

#### 4.1 Unit Tests

Create `tests/test_stream_processor.py`:

```python
"""Unit tests for DynamoDB Stream processor."""

import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

# Import the handler (assuming it's made importable)
from infra.dynamodb_stream_processor.handler import (
    lambda_handler,
    parse_receipt_entity_key,
    get_chromadb_relevant_changes,
    extract_dynamodb_value
)

class TestStreamProcessor:
    
    def test_parse_receipt_line_key(self):
        """Test parsing ReceiptLine keys."""
        pk = "IMAGE#12345678-1234-1234-1234-123456789012"
        sk = "RECEIPT#00001#LINE#00042"
        
        result = parse_receipt_entity_key(pk, sk)
        
        assert result is not None
        assert result['entity_type'] == 'RECEIPT_LINE'
        assert result['image_id'] == '12345678-1234-1234-1234-123456789012'
        assert result['receipt_id'] == 1
        assert result['line_id'] == 42
    
    def test_parse_receipt_word_key(self):
        """Test parsing ReceiptWord keys."""
        pk = "IMAGE#12345678-1234-1234-1234-123456789012"
        sk = "RECEIPT#00001#LINE#00042#WORD#00007"
        
        result = parse_receipt_entity_key(pk, sk)
        
        assert result is not None
        assert result['entity_type'] == 'RECEIPT_WORD'
        assert result['word_id'] == 7
    
    def test_parse_receipt_metadata_key(self):
        """Test parsing ReceiptMetadata keys.""" 
        pk = "IMAGE#12345678-1234-1234-1234-123456789012"
        sk = "RECEIPT#00001#METADATA"
        
        result = parse_receipt_entity_key(pk, sk)
        
        assert result is not None
        assert result['entity_type'] == 'RECEIPT_METADATA'
        assert 'line_id' not in result
        assert 'word_id' not in result
    
    def test_ignore_irrelevant_keys(self):
        """Test that non-receipt entities are ignored."""
        pk = "USER#someone@example.com"
        sk = "PROFILE"
        
        result = parse_receipt_entity_key(pk, sk)
        assert result is None
    
    def test_extract_dynamodb_values(self):
        """Test DynamoDB value extraction."""
        assert extract_dynamodb_value({'S': 'hello'}) == 'hello'
        assert extract_dynamodb_value({'N': '123.45'}) == 123.45
        assert extract_dynamodb_value({'BOOL': True}) == True
        assert extract_dynamodb_value({'NULL': True}) is None
        assert extract_dynamodb_value({}) is None
    
    def test_detect_text_changes(self):
        """Test detection of text changes.""" 
        entity_data = {'entity_type': 'RECEIPT_LINE'}
        old_image = {'text': {'S': 'old text'}}
        new_image = {'text': {'S': 'new text'}}
        
        changes = get_chromadb_relevant_changes(entity_data, old_image, new_image)
        
        assert 'text' in changes
        assert changes['text']['old'] == 'old text'
        assert changes['text']['new'] == 'new text'
    
    def test_ignore_irrelevant_changes(self):
        """Test that irrelevant field changes are ignored."""
        entity_data = {'entity_type': 'RECEIPT_LINE'}
        old_image = {'some_other_field': {'S': 'old value'}}
        new_image = {'some_other_field': {'S': 'new value'}}
        
        changes = get_chromadb_relevant_changes(entity_data, old_image, new_image)
        
        assert changes == {}
    
    @patch('boto3.client')
    def test_lambda_handler_success(self, mock_boto3):
        """Test successful Lambda handler execution."""
        # Mock SQS client
        mock_sqs = MagicMock()
        mock_boto3.return_value = mock_sqs
        mock_sqs.send_message_batch.return_value = {'Successful': [{'Id': '0'}]}
        
        # Create test event
        event = {
            'Records': [{
                'eventName': 'MODIFY',
                'eventID': 'test-event-1',
                'dynamodb': {
                    'Keys': {
                        'PK': {'S': 'IMAGE#12345678-1234-1234-1234-123456789012'},
                        'SK': {'S': 'RECEIPT#00001#LINE#00001'}
                    },
                    'OldImage': {'text': {'S': 'old text'}},
                    'NewImage': {'text': {'S': 'new text'}}
                }
            }]
        }
        
        with patch.dict('os.environ', {'COMPACTION_QUEUE_URL': 'test-queue-url'}):
            result = lambda_handler(event, {})
        
        assert result['statusCode'] == 200
        assert result['processed_records'] == 1
        assert result['queued_messages'] == 1
        
        # Verify SQS call
        mock_sqs.send_message_batch.assert_called_once()
        call_args = mock_sqs.send_message_batch.call_args[1]
        assert call_args['QueueUrl'] == 'test-queue-url'
        assert len(call_args['Entries']) == 1
```

#### 4.2 Integration Tests

Create `tests/test_compaction_integration.py`:

```python
"""Integration tests for enhanced compaction."""

import json
import boto3
import pytest
from moto import mock_dynamodb, mock_sqs
from datetime import datetime, timedelta

from receipt_dynamo.entities.compaction_lock import CompactionLock
from infra.chromadb_compaction.enhanced_compaction_handler import (
    enhanced_compaction_handler,
    try_acquire_lock,
    release_compaction_lock
)

@mock_dynamodb
@mock_sqs
class TestCompactionIntegration:
    
    @pytest.fixture
    def dynamodb_table(self):
        """Create test DynamoDB table."""
        dynamodb = boto3.client('dynamodb', region_name='us-east-1')
        
        table_name = 'test-portfolio-table'
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {'AttributeName': 'PK', 'KeyType': 'HASH'},
                {'AttributeName': 'SK', 'KeyType': 'RANGE'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'PK', 'AttributeType': 'S'},
                {'AttributeName': 'SK', 'AttributeType': 'S'}
            ],
            BillingMode='PAY_PER_REQUEST'
        )
        
        return table_name
    
    @pytest.fixture
    def sqs_queue(self):
        """Create test SQS queue."""
        sqs = boto3.client('sqs', region_name='us-east-1')
        
        response = sqs.create_queue(QueueName='test-compaction-queue')
        return response['QueueUrl']
    
    def test_lock_acquire_and_release(self, dynamodb_table):
        """Test CompactionLock acquire and release."""
        import os
        os.environ['DYNAMODB_TABLE'] = dynamodb_table
        
        lock = CompactionLock(
            lock_id='test-lock',
            owner='test-owner-123',
            expires=datetime.utcnow() + timedelta(minutes=15)
        )
        
        # Should successfully acquire lock
        assert try_acquire_lock(lock) == True
        
        # Should fail to acquire same lock
        duplicate_lock = CompactionLock(
            lock_id='test-lock',
            owner='different-owner-456', 
            expires=datetime.utcnow() + timedelta(minutes=15)
        )
        assert try_acquire_lock(duplicate_lock) == False
        
        # Should successfully release lock
        release_compaction_lock('test-lock', 'test-owner-123')
        
        # Should now be able to acquire again
        assert try_acquire_lock(duplicate_lock) == True
    
    def test_stream_message_processing(self, dynamodb_table, sqs_queue):
        """Test processing of stream messages in SQS."""
        import os
        os.environ['DYNAMODB_TABLE'] = dynamodb_table
        os.environ['COMPACTION_QUEUE_URL'] = sqs_queue
        os.environ['COMPACTION_LOCK_ID'] = 'test-compaction-lock'
        
        # Put a stream message in the queue
        sqs = boto3.client('sqs', region_name='us-east-1')
        
        message = {
            "source": "dynamodb_stream",
            "entity_type": "RECEIPT_LINE",
            "entity_data": {
                "image_id": "12345678-1234-1234-1234-123456789012",
                "receipt_id": 1,
                "line_id": 42
            },
            "changes": {
                "text": {"old": "old text", "new": "new text"}
            },
            "event_name": "MODIFY",
            "timestamp": datetime.utcnow().isoformat()
        }
        
        sqs.send_message(
            QueueUrl=sqs_queue,
            MessageBody=json.dumps(message),
            MessageAttributes={
                'source': {'StringValue': 'dynamodb_stream', 'DataType': 'String'},
                'entity_type': {'StringValue': 'RECEIPT_LINE', 'DataType': 'String'}
            }
        )
        
        # Run compaction handler
        result = enhanced_compaction_handler({}, {})
        
        assert result['statusCode'] == 200
        assert result['status'] == 'completed'
        assert result['processed_deltas'] == 1
        
        # Verify queue is empty
        response = sqs.receive_message(QueueUrl=sqs_queue, WaitTimeSeconds=1)
        assert 'Messages' not in response
```

#### 4.3 Load Testing

Create `tests/load_test_stream_processor.py`:

```python
"""Load testing for stream processor."""

import asyncio
import boto3
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

def generate_test_stream_record(entity_type='RECEIPT_LINE', record_id=None):
    """Generate a test DynamoDB stream record."""
    record_id = record_id or int(time.time() * 1000)
    
    base_record = {
        'eventName': 'MODIFY',
        'eventID': f'test-event-{record_id}',
        'awsRegion': 'us-east-1',
        'dynamodb': {
            'Keys': {
                'PK': {'S': f'IMAGE#{record_id:012d}-1234-1234-1234-123456789012'},
                'SK': {'S': f'RECEIPT#00001#LINE#{record_id:05d}'}
            },
            'OldImage': {'text': {'S': f'old text {record_id}'}},
            'NewImage': {'text': {'S': f'new text {record_id}'}}
        }
    }
    
    if entity_type == 'RECEIPT_WORD':
        base_record['dynamodb']['Keys']['SK']['S'] += f'#WORD#00001'
    elif entity_type == 'RECEIPT_METADATA':
        base_record['dynamodb']['Keys']['SK']['S'] = f'RECEIPT#00001#METADATA'
        base_record['dynamodb']['OldImage'] = {'merchant_name': {'S': f'Old Merchant {record_id}'}}
        base_record['dynamodb']['NewImage'] = {'merchant_name': {'S': f'New Merchant {record_id}'}}
    
    return base_record

def simulate_stream_batch(batch_size=10, entity_types=['RECEIPT_LINE']):
    """Simulate a batch of stream records."""
    records = []
    
    for i in range(batch_size):
        entity_type = entity_types[i % len(entity_types)]
        record = generate_test_stream_record(entity_type, i)
        records.append(record)
    
    return {'Records': records}

async def run_load_test(
    concurrent_lambdas=5,
    records_per_lambda=100, 
    batch_size=10
):
    """
    Run load test against stream processor.
    
    Args:
        concurrent_lambdas: Number of concurrent Lambda invocations
        records_per_lambda: Number of records per Lambda
        batch_size: Size of each batch within a Lambda
    """
    # Import Lambda handler (this would need to be importable)
    from infra.dynamodb_stream_processor.handler import lambda_handler
    
    async def run_single_lambda(lambda_id):
        """Run a single Lambda invocation."""
        start_time = time.time()
        
        batches = []
        for batch_num in range(0, records_per_lambda, batch_size):
            batch = simulate_stream_batch(batch_size)
            batches.append(batch)
        
        total_processed = 0
        for batch in batches:
            try:
                result = lambda_handler(batch, {})
                total_processed += result.get('processed_records', 0)
            except Exception as e:
                print(f"Lambda {lambda_id} batch failed: {e}")
        
        end_time = time.time()
        duration = end_time - start_time
        
        return {
            'lambda_id': lambda_id,
            'records_processed': total_processed,
            'duration_seconds': duration,
            'records_per_second': total_processed / duration if duration > 0 else 0
        }
    
    # Run concurrent Lambdas
    tasks = [run_single_lambda(i) for i in range(concurrent_lambdas)]
    results = await asyncio.gather(*tasks)
    
    # Calculate aggregate statistics
    total_records = sum(r['records_processed'] for r in results)
    total_duration = max(r['duration_seconds'] for r in results)
    avg_records_per_second = sum(r['records_per_second'] for r in results) / len(results)
    
    print(f"\nLoad Test Results:")
    print(f"Concurrent Lambdas: {concurrent_lambdas}")
    print(f"Total Records Processed: {total_records}")
    print(f"Total Duration: {total_duration:.2f}s")
    print(f"Average Records/Second per Lambda: {avg_records_per_second:.2f}")
    print(f"Overall Throughput: {total_records / total_duration:.2f} records/second")
    
    return results

if __name__ == "__main__":
    # Run load test
    asyncio.run(run_load_test(
        concurrent_lambdas=3,
        records_per_lambda=50,
        batch_size=10
    ))
```

### Phase 5: Monitoring and Observability

#### 5.1 CloudWatch Metrics

Create `infra/monitoring/stream_processor_metrics.py`:

```python
"""CloudWatch metrics for stream processor."""

import boto3
import json
from datetime import datetime

def create_custom_metrics():
    """Create custom CloudWatch metrics for stream processing."""
    
    cloudwatch = boto3.client('cloudwatch')
    
    # Define custom metrics
    metrics = [
        {
            'MetricName': 'StreamRecordsProcessed',
            'Namespace': 'ChromaDB/StreamProcessor',
            'Description': 'Number of DynamoDB stream records processed'
        },
        {
            'MetricName': 'StreamProcessingLatency',
            'Namespace': 'ChromaDB/StreamProcessor', 
            'Description': 'Time taken to process stream records (ms)'
        },
        {
            'MetricName': 'SQSMessagesQueued',
            'Namespace': 'ChromaDB/StreamProcessor',
            'Description': 'Number of messages sent to compaction queue'
        },
        {
            'MetricName': 'StreamProcessingErrors',
            'Namespace': 'ChromaDB/StreamProcessor',
            'Description': 'Number of errors processing stream records'
        },
        {
            'MetricName': 'CompactionLockContentions', 
            'Namespace': 'ChromaDB/Compaction',
            'Description': 'Number of failed lock acquisitions'
        },
        {
            'MetricName': 'CompactionDuration',
            'Namespace': 'ChromaDB/Compaction',
            'Description': 'Time taken for compaction operations (ms)'
        },
        {
            'MetricName': 'MetadataUpdatesApplied',
            'Namespace': 'ChromaDB/Compaction',
            'Description': 'Number of metadata updates applied to ChromaDB'
        }
    ]
    
    return metrics

def publish_stream_processing_metrics(
    records_processed: int,
    processing_latency_ms: float,
    messages_queued: int,
    errors: int = 0
):
    """
    Publish metrics for stream processing.
    
    Args:
        records_processed: Number of stream records processed
        processing_latency_ms: Processing latency in milliseconds
        messages_queued: Number of SQS messages sent
        errors: Number of errors encountered
    """
    cloudwatch = boto3.client('cloudwatch')
    
    metrics = [
        {
            'MetricName': 'StreamRecordsProcessed',
            'Value': records_processed,
            'Unit': 'Count',
            'Timestamp': datetime.utcnow()
        },
        {
            'MetricName': 'StreamProcessingLatency',
            'Value': processing_latency_ms,
            'Unit': 'Milliseconds',
            'Timestamp': datetime.utcnow()
        },
        {
            'MetricName': 'SQSMessagesQueued',
            'Value': messages_queued,
            'Unit': 'Count',
            'Timestamp': datetime.utcnow()
        }
    ]
    
    if errors > 0:
        metrics.append({
            'MetricName': 'StreamProcessingErrors',
            'Value': errors,
            'Unit': 'Count', 
            'Timestamp': datetime.utcnow()
        })
    
    try:
        cloudwatch.put_metric_data(
            Namespace='ChromaDB/StreamProcessor',
            MetricData=metrics
        )
    except Exception as e:
        print(f"Failed to publish metrics: {e}")

def publish_compaction_metrics(
    compaction_duration_ms: float,
    metadata_updates: int,
    lock_contentions: int = 0
):
    """
    Publish metrics for compaction operations.
    
    Args:
        compaction_duration_ms: Duration of compaction operation
        metadata_updates: Number of metadata updates applied
        lock_contentions: Number of failed lock acquisitions
    """
    cloudwatch = boto3.client('cloudwatch')
    
    metrics = [
        {
            'MetricName': 'CompactionDuration',
            'Value': compaction_duration_ms,
            'Unit': 'Milliseconds',
            'Timestamp': datetime.utcnow()
        },
        {
            'MetricName': 'MetadataUpdatesApplied',
            'Value': metadata_updates,
            'Unit': 'Count',
            'Timestamp': datetime.utcnow()
        }
    ]
    
    if lock_contentions > 0:
        metrics.append({
            'MetricName': 'CompactionLockContentions',
            'Value': lock_contentions,
            'Unit': 'Count',
            'Timestamp': datetime.utcnow()
        })
    
    try:
        cloudwatch.put_metric_data(
            Namespace='ChromaDB/Compaction',
            MetricData=metrics
        )
    except Exception as e:
        print(f"Failed to publish compaction metrics: {e}")
```

#### 5.2 CloudWatch Alarms

Create `infra/monitoring/cloudwatch_alarms.py`:

```python
"""CloudWatch alarms for stream processing."""

import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions

class StreamProcessingAlarms(ComponentResource):
    """CloudWatch alarms for monitoring stream processing."""
    
    def __init__(self, name: str, opts: ResourceOptions = None):
        super().__init__("chromadb:monitoring:StreamProcessingAlarms", name, None, opts)
        
        # Alarm for high error rate
        self.high_error_rate_alarm = aws.cloudwatch.MetricAlarm(
            f"{name}-high-error-rate",
            alarm_name="ChromaDB-StreamProcessor-HighErrorRate",
            alarm_description="High error rate in DynamoDB stream processing",
            metric_name="StreamProcessingErrors",
            namespace="ChromaDB/StreamProcessor",
            statistic="Sum",
            period=300,  # 5 minutes
            evaluation_periods=2,
            threshold=10,  # More than 10 errors in 10 minutes
            comparison_operator="GreaterThanThreshold",
            alarm_actions=[
                # Add SNS topic ARN here for notifications
            ],
            tags={
                "Component": "StreamProcessor",
                "Severity": "High"
            },
            opts=ResourceOptions(parent=self)
        )
        
        # Alarm for high processing latency
        self.high_latency_alarm = aws.cloudwatch.MetricAlarm(
            f"{name}-high-latency",
            alarm_name="ChromaDB-StreamProcessor-HighLatency",
            alarm_description="High latency in stream processing",
            metric_name="StreamProcessingLatency",
            namespace="ChromaDB/StreamProcessor",
            statistic="Average",
            period=300,
            evaluation_periods=3,
            threshold=30000,  # 30 seconds
            comparison_operator="GreaterThanThreshold",
            alarm_actions=[
                # Add SNS topic ARN here
            ],
            tags={
                "Component": "StreamProcessor",
                "Severity": "Medium"
            },
            opts=ResourceOptions(parent=self)
        )
        
        # Alarm for compaction lock contention
        self.lock_contention_alarm = aws.cloudwatch.MetricAlarm(
            f"{name}-lock-contention",
            alarm_name="ChromaDB-Compaction-LockContention",
            alarm_description="High compaction lock contention",
            metric_name="CompactionLockContentions",
            namespace="ChromaDB/Compaction",
            statistic="Sum",
            period=300,
            evaluation_periods=2,
            threshold=5,  # More than 5 contentions in 10 minutes
            comparison_operator="GreaterThanThreshold",
            alarm_actions=[
                # Add SNS topic ARN here
            ],
            tags={
                "Component": "Compaction",
                "Severity": "Medium"
            },
            opts=ResourceOptions(parent=self)
        )
        
        # Alarm for SQS queue depth
        self.high_queue_depth_alarm = aws.cloudwatch.MetricAlarm(
            f"{name}-high-queue-depth",
            alarm_name="ChromaDB-CompactionQueue-HighDepth",
            alarm_description="High depth in compaction queue",
            metric_name="ApproximateNumberOfMessages",
            namespace="AWS/SQS",
            statistic="Average",
            period=300,
            evaluation_periods=2,
            threshold=100,  # More than 100 messages
            comparison_operator="GreaterThanThreshold",
            dimensions={
                "QueueName": "chromadb-delta-queue"  # Update with actual queue name
            },
            alarm_actions=[
                # Add SNS topic ARN here
            ],
            tags={
                "Component": "CompactionQueue",
                "Severity": "Medium"
            },
            opts=ResourceOptions(parent=self)
        )
```

#### 5.3 CloudWatch Dashboard

Create `infra/monitoring/cloudwatch_dashboard.py`:

```python
"""CloudWatch dashboard for ChromaDB stream processing."""

import json
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions, Output

class StreamProcessingDashboard(ComponentResource):
    """CloudWatch dashboard for monitoring ChromaDB stream processing."""
    
    def __init__(self, name: str, opts: ResourceOptions = None):
        super().__init__("chromadb:monitoring:StreamProcessingDashboard", name, None, opts)
        
        dashboard_body = {
            "widgets": [
                {
                    "type": "metric",
                    "x": 0,
                    "y": 0,
                    "width": 12,
                    "height": 6,
                    "properties": {
                        "metrics": [
                            ["ChromaDB/StreamProcessor", "StreamRecordsProcessed"],
                            [".", "SQSMessagesQueued"],
                            [".", "StreamProcessingErrors"]
                        ],
                        "view": "timeSeries",
                        "stacked": False,
                        "region": "us-east-1",  # Update with your region
                        "title": "Stream Processing Overview",
                        "period": 300
                    }
                },
                {
                    "type": "metric",
                    "x": 12,
                    "y": 0,
                    "width": 12,
                    "height": 6,
                    "properties": {
                        "metrics": [
                            ["ChromaDB/StreamProcessor", "StreamProcessingLatency"],
                            ["AWS/Lambda", "Duration", "FunctionName", "stream-processor"]
                        ],
                        "view": "timeSeries",
                        "stacked": False,
                        "region": "us-east-1",
                        "title": "Processing Latency",
                        "period": 300,
                        "yAxis": {
                            "left": {
                                "min": 0
                            }
                        }
                    }
                },
                {
                    "type": "metric",
                    "x": 0,
                    "y": 6,
                    "width": 12,
                    "height": 6,
                    "properties": {
                        "metrics": [
                            ["ChromaDB/Compaction", "CompactionDuration"],
                            [".", "MetadataUpdatesApplied"],
                            [".", "CompactionLockContentions"]
                        ],
                        "view": "timeSeries",
                        "stacked": False,
                        "region": "us-east-1",
                        "title": "Compaction Metrics",
                        "period": 300
                    }
                },
                {
                    "type": "metric",
                    "x": 12,
                    "y": 6,
                    "width": 12,
                    "height": 6,
                    "properties": {
                        "metrics": [
                            ["AWS/SQS", "ApproximateNumberOfMessages", "QueueName", "chromadb-delta-queue"],
                            [".", "ApproximateNumberOfMessagesVisible", ".", "."],
                            [".", "ApproximateNumberOfMessagesNotVisible", ".", "."]
                        ],
                        "view": "timeSeries",
                        "stacked": False,
                        "region": "us-east-1",
                        "title": "SQS Queue Metrics",
                        "period": 300
                    }
                },
                {
                    "type": "log",
                    "x": 0,
                    "y": 12,
                    "width": 24,
                    "height": 6,
                    "properties": {
                        "query": "SOURCE '/aws/lambda/stream-processor'\n| fields @timestamp, @message\n| filter @message like /ERROR/\n| sort @timestamp desc\n| limit 50",
                        "region": "us-east-1",
                        "title": "Recent Errors",
                        "view": "table"
                    }
                }
            ]
        }
        
        self.dashboard = aws.cloudwatch.Dashboard(
            f"{name}-dashboard",
            dashboard_name="ChromaDB-StreamProcessing",
            dashboard_body=json.dumps(dashboard_body),
            opts=ResourceOptions(parent=self)
        )
```

### Phase 6: Error Handling and Recovery

#### 6.1 Error Handling Strategy

Create `docs/operations/error-handling-strategy.md`:

```markdown
# Error Handling Strategy

## Error Types and Recovery Actions

### 1. Stream Processing Errors

#### DynamoDB Stream Record Parsing Failures
- **Cause**: Malformed stream records, unexpected key formats
- **Detection**: Lambda function errors, CloudWatch metrics
- **Recovery**: 
  - Log error details for investigation
  - Skip malformed records (don't block processing)
  - Alert on high error rates

#### SQS Message Send Failures  
- **Cause**: Queue unavailable, permission issues, message size limits
- **Detection**: SQS API errors, failed message counts
- **Recovery**:
  - Retry with exponential backoff (3 attempts)
  - Log failed messages for manual recovery
  - Alert on sustained failures

### 2. Compaction Lock Issues

#### Lock Acquisition Failures
- **Cause**: Another compaction process running, expired locks not cleaned
- **Detection**: Conditional check failed exceptions
- **Recovery**:
  - Skip current invocation (normal behavior)
  - Monitor for excessive lock contention
  - Manual lock cleanup if needed

#### Lock Release Failures
- **Cause**: DynamoDB unavailable, permission issues
- **Detection**: Delete item failures
- **Recovery**:
  - Log error (lock will auto-expire via TTL)
  - Monitor for stuck locks
  - Manual cleanup procedure

### 3. ChromaDB Operations

#### Metadata Update Failures
- **Cause**: ChromaDB unavailable, record not found, schema mismatches
- **Detection**: ChromaDB client errors
- **Recovery**:
  - Retry with exponential backoff
  - Dead letter queue for failed updates
  - Manual reconciliation tools

## Recovery Procedures

### Manual Lock Cleanup
```bash
# Check current locks
aws dynamodb scan \
  --table-name portfolio-prod \
  --filter-expression "begins_with(PK, :pk)" \
  --expression-attribute-values '{":pk":{"S":"LOCK#"}}'

# Force release expired lock
aws dynamodb delete-item \
  --table-name portfolio-prod \
  --key '{"PK":{"S":"LOCK#chroma-main-snapshot"},"SK":{"S":"LOCK"}}'
```

### SQS Queue Recovery
```bash
# Check queue depth
aws sqs get-queue-attributes \
  --queue-url $QUEUE_URL \
  --attribute-names ApproximateNumberOfMessages

# Redrive messages from DLQ
aws sqs redrive-all-messages \
  --source-queue-url $DLQ_URL
```

### Failed Message Recovery
```bash
# Query CloudWatch Logs for failed messages
aws logs filter-log-events \
  --log-group-name /aws/lambda/stream-processor \
  --filter-pattern "ERROR Failed to send message"

# Manually replay failed messages
# (Implementation depends on logging format)
```

## Monitoring and Alerting

### Key Metrics to Monitor
1. **StreamProcessingErrors** > 10 errors/10 minutes
2. **CompactionLockContentions** > 5 contentions/10 minutes  
3. **SQS ApproximateNumberOfMessages** > 100 messages
4. **Lambda Duration** > 4 minutes (approaching timeout)
5. **Lambda Errors** > 5% error rate

### Alert Escalation
1. **Immediate**: Page on call engineer for critical errors
2. **15 minutes**: Escalate to development team
3. **1 hour**: Escalate to management if unresolved

### Runbook Links
- [Stream Processor Troubleshooting](#stream-processor-troubleshooting)
- [Compaction Lock Recovery](#compaction-lock-recovery) 
- [ChromaDB Reconciliation](#chromadb-reconciliation)
```

#### 6.2 Circuit Breaker Implementation

Create `infra/utils/circuit_breaker.py`:

```python
"""Circuit breaker for external service calls."""

import time
from enum import Enum
from typing import Callable, Any
import logging

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject calls  
    HALF_OPEN = "half_open"  # Testing if service recovered

class CircuitBreaker:
    """
    Circuit breaker pattern implementation for external service calls.
    
    Prevents cascading failures by temporarily stopping calls to failing services.
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception
    ):
        """
        Initialize circuit breaker.
        
        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before trying again
            expected_exception: Exception type that counts as failure
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = CircuitState.CLOSED
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Call function with circuit breaker protection.
        
        Args:
            func: Function to call
            *args, **kwargs: Arguments to pass to function
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerException: When circuit is open
            Original exception: When function fails
        """
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time < self.recovery_timeout:
                raise CircuitBreakerException(
                    f"Circuit breaker is OPEN, failing fast. "
                    f"Last failure: {self.last_failure_time}, "
                    f"Recovery timeout: {self.recovery_timeout}s"
                )
            else:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker transitioning to HALF_OPEN")
        
        try:
            result = func(*args, **kwargs)
            
            # Success - reset failure count
            if self.state == CircuitState.HALF_OPEN:
                logger.info("Circuit breaker call succeeded, closing circuit")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                
            return result
            
        except self.expected_exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            logger.warning(
                f"Circuit breaker recorded failure {self.failure_count}/{self.failure_threshold}: {e}"
            )
            
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.error(
                    f"Circuit breaker OPENED after {self.failure_count} failures"
                )
            
            raise  # Re-raise original exception

class CircuitBreakerException(Exception):
    """Exception raised when circuit breaker is open."""
    pass

# Global circuit breakers for different services
chromadb_circuit_breaker = CircuitBreaker(
    failure_threshold=3,
    recovery_timeout=30
)

sqs_circuit_breaker = CircuitBreaker(
    failure_threshold=5, 
    recovery_timeout=60
)

def with_circuit_breaker(circuit_breaker: CircuitBreaker):
    """Decorator to add circuit breaker to function."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            return circuit_breaker.call(func, *args, **kwargs)
        return wrapper
    return decorator
```

### Phase 7: Performance Optimization

#### 7.1 Batch Processing Optimization

Create `infra/optimization/batch_processor.py`:

```python
"""Optimized batch processing for stream records."""

import json
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass
from collections import defaultdict

@dataclass
class ProcessingBatch:
    """Represents a batch of related processing items."""
    entity_type: str
    items: List[Dict[str, Any]]
    
    def size(self) -> int:
        return len(self.items)

class StreamBatchOptimizer:
    """Optimizes processing of DynamoDB stream records."""
    
    def __init__(self, max_batch_size: int = 25):
        """
        Initialize batch optimizer.
        
        Args:
            max_batch_size: Maximum items per batch (DynamoDB batch limit is 25)
        """
        self.max_batch_size = max_batch_size
    
    def optimize_stream_records(self, records: List[Dict[str, Any]]) -> List[ProcessingBatch]:
        """
        Group stream records into optimized processing batches.
        
        Groups by:
        1. Entity type (RECEIPT_LINE, RECEIPT_WORD, RECEIPT_METADATA)
        2. Receipt (to batch updates for same receipt)
        3. Batch size limits
        
        Args:
            records: List of parsed stream records
            
        Returns:
            List of optimized processing batches
        """
        # Group by entity type and receipt
        grouped = defaultdict(lambda: defaultdict(list))
        
        for record in records:
            entity_type = record['entity_type']
            receipt_key = f"{record['image_id']}#{record['receipt_id']}"
            grouped[entity_type][receipt_key].append(record)
        
        batches = []
        
        for entity_type, receipts in grouped.items():
            current_batch = []
            
            for receipt_key, receipt_records in receipts.items():
                # If adding this receipt would exceed batch size, start new batch
                if len(current_batch) + len(receipt_records) > self.max_batch_size:
                    if current_batch:
                        batches.append(ProcessingBatch(entity_type, current_batch))
                        current_batch = []
                
                current_batch.extend(receipt_records)
            
            # Add final batch if not empty
            if current_batch:
                batches.append(ProcessingBatch(entity_type, current_batch))
        
        return batches
    
    def create_batch_sqs_messages(self, batches: List[ProcessingBatch]) -> List[Dict[str, Any]]:
        """
        Create optimized SQS messages from processing batches.
        
        Args:
            batches: List of processing batches
            
        Returns:
            List of SQS message bodies
        """
        messages = []
        
        for batch in batches:
            if batch.size() == 1:
                # Single item - use existing message format
                item = batch.items[0]
                message = {
                    "source": "dynamodb_stream",
                    "entity_type": item['entity_type'],
                    "entity_data": item['entity_data'],
                    "changes": item['changes'],
                    "event_name": item['event_name'],
                    "timestamp": item['timestamp']
                }
                messages.append(message)
            else:
                # Batch message - new format for efficiency
                message = {
                    "source": "dynamodb_stream_batch",
                    "entity_type": batch.entity_type,
                    "batch_size": batch.size(),
                    "items": batch.items,
                    "timestamp": batch.items[0]['timestamp']  # Use first item timestamp
                }
                messages.append(message)
        
        return messages

def calculate_batch_savings(original_count: int, batched_count: int) -> Dict[str, Any]:
    """
    Calculate efficiency gains from batch processing.
    
    Args:
        original_count: Number of individual messages
        batched_count: Number of batch messages
        
    Returns:
        Dictionary with efficiency metrics
    """
    if original_count == 0:
        return {"savings_percent": 0, "message_reduction": 0}
    
    message_reduction = original_count - batched_count
    savings_percent = (message_reduction / original_count) * 100
    
    return {
        "original_messages": original_count,
        "batched_messages": batched_count,
        "message_reduction": message_reduction,
        "savings_percent": savings_percent,
        "efficiency_ratio": original_count / batched_count if batched_count > 0 else 0
    }
```

#### 7.2 Memory and Performance Monitoring

Create `infra/optimization/performance_monitor.py`:

```python
"""Performance monitoring for stream processing."""

import psutil
import time
import json
from typing import Dict, Any, Optional
from contextlib import contextmanager
from dataclasses import dataclass, asdict

@dataclass
class PerformanceMetrics:
    """Container for performance metrics."""
    cpu_percent: float
    memory_mb: float
    memory_percent: float
    execution_time_ms: float
    records_processed: int
    records_per_second: float
    peak_memory_mb: float
    gc_collections: Optional[int] = None

class PerformanceMonitor:
    """Monitor performance of stream processing operations."""
    
    def __init__(self):
        """Initialize performance monitor."""
        self.start_time = None
        self.start_memory = None
        self.peak_memory = 0
        self.records_processed = 0
    
    @contextmanager
    def monitor_operation(self, operation_name: str):
        """
        Context manager to monitor an operation's performance.
        
        Args:
            operation_name: Name of the operation being monitored
            
        Yields:
            Performance monitor instance
        """
        # Record starting metrics
        self.start_time = time.time()
        process = psutil.Process()
        self.start_memory = process.memory_info().rss / 1024 / 1024  # MB
        self.peak_memory = self.start_memory
        
        try:
            yield self
        finally:
            # Calculate final metrics
            end_time = time.time()
            execution_time_ms = (end_time - self.start_time) * 1000
            
            # Get current process info
            current_memory = process.memory_info().rss / 1024 / 1024  # MB
            self.peak_memory = max(self.peak_memory, current_memory)
            
            # Calculate rates
            records_per_second = (
                self.records_processed / (execution_time_ms / 1000)
                if execution_time_ms > 0 else 0
            )
            
            metrics = PerformanceMetrics(
                cpu_percent=process.cpu_percent(),
                memory_mb=current_memory,
                memory_percent=process.memory_percent(),
                execution_time_ms=execution_time_ms,
                records_processed=self.records_processed,
                records_per_second=records_per_second,
                peak_memory_mb=self.peak_memory
            )
            
            # Log performance metrics
            self._log_metrics(operation_name, metrics)
    
    def record_processed(self, count: int = 1):
        """
        Record that records have been processed.
        
        Args:
            count: Number of records processed
        """
        self.records_processed += count
        
        # Update peak memory if needed
        try:
            current_memory = psutil.Process().memory_info().rss / 1024 / 1024
            self.peak_memory = max(self.peak_memory, current_memory)
        except:
            pass  # Don't fail processing for monitoring issues
    
    def _log_metrics(self, operation_name: str, metrics: PerformanceMetrics):
        """
        Log performance metrics.
        
        Args:
            operation_name: Name of the operation
            metrics: Performance metrics to log
        """
        metrics_dict = asdict(metrics)
        metrics_dict['operation'] = operation_name
        
        # Log as structured JSON for CloudWatch parsing
        print(json.dumps({
            "type": "performance_metrics",
            "data": metrics_dict
        }))
        
        # Also log human-readable summary
        print(
            f"PERFORMANCE [{operation_name}]: "
            f"{metrics.records_processed} records in {metrics.execution_time_ms:.1f}ms "
            f"({metrics.records_per_second:.1f} records/sec), "
            f"Memory: {metrics.memory_mb:.1f}MB (peak: {metrics.peak_memory_mb:.1f}MB), "
            f"CPU: {metrics.cpu_percent:.1f}%"
        )

# Global performance monitor instance
performance_monitor = PerformanceMonitor()

def monitor_performance(operation_name: str):
    """Decorator to monitor function performance."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            with performance_monitor.monitor_operation(operation_name):
                return func(*args, **kwargs)
        return wrapper
    return decorator
```

### Phase 8: Security Considerations

#### 8.1 IAM Permissions

Create `infra/security/iam_policies.py`:

```python
"""IAM policies for stream processing security."""

import json
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions, Output

class StreamProcessingSecurity(ComponentResource):
    """Security configuration for stream processing."""
    
    def __init__(
        self, 
        name: str,
        dynamodb_table_arn: Output[str],
        sqs_queue_arn: Output[str],
        chromadb_bucket_arn: Output[str],
        opts: ResourceOptions = None
    ):
        super().__init__("chromadb:security:StreamProcessingSecurity", name, None, opts)
        
        # Stream processor role with minimal permissions
        self.stream_processor_role = self._create_stream_processor_role(
            name, dynamodb_table_arn, sqs_queue_arn
        )
        
        # Compaction role with broader permissions for ChromaDB operations
        self.compaction_role = self._create_compaction_role(
            name, dynamodb_table_arn, sqs_queue_arn, chromadb_bucket_arn
        )
        
        # CloudWatch Logs encryption key
        self.logs_kms_key = self._create_logs_encryption_key(name)
    
    def _create_stream_processor_role(
        self, 
        name: str,
        dynamodb_table_arn: Output[str], 
        sqs_queue_arn: Output[str]
    ):
        """Create minimal IAM role for stream processor Lambda."""
        
        # Assume role policy
        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "sts:AssumeRole",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "lambda.amazonaws.com"
                    }
                }
            ]
        }
        
        role = aws.iam.Role(
            f"{name}-stream-processor-role",
            name=f"ChromaDB-StreamProcessor-{name}",
            assume_role_policy=json.dumps(assume_role_policy),
            tags={
                "Component": "StreamProcessor",
                "Principle": "LeastPrivilege"
            },
            opts=ResourceOptions(parent=self)
        )
        
        # Basic Lambda execution policy
        aws.iam.RolePolicyAttachment(
            f"{name}-stream-processor-basic",
            role=role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self)
        )
        
        # Custom policy with minimal permissions
        policy = aws.iam.RolePolicy(
            f"{name}-stream-processor-policy",
            role=role.id,
            policy=Output.all(
                dynamodb_table_arn,
                sqs_queue_arn
            ).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "DynamoDBStreamAccess",
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:DescribeStream",
                            "dynamodb:GetRecords",
                            "dynamodb:GetShardIterator",
                            "dynamodb:ListStreams"
                        ],
                        "Resource": f"{args[0]}/stream/*"
                    },
                    {
                        "Sid": "SQSPublishAccess",
                        "Effect": "Allow",
                        "Action": [
                            "sqs:SendMessage",
                            "sqs:SendMessageBatch",
                            "sqs:GetQueueAttributes"
                        ],
                        "Resource": args[1]
                    },
                    {
                        "Sid": "CloudWatchMetrics",
                        "Effect": "Allow",
                        "Action": [
                            "cloudwatch:PutMetricData"
                        ],
                        "Resource": "*",
                        "Condition": {
                            "StringEquals": {
                                "cloudwatch:namespace": "ChromaDB/StreamProcessor"
                            }
                        }
                    }
                ]
            })),
            opts=ResourceOptions(parent=self)
        )
        
        return role
    
    def _create_compaction_role(
        self,
        name: str,
        dynamodb_table_arn: Output[str],
        sqs_queue_arn: Output[str], 
        chromadb_bucket_arn: Output[str]
    ):
        """Create IAM role for compaction Lambda."""
        
        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "sts:AssumeRole",
                    "Effect": "Allow", 
                    "Principal": {
                        "Service": "lambda.amazonaws.com"
                    }
                }
            ]
        }
        
        role = aws.iam.Role(
            f"{name}-compaction-role",
            name=f"ChromaDB-Compaction-{name}",
            assume_role_policy=json.dumps(assume_role_policy),
            tags={
                "Component": "Compaction",
                "Principle": "LeastPrivilege"
            },
            opts=ResourceOptions(parent=self)
        )
        
        # Basic Lambda execution policy
        aws.iam.RolePolicyAttachment(
            f"{name}-compaction-basic",
            role=role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self)
        )
        
        # Custom policy for compaction operations
        policy = aws.iam.RolePolicy(
            f"{name}-compaction-policy",
            role=role.id,
            policy=Output.all(
                dynamodb_table_arn,
                sqs_queue_arn,
                chromadb_bucket_arn
            ).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "DynamoDBLockAccess",
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:PutItem",
                            "dynamodb:DeleteItem",
                            "dynamodb:UpdateItem",
                            "dynamodb:GetItem"
                        ],
                        "Resource": args[0],
                        "Condition": {
                            "ForAllValues:StringEquals": {
                                "dynamodb:Attributes": ["PK", "SK", "owner", "expires", "heartbeat"]
                            },
                            "StringLike": {
                                "dynamodb:LeadingKeys": ["LOCK#*"]
                            }
                        }
                    },
                    {
                        "Sid": "SQSConsumerAccess",
                        "Effect": "Allow",
                        "Action": [
                            "sqs:ReceiveMessage",
                            "sqs:DeleteMessage",
                            "sqs:GetQueueAttributes",
                            "sqs:ChangeMessageVisibility"
                        ],
                        "Resource": args[1]
                    },
                    {
                        "Sid": "S3ChromaDBAccess",
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:PutObject",
                            "s3:DeleteObject",
                            "s3:ListBucket"
                        ],
                        "Resource": [
                            args[2],
                            f"{args[2]}/*"
                        ]
                    },
                    {
                        "Sid": "CloudWatchMetrics",
                        "Effect": "Allow",
                        "Action": [
                            "cloudwatch:PutMetricData"
                        ],
                        "Resource": "*",
                        "Condition": {
                            "StringEquals": {
                                "cloudwatch:namespace": [
                                    "ChromaDB/Compaction",
                                    "ChromaDB/StreamProcessor"
                                ]
                            }
                        }
                    }
                ]
            })),
            opts=ResourceOptions(parent=self)
        )
        
        return role
    
    def _create_logs_encryption_key(self, name: str):
        """Create KMS key for CloudWatch Logs encryption."""
        
        key_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "Enable IAM User Permissions",
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": f"arn:aws:iam::{aws.get_caller_identity().account_id}:root"
                    },
                    "Action": "kms:*",
                    "Resource": "*"
                },
                {
                    "Sid": "Allow CloudWatch Logs",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "logs.amazonaws.com"
                    },
                    "Action": [
                        "kms:Encrypt",
                        "kms:Decrypt",
                        "kms:ReEncrypt*",
                        "kms:GenerateDataKey*",
                        "kms:DescribeKey"
                    ],
                    "Resource": "*"
                }
            ]
        }
        
        key = aws.kms.Key(
            f"{name}-logs-key",
            description=f"KMS key for ChromaDB {name} CloudWatch Logs encryption",
            policy=json.dumps(key_policy),
            tags={
                "Component": "CloudWatchLogs",
                "Purpose": "Encryption"
            },
            opts=ResourceOptions(parent=self)
        )
        
        aws.kms.Alias(
            f"{name}-logs-key-alias",
            name=f"alias/chromadb-{name}-logs",
            target_key_id=key.key_id,
            opts=ResourceOptions(parent=self)
        )
        
        return key
```

#### 8.2 Data Encryption and Privacy

Create `infra/security/encryption_config.py`:

```python
"""Encryption configuration for stream processing."""

import json
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions, Output

class EncryptionConfig(ComponentResource):
    """Encryption configuration for all ChromaDB components."""
    
    def __init__(self, name: str, opts: ResourceOptions = None):
        super().__init__("chromadb:security:EncryptionConfig", name, None, opts)
        
        # Create encryption keys
        self.dynamodb_key = self._create_dynamodb_key(name)
        self.s3_key = self._create_s3_key(name) 
        self.sqs_key = self._create_sqs_key(name)
        self.lambda_key = self._create_lambda_key(name)
    
    def _create_dynamodb_key(self, name: str):
        """Create KMS key for DynamoDB encryption."""
        
        key_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "Enable IAM User Permissions",
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": Output.concat(
                            "arn:aws:iam::",
                            aws.get_caller_identity().account_id,
                            ":root"
                        )
                    },
                    "Action": "kms:*",
                    "Resource": "*"
                },
                {
                    "Sid": "Allow DynamoDB Service",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "dynamodb.amazonaws.com"
                    },
                    "Action": [
                        "kms:Decrypt",
                        "kms:DescribeKey",
                        "kms:Encrypt",
                        "kms:GenerateDataKey*",
                        "kms:ReEncrypt*"
                    ],
                    "Resource": "*"
                }
            ]
        }
        
        return aws.kms.Key(
            f"{name}-dynamodb-key",
            description=f"KMS key for DynamoDB encryption - {name}",
            policy=json.dumps(key_policy),
            tags={
                "Component": "DynamoDB",
                "Purpose": "Encryption",
                "Environment": name
            },
            opts=ResourceOptions(parent=self)
        )
    
    def _create_s3_key(self, name: str):
        """Create KMS key for S3 encryption."""
        
        key_policy = {
            "Version": "2012-10-17", 
            "Statement": [
                {
                    "Sid": "Enable IAM User Permissions",
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": Output.concat(
                            "arn:aws:iam::",
                            aws.get_caller_identity().account_id, 
                            ":root"
                        )
                    },
                    "Action": "kms:*",
                    "Resource": "*"
                },
                {
                    "Sid": "Allow S3 Service",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "s3.amazonaws.com"
                    },
                    "Action": [
                        "kms:Decrypt",
                        "kms:DescribeKey",
                        "kms:Encrypt", 
                        "kms:GenerateDataKey*",
                        "kms:ReEncrypt*"
                    ],
                    "Resource": "*"
                }
            ]
        }
        
        return aws.kms.Key(
            f"{name}-s3-key",
            description=f"KMS key for S3 encryption - {name}",
            policy=json.dumps(key_policy),
            tags={
                "Component": "S3",
                "Purpose": "Encryption", 
                "Environment": name
            },
            opts=ResourceOptions(parent=self)
        )
    
    def _create_sqs_key(self, name: str):
        """Create KMS key for SQS encryption."""
        
        key_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "Enable IAM User Permissions", 
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": Output.concat(
                            "arn:aws:iam::",
                            aws.get_caller_identity().account_id,
                            ":root"
                        )
                    },
                    "Action": "kms:*",
                    "Resource": "*"
                },
                {
                    "Sid": "Allow SQS Service",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "sqs.amazonaws.com"
                    },
                    "Action": [
                        "kms:Decrypt",
                        "kms:GenerateDataKey"
                    ],
                    "Resource": "*"
                }
            ]
        }
        
        return aws.kms.Key(
            f"{name}-sqs-key",
            description=f"KMS key for SQS encryption - {name}",
            policy=json.dumps(key_policy),
            tags={
                "Component": "SQS",
                "Purpose": "Encryption",
                "Environment": name
            },
            opts=ResourceOptions(parent=self)
        )
    
    def _create_lambda_key(self, name: str):
        """Create KMS key for Lambda environment variable encryption."""
        
        key_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "Enable IAM User Permissions",
                    "Effect": "Allow", 
                    "Principal": {
                        "AWS": Output.concat(
                            "arn:aws:iam::",
                            aws.get_caller_identity().account_id,
                            ":root"
                        )
                    },
                    "Action": "kms:*",
                    "Resource": "*"
                },
                {
                    "Sid": "Allow Lambda Service",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "lambda.amazonaws.com"
                    },
                    "Action": [
                        "kms:Decrypt"
                    ],
                    "Resource": "*"
                }
            ]
        }
        
        return aws.kms.Key(
            f"{name}-lambda-key",
            description=f"KMS key for Lambda encryption - {name}",
            policy=json.dumps(key_policy),
            tags={
                "Component": "Lambda",
                "Purpose": "Encryption",
                "Environment": name
            },
            opts=ResourceOptions(parent=self)
        )
```

### Phase 9: Deployment Strategy

#### 9.1 Phased Rollout Plan

Create `docs/deployment/phased-rollout-plan.md`:

```markdown
# Phased Rollout Plan for DynamoDB Stream Integration

## Overview

This document outlines a phased approach to deploying the DynamoDB Stream integration with ChromaDB compaction, minimizing risk and allowing for validation at each stage.

## Phase 1: Infrastructure Setup (Week 1)

### Goals
- Deploy stream processing infrastructure without enabling streams
- Validate IAM permissions and resource creation
- Test compaction lock mechanism

### Tasks
1. **Deploy Infrastructure Components**
   ```bash
   # Deploy stream processor Lambda (not connected to stream)
   cd infra/embedding_step_functions
   pulumi up -t stream-processor-lambda
   
   # Deploy enhanced compaction Lambda
   pulumi up -t enhanced-compaction-lambda
   
   # Verify SQS queue and DLQ configuration
   pulumi up -t chromadb-queues
   ```

2. **Test Compaction Lock**
   ```bash
   # Run compaction Lambda manually to test lock mechanism
   aws lambda invoke \
     --function-name chromadb-compaction-enhanced-dev \
     --invocation-type Event \
     /tmp/response.json
   
   # Verify lock creation in DynamoDB
   aws dynamodb scan \
     --table-name portfolio-dev \
     --filter-expression "begins_with(PK, :pk)" \
     --expression-attribute-values '{":pk":{"S":"LOCK#"}}'
   ```

3. **Validate Monitoring**
   ```bash
   # Check CloudWatch dashboard creation
   aws cloudwatch get-dashboard \
     --dashboard-name ChromaDB-StreamProcessing
   
   # Verify custom metrics namespace
   aws cloudwatch list-metrics \
     --namespace ChromaDB/StreamProcessor
   ```

### Success Criteria
- [ ] All infrastructure components deployed successfully
- [ ] Compaction lock acquires and releases correctly
- [ ] CloudWatch dashboard displays metrics
- [ ] No errors in Lambda function logs
- [ ] SQS queue and DLQ created with proper configuration

### Rollback Plan
- Delete all created resources using `pulumi destroy`
- Existing compaction process remains unchanged

## Phase 2: Stream Processing Testing (Week 2)

### Goals
- Enable DynamoDB streams on non-critical environments
- Test stream processing without affecting production
- Validate message routing to SQS queue

### Tasks
1. **Enable Streams on Dev Environment**
   ```bash
   # Update DynamoDB table to enable streams
   cd infra
   pulumi config set enable-dynamodb-streams true
   pulumi up
   ```

2. **Connect Stream to Processor**
   ```bash
   # Deploy event source mapping
   pulumi up -t dynamodb-stream-mapping
   ```

3. **Generate Test Data**
   ```bash
   # Create test receipt entities to trigger streams
   python scripts/create_test_receipts.py --count 10
   
   # Modify entities to trigger stream events
   python scripts/modify_test_receipts.py --batch-size 5
   ```

4. **Monitor Stream Processing**
   ```bash
   # Watch Lambda logs in real-time
   aws logs tail /aws/lambda/stream-processor-dev --follow
   
   # Check SQS message counts
   aws sqs get-queue-attributes \
     --queue-url $COMPACTION_QUEUE_URL \
     --attribute-names ApproximateNumberOfMessages
   ```

### Success Criteria
- [ ] Stream events are generated for receipt entity changes
- [ ] Stream processor Lambda processes events without errors
- [ ] SQS messages are created for relevant entity changes
- [ ] CloudWatch metrics show expected activity
- [ ] No backlog buildup in stream or queue

### Rollback Plan
- Disable event source mapping: `aws lambda delete-event-source-mapping --uuid $MAPPING_UUID`
- Disable streams on DynamoDB table
- Messages in SQS will be processed normally or expire

## Phase 3: End-to-End Integration Testing (Week 3)

### Goals
- Test complete flow from DynamoDB changes to ChromaDB updates
- Validate metadata synchronization accuracy
- Performance testing under load

### Tasks
1. **Deploy Complete Integration**
   ```bash
   # Deploy full integration stack
   cd infra/embedding_step_functions
   pulumi up
   ```

2. **Run End-to-End Tests**
   ```bash
   # Execute integration test suite
   pytest tests/test_e2e_stream_integration.py -v
   
   # Run load testing
   python tests/load_test_stream_processor.py
   ```

3. **Validate ChromaDB Synchronization**
   ```bash
   # Query ChromaDB to verify metadata updates
   python scripts/verify_chromadb_metadata_sync.py
   
   # Compare DynamoDB entities with ChromaDB metadata
   python scripts/compare_entity_metadata.py
   ```

4. **Performance Benchmarking**
   ```bash
   # Measure end-to-end latency
   python scripts/measure_sync_latency.py
   
   # Test with high-volume changes
   python scripts/stress_test_sync.py --changes-per-second 100
   ```

### Success Criteria
- [ ] Metadata changes in DynamoDB appear in ChromaDB within 5 minutes
- [ ] No data inconsistencies between DynamoDB and ChromaDB
- [ ] System handles 1000+ changes per hour without backlog
- [ ] Lock contention remains below 5% of attempts
- [ ] Average end-to-end latency < 2 minutes

### Rollback Plan
- Disable stream processing: `pulumi config set enable-stream-processing false`
- Existing ChromaDB compaction continues normally
- Manual metadata reconciliation available if needed

## Phase 4: Production Deployment (Week 4)

### Goals
- Deploy to production environment with monitoring
- Gradual enablement with feature flags
- 24/7 monitoring and alerting

### Tasks
1. **Deploy to Production**
   ```bash
   # Switch to production stack
   pulumi stack select prod
   
   # Deploy with feature flag disabled initially
   pulumi config set enable-metadata-sync false
   pulumi up
   ```

2. **Enable Feature Gradually**
   ```bash
   # Enable for RECEIPT_METADATA changes only
   pulumi config set enable-metadata-updates true
   pulumi config set enable-line-updates false
   pulumi config set enable-word-updates false
   pulumi up
   
   # Monitor for 24 hours, then enable line updates
   pulumi config set enable-line-updates true
   pulumi up
   
   # Monitor for 24 hours, then enable word updates
   pulumi config set enable-word-updates true
   pulumi up
   ```

3. **Setup Production Monitoring**
   ```bash
   # Configure alerts for production
   pulumi up -t production-alerts
   
   # Setup PagerDuty integration (if available)
   python scripts/setup_pagerduty_alerts.py
   ```

4. **Performance Monitoring**
   ```bash
   # Monitor key production metrics
   aws cloudwatch get-metric-statistics \
     --namespace ChromaDB/StreamProcessor \
     --metric-name StreamProcessingErrors \
     --start-time $(date -d '1 hour ago' -Iseconds) \
     --end-time $(date -Iseconds) \
     --period 300 \
     --statistics Sum
   ```

### Success Criteria
- [ ] Production deployment completed without errors
- [ ] All monitoring and alerting operational
- [ ] No increase in overall system latency
- [ ] ChromaDB metadata sync accuracy > 99.9%
- [ ] System stable for 7 consecutive days

### Rollback Plan
- Emergency feature flag disable: `pulumi config set enable-stream-processing false`
- Remove event source mapping if needed
- Existing compaction process handles all operations
- Post-rollback metadata reconciliation if required

## Risk Mitigation

### High-Risk Scenarios

1. **DynamoDB Stream Overwhelm**
   - **Risk**: High-volume changes cause stream processing delays
   - **Mitigation**: Batch size limits, parallel processing controls
   - **Detection**: CloudWatch metrics for stream lag
   - **Response**: Auto-scaling or feature flag disable

2. **Compaction Lock Contention**
   - **Risk**: Multiple processes compete for compaction lock
   - **Mitigation**: Lock timeout optimization, heartbeat updates
   - **Detection**: CompactionLockContentions metric
   - **Response**: Lock cleanup procedures, process optimization

3. **ChromaDB Metadata Corruption**
   - **Risk**: Incorrect metadata updates corrupt vector database
   - **Mitigation**: Validation checks, rollback procedures
   - **Detection**: Metadata consistency checks
   - **Response**: Stop updates, manual reconciliation

4. **SQS Queue Backlog**
   - **Risk**: Processing can't keep up with change volume
   - **Mitigation**: Dead letter queue, priority queuing
   - **Detection**: Queue depth monitoring
   - **Response**: Increase processing frequency, manual intervention

### Monitoring Checklist

- [ ] StreamProcessingErrors < 1% of total
- [ ] CompactionLockContentions < 5% of attempts
- [ ] SQS queue depth < 100 messages
- [ ] Average processing latency < 30 seconds
- [ ] ChromaDB metadata accuracy > 99%
- [ ] No Lambda function timeouts
- [ ] Memory usage within acceptable limits

## Communication Plan

### Stakeholders
- **Development Team**: Daily updates during deployment phases
- **Operations Team**: Real-time alerts and monitoring access
- **Product Team**: Weekly progress reports
- **Management**: Milestone completion notifications

### Rollback Communication
- **Immediate**: Slack/Teams notification of rollback initiation
- **15 minutes**: Email summary of issue and resolution steps
- **1 hour**: Post-mortem scheduling and preliminary analysis
- **24 hours**: Complete incident report and lessons learned

## Success Metrics

### Technical Metrics
- **Deployment Success Rate**: 100% of phases deployed without rollback
- **System Availability**: > 99.9% uptime during and after deployment
- **Data Consistency**: > 99.9% accuracy between DynamoDB and ChromaDB
- **Performance**: End-to-end sync latency < 2 minutes (95th percentile)

### Operational Metrics
- **Alert Accuracy**: < 5% false positive rate on monitoring alerts
- **Response Time**: < 15 minutes average incident response time
- **Knowledge Transfer**: 100% of operations team trained on new system
- **Documentation**: Complete runbooks and troubleshooting guides

## Post-Deployment Activities

### Week 1 Post-Production
- Daily monitoring review and optimization
- Performance tuning based on production load
- Alert threshold adjustment
- Documentation updates based on operational learnings

### Month 1 Post-Production
- Complete system performance review
- Cost analysis and optimization opportunities
- Lessons learned documentation
- Planning for next enhancement phase
```

### Phase 10: Cost Analysis and Optimization

#### 10.1 Cost Analysis

Create `docs/cost-analysis/stream-integration-costs.md`:

```markdown
# Cost Analysis: DynamoDB Stream Integration

## Current State Costs (Before Integration)

### Existing ChromaDB Compaction Infrastructure
- **Lambda Execution (Compaction)**: 
  - Executions: ~96/day (every 15 minutes)
  - Duration: 2 minutes average
  - Memory: 8GB
  - **Monthly Cost**: ~$25
  
- **S3 Storage (ChromaDB Vectors)**:
  - Storage: ~100GB
  - Requests: ~1000/month
  - **Monthly Cost**: ~$3
  
- **SQS Queue**: 
  - Messages: ~2000/month
  - **Monthly Cost**: ~$1
  
- **DynamoDB (CompactionLock)**:
  - Reads/Writes: ~200/month
  - **Monthly Cost**: <$1

**Total Current Monthly Cost**: ~$29

## New Integration Costs

### DynamoDB Streams
- **Stream Reads**: 
  - Estimated: 10,000 records/day (receipt entity changes)
  - 300,000 records/month
  - **Cost**: $0.02 per 100,000 reads = **$0.06/month**

### Stream Processor Lambda
- **Executions**:
  - Trigger frequency: ~500/day (batched stream events)
  - Duration: 30 seconds average  
  - Memory: 256MB
  - **Cost**: ~$5/month

### Enhanced Compaction Lambda
- **Additional overhead**: 10% increase in execution time
- **Incremental cost**: ~$2.50/month

### CloudWatch Monitoring
- **Custom Metrics**: 7 metrics × $0.30 = **$2.10/month**
- **Log Storage**: ~5GB/month = **$2.50/month**
- **Alarms**: 4 alarms × $0.10 = **$0.40/month**

### Data Transfer
- **SQS Message Size**: Average 2KB per message
- **Monthly Transfer**: ~1GB
- **Cost**: ~$0.10/month

## Total Additional Monthly Cost: ~$12.66

## Cost Comparison by Scale

### Small Scale (Current)
- **Entity Changes**: 10,000/month
- **Additional Cost**: $12.66/month
- **Cost per Change**: $0.0013

### Medium Scale  
- **Entity Changes**: 100,000/month
- **Additional Cost**: $15.50/month (economies of scale)
- **Cost per Change**: $0.00016

### Large Scale
- **Entity Changes**: 1,000,000/month  
- **Additional Cost**: $35/month
- **Cost per Change**: $0.000035

## Cost Optimization Strategies

### 1. Stream Processing Optimization
```python
# Batch processing reduces Lambda invocations
BATCH_SIZE = 25  # Process 25 records per invocation
# Reduces invocations by 96% while maintaining throughput
```

### 2. Memory Right-Sizing
```python
# Stream processor doesn't need much memory
STREAM_PROCESSOR_MEMORY = 256  # vs 512MB saves 50%

# Compaction may need optimization based on workload
COMPACTION_MEMORY = 8192  # Monitor and adjust
```

### 3. Intelligent Filtering
```python
# Only process changes that affect ChromaDB metadata
RELEVANT_FIELDS = ['text', 'confidence', 'merchant_name']
# Reduces processing by ~60% for non-relevant changes
```

### 4. Feature Flag Cost Control
```python
# Granular control over what triggers processing
ENABLE_METADATA_UPDATES = True   # High value
ENABLE_LINE_UPDATES = True       # Medium value  
ENABLE_WORD_UPDATES = False      # Low value, high volume
```

## ROI Analysis

### Benefits
1. **Real-time Metadata Sync**: Previously manual process
2. **Improved Data Consistency**: Reduces data quality issues
3. **Reduced Manual Operations**: ~4 hours/month saved
4. **Better User Experience**: Faster search results with current data

### Cost-Benefit Calculation
- **Additional Monthly Cost**: $12.66
- **Operations Time Saved**: 4 hours × $100/hour = $400/month value
- **ROI**: 3,162% (ignoring data quality improvements)

## Monitoring and Cost Controls

### AWS Budgets Setup
```bash
aws budgets create-budget --cli-input-json '{
  "Budget": {
    "BudgetName": "ChromaDB-StreamIntegration",
    "BudgetLimit": {
      "Amount": "50",
      "Unit": "USD"
    },
    "TimeUnit": "MONTHLY",
    "BudgetType": "COST",
    "CostFilters": {
      "Service": ["Amazon DynamoDB", "AWS Lambda", "Amazon SQS", "Amazon CloudWatch"]
    }
  },
  "NotificationsWithSubscribers": [{
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 80
    },
    "Subscribers": [{
      "Address": "alerts@company.com",
      "SubscriptionType": "EMAIL"
    }]
  }]
}'
```

### Cost Anomaly Detection
```python
# CloudWatch alarm for unexpected cost spikes
aws cloudwatch put-metric-alarm \
  --alarm-name "ChromaDB-HighCost" \
  --alarm-description "Alert on high ChromaDB costs" \
  --metric-name "EstimatedCharges" \
  --namespace "AWS/Billing" \
  --statistic "Maximum" \
  --period 86400 \
  --threshold 100 \
  --comparison-operator "GreaterThanThreshold" \
  --dimensions "Name=ServiceName,Value=AmazonDynamoDB" "Name=Currency,Value=USD"
```

## Projected 12-Month Costs

| Component | Month 1 | Month 6 | Month 12 | 
|-----------|---------|---------|----------|
| DynamoDB Streams | $0.06 | $0.12 | $0.18 |
| Stream Processor Lambda | $5.00 | $8.00 | $12.00 |
| Enhanced Compaction | $2.50 | $3.00 | $4.00 |
| CloudWatch | $5.00 | $5.00 | $5.00 |
| **Total Monthly** | **$12.66** | **$16.12** | **$21.18** |
| **Annual Total** | | | **$254.16** |

## Conclusion

The DynamoDB Stream integration adds approximately **$12.66/month** to current costs while providing significant operational value. The 3,162% ROI from reduced manual operations alone justifies the investment, before considering data quality and user experience improvements.

### Recommendations
1. **Proceed with implementation** - ROI is overwhelmingly positive
2. **Start with feature flags enabled** for gradual cost control
3. **Monitor usage patterns** for optimization opportunities  
4. **Set up cost alerts** to prevent unexpected charges
5. **Review monthly** for right-sizing opportunities
```

## Conclusion

This comprehensive implementation plan provides a complete roadmap for integrating DynamoDB Streams with the existing ChromaDB compaction process. The design leverages existing infrastructure (SQS queue, CompactionLock entity) while adding real-time metadata synchronization capabilities.

Key benefits of this approach:
- **Leverages Existing Infrastructure**: Uses current SQS queue and compaction lock
- **Minimal Risk**: Phased rollout with feature flags and rollback procedures  
- **Cost Effective**: ~$12.66/month additional cost with 3,162% ROI
- **Scalable**: Handles high-volume changes through intelligent batching
- **Secure**: Comprehensive IAM policies and encryption
- **Monitorable**: Complete observability with CloudWatch metrics and alarms

The implementation is ready to begin with Phase 1 infrastructure deployment.