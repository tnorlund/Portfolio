"""Submit to OpenAI handler - unified container version."""

from typing import Any, Dict

from receipt_label.embedding.line import (
    add_batch_summary,
    create_batch_summary,
    deserialize_receipt_lines,
    download_serialized_lines,
    format_line_context_embedding,
    generate_batch_id,
    submit_openai_batch,
    update_line_embedding_status,
    upload_to_openai,
    write_ndjson,
)

from .base import BaseLambdaHandler


class SubmitOpenAIHandler(BaseLambdaHandler):
    """Handler for submitting line embedding batches to OpenAI.
    
    This is a direct port of the original submit_to_openai_lambda/handler.py
    to work within the unified container architecture.
    
    This Lambda takes prepared batches and submits them to OpenAI's Batch API
    for embedding generation.
    """
    
    def __init__(self):
        super().__init__("SubmitOpenAI")
        
    def handle(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Submit a line embedding batch to OpenAI's Batch API.
        
        Args:
            event: Contains s3_key, s3_bucket, image_id, receipt_id
            context: Lambda context (unused)
            
        Returns:
            dict: Contains statusCode and batch_id
        """
        self.logger.info("Starting submit_to_openai handler")
        
        try:
            s3_key = event["s3_key"]
            s3_bucket = event["s3_bucket"]
            # These are passed in event but not used in current implementation
            # image_id = event["image_id"]
            # receipt_id = int(event["receipt_id"])
            batch_id = generate_batch_id()

            # Download the serialized lines
            filepath = download_serialized_lines(s3_bucket=s3_bucket, s3_key=s3_key)
            self.logger.info("Downloaded file to %s", filepath)

            lines = deserialize_receipt_lines(filepath)
            self.logger.info("Deserialized %d lines", len(lines))

            # Develop the embedding input
            formatted = format_line_context_embedding(lines)
            self.logger.info("Formatted %d lines", len(formatted))

            # Write the formatted lines to a file
            input_file = write_ndjson(batch_id, formatted)
            self.logger.info("Wrote input file to %s", input_file)

            # Upload the input file to OpenAI
            openai_file = upload_to_openai(input_file)
            self.logger.info("Uploaded input file to OpenAI")

            # Submit the OpenAI batch
            openai_batch = submit_openai_batch(openai_file.id)
            self.logger.info("Submitted OpenAI batch %s", openai_batch.id)

            # Create a batch summary
            batch_summary = create_batch_summary(batch_id, openai_batch.id, input_file)
            self.logger.info("Created batch summary with ID %s", batch_summary.batch_id)

            # Update the line embedding status
            update_line_embedding_status(lines)
            self.logger.info("Updated line embedding status")

            # Add the batch summary to the database
            add_batch_summary(batch_summary)
            self.logger.info("Added batch summary with ID %s", batch_summary.batch_id)

            return {"batch_id": batch_id}
            
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.logger.error("Error submitting to OpenAI: %s", str(e))
            # Re-raise for proper Step Function error handling
            raise RuntimeError(f"Error submitting to OpenAI: {str(e)}") from e