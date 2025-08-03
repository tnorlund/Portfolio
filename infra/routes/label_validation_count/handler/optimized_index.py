"""
Optimized Lambda handler for label validation counts.

This implementation follows AWS Lambda best practices for performance:
1. Connection reuse via global client initialization
2. Minimal cold start overhead
3. Efficient batch operations
4. Proper error handling and logging
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.dynamo_client import DynamoClient

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Constants
CORE_LABELS = [
    "MERCHANT_NAME",
    "STORE_HOURS",
    "PHONE_NUMBER",
    "WEBSITE",
    "LOYALTY_ID",
    "ADDRESS_LINE",
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "DISCOUNT",
    "PRODUCT_NAME",
    "QUANTITY",
    "UNIT_PRICE",
    "LINE_TOTAL",
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",
]

# Initialize client outside handler for connection reuse
# This is THE most important optimization for Lambda
_dynamo_client: Optional[DynamoClient] = None


def get_dynamo_client() -> DynamoClient:
    """Get or create DynamoDB client with lazy initialization."""
    global _dynamo_client
    if _dynamo_client is None:
        _dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    return _dynamo_client


def handler(event: Dict, context: Dict) -> Dict:
    """
    AWS Lambda handler for label validation count API.
    
    Optimizations:
    1. Client reuse across warm invocations
    2. Batch operations for cache retrieval
    3. Parallel processing for real-time counts
    4. Efficient response serialization
    """
    # Log only essential information to reduce CloudWatch costs
    logger.info("method=%s", event["requestContext"]["http"]["method"])
    
    http_method = event["requestContext"]["http"]["method"].upper()
    
    if http_method == "GET":
        try:
            # Try cache first (single batch query)
            core_label_counts, missing_labels = get_cached_label_counts()
            
            # Fetch missing labels in parallel
            if missing_labels:
                logger.info("cache_miss_count=%d", len(missing_labels))
                
                # Use ThreadPoolExecutor for I/O bound operations
                # Limit workers to avoid overwhelming DynamoDB
                with ThreadPoolExecutor(max_workers=min(5, len(missing_labels))) as executor:
                    future_to_label = {
                        executor.submit(fetch_label_counts, label): label
                        for label in missing_labels
                    }
                    
                    for future in as_completed(future_to_label):
                        label, counts = future.result()
                        core_label_counts[label] = counts
            else:
                logger.info("cache_hit=complete")
            
            # Sort for consistent output
            core_label_counts = dict(sorted(core_label_counts.items()))
            
            return {
                "statusCode": 200,
                "body": json.dumps(core_label_counts),
                "headers": {
                    "Content-Type": "application/json",
                    # Enable caching at API Gateway level
                    "Cache-Control": "public, max-age=60"
                }
            }
            
        except Exception as exc:
            logger.error("error=%s", str(exc), exc_info=True)
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Internal server error"}),
                "headers": {"Content-Type": "application/json"}
            }
    
    return {
        "statusCode": 405,
        "body": json.dumps({"error": f"Method {http_method} not allowed"}),
        "headers": {"Content-Type": "application/json"}
    }


def get_cached_label_counts() -> Tuple[Dict[str, Dict[str, int]], List[str]]:
    """
    Retrieve label counts from cache using efficient batch query.
    
    Returns:
        Tuple of (cached_counts, missing_labels)
    """
    cached_counts = {}
    missing_labels = set(CORE_LABELS)  # Use set for O(1) lookups
    
    try:
        dynamo_client = get_dynamo_client()
        cached_entries, _ = dynamo_client.list_label_count_caches()
        
        # Process cache entries
        current_time = int(time.time())
        cached_by_label = {entry.label: entry for entry in cached_entries}
        
        for label in CORE_LABELS:
            if label in cached_by_label:
                entry = cached_by_label[label]
                
                # Skip expired entries
                if entry.time_to_live and entry.time_to_live <= current_time:
                    continue
                
                cached_counts[label] = {
                    "VALID": entry.valid_count,
                    "INVALID": entry.invalid_count,
                    "PENDING": entry.pending_count,
                    "NEEDS_REVIEW": entry.needs_review_count,
                    "NONE": entry.none_count,
                }
                missing_labels.discard(label)
        
        logger.info(
            "cache_stats hits=%d misses=%d",
            len(cached_counts),
            len(missing_labels)
        )
        
    except Exception as exc:
        logger.error("cache_error=%s", str(exc))
        # Fall back to fetching all labels
        missing_labels = set(CORE_LABELS)
        cached_counts = {}
    
    return cached_counts, list(missing_labels)


def fetch_label_counts(label: str) -> Tuple[str, Dict[str, int]]:
    """
    Fetch real-time validation counts for a specific label.
    
    Uses pagination to handle large result sets efficiently.
    """
    dynamo_client = get_dynamo_client()
    receipt_word_labels = []
    last_evaluated_key = None
    
    # Paginate through results
    while True:
        page_labels, last_evaluated_key = (
            dynamo_client.get_receipt_word_labels_by_label(
                label=label,
                limit=1000,  # Max items per query
                last_evaluated_key=last_evaluated_key,
            )
        )
        receipt_word_labels.extend(page_labels)
        
        if last_evaluated_key is None:
            break
    
    # Count by status
    label_counts = {status.value: 0 for status in ValidationStatus}
    for rwl in receipt_word_labels:
        label_counts[rwl.validation_status] += 1
    
    return label, label_counts


# Additional optimizations for production:

# 1. Use AWS X-Ray for tracing (add to requirements.txt):
# from aws_xray_sdk.core import xray_recorder
# @xray_recorder.capture('fetch_label_counts')

# 2. Use connection pooling for DynamoDB:
# In DynamoClient, configure:
# config = Config(
#     region_name='us-east-1',
#     max_pool_connections=50
# )

# 3. Consider caching at multiple levels:
# - API Gateway caching (already enabled with Cache-Control)
# - Lambda memory caching (for frequently accessed data)
# - DynamoDB DAX for microsecond latency

# 4. For extremely high performance, consider:
# - Lambda@Edge for geographic distribution
# - Step Functions for complex orchestration
# - EventBridge for async processing