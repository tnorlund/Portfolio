"""Simple Lambda handler for listing pending embedding batches.

This is a lightweight, zip-based Lambda function that only depends on
receipt_label and boto3. No container overhead needed.
"""

import logging
from typing import Any, Dict, List
from receipt_label.embedding.line import list_pending_line_embedding_batches
from receipt_label.embedding.word import (
    list_pending_embedding_batches as list_pending_word_embedding_batches,
)

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(
    event: Dict[str, Any], context: Any
) -> List[Dict[str, str]]:
    """List pending embedding batches from DynamoDB.

    Supports both line and word embedding batches based on the
    batch_type parameter in the event.

    Args:
        event: Lambda event with optional 'batch_type' field ('line' or 'word')
        context: Lambda context (unused)

    Returns:
        List of pending batches with batch_id and openai_batch_id

    Raises:
        RuntimeError: If there's an error accessing DynamoDB
    """
    # Determine batch type from event (default to 'line' for backward
    # compatibility)
    batch_type = event.get("batch_type", "line")
    logger.info(
        "Starting list_pending_batches handler for batch_type: %s", batch_type
    )

    try:
        # Get pending batches from DynamoDB based on type
        if batch_type == "word":
            pending_batches = list_pending_word_embedding_batches()
        else:
            pending_batches = list_pending_line_embedding_batches()

        logger.info(
            "Found %d pending %s embedding batches",
            len(pending_batches),
            batch_type,
        )

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
        logger.error(
            "Unexpected error listing pending line batches: %s", str(e)
        )
        raise RuntimeError(f"Internal error: {str(e)}") from e
