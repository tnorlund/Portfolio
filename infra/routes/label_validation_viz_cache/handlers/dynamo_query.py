import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Limit receipts to prevent memory issues - visualization only needs a sample
MAX_RECEIPTS = 500


def handler(event, context):
    """Query receipts, words, and labels from DynamoDB and write to S3.

    The EMR job needs:
    - Receipt CDN keys (for image display)
    - Word bounding boxes (for overlays)
    - Labels (for validation word filtering)

    Limited to MAX_RECEIPTS to prevent memory exhaustion.
    """
    logger.info("Received event: %s", json.dumps(event))

    table_name = os.environ["DYNAMODB_TABLE"]
    cache_bucket = os.environ["CACHE_BUCKET"]

    # Import receipt_dynamo (from layer)
    from receipt_dynamo import DynamoClient

    client = DynamoClient(table_name)

    logger.info(
        "Querying receipts from DynamoDB table: %s (limit: %d)",
        table_name,
        MAX_RECEIPTS,
    )

    receipt_lookup = {}
    page_count = 0
    last_key = None

    # First page of receipts
    receipts, last_key = client.list_receipts(limit=500)
    page_count += 1

    for r in receipts:
        if len(receipt_lookup) >= MAX_RECEIPTS:
            break

        key = f"{r.image_id}_{r.receipt_id}"

        words = client.list_receipt_words_from_receipt(
            r.image_id, r.receipt_id
        )
        words_data = [
            {
                "line_id": w.line_id,
                "word_id": w.word_id,
                "text": w.text,
                "bbox": w.bounding_box,
            }
            for w in words
        ]

        labels, _ = client.list_receipt_word_labels_for_receipt(
            r.image_id, r.receipt_id
        )
        labels_data = {
            f"{label.line_id}_{label.word_id}": label.label for label in labels
        }

        receipt_lookup[key] = {
            "cdn_s3_key": r.cdn_s3_key or "",
            "cdn_webp_s3_key": r.cdn_webp_s3_key,
            "cdn_avif_s3_key": r.cdn_avif_s3_key,
            "cdn_medium_s3_key": r.cdn_medium_s3_key,
            "cdn_medium_webp_s3_key": r.cdn_medium_webp_s3_key,
            "cdn_medium_avif_s3_key": r.cdn_medium_avif_s3_key,
            "width": r.width or 0,
            "height": r.height or 0,
            "words": words_data,
            "labels": labels_data,
        }

    # Paginate through remaining receipts until limit reached
    while last_key and len(receipt_lookup) < MAX_RECEIPTS:
        receipts, last_key = client.list_receipts(
            limit=500, last_evaluated_key=last_key
        )
        page_count += 1

        for r in receipts:
            if len(receipt_lookup) >= MAX_RECEIPTS:
                break

            key = f"{r.image_id}_{r.receipt_id}"

            words = client.list_receipt_words_from_receipt(
                r.image_id, r.receipt_id
            )
            words_data = [
                {
                    "line_id": w.line_id,
                    "word_id": w.word_id,
                    "text": w.text,
                    "bbox": w.bounding_box,
                }
                for w in words
            ]

            labels, _ = client.list_receipt_word_labels_for_receipt(
                r.image_id, r.receipt_id
            )
            labels_data = {
                f"{label.line_id}_{label.word_id}": label.label
                for label in labels
            }

            receipt_lookup[key] = {
                "cdn_s3_key": r.cdn_s3_key or "",
                "cdn_webp_s3_key": r.cdn_webp_s3_key,
                "cdn_avif_s3_key": r.cdn_avif_s3_key,
                "cdn_medium_s3_key": r.cdn_medium_s3_key,
                "cdn_medium_webp_s3_key": r.cdn_medium_webp_s3_key,
                "cdn_medium_avif_s3_key": r.cdn_medium_avif_s3_key,
                "width": r.width or 0,
                "height": r.height or 0,
                "words": words_data,
                "labels": labels_data,
            }

        logger.info(
            "Processed page %d, total receipts: %d",
            page_count,
            len(receipt_lookup),
        )

    logger.info(
        "Collected %d receipts across %d pages (limit: %d)",
        len(receipt_lookup),
        page_count,
        MAX_RECEIPTS,
    )

    # Write to S3
    s3 = boto3.client("s3")
    s3_key = "receipts-lookup.json"
    try:
        s3.put_object(
            Bucket=cache_bucket,
            Key=s3_key,
            Body=json.dumps(receipt_lookup),
            ContentType="application/json",
        )
    except ClientError:
        logger.exception("Failed to write receipts lookup to S3")
        raise

    logger.info("Wrote %s to s3://%s/%s", s3_key, cache_bucket, s3_key)

    return {
        "receipt_count": len(receipt_lookup),
        "receipts_s3_path": f"s3://{cache_bucket}/{s3_key}",
    }
