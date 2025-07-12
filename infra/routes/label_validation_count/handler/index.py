import concurrent.futures
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.dynamo_client import DynamoClient

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
    receipt_word_labels, last_evaluated_key = (
        dynamo_client.get_receipt_word_labels_by_label(
            label=core_label,
            limit=1000,
        )
    )
    while last_evaluated_key is not None:
        next_receipt_word_labels, last_evaluated_key = (
            dynamo_client.get_receipt_word_labels_by_label(
                label=core_label,
                limit=1000,
                last_evaluated_key=last_evaluated_key,
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


def get_cached_label_counts():
    """Try to get label counts from cache first using efficient list query."""
    cached_counts = {}
    missing_labels = list(CORE_LABELS)  # Start with all labels as missing

    try:
        # Get all cached label counts in a single efficient query
        logger.info("Fetching cached label counts from DynamoDB")
        cached_entries, _ = dynamo_client.list_label_count_caches()
        logger.info(
            f"Retrieved {len(cached_entries)} cached entries from DynamoDB"
        )

        # Convert to dictionary for quick lookup
        cached_by_label = {entry.label: entry for entry in cached_entries}

        # Process each core label
        import time
        from datetime import datetime

        current_time = int(time.time())

        for label in CORE_LABELS:
            if label in cached_by_label:
                cached_entry = cached_by_label[label]

                # Check cache age
                last_updated = datetime.fromisoformat(
                    cached_entry.last_updated
                )
                age_minutes = (
                    datetime.now() - last_updated
                ).total_seconds() / 60

                # Check if entry has valid TTL
                ttl_status = "No TTL"
                if cached_entry.time_to_live:
                    if cached_entry.time_to_live > current_time:
                        ttl_status = f"Valid (expires in {(cached_entry.time_to_live - current_time) / 60:.1f} min)"
                    else:
                        ttl_status = f"EXPIRED ({(current_time - cached_entry.time_to_live) / 60:.1f} min ago)"

                cached_counts[label] = {
                    "VALID": cached_entry.valid_count,
                    "INVALID": cached_entry.invalid_count,
                    "PENDING": cached_entry.pending_count,
                    "NEEDS_REVIEW": cached_entry.needs_review_count,
                    "NONE": cached_entry.none_count,
                }
                missing_labels.remove(label)
                logger.info(
                    f"Cache hit for label: {label} (age: {age_minutes:.1f} min, TTL: {ttl_status})"
                )
            else:
                logger.info(f"Cache miss for label: {label}")

        logger.info(
            f"Cache performance: {len(cached_counts)} hits, {len(missing_labels)} misses"
        )

    except Exception as e:
        logger.error(f"Error listing cached label counts: {e}")
        # Fall back to treating all labels as missing
        missing_labels = list(CORE_LABELS)
        cached_counts = {}

    return cached_counts, missing_labels


def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        # Try to get cached counts first
        core_label_counts, missing_labels = get_cached_label_counts()

        # If we have missing labels, fetch them in real-time
        if missing_labels:
            logger.info(
                f"Fetching real-time counts for {len(missing_labels)} labels: {missing_labels}"
            )
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    executor.submit(fetch_label_counts, label): label
                    for label in missing_labels
                }
                for future in as_completed(futures):
                    label, counts = future.result()
                    core_label_counts[label] = counts
        else:
            logger.info("All label counts retrieved from cache")

        # Order the by the key in alphabetical order
        core_label_counts = dict(
            sorted(core_label_counts.items(), key=lambda x: x[0])
        )
        return {
            "statusCode": 200,
            "body": json.dumps(core_label_counts),
        }

    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
