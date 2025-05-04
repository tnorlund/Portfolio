import os
import concurrent.futures
import logging
import json
from receipt_dynamo.constants import ValidationStatus
from concurrent.futures import ThreadPoolExecutor, as_completed
from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)


CORE_LABELS = [
    # Merchant & store info
    "MERCHANT_NAME",
    "STORE_HOURS",
    "PHONE_NUMBER",
    "WEBSITE",
    "LOYALTY_ID",
    # Location/address (either as one line or broken out)
    "ADDRESS_LINE",  # or, for finer breakdown:
    # "ADDRESS_NUMBER",
    # "STREET_NAME",
    # "CITY",
    # "STATE",
    # "POSTAL_CODE",
    # Transaction info
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "DISCOUNT",  # if you want to distinguish coupons vs. generic discounts
    # Line‑item fields
    "PRODUCT_NAME",  # or ITEM_NAME
    "QUANTITY",  # or ITEM_QUANTITY
    "UNIT_PRICE",  # or ITEM_PRICE
    "LINE_TOTAL",  # or ITEM_TOTAL
    # Totals & taxes
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",  # or TOTAL
]

dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]
dynamo_client = DynamoClient(dynamodb_table_name)


def fetch_label_counts(core_label):
    receipt_word_labels, last_evaluated_key = dynamo_client.getReceiptWordLabelsByLabel(
        label=core_label,
        limit=1000,
    )
    while last_evaluated_key is not None:
        next_receipt_word_labels, last_evaluated_key = (
            dynamo_client.getReceiptWordLabelsByLabel(
                label=core_label,
                limit=1000,
                lastEvaluatedKey=last_evaluated_key,
            )
        )
        receipt_word_labels.extend(next_receipt_word_labels)

    label_counts = {}
    for validation_status in ValidationStatus:
        label_counts[validation_status.value] = sum(
            receipt_word_label.validation_status == validation_status
            for receipt_word_label in receipt_word_labels
        )
    return core_label, label_counts


def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        core_label_counts = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(fetch_label_counts, label): label
                for label in CORE_LABELS
            }
            for future in as_completed(futures):
                label, counts = future.result()
                core_label_counts[label] = counts

        # Order the by the key in alphabetical order
        core_label_counts = dict(sorted(core_label_counts.items()))
        return {
            "statusCode": 200,
            "body": json.dumps(core_label_counts),
        }

    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
