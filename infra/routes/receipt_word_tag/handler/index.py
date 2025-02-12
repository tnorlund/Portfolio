"""
This handler is used to get and update receipt word tags.


POST:
    - Update receipt word tag
    - Expects
        - selected_tag: Tag entity as a dictionary
        - selected_word: Word entity as a dictionary
"""

import os
import logging
import json
from dynamo import DynamoClient, ReceiptWord, ReceiptWordTag, Word, WordTag
import random
from datetime import UTC, datetime


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event, context):
    # Get the origin from the request headers
    origin = event.get('headers', {}).get('origin', '')
    allowed_origins = [
        "http://localhost:3000",
        "https://tylernorlund.com",
        "https://dev.tylernorlund.com"
    ]
    
    # Check if the origin is allowed
    if origin not in allowed_origins:
        origin = allowed_origins[0]  # Default to first allowed origin
    
    # Common headers for CORS
    cors_headers = {
        'Access-Control-Allow-Origin': origin,
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
        return {"statusCode": 405, "body": "Method not allowed"}
    elif http_method == "POST":
        try:
            client = DynamoClient(dynamodb_table_name)
            query_params = event.get("queryStringParameters") or {}

            # Parse the request body
            body = json.loads(event.get("body", "{}"))
            logger.info("Received POST body: %s", body)

            # Validate request structure
            if not isinstance(body, dict):
                return {
                    "statusCode": 400,
                    "headers": cors_headers,
                    "body": json.dumps({"error": "Request body must be an object"})
                }

            if "selected_tag" not in body or "selected_word" not in body or "action" not in body:
                return {
                    "statusCode": 400,
                    "headers": cors_headers,
                    "body": json.dumps({"error": "Request must include 'selected_tag', 'selected_word', and 'action'"})
                }

            selected_tag = body["selected_tag"]
            selected_word = body["selected_word"]
            action = body["action"]

            # Convert to receipt entities
            receipt_word_tag = ReceiptWordTag(**selected_tag)
            receipt_word = ReceiptWord(**selected_word)

            # Create word entities from receipt entities
            word_tag = WordTag(
                image_id=receipt_word_tag.image_id,
                line_id=receipt_word_tag.line_id,
                word_id=receipt_word_tag.word_id,
                tag=receipt_word_tag.tag,
                timestamp_added=receipt_word_tag.timestamp_added,
                validated=receipt_word_tag.validated,
                timestamp_validated=receipt_word_tag.timestamp_validated,
                gpt_confidence=receipt_word_tag.gpt_confidence,
                flag=receipt_word_tag.flag,
                revised_tag=receipt_word_tag.revised_tag,
                human_validated=receipt_word_tag.human_validated,
                timestamp_human_validated=receipt_word_tag.timestamp_human_validated
            )

            # Remove receipt_id before creating Word entity
            word_params = {k: v for k, v in selected_word.items() if k != 'receipt_id'}
            word = Word(**word_params)

            # Get current timestamp
            current_time = datetime.now(UTC)
            timestamp_str = current_time.isoformat()

            if action == "validate":
                # Toggle validation status
                new_validation_status = not receipt_word_tag.human_validated
                receipt_word_tag.human_validated = new_validation_status
                word_tag.human_validated = new_validation_status
                
                if new_validation_status:
                    receipt_word_tag.timestamp_human_validated = timestamp_str
                    word_tag.timestamp_human_validated = timestamp_str
                else:
                    receipt_word_tag.timestamp_human_validated = None
                    word_tag.timestamp_human_validated = None
                    # Remove tag from word entities when invalidating
                    word.tags = [t for t in word.tags if t != receipt_word_tag.tag]
                    receipt_word.tags = word.tags.copy()

            elif action == "change_tag":
                if "new_tag" not in body:
                    return {
                        "statusCode": 400,
                        "headers": cors_headers,
                        "body": json.dumps({"error": "new_tag is required for change_tag action"})
                    }
                
                new_tag_type = body["new_tag"]
                old_tag_type = receipt_word_tag.tag

                # Update tag while preserving other attributes
                receipt_word_tag.tag = new_tag_type
                word_tag.tag = new_tag_type
                
                # Update validation status
                receipt_word_tag.human_validated = True
                word_tag.human_validated = True
                receipt_word_tag.timestamp_human_validated = timestamp_str
                word_tag.timestamp_human_validated = timestamp_str
                
                # Keep existing attributes
                receipt_word_tag.validated = True  # Since human validated
                word_tag.validated = True
                receipt_word_tag.timestamp_validated = timestamp_str
                word_tag.timestamp_validated = timestamp_str
                
                # Preserve these attributes from the original tag
                # (they're already preserved since we're modifying the existing object)
                # - gpt_confidence
                # - flag
                # - revised_tag
                # - timestamp_added

                # Update word's tag list
                current_tags = [t for t in word.tags if t != old_tag_type]
                word.tags = list(set(current_tags + [new_tag_type]))
                receipt_word.tags = word.tags.copy()

            elif action == "add_tag":
                if "new_tag" not in body:
                    return {
                        "statusCode": 400,
                        "headers": cors_headers,
                        "body": json.dumps({"error": "new_tag is required for add_tag action"})
                    }
                
                new_tag_type = body["new_tag"]
                
                # Add new tag type
                receipt_word_tag.tag = new_tag_type
                word_tag.tag = new_tag_type
                receipt_word_tag.human_validated = True
                word_tag.human_validated = True
                receipt_word_tag.timestamp_human_validated = timestamp_str
                word_tag.timestamp_human_validated = timestamp_str

                # Update word tags (ensure no duplicates)
                word.tags = list(set(word.tags + [new_tag_type]))
                receipt_word.tags = word.tags.copy()

            else:
                return {
                    "statusCode": 400,
                    "headers": cors_headers,
                    "body": json.dumps({"error": f"Invalid action: {action}"})
                }

            # Persist all changes
            client.updateWordTag(word_tag)
            client.updateReceiptWordTag(receipt_word_tag)
            client.updateWord(word)
            client.updateReceiptWord(receipt_word)

            # Log the response we're sending back
            response = {
                'updated': {
                    'word': dict(word),
                    'word_tag': dict(word_tag),
                    'receipt_word': dict(receipt_word),
                    'receipt_word_tag': dict(receipt_word_tag)
                }
            }
            logger.info("Sending response: %s", response)

            return {
                'statusCode': 200,
                'headers': cors_headers,
                'body': json.dumps(response)
            }

        except Exception as e:
            logger.error(f"Error processing request: {str(e)}", exc_info=True)
            return {
                'statusCode': 500,
                'headers': cors_headers,
                'body': json.dumps({
                    'error': f'Internal server error: {str(e)}'
                })
            }

    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
