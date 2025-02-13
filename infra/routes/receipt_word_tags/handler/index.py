"""
This handler is used to get and update receipt word tags.

GET:
    - Get receipt word tags by tag.
    - Paginated.

POST:
    - Update receipt word tags.
    - Expects
        - selected_tag: string
        - selected_words: list of objects with the following keys:
            - word: Word entity as a dictionary
            - tags: list of tag entities as dictionaries
"""

import os
import logging
import json
from dynamo import DynamoClient, ReceiptWord, ReceiptWordTag, Word, WordTag
import random
from datetime import datetime, UTC


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]


def is_local_network(origin):
    """Check if origin is from local network"""
    if origin:
        # Check for localhost
        if origin.startswith('http://localhost:'):
            return True
        # Check for local IP addresses (192.168.*.*)
        if origin.startswith('http://192.168.'):
            return True
        # You can add other local network patterns here
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
        try:
            client = DynamoClient(dynamodb_table_name)
            query_params = event.get("queryStringParameters") or {}

            if "tag" in query_params:
                tag = query_params["tag"]
                # Set a default page size (limit) of 5 if not provided.
                limit = int(query_params.get("limit", 5))

                # If a lastEvaluatedKey is provided, decode it from JSON.
                lastEvaluatedKey = None
                if "lastEvaluatedKey" in query_params:
                    try:
                        lastEvaluatedKey = json.loads(query_params["lastEvaluatedKey"])
                    except json.JSONDecodeError:
                        logger.error("Error decoding lastEvaluatedKey; ignoring it.")
                        lastEvaluatedKey = None

                # Query the DynamoDB GSI for words with the specified tag, paginated.
                rwts, lek = client.getReceiptWordTags(tag, limit=limit, lastEvaluatedKey=lastEvaluatedKey)
                rws = client.getReceiptWordsByKeys([rwt.to_ReceiptWord_key() for rwt in rwts])

                # Combine the rwts and rws into a single list of dictionaries
                combined = []
                for rwt, rw in zip(rwts, rws):
                    combined.append({
                        "word": dict(rw),
                        "tag": dict(rwt),
                    })
                response_body = {
                    "payload": combined,
                    "lastEvaluatedKey": lek  # This will be None if there are no more pages.
                }
                return {
                    "statusCode": 200,
                    "body": json.dumps(response_body),
                }
            else:
                return {"statusCode": 400, "body": "Missing required query parameter 'tag'"}
        except Exception as e:
            return {"statusCode": 500, "body": f"Internal server error: {str(e)}"}
    elif http_method == "POST":
        try:
            client = DynamoClient(dynamodb_table_name)
            
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
            
            if "selected_tag" not in body or "selected_words" not in body:
                return {
                    "statusCode": 400,
                    "headers": cors_headers,
                    "body": json.dumps({"error": "Request must include 'selected_tag' and 'selected_words'"})
                }
            
            selected_tag = body["selected_tag"]
            selected_words = body["selected_words"]
            
            if not isinstance(selected_words, list):
                return {
                    "statusCode": 400,
                    "headers": cors_headers,
                    "body": json.dumps({"error": "'selected_words' must be an array"})
                }

            # Get current timestamp
            current_time = datetime.now(UTC)
            timestamp_str = current_time.isoformat()

            updated_items = []
            
            # Process each word
            for word_data in selected_words:
                if "word" not in word_data or "tags" not in word_data:
                    return {
                        "statusCode": 400,
                        "headers": cors_headers,
                        "body": json.dumps({"error": "Each item must contain 'word' and 'tags'"})
                    }

                # Create receipt word entity
                receipt_word = ReceiptWord(**word_data["word"])
                
                # Remove receipt_id before creating Word entity
                word_params = {k: v for k, v in word_data["word"].items() if k != 'receipt_id'}
                word = Word(**word_params)

                # Delete all existing tags for this word
                existing_tags = word_data["tags"]
                for tag in existing_tags:
                    # Skip if this is the selected tag
                    if tag["tag"] == selected_tag:
                        continue
                        
                    # Delete the tag entries
                    client.deleteWordTag(
                        image_id=tag["image_id"],
                        line_id=tag["line_id"],
                        word_id=tag["word_id"],
                        tag=tag["tag"]
                    )
                    client.deleteReceiptWordTag(
                        image_id=tag["image_id"],
                        receipt_id=tag["receipt_id"],
                        line_id=tag["line_id"],
                        word_id=tag["word_id"],
                        tag=tag["tag"]
                    )

                # Find matching tag if it exists
                matching_tag = None
                for tag in existing_tags:
                    if tag["tag"] == selected_tag:
                        matching_tag = tag
                        break

                if matching_tag:
                    # Create entities from existing tag
                    old_receipt_word_tag = ReceiptWordTag(**matching_tag)
                    old_word_tag = WordTag(
                        image_id=old_receipt_word_tag.image_id,
                        line_id=old_receipt_word_tag.line_id,
                        word_id=old_receipt_word_tag.word_id,
                        tag=old_receipt_word_tag.tag,
                        timestamp_added=old_receipt_word_tag.timestamp_added,
                        validated=old_receipt_word_tag.validated,
                        timestamp_validated=old_receipt_word_tag.timestamp_validated,
                        gpt_confidence=old_receipt_word_tag.gpt_confidence,
                        flag=old_receipt_word_tag.flag,
                        revised_tag=old_receipt_word_tag.revised_tag,
                        human_validated=old_receipt_word_tag.human_validated,
                        timestamp_human_validated=old_receipt_word_tag.timestamp_human_validated
                    )
                    
                    # Delete the old tag entries
                    client.deleteWordTag(
                        image_id=old_word_tag.image_id,
                        line_id=old_word_tag.line_id,
                        word_id=old_word_tag.word_id,
                        tag=old_word_tag.tag
                    )
                    client.deleteReceiptWordTag(
                        image_id=old_receipt_word_tag.image_id,
                        receipt_id=old_receipt_word_tag.receipt_id,
                        line_id=old_receipt_word_tag.line_id,
                        word_id=old_receipt_word_tag.word_id,
                        tag=old_receipt_word_tag.tag
                    )

                # Create new tag (whether updating or creating fresh)
                receipt_word_tag = ReceiptWordTag(
                    image_id=receipt_word.image_id,
                    receipt_id=receipt_word.receipt_id,
                    line_id=receipt_word.line_id,
                    word_id=receipt_word.word_id,
                    tag=selected_tag,
                    timestamp_added=old_receipt_word_tag.timestamp_added if matching_tag else timestamp_str,
                    validated=old_receipt_word_tag.validated if matching_tag else None,
                    timestamp_validated=old_receipt_word_tag.timestamp_validated if matching_tag else None,
                    gpt_confidence=old_receipt_word_tag.gpt_confidence if matching_tag else None,
                    flag=old_receipt_word_tag.flag if matching_tag else None,
                    revised_tag=old_receipt_word_tag.revised_tag if matching_tag else None,
                    human_validated=True,
                    timestamp_human_validated=timestamp_str
                )
                
                word_tag = WordTag(
                    image_id=word.image_id,
                    line_id=word.line_id,
                    word_id=word.word_id,
                    tag=selected_tag,
                    timestamp_added=old_word_tag.timestamp_added if matching_tag else timestamp_str,
                    validated=old_word_tag.validated if matching_tag else None,
                    timestamp_validated=old_word_tag.timestamp_validated if matching_tag else None,
                    gpt_confidence=old_word_tag.gpt_confidence if matching_tag else None,
                    flag=old_word_tag.flag if matching_tag else None,
                    revised_tag=old_word_tag.revised_tag if matching_tag else None,
                    human_validated=True,
                    timestamp_human_validated=timestamp_str
                )

                # Update word tags - now only containing the selected tag
                word.tags = [selected_tag]
                receipt_word.tags = [selected_tag]

                # Add the new tags
                client.addWordTag(word_tag)
                client.addReceiptWordTag(receipt_word_tag)
                client.updateWord(word)
                client.updateReceiptWord(receipt_word)

                updated_items.append({
                    'word': dict(word),
                    'word_tag': dict(word_tag),
                    'receipt_word': dict(receipt_word),
                    'receipt_word_tag': dict(receipt_word_tag)
                })

            response = {
                'updated_items': updated_items
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
