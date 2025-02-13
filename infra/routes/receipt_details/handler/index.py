import os
import logging
import json
from dynamo import DynamoClient  # type: ignore

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]

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

def handler(event, context):
    # Get the origin from the request headers
    origin = event.get('headers', {}).get('origin', '')
    
    # Define production origins
    production_origins = [
        "https://tylernorlund.com",
        "https://dev.tylernorlund.com"
    ]
    
    # Allow the origin if it's from production or local network
    if origin in production_origins or is_local_network(origin):
        allowed_origin = origin
    else:
        allowed_origin = "http://localhost:3000"  # Default fallback
    
    # Common headers for CORS
    cors_headers = {
        'Access-Control-Allow-Origin': allowed_origin,
        'Access-Control-Allow-Credentials': 'true',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS,HEAD,PATCH',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token,Origin,Accept',
        'Content-Type': 'application/json'
    }
    
    # For OPTIONS requests (preflight)
    if event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': cors_headers,
            'body': ''
        }

    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        client = DynamoClient(dynamodb_table_name)
        query_params = event.get("queryStringParameters") or {}
        
        limit = int(query_params.get("limit", 0))  # Default to 0 if not provided
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
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
