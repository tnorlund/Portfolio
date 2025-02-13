import os
import logging
import json
from dynamo import DynamoClient  # type: ignore
from typing import Dict, Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]
allowed_origins = os.environ['ALLOWED_ORIGINS'].split(',')

def convert_payload_to_dict(payload):
    """Convert all objects in the receipt details payload to dictionaries."""
    result = {}
    for key, detail in payload.items():
        result[key] = {
            "receipt": dict(detail["receipt"]),
            "words": [dict(word) for word in detail["words"]],
            "word_tags": [dict(tag) for tag in detail["word_tags"]]
        }
    return result

def is_local_network(origin):
    """Check if origin is from local network"""
    if origin:
        # Check for localhost
        if origin.startswith('http://localhost:'):
            return True
        # Check for local IP addresses (192.168.*.*)
        if origin.startswith('http://192.168.'):
            return True
        # Add specific iPad address
        if origin == "http://192.168.4.117:3000":
            return True
    return False

def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # Get the origin from the request headers
    headers = event.get('headers', {})
    origin = headers.get('origin') or headers.get('Origin')

    # Default CORS headers
    cors_headers = {
        'Access-Control-Allow-Methods': 'GET,OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Credentials': 'true',
        'Content-Type': 'application/json'
    }

    # Add Access-Control-Allow-Origin if origin is allowed
    if origin in allowed_origins:
        cors_headers['Access-Control-Allow-Origin'] = origin

    # Handle OPTIONS request (preflight)
    if event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': cors_headers,
            'body': ''
        }

    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        try:
            client = DynamoClient(dynamodb_table_name)
            query_params = event.get("queryStringParameters") or {}
            
            limit = int(query_params.get("limit", 0))
            last_evaluated_key = None
            
            if "last_evaluated_key" in query_params:
                last_evaluated_key = json.loads(query_params["last_evaluated_key"])
            
            payload, last_evaluated_key = client.listReceiptDetails(
                limit if limit > 0 else None,
                last_evaluated_key
            )
            
            return {
                "statusCode": 200,
                "headers": cors_headers,
                "body": json.dumps({
                    "payload": convert_payload_to_dict(payload),
                    "last_evaluated_key": last_evaluated_key,
                }),
            }
        except Exception as e:
            logger.error(f"Error processing request: {str(e)}")
            return {
                "statusCode": 500,
                "headers": cors_headers,
                "body": json.dumps({"error": "Internal server error"})
            }
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
