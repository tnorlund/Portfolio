"""Compaction handler for ChromaDB deltas."""

import os
from typing import Any, Dict

from .base import BaseLambdaHandler


class CompactionHandler(BaseLambdaHandler):
    """Handler for compacting ChromaDB deltas into final storage."""
    
    def __init__(self):
        super().__init__("Compaction")
        
    def handle(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Compact multiple ChromaDB deltas into a single collection.
        
        Args:
            event: Contains delta paths and target collection info
            context: Lambda context
            
        Returns:
            Compaction status and metrics
        """
        # Import here to avoid loading heavy dependencies at module level
        from receipt_label.embedding.compaction import (
            compact_deltas,
            cleanup_intermediate_files,
            update_compaction_status,
        )
        
        delta_paths = event.get("delta_paths", [])
        target_collection = event.get("target_collection")
        chunk_size = int(os.environ.get("CHUNK_SIZE", "10"))
        
        self.logger.info(f"Compacting {len(delta_paths)} deltas into {target_collection}")
        
        try:
            # Perform compaction
            result = compact_deltas(
                delta_paths=delta_paths,
                target_collection=target_collection,
                chunk_size=chunk_size,
            )
            
            # Cleanup if configured
            if os.environ.get("DELETE_INTERMEDIATE_CHUNKS", "true").lower() == "true":
                cleanup_result = cleanup_intermediate_files(
                    paths=result.get("intermediate_paths", [])
                )
                result["cleanup"] = cleanup_result
            
            # Update status in DynamoDB
            status_result = update_compaction_status(
                collection=target_collection,
                status="completed",
                metrics=result.get("metrics", {})
            )
            result["status_updated"] = status_result
            
            return result
            
        except Exception as e:
            self.logger.error(f"Compaction failed: {str(e)}")
            # Update status as failed
            update_compaction_status(
                collection=target_collection,
                status="failed",
                error=str(e)
            )
            raise