"""
DynamoDB Stream Processor Lambda for ChromaDB Compaction Integration

This module defines the Lambda function that processes DynamoDB stream events
for receipt metadata and word label changes, triggering ChromaDB metadata updates
through the existing compaction SQS queue.

Focuses on:
- RECEIPT_METADATA entities (merchant info changes)
- RECEIPT_WORD_LABEL entities (word label changes)
- Both MODIFY and REMOVE operations
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process DynamoDB stream events for ChromaDB metadata synchronization.
    
    This function is lightweight and focuses only on:
    1. Parsing stream events for relevant entities
    2. Detecting ChromaDB-relevant field changes
    3. Creating SQS messages for the compaction Lambda
    
    Heavy processing is delegated to the compaction Lambda.
    
    Args:
        event: DynamoDB stream event
        context: Lambda context
        
    Returns:
        Response with processing statistics
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
                
            # Process MODIFY and REMOVE events
            if record['eventName'] in ['MODIFY', 'REMOVE']:
                old_image = record['dynamodb'].get('OldImage', {})
                new_image = record['dynamodb'].get('NewImage', {})
                
                # For REMOVE events, we only need the old image
                if record['eventName'] == 'REMOVE':
                    new_image = {}
                
                # Check if any ChromaDB-relevant fields changed
                changes = get_chromadb_relevant_changes(
                    entity_data, old_image, new_image
                )
                
                # Always process REMOVE events, even without specific field changes
                if changes or record['eventName'] == 'REMOVE':
                    # Create SQS message for the compaction Lambda
                    message = {
                        "source": "dynamodb_stream",
                        "entity_type": entity_data['entity_type'],
                        "entity_data": entity_data,
                        "changes": changes,
                        "event_name": record['eventName'],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "stream_record_id": record.get('eventID', 'unknown'),
                        "aws_region": record.get('awsRegion', 'unknown')
                    }
                    messages_to_send.append(message)
                    processed_records += 1
                    
        except Exception as e:
            logger.error(
                f"Error processing stream record {record.get('eventID', 'unknown')}: {e}"
            )
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
    
    Only processes entities that affect ChromaDB metadata:
    - RECEIPT_METADATA: merchant info that affects all embeddings
    - RECEIPT_WORD_LABEL: labels that affect specific word embeddings
    
    Args:
        pk: Primary key (e.g., "IMAGE#uuid")
        sk: Sort key (e.g., "RECEIPT#00001#METADATA")
    
    Returns:
        Dictionary with entity info or None if not relevant
    """
    if not pk.startswith('IMAGE#'):
        return None
        
    image_id = pk.split('#')[1] if '#' in pk else None
    if not image_id:
        return None
    
    # RECEIPT_METADATA: RECEIPT#00001#METADATA
    if '#METADATA' in sk:
        parts = sk.split('#')
        if len(parts) >= 3 and parts[0] == 'RECEIPT' and parts[2] == 'METADATA':
            try:
                receipt_id = int(parts[1])
                return {
                    'entity_type': 'RECEIPT_METADATA',
                    'image_id': image_id,
                    'receipt_id': receipt_id
                }
            except ValueError:
                logger.warning(f"Invalid receipt_id in METADATA SK: {sk}")
                return None
    
    # RECEIPT_WORD_LABEL: RECEIPT#00001#LINE#00001#WORD#00001#LABEL#TOTAL
    elif '#LABEL#' in sk:
        parts = sk.split('#')
        if (len(parts) >= 8 and parts[0] == 'RECEIPT' and 
            parts[2] == 'LINE' and parts[4] == 'WORD' and parts[6] == 'LABEL'):
            try:
                receipt_id = int(parts[1])
                line_id = int(parts[3])
                word_id = int(parts[5])
                label = parts[7]
                return {
                    'entity_type': 'RECEIPT_WORD_LABEL',
                    'image_id': image_id,
                    'receipt_id': receipt_id,
                    'line_id': line_id,
                    'word_id': word_id,
                    'label': label
                }
            except ValueError:
                logger.warning(f"Invalid IDs in LABEL SK: {sk}")
                return None
    
    return None  # Not a relevant entity type


def get_chromadb_relevant_changes(
    entity_data: Dict[str, Any], 
    old_image: Dict[str, Any], 
    new_image: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Identify changes to fields that affect ChromaDB metadata.
    
    Args:
        entity_data: Parsed entity information
        old_image: Previous DynamoDB item state
        new_image: Current DynamoDB item state (empty for REMOVE events)
        
    Returns:
        Dictionary of changed fields with old/new values
    """
    # Define ChromaDB-relevant fields for each entity type
    relevant_fields = {
        'RECEIPT_METADATA': [
            'canonical_merchant_name', 'merchant_name', 'merchant_category', 
            'address', 'phone_number', 'place_id'
        ],
        'RECEIPT_WORD_LABEL': [
            'label', 'reasoning', 'validation_status', 
            'label_proposed_by', 'label_consolidated_from'
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
                'Id': str(i + j),
                'MessageBody': json.dumps(message),
                'MessageAttributes': {
                    'source': {
                        'StringValue': 'dynamodb_stream',
                        'DataType': 'String'
                    },
                    'entity_type': {
                        'StringValue': message['entity_type'],
                        'DataType': 'String'
                    },
                    'event_name': {
                        'StringValue': message['event_name'],
                        'DataType': 'String'
                    }
                }
            })
        
        try:
            response = sqs.send_message_batch(
                QueueUrl=queue_url,
                Entries=entries
            )
            
            # Count successful sends
            sent_count += len(response.get('Successful', []))
            
            # Log any failures
            if 'Failed' in response and response['Failed']:
                for failed in response['Failed']:
                    logger.error(
                        f"Failed to send message {failed['Id']}: "
                        f"{failed.get('Message', 'Unknown error')}"
                    )
                    
        except Exception as e:
            logger.error(f"Error sending SQS batch: {e}")
    
    return sent_count