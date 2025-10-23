"""Compaction run processing for COMPACTION_RUN entities."""

from typing import Any, Dict, List, Optional

from receipt_dynamo.constants import ChromaDBCollection

from .s3_operations import process_compaction_runs


def process_compaction_run_messages(
    compaction_runs: List[Any],  # StreamMessage type
    collection: ChromaDBCollection,
    logger,
    metrics=None,
    OBSERVABILITY_AVAILABLE=False,
    lock_manager: Optional[Any] = None,
    get_dynamo_client_func=None
) -> int:
    """Process COMPACTION_RUN messages for delta merging.
    
    Args:
        compaction_runs: List of StreamMessage objects for COMPACTION_RUN entities
        collection: ChromaDBCollection enum value
        logger: Logger instance
        metrics: Metrics collector (optional)
        OBSERVABILITY_AVAILABLE: Whether observability features are available
        lock_manager: Lock manager instance for atomic operations
        get_dynamo_client_func: Function to get DynamoDB client
        
    Returns:
        Total number of vectors merged across all compaction runs
    """
    if not compaction_runs:
        return 0
        
    logger.info(
        "Processing compaction runs",
        count=len(compaction_runs),
        collection=collection.value
    )
    
    return process_compaction_runs(
        compaction_runs=compaction_runs,
        collection=collection,
        logger=logger,
        metrics=metrics,
        OBSERVABILITY_AVAILABLE=OBSERVABILITY_AVAILABLE,
        get_dynamo_client_func=get_dynamo_client_func,
        lock_manager=lock_manager
    )
