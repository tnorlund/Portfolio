"""Word polling handler for OpenAI batch results."""

from typing import Any, Dict

from receipt_label.embedding.common import handle_batch_status
from receipt_label.embedding.word.poll import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_receipt_descriptions,
    mark_batch_complete,
    save_word_embeddings_as_delta,
    write_embedding_results_to_dynamo,
)
from receipt_label.utils import get_client_manager

from .base import BaseLambdaHandler


class WordPollingHandler(BaseLambdaHandler):
    """Handler for polling word embedding batches from OpenAI."""
    
    def __init__(self):
        super().__init__("WordPolling")
        
    def handle(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Poll a word embedding batch and save results as deltas to S3.
        
        Args:
            event: Contains batch_id and metadata
            context: Lambda context
            
        Returns:
            Status of the polling operation
        """
        batch_id = event["batch_id"]
        self.logger.info(f"Polling word embedding batch: {batch_id}")
        
        # Get batch status from OpenAI
        status_info = get_openai_batch_status(batch_id)
        self.logger.info(f"Batch status: {status_info['status']}")
        
        # Handle different batch statuses
        result = handle_batch_status(
            batch_id=batch_id,
            status_info=status_info,
            download_func=download_openai_batch_result,
            process_func=self._process_word_results,
            mark_complete_func=mark_batch_complete,
            batch_type="word_embedding"
        )
        
        return result
    
    def _process_word_results(self, batch_id: str, results: Dict) -> Dict[str, Any]:
        """Process successful word embedding results.
        
        Args:
            batch_id: OpenAI batch ID
            results: Downloaded batch results
            
        Returns:
            Processing status
        """
        # Get receipt descriptions if needed
        receipt_descriptions = get_receipt_descriptions(batch_id)
        
        # Write results to DynamoDB
        dynamo_result = write_embedding_results_to_dynamo(
            batch_id=batch_id,
            results=results,
            descriptions=receipt_descriptions
        )
        
        # Save as ChromaDB delta for later compaction
        delta_result = save_word_embeddings_as_delta(
            batch_id=batch_id,
            results=results,
            metadata=receipt_descriptions
        )
        
        return {
            "dynamo_items_written": dynamo_result["items_written"],
            "delta_saved": delta_result["success"],
            "delta_path": delta_result.get("path")
        }