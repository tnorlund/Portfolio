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

def get_tag_validation_stats(dynamo_client: DynamoClient) -> Dict[str, Dict[str, int]]:
    """
    Gets validation statistics for each tag in tags_wanted list.
    
    Returns:
        Dict[str, Dict[str, int]]: A dictionary where:
            - key: tag name
            - value: dictionary of counts for different validation states
    """
    tags_wanted = ["line_item_name", "address", "line_item_price", "store_name", 
                  "date", "time", "total_amount", "phone_number", "taxes"]

    # Get all receipt word tags
    receipt_tags, _ = dynamo_client.listReceiptWordTags()
    
    # Initialize counters for specified tags only
    tag_stats = {tag: {
        "validated_true_human_true": 0,    # validated=True, human_validated=True
        "validated_true_human_false": 0,   # validated=True, human_validated=False
        "validated_false_human_true": 0,   # validated=False, human_validated=True
        "validated_false_human_false": 0,  # validated=False, human_validated=False
        "validated_none_human_true": 0,    # validated=None, human_validated=True
        "validated_none_human_false": 0,   # validated=None, human_validated=False
    } for tag in tags_wanted}
    
    # Count validation states for each tag
    for tag in receipt_tags:
        tag_name = tag.tag.lower()  # Normalize tag names to lowercase
        if tag_name in tags_wanted:
            validated = getattr(tag, 'validated', None)
            human_validated = getattr(tag, 'human_validated', False)
            
            if validated is True:
                if human_validated:
                    tag_stats[tag_name]["validated_true_human_true"] += 1
                else:
                    tag_stats[tag_name]["validated_true_human_false"] += 1
            elif validated is False:
                if human_validated:
                    tag_stats[tag_name]["validated_false_human_true"] += 1
                else:
                    tag_stats[tag_name]["validated_false_human_false"] += 1
            elif validated is None:
                if human_validated:
                    tag_stats[tag_name]["validated_none_human_true"] += 1
                else:
                    tag_stats[tag_name]["validated_none_human_false"] += 1
    
    return tag_stats

def handler(event, _):
    """
    Handles API Gateway requests for tag validation statistics.
    
    Returns:
        dict: API Gateway response containing tag validation statistics:
        {
            "tag_stats": {
                "date": {
                    "validated_true_human_true": 10,
                    "validated_true_human_false": 20,
                    "validated_false_human_true": 5,
                    "validated_false_human_false": 15,
                    "validated_none_human_true": 3,
                    "total": 53
                },
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
        for tag, counts in sorted(stats.items()):
            formatted_stats[tag] = {
                **counts,  # Include all the detailed counts
                "total": sum(counts.values())  # Add total
            }
        
        response_body = {
            "tag_stats": formatted_stats
        }
        
        return {
            "statusCode": 200,
            "body": json.dumps(response_body)
        }
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}