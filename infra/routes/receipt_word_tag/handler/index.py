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

            # Validate request structure
            if not isinstance(body, dict):
                return {"statusCode": 400, "body": "Request body must be an object"}

            if "selected_tag" not in body or "selected_word" not in body:
                return {
                    "statusCode": 400,
                    "body": "Request must include 'selected_tag' and 'selected_word'",
                }

            selected_tag = body["selected_tag"]
            selected_word = body["selected_word"]

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

            # Update all entities
            current_time = datetime.now(UTC)
            word_tag.timestamp_human_validated = current_time
            receipt_word_tag.timestamp_human_validated = current_time
            
            client.updateWordTag(word_tag)
            client.updateWord(word)
            client.updateReceiptWord(receipt_word)
            client.updateReceiptWordTag(receipt_word_tag)

            return {
                "statusCode": 200,
                "body": "Receipt word tag updated successfully",
            }

        except Exception as e:
            return {"statusCode": 500, "body": f"Internal server error: {str(e)}"}

    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
