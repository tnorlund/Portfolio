"""
Lambda handler for triggering compaction of multiple delta files after batch processing.

This lightweight Lambda is called at the end of the step function to trigger
compaction of all deltas created during the parallel embedding process.

NOTE: This Lambda does NOT do the actual compaction - it just triggers the
containerized ChromaDB Lambda that has the necessary packages.
"""

import json
import os
from logging import INFO, Formatter, StreamHandler, getLogger

import boto3

logger = getLogger()
logger.setLevel(INFO)

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)


def compact_handler(event, context):
    """
    Compact multiple delta files into ChromaDB.
    
    This handler is designed to be called at the end of a step function
    after all parallel embedding tasks have completed.
    
    Input event format:
    {
        "delta_results": [
            {
                "delta_key": "delta/abc123/",
                "delta_id": "abc123",
                "embedding_count": 100
            },
            ...
        ]
    }
    """
    logger.info("Starting batch delta compaction")
    logger.info(f"Event: {json.dumps(event)}")
    
    delta_results = event.get("delta_results", [])
    
    if not delta_results:
        logger.warning("No delta results provided for compaction")
        return {
            "statusCode": 200,
            "message": "No deltas to compact"
        }
    
    # Extract delta keys
    delta_keys = [result["delta_key"] for result in delta_results]
    total_embeddings = sum(result.get("embedding_count", 0) for result in delta_results)
    
    logger.info(f"Compacting {len(delta_keys)} deltas with {total_embeddings} total embeddings")
    
    # Get the compaction Lambda ARN from environment
    compaction_lambda_arn = os.environ.get("COMPACTION_LAMBDA_ARN")
    if not compaction_lambda_arn:
        # Fallback: Send to SQS queue for async processing
        logger.info("COMPACTION_LAMBDA_ARN not set, using SQS queue")
        
        sqs_queue_url = os.environ.get("COMPACTION_QUEUE_URL")
        if sqs_queue_url:
            sqs = boto3.client("sqs")
            
            # Send a single message for batch compaction
            message_body = {
                "source": "step_function_batch",
                "delta_keys": delta_keys,
                "total_embeddings": total_embeddings,
                "batch_compaction": True
            }
            
            sqs.send_message(
                QueueUrl=sqs_queue_url,
                MessageBody=json.dumps(message_body),
                MessageAttributes={
                    'batch_size': {
                        'StringValue': str(len(delta_keys)),
                        'DataType': 'Number'
                    },
                    'priority': {
                        'StringValue': 'high',
                        'DataType': 'String'
                    }
                }
            )
            
            logger.info(f"Sent batch compaction request to SQS queue")
            
            return {
                "statusCode": 200,
                "compaction_method": "sqs_queue",
                "delta_count": len(delta_keys),
                "total_embeddings": total_embeddings,
                "message": "Compaction queued for async processing"
            }
    
    else:
        # Direct Lambda invocation for immediate compaction
        logger.info(f"Invoking compaction Lambda directly: {compaction_lambda_arn}")
        
        lambda_client = boto3.client("lambda")
        
        # Invoke the compaction Lambda synchronously
        response = lambda_client.invoke(
            FunctionName=compaction_lambda_arn,
            InvocationType="RequestResponse",  # Synchronous
            Payload=json.dumps({
                "source": "step_function_batch",
                "delta_keys": delta_keys,
                "priority": "high",
                "batch_compaction": True
            })
        )
        
        # Parse the response
        response_payload = json.loads(response["Payload"].read())
        
        if response["StatusCode"] == 200:
            logger.info(f"Compaction completed successfully")
            
            return {
                "statusCode": 200,
                "compaction_method": "direct_lambda",
                "delta_count": len(delta_keys),
                "total_embeddings": total_embeddings,
                "compaction_result": response_payload,
                "message": "Compaction completed successfully"
            }
        else:
            logger.error(f"Compaction failed with status code: {response['StatusCode']}")
            
            return {
                "statusCode": 500,
                "error": "Compaction failed",
                "lambda_response": response_payload
            }
    
    return {
        "statusCode": 500,
        "error": "No compaction method available"
    }