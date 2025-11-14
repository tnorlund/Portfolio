import json
import logging
import os

from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)


dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]
dynamo_client = DynamoClient(dynamodb_table_name)


def fetch_merchant_counts():
    receipt_metadatas, last_evaluated_key = (
        dynamo_client.list_receipt_metadatas(
            limit=1000,
        )
    )
    while last_evaluated_key is not None:
        next_receipt_metadatas, last_evaluated_key = (
            dynamo_client.list_receipt_metadatas(
                limit=1000,
                last_evaluated_key=last_evaluated_key,
            )
        )
        receipt_metadatas.extend(next_receipt_metadatas)

    # Count the number of receipts for each canonical merchant name
    merchant_counts = {}
    for receipt_metadata in receipt_metadatas:
        merchant_name = receipt_metadata.canonical_merchant_name
        # Skip receipts with empty or missing merchant names
        if not merchant_name or not merchant_name.strip():
            continue
        if merchant_name not in merchant_counts:
            merchant_counts[merchant_name] = 0
        merchant_counts[merchant_name] += 1

    return merchant_counts


def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        try:
            merchant_counts = fetch_merchant_counts()
            # Sort the merchant counts by count in descending order
            sorted_merchant_counts = sorted(
                merchant_counts.items(), key=lambda x: x[1], reverse=True
            )
            # Convert back to JSON
            sorted_merchant_counts = [
                {name: count} for name, count in sorted_merchant_counts
            ]
            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
                "body": json.dumps(sorted_merchant_counts),
            }
        except Exception as e:
            logger.error("Error fetching merchant counts: %s", e, exc_info=True)
            return {
                "statusCode": 500,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
                "body": json.dumps({"error": str(e)}),
            }

    elif http_method == "POST":
        return {
            "statusCode": 405,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": "Method not allowed"}),
        }
    else:
        return {
            "statusCode": 405,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": f"Method {http_method} not allowed"}),
        }
