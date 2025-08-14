"""List pending batches handler - unified container version."""

from typing import Any, Dict, List

from receipt_label.embedding.line import list_pending_line_embedding_batches

from .base import BaseLambdaHandler


class ListPendingHandler(BaseLambdaHandler):
    """Handler for listing pending line embedding batches.

    This is a direct port of the original list_pending_batches_lambda/handler.py
    to work within the unified container architecture.

    This Lambda queries DynamoDB for line embedding batches that are ready
    for polling.
    """

    def __init__(self):
        super().__init__("ListPending")

    def handle(
        self, event: Dict[str, Any], context: Any
    ) -> List[Dict[str, str]]:
        """List pending line embedding batches from DynamoDB.

        Args:
            event: Lambda event (unused in current implementation)
            context: Lambda context (unused)

        Returns:
            List of pending batches with batch_id and openai_batch_id
        """
        self.logger.info(
            "Starting list_pending_line_batches_for_polling handler"
        )

        try:
            # Get pending batches
            pending_batches = list_pending_line_embedding_batches()

            self.logger.info(
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
            self.logger.error("Client manager configuration error: %s", str(e))
            # For Step Functions, throwing an exception is better than returning an error object
            # This will cause the state to fail with proper error handling
            raise RuntimeError(f"Configuration error: {str(e)}") from e
        except KeyError as e:
            self.logger.error(
                "Missing expected field in DynamoDB response: %s", str(e)
            )
            raise RuntimeError(f"Data format error: {str(e)}") from e
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Catch-all for other exceptions (network errors, etc.)
            self.logger.error(
                "Unexpected error listing pending line batches: %s", str(e)
            )
            raise RuntimeError(f"Internal error: {str(e)}") from e
