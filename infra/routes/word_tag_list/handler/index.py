import os
import logging
import json
from receipt_dynamo import DynamoClient
import random


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event, context):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        try:
            client = DynamoClient(dynamodb_table_name)

            # Paginate through all word tags
            word_tags, lek = client.listWordTags(200)
            while lek:
                next_word_tags, lek = client.listWordTags(200, lek)
                word_tags.extend(next_word_tags)

            # Get the unique list of word tags
            word_tags = list(set([word_tag.tag for word_tag in word_tags]))

            return {
                "statusCode": 200,
                "body": json.dumps(word_tags),
            }
        except Exception as e:
            return {"statusCode": 500, "body": f"Internal server error: {str(e)}"}
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
