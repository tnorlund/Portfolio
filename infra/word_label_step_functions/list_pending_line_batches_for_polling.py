"""
Lambda handler for listing pending line embedding batches.

This Lambda queries DynamoDB for line embedding batches that are ready for polling.
"""

import json
import os
from logging import INFO, Formatter, StreamHandler, getLogger

from receipt_label.embedding.line import list_pending_line_embedding_batches

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


def poll_handler(event, context):
    """
    List pending line embedding batches from DynamoDB.
    
    Returns:
        dict: Contains statusCode and body with list of pending batches
    """
    logger.info("Starting list_pending_line_batches_for_polling handler")
    
    try:
        # Get pending batches
        pending_batches = list_pending_line_embedding_batches()
        
        logger.info(f"Found {len(pending_batches)} pending line embedding batches")
        
        # Format response for step function
        batch_list = []
        for batch in pending_batches:
            batch_list.append({
                "batch_id": batch.batch_id,
                "openai_batch_id": batch.openai_batch_id,
            })
        
        return {
            "statusCode": 200,
            "body": batch_list,
        }
        
    except Exception as e:
        logger.error(f"Error listing pending line batches: {str(e)}")
        return {
            "statusCode": 500,
            "error": str(e),
            "body": [],
        }