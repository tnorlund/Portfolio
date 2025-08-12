"""Simple Lambda handler for listing pending embedding batches.

This is a lightweight, zip-based Lambda function that only depends on
receipt_label and boto3. No container overhead needed.
"""

import logging
from typing import Any, Dict, List
from receipt_label.embedding.line import list_pending_line_embedding_batches

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> List[Dict[str, str]]:
    """List pending line embedding batches from DynamoDB.
    
    Args:
        event: Lambda event (unused in current implementation)
        context: Lambda context (unused)
        
    Returns:
        List of pending batches with batch_id and openai_batch_id
        
    Raises:
        RuntimeError: If there's an error accessing DynamoDB
    """
    logger.info("Starting list_pending_line_batches handler")
    
    try:
        # Get pending batches from DynamoDB
        pending_batches = list_pending_line_embedding_batches()
        
        logger.info("Found %d pending line embedding batches", len(pending_batches))
        
        # Format response for Step Function
        batch_list = [
            {
                "batch_id": batch.batch_id,
                "openai_batch_id": batch.openai_batch_id,
            }
            for batch in pending_batches
        ]
        
        return batch_list
        
    except AttributeError as e:
        logger.error("Client manager configuration error: %s", str(e))
        raise RuntimeError(f"Configuration error: {str(e)}") from e
        
    except KeyError as e:
        logger.error("Missing expected field in DynamoDB response: %s", str(e))
        raise RuntimeError(f"Data format error: {str(e)}") from e
        
    except Exception as e:
        logger.error("Unexpected error listing pending line batches: %s", str(e))
        raise RuntimeError(f"Internal error: {str(e)}") from e