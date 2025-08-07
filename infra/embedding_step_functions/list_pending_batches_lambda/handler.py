"""
Lambda handler for listing pending line embedding batches.

This Lambda queries DynamoDB for line embedding batches that are ready
for polling.
"""

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


def poll_handler(_event, _context):
    """
    List pending line embedding batches from DynamoDB.

    Returns:
        dict: Contains statusCode and body with list of pending batches
    """
    logger.info("Starting list_pending_line_batches_for_polling handler")

    try:
        # Get pending batches
        pending_batches = list_pending_line_embedding_batches()

        logger.info(
            "Found %d pending line embedding batches", len(pending_batches)
        )

        # Format response for step function
        batch_list = []
        for batch in pending_batches[0:5]:
            # TODO: Remove this once we have a proper polling mechanism  # pylint: disable=fixme
            batch_list.append(
                {
                    "batch_id": batch.batch_id,
                    "openai_batch_id": batch.openai_batch_id,
                }
            )

        # Return the list directly for Step Functions
        # Step Functions doesn't need HTTP-style responses
        return batch_list

    except AttributeError as e:
        logger.error("Client manager configuration error: %s", str(e))
        # For Step Functions, throwing an exception is better than returning an error object
        # This will cause the state to fail with proper error handling
        raise RuntimeError(f"Configuration error: {str(e)}") from e
    except KeyError as e:
        logger.error("Missing expected field in DynamoDB response: %s", str(e))
        raise RuntimeError(f"Data format error: {str(e)}") from e
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Catch-all for other exceptions (network errors, etc.)
        logger.error(
            "Unexpected error listing pending line batches: %s", str(e)
        )
        raise RuntimeError(f"Internal error: {str(e)}") from e
