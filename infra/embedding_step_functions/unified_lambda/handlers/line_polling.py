"""Line polling handler for OpenAI batch results."""

from typing import Any, Dict

from .base import BaseLambdaHandler


class LinePollingHandler(BaseLambdaHandler):
    """Handler for polling line embedding batches from OpenAI."""
    
    def __init__(self):
        super().__init__("LinePolling")
        
    def handle(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Poll a line embedding batch and save results.
        
        Args:
            event: Contains batch_id and metadata
            context: Lambda context
            
        Returns:
            Status of the polling operation
        """
        # Import here to avoid loading at module level
        from receipt_label.embedding.line.poll import (
            download_line_batch_result,
            get_line_batch_status,
            mark_line_batch_complete,
            save_line_embeddings_as_delta,
            write_line_embedding_results,
        )
        from receipt_label.embedding.common import handle_batch_status
        
        batch_id = event["batch_id"]
        self.logger.info(f"Polling line embedding batch: {batch_id}")
        
        # Get batch status from OpenAI
        status_info = get_line_batch_status(batch_id)
        self.logger.info(f"Batch status: {status_info['status']}")
        
        # Handle different batch statuses
        result = handle_batch_status(
            batch_id=batch_id,
            status_info=status_info,
            download_func=download_line_batch_result,
            process_func=self._process_line_results,
            mark_complete_func=mark_line_batch_complete,
            batch_type="line_embedding"
        )
        
        return result
    
    def _process_line_results(self, batch_id: str, results: Dict) -> Dict[str, Any]:
        """Process successful line embedding results.
        
        Args:
            batch_id: OpenAI batch ID
            results: Downloaded batch results
            
        Returns:
            Processing status
        """
        from receipt_label.embedding.line.poll import (
            save_line_embeddings_as_delta,
            write_line_embedding_results,
        )
        
        # Write results to DynamoDB
        dynamo_result = write_line_embedding_results(
            batch_id=batch_id,
            results=results
        )
        
        # Save as ChromaDB delta
        delta_result = save_line_embeddings_as_delta(
            batch_id=batch_id,
            results=results
        )
        
        return {
            "dynamo_items_written": dynamo_result["items_written"],
            "delta_saved": delta_result["success"],
            "delta_path": delta_result.get("path")
        }