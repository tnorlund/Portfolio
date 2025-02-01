import os
import logging
import json
from dynamo import process

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        query_params = event.get("queryStringParameters") or {}

        if "table_name" not in query_params:
            return {
                "statusCode": 400,
                "body": json.dumps({"body": "Missing required parameter 'table_name'"}),
            }

        if "raw_bucket_name" not in query_params:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "body": "Missing required parameter 'raw_bucket_name'",
                    }
                ),
            }

        if "raw_prefix" not in query_params:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "body": "Missing required parameter 'raw_prefix'",
                    }
                ),
            }

        if "uuid" not in query_params:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "body": "Missing required parameter 'uuid'",
                    }
                ),
            }

        if "cdn_bucket_name" not in query_params:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "body": "Missing required parameter 'cdn_bucket_name'",
                    }
                ),
            }

        if "cdn_prefix" not in query_params:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "body": "Missing required parameter 'cdn_prefix'",
                    }
                ),
            }
        
        table_name = query_params["table_name"]
        raw_bucket_name = query_params["raw_bucket_name"]
        raw_prefix = query_params["raw_prefix"]
        uuid = query_params["uuid"]
        cdn_bucket_name = query_params["cdn_bucket_name"]
        cdn_prefix = query_params["cdn_prefix"]

        try:
            process(
                table_name=table_name,
                raw_bucket_name=raw_bucket_name,
                raw_prefix=raw_prefix,
                uuid=uuid,
                cdn_bucket_name=cdn_bucket_name,
                cdn_prefix=cdn_prefix,
            )
            return {
                "statusCode": 200,
                "body": json.dumps({"body": "Success"}),
            }
        except Exception as e:
            return {
                "statusCode": 500,
                "body": json.dumps({"body": str(e)}),
            }
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
