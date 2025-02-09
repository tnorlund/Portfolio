import os
import logging
import json
from collections import defaultdict
from typing import Dict, Tuple

from dynamo import DynamoClient  # type: ignore

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]

def get_tag_validation_stats(dynamo_client: DynamoClient) -> Dict[str, Tuple[int, int]]:
    """
    Gets validation statistics for each unique tag in the database.
    
    Returns:
        Dict[str, Tuple[int, int]]: A dictionary where:
            - key: tag name
            - value: tuple of (valid_count, invalid_count)
    """
    # Get all receipt word tags
    receipt_tags, _ = dynamo_client.listReceiptWordTags()
    
    # Initialize counters for each tag
    tag_stats = defaultdict(lambda: [0, 0])  # [valid_count, invalid_count]
    
    # Count valid/invalid for each tag
    for tag in receipt_tags:
        if hasattr(tag, 'validated'):  # Only count tags that have been validated
            tag_name = tag.tag.lower()  # Normalize tag names to lowercase
            if tag.validated:
                tag_stats[tag_name][0] += 1  # Increment valid count
            else:
                tag_stats[tag_name][1] += 1  # Increment invalid count
    
    # Convert defaultdict to regular dict and lists to tuples
    return {tag: tuple(counts) for tag, counts in tag_stats.items()}

def handler(event, _):
    """
    Handles API Gateway requests for tag validation statistics.
    
    Returns:
        dict: API Gateway response containing tag validation statistics:
        {
            "tag_stats": {
                "total": {"valid": 45, "invalid": 3, "total": 48},
                "date": {"valid": 32, "invalid": 1, "total": 33},
                ...
            }
        }
    """
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        dynamo_client = DynamoClient(dynamodb_table_name)
        stats = get_tag_validation_stats(dynamo_client)
        
        # Format the statistics for the response
        formatted_stats = {}
        for tag, (valid, invalid) in sorted(stats.items()):
            total = valid + invalid
            formatted_stats[tag] = {
                "valid": valid,
                "invalid": invalid,
                "total": total
            }
        
        response_body = {
            "tag_stats": formatted_stats
        }
        
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"  # Enable CORS
            },
            "body": json.dumps(response_body)
        }
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}