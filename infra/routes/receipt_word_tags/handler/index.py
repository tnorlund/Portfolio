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
from dynamo import DynamoClient, ReceiptWord, ReceiptWordTag
import random
import datetime


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event, context):
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
            
            # Validate request structure
            if not isinstance(body, dict):
                return {"statusCode": 400, "body": "Request body must be an object"}
            
            if "selected_tag" not in body or "selected_words" not in body:
                return {"statusCode": 400, "body": "Request must include 'selected_tag' and 'selected_words'"}
            
            selected_tag = body["selected_tag"]
            selected_words = body["selected_words"]
            
            if not isinstance(selected_words, list):
                return {"statusCode": 400, "body": "'selected_words' must be an array"}

            # Process each word
            for word_data in selected_words:
                if "word" not in word_data:
                    return {"statusCode": 400, "body": "Each item must contain a 'word' object"} 
                if "tags" not in word_data:
                    return {"statusCode": 400, "body": "Each item must contain a 'tags' array"}
                
                # Create the receipt word and receipt word tags
                word = ReceiptWord(**word_data["word"])
                existing_tags = [ReceiptWordTag(**tag) for tag in word_data["tags"]]

                # Create new word tag if it doesn't exist
                new_tag = ReceiptWordTag(
                    image_id=word.image_id,
                    receipt_id=word.receipt_id,
                    line_id=word.line_id,
                    word_id=word.word_id,
                    tag=selected_tag,
                    timestamp_added=datetime.datetime.now(),
                    human_validated=True,
                    timestamp_human_validated=datetime.datetime.now()
                )

                try:
                    # Check if this tag already exists for this word
                    tag_exists = any(tag.tag == selected_tag for tag in existing_tags)
                    
                    if tag_exists:
                        # Update existing tag with human validation
                        client.updateWordTag(new_tag)
                    else:
                        # Create new tag
                        client.addWordTag(new_tag)

                        # Update the word's tags list if needed
                        if selected_tag not in word.tags:
                            word.tags.append(selected_tag)
                            client.updateReceiptWord(word)

                except Exception as e:
                    logger.error(f"Error processing word {word.word_id}: {str(e)}")
                    return {"statusCode": 500, "body": f"Error processing word: {str(e)}"}

            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": "Successfully updated word tags",
                    "tag": selected_tag,
                    "word_count": len(selected_words)
                })
            }

        except json.JSONDecodeError:
            return {"statusCode": 400, "body": "Invalid JSON in request body"}
        except Exception as e:
            logger.error("Error processing request: %s", str(e))
            return {"statusCode": 500, "body": f"Internal server error: {str(e)}"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
