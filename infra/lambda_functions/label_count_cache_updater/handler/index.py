import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.label_count_cache import LabelCountCache

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
    """Fetch label counts for a specific core label."""
    logger.info(f"Fetching counts for label: {core_label}")

    receipt_word_labels = []
    last_evaluated_key = None

    # Fetch all receipt word labels for this label
    while True:
        batch, last_evaluated_key = (
            dynamo_client.get_receipt_word_labels_by_label(
                label=core_label,
                limit=1000,
                last_evaluated_key=last_evaluated_key,
            )
        )
        receipt_word_labels.extend(batch)

        if last_evaluated_key is None:
            break

    # Count by validation status
    label_counts = {}
    for validation_status in ValidationStatus:
        label_counts[validation_status.value] = sum(
            receipt_word_label.validation_status == validation_status
            for receipt_word_label in receipt_word_labels
        )

    logger.info(f"Counts for {core_label}: {label_counts}")
    return core_label, label_counts


def update_label_cache(label, counts, timestamp, ttl):
    """Update or create a cache entry for a label."""
    try:
        cache_entry = LabelCountCache(
            label=label,
            valid_count=counts.get("VALID", 0),
            invalid_count=counts.get("INVALID", 0),
            pending_count=counts.get("PENDING", 0),
            needs_review_count=counts.get("NEEDS_REVIEW", 0),
            none_count=counts.get("NONE", 0),
            last_updated=timestamp,
            time_to_live=ttl,
        )

        # Try to update existing cache entry, if it doesn't exist, add new one
        try:
            dynamo_client.update_label_count_cache(cache_entry)
            logger.info(f"Updated cache for label: {label}")
        except EntityNotFoundError:
            # Cache entry doesn't exist, so add it
            dynamo_client.add_label_count_cache(cache_entry)
            logger.info(f"Added new cache for label: {label}")

    except Exception as e:
        logger.error(f"Error updating cache for label {label}: {e}")
        raise e


def handler(event, context):
    """Lambda handler function."""
    logger.info("Starting label count cache update")

    # Set TTL to 6 minutes from now (expires 1 minute after next scheduled run)
    ttl = int(time.time()) + (6 * 60)  # 6 minutes from now
    timestamp = datetime.now().isoformat()

    # Fetch counts for all core labels concurrently
    core_label_counts = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all tasks
        futures = {
            executor.submit(fetch_label_counts, label): label
            for label in CORE_LABELS
        }

        # Collect results
        for future in as_completed(futures):
            try:
                label, counts = future.result()
                core_label_counts[label] = counts
            except Exception as e:
                label = futures[future]
                logger.error(f"Error fetching counts for label {label}: {e}")
                # Continue with other labels even if one fails

    # Update cache entries
    cache_updates = []
    for label, counts in core_label_counts.items():
        try:
            update_label_cache(label, counts, timestamp, ttl)
            cache_updates.append(label)
        except Exception as e:
            logger.error(f"Failed to update cache for label {label}: {e}")

    logger.info(
        f"Successfully updated cache for {len(cache_updates)} labels out of {len(CORE_LABELS)} total"
    )
    logger.info(f"Updated labels: {cache_updates}")

    return {
        "statusCode": 200,
        "body": {
            "message": "Label count cache updated successfully",
            "updated_labels": cache_updates,
            "total_labels": len(CORE_LABELS),
            "ttl": ttl,
            "timestamp": timestamp,
        },
    }
