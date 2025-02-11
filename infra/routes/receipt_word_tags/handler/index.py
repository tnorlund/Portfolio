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
                
                # Create both receipt word and regular word entities
                receipt_word = ReceiptWord(**word_data["word"])
                word = Word(
                    image_id=receipt_word.image_id,
                    line_id=receipt_word.line_id,
                    word_id=receipt_word.word_id,
                    text=receipt_word.text,
                    bounding_box=receipt_word.bounding_box,
                    top_right=receipt_word.top_right,
                    top_left=receipt_word.top_left,
                    bottom_right=receipt_word.bottom_right,
                    bottom_left=receipt_word.bottom_left,
                    angle_degrees=receipt_word.angle_degrees,
                    angle_radians=receipt_word.angle_radians,
                    confidence=receipt_word.confidence,
                    tags=receipt_word.tags
                )

                # Create both receipt word tags and regular word tags
                existing_receipt_tags = [ReceiptWordTag(**tag) for tag in word_data["tags"]]
                existing_word_tags = [
                    WordTag(
                        image_id=tag.image_id,
                        line_id=tag.line_id,
                        word_id=tag.word_id,
                        tag=tag.tag,
                        timestamp_added=tag.timestamp_added,
                        validated=tag.validated,
                        timestamp_validated=tag.timestamp_validated,
                        gpt_confidence=tag.gpt_confidence,
                        flag=tag.flag,
                        revised_tag=tag.revised_tag,
                        human_validated=tag.human_validated,
                        timestamp_human_validated=tag.timestamp_human_validated
                    ) 
                    for tag in existing_receipt_tags
                ]

                # Create new tags
                new_receipt_tag = ReceiptWordTag(
                    image_id=receipt_word.image_id,
                    receipt_id=receipt_word.receipt_id,
                    line_id=receipt_word.line_id,
                    word_id=receipt_word.word_id,
                    tag=selected_tag,
                    timestamp_added=datetime.datetime.now(),
                    human_validated=True,
                    timestamp_human_validated=datetime.datetime.now()
                )
                
                new_word_tag = WordTag(
                    image_id=word.image_id,
                    line_id=word.line_id,
                    word_id=word.word_id,
                    tag=selected_tag,
                    timestamp_added=datetime.datetime.now(),
                    human_validated=True,
                    timestamp_human_validated=datetime.datetime.now()
                )

                try:
                    # Check if tags already exist
                    receipt_tag_exists = any(tag.tag == selected_tag for tag in existing_receipt_tags)
                    
                    if receipt_tag_exists:
                        # Find and update the existing tags
                        for tag in existing_receipt_tags:
                            if tag.tag == selected_tag:
                                tag.human_validated = True
                                tag.timestamp_human_validated = datetime.datetime.now()
                                client.updateReceiptWordTag(tag)
                                break
                        
                        for tag in existing_word_tags:
                            if tag.tag == selected_tag:
                                tag.human_validated = True
                                tag.timestamp_human_validated = datetime.datetime.now()
                                client.updateWordTag(tag)
                                break
                    else:
                        # Create new tags
                        client.addReceiptWordTag(new_receipt_tag)
                        client.addWordTag(new_word_tag)

                        # Update both words' tags lists if needed
                        if selected_tag not in receipt_word.tags:
                            receipt_word.tags.append(selected_tag)
                            word.tags.append(selected_tag)
                            client.updateReceiptWord(receipt_word)
                            client.updateWord(word)

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
