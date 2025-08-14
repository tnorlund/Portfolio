"""Find unembedded lines handler - unified container version."""

import os
from typing import Any, Dict

from receipt_label.embedding.line import (
    chunk_into_line_embedding_batches,
    list_receipt_lines_with_no_embeddings,
    serialize_receipt_lines,
    upload_serialized_lines,
)

from .base import BaseLambdaHandler


class FindUnembeddedHandler(BaseLambdaHandler):
    """Handler for finding receipt lines that need embeddings.

    This is a direct port of the original find_unembedded_lines_lambda/handler.py
    to work within the unified container architecture.

    This Lambda queries DynamoDB for lines with embedding_status=NONE and
    prepares them for batch submission to OpenAI.
    """

    def __init__(self):
        super().__init__("FindUnembedded")
        self.bucket = os.environ["S3_BUCKET"]

    def handle(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Find receipt lines without embeddings and prepare batches for submission.

        Args:
            event: Lambda event (unused in current implementation)
            context: Lambda context (unused)

        Returns:
            dict: Contains statusCode and batches ready for processing
        """
        self.logger.info("Starting find_unembedded_lines handler")

        try:
            lines_without_embeddings = list_receipt_lines_with_no_embeddings()
            self.logger.info(
                "Found %d lines without embeddings",
                len(lines_without_embeddings),
            )

            batches = chunk_into_line_embedding_batches(
                lines_without_embeddings
            )
            self.logger.info("Chunked into %d batches", len(batches))

            uploaded = upload_serialized_lines(
                serialize_receipt_lines(batches), self.bucket
            )
            self.logger.info("Uploaded %d files", len(uploaded))

            cleaned = [
                {
                    "s3_key": e["s3_key"],
                    "s3_bucket": e["s3_bucket"],
                    "image_id": e["image_id"],
                    "receipt_id": e["receipt_id"],
                }
                for e in uploaded
            ]

            return {"batches": cleaned}

        except Exception as e:  # pylint: disable=broad-exception-caught
            self.logger.error("Error finding unembedded lines: %s", str(e))
            # Re-raise for proper Step Function error handling
            raise RuntimeError(
                f"Error finding unembedded lines: {str(e)}"
            ) from e
