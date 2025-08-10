"""Handler for submitting batches to OpenAI."""

import os
from typing import Any, Dict

from .base import BaseLambdaHandler


class SubmitOpenAIHandler(BaseLambdaHandler):
    """Handler for submitting embedding batches to OpenAI."""
    
    def __init__(self):
        super().__init__("SubmitOpenAI")
        
    def handle(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Submit a batch to OpenAI for embedding.
        
        Args:
            event: Contains batch data or S3 path
            context: Lambda context
            
        Returns:
            OpenAI batch ID and submission status
        """
        # Import here to avoid loading at module level
        from receipt_label.embedding.submit import (
            create_openai_batch,
            store_batch_metadata,
            validate_batch_data,
        )
        
        # Get batch data from event or S3
        batch_data = event.get("batch_data")
        s3_path = event.get("s3_path")
        batch_type = event.get("batch_type", "line")
        
        if s3_path and not batch_data:
            # Load from S3
            from receipt_label.utils import download_from_s3
            batch_data = download_from_s3(s3_path)
        
        if not batch_data:
            raise ValueError("No batch data provided")
        
        # Validate batch
        validation_result = validate_batch_data(batch_data, batch_type)
        if not validation_result["valid"]:
            raise ValueError(f"Invalid batch: {validation_result['errors']}")
        
        self.logger.info(
            f"Submitting {batch_type} batch with {validation_result['item_count']} items"
        )
        
        # Submit to OpenAI
        submission_result = create_openai_batch(
            batch_data=batch_data,
            batch_type=batch_type,
            metadata=event.get("metadata", {})
        )
        
        batch_id = submission_result["batch_id"]
        self.logger.info(f"Created OpenAI batch: {batch_id}")
        
        # Store metadata in DynamoDB
        metadata_result = store_batch_metadata(
            batch_id=batch_id,
            batch_type=batch_type,
            item_count=validation_result["item_count"],
            metadata=submission_result.get("metadata", {})
        )
        
        return {
            "batch_id": batch_id,
            "status": submission_result["status"],
            "item_count": validation_result["item_count"],
            "metadata_stored": metadata_result["success"]
        }