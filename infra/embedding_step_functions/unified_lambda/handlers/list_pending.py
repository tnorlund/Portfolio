"""Handler for listing pending OpenAI batches."""

from typing import Any, Dict

from .base import BaseLambdaHandler


class ListPendingHandler(BaseLambdaHandler):
    """Handler for listing pending OpenAI batches."""
    
    def __init__(self):
        super().__init__("ListPending")
        
    def handle(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """List all pending OpenAI batches.
        
        Args:
            event: Contains filter parameters
            context: Lambda context
            
        Returns:
            List of pending batches with their status
        """
        # Import here to avoid loading at module level
        from receipt_label.embedding.monitoring import (
            get_pending_batches,
            get_batch_metrics,
            check_batch_health,
        )
        
        # Get filter parameters
        batch_type = event.get("batch_type")  # "word", "line", or None for all
        max_age_hours = event.get("max_age_hours", 24)
        include_metrics = event.get("include_metrics", True)
        
        self.logger.info(f"Listing pending batches (type: {batch_type or 'all'})")
        
        # Get pending batches from DynamoDB
        pending_batches = get_pending_batches(
            batch_type=batch_type,
            max_age_hours=max_age_hours
        )
        
        self.logger.info(f"Found {len(pending_batches)} pending batches")
        
        # Enrich with metrics if requested
        if include_metrics and pending_batches:
            for batch in pending_batches:
                batch["metrics"] = get_batch_metrics(batch["batch_id"])
                batch["health"] = check_batch_health(batch)
        
        # Categorize by status
        categorized = {
            "validating": [],
            "in_progress": [],
            "finalizing": [],
            "stalled": [],
            "unknown": []
        }
        
        for batch in pending_batches:
            status = batch.get("status", "unknown")
            health = batch.get("health", {})
            
            if health.get("is_stalled"):
                categorized["stalled"].append(batch)
            elif status in categorized:
                categorized[status].append(batch)
            else:
                categorized["unknown"].append(batch)
        
        return {
            "total_pending": len(pending_batches),
            "batches": pending_batches,
            "categorized": categorized,
            "summary": {
                "validating": len(categorized["validating"]),
                "in_progress": len(categorized["in_progress"]),
                "finalizing": len(categorized["finalizing"]),
                "stalled": len(categorized["stalled"]),
                "unknown": len(categorized["unknown"])
            }
        }