"""Handler for finding unembedded lines."""

import os
from typing import Any, Dict

from .base import BaseLambdaHandler


class FindUnembeddedHandler(BaseLambdaHandler):
    """Handler for finding lines that need embedding."""
    
    def __init__(self):
        super().__init__("FindUnembedded")
        
    def handle(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Find lines without embeddings and prepare them for processing.
        
        Args:
            event: Contains scan parameters
            context: Lambda context
            
        Returns:
            List of unembedded lines and batch info
        """
        # Import here to avoid loading at module level
        from receipt_dynamo.entities import Line
        from receipt_label.embedding.utils import (
            find_unembedded_items,
            prepare_embedding_batch,
            upload_batch_to_s3,
        )
        
        # Get scan parameters
        limit = event.get("limit", 1000)
        start_key = event.get("start_key")
        receipt_filter = event.get("receipt_filter")
        
        self.logger.info(f"Scanning for unembedded lines (limit: {limit})")
        
        # Find unembedded lines
        scan_result = find_unembedded_items(
            entity_type=Line,
            limit=limit,
            start_key=start_key,
            filter_expression=receipt_filter
        )
        
        unembedded_lines = scan_result["items"]
        next_key = scan_result.get("last_evaluated_key")
        
        self.logger.info(f"Found {len(unembedded_lines)} unembedded lines")
        
        if not unembedded_lines:
            return {
                "found": 0,
                "next_key": next_key,
                "batch_created": False
            }
        
        # Prepare batch for OpenAI
        batch_data = prepare_embedding_batch(
            items=unembedded_lines,
            batch_type="line"
        )
        
        # Upload to S3 if configured
        s3_bucket = os.environ.get("S3_BUCKET")
        if s3_bucket and batch_data:
            s3_result = upload_batch_to_s3(
                batch_data=batch_data,
                bucket=s3_bucket,
                prefix="line-embeddings/batches"
            )
            batch_data["s3_path"] = s3_result["path"]
        
        return {
            "found": len(unembedded_lines),
            "next_key": next_key,
            "batch_created": True,
            "batch_info": batch_data
        }