import concurrent.futures
import json
import logging

from receipt_dynamo import process

logger = logging.getLogger()
logger.setLevel(logging.INFO)


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

        if "uuids" not in query_params:
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
        uuids = query_params["uuids"].split(",")
        cdn_bucket_name = query_params["cdn_bucket_name"]
        cdn_prefix = query_params["cdn_prefix"]

        def process_one_uuid(u: str):
            return process(
                table_name=table_name,
                raw_bucket_name=raw_bucket_name,
                raw_prefix=raw_prefix,
                uuid=u,
                cdn_bucket_name=cdn_bucket_name,
                cdn_prefix=cdn_prefix,
            )

        try:
            # You can adjust max_workers as needed
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                # executor.map runs each UUID in parallel threads
                list(executor.map(process_one_uuid, uuids))

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"body": f"Processed {len(uuids)} UUIDs concurrently."}
                ),
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
