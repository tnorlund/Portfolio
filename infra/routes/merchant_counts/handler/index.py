import json
import logging
import os
import re

from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)


dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]
dynamo_client = DynamoClient(dynamodb_table_name)


def normalize_for_display(name: str) -> str:
    """Convert normalized name to display format.

    Example: "SPROUTS_FARMERS_MARKET" -> "Sprouts Farmers Market"
    """
    return " ".join(word.capitalize() for word in name.split("_"))


def fetch_merchant_counts():
    receipt_places, last_evaluated_key = (
        dynamo_client.list_receipt_places(
            limit=1000,
        )
    )
    while last_evaluated_key is not None:
        next_receipt_places, last_evaluated_key = (
            dynamo_client.list_receipt_places(
                limit=1000,
                last_evaluated_key=last_evaluated_key,
            )
        )
        receipt_places.extend(next_receipt_places)

    # Count the number of receipts for each normalized merchant name
    merchant_counts = {}
    for receipt_place in receipt_places:
        merchant_name = receipt_place.merchant_name
        # Skip receipts with empty or missing merchant names
        if not merchant_name or not merchant_name.strip():
            continue
        normalized_name = merchant_name.upper()
        normalized_name = re.sub(r"[^A-Z0-9]+", "_", normalized_name)
        normalized_name = normalized_name.strip("_")
        if not normalized_name:
            continue
        if normalized_name not in merchant_counts:
            merchant_counts[normalized_name] = 0
        merchant_counts[normalized_name] += 1

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
            # Convert to display-friendly format
            sorted_merchant_counts = [
                {normalize_for_display(name): count}
                for name, count in sorted_merchant_counts
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
            logger.error(
                "Error fetching merchant counts: %s", e, exc_info=True
            )
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
