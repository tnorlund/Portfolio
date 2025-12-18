"""
NDJSON Export Management for Receipt Lifecycle

Handles exporting receipt lines and words to NDJSON files in S3.
"""

import json
from typing import Optional

import boto3

from receipt_dynamo import DynamoClient


def export_receipt_ndjson(
    client: DynamoClient,
    artifacts_bucket: str,
    image_id: str,
    receipt_id: int,
    receipt_lines: Optional[list] = None,
    receipt_words: Optional[list] = None,
) -> None:
    """
    Export receipt lines and words to NDJSON files in S3.

    This matches the upload workflow pattern from process_ocr_results.py.
    The NDJSON files are exported for consistency and audit trail, even though
    we're doing direct embedding instead of queue-based processing.

    Args:
        client: DynamoDB client
        artifacts_bucket: S3 bucket for artifacts (NDJSON files)
        image_id: Image ID
        receipt_id: Receipt ID
        receipt_lines: Optional list of ReceiptLine entities (fetched if not provided)
        receipt_words: Optional list of ReceiptWord entities (fetched if not provided)
    """
    try:
        # Fetch authoritative words/lines from DynamoDB if not provided
        if receipt_lines is None:
            receipt_lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
        if receipt_words is None:
            receipt_words = client.list_receipt_words_from_receipt(image_id, receipt_id)

        prefix = f"receipts/{image_id}/receipt-{receipt_id:05d}/"
        lines_key = prefix + "lines.ndjson"
        words_key = prefix + "words.ndjson"

        # Serialize full dataclass objects so the consumer can rehydrate with
        # ReceiptLine(**d)/ReceiptWord(**d) preserving geometry and methods
        line_rows = [dict(l) for l in (receipt_lines or [])]
        word_rows = [dict(w) for w in (receipt_words or [])]

        # Upload NDJSON files to S3
        s3_client = boto3.client("s3")

        # Upload lines NDJSON
        if line_rows:
            lines_ndjson_content = "\n".join(
                json.dumps(row, default=str) for row in line_rows
            )
            s3_client.put_object(
                Bucket=artifacts_bucket,
                Key=lines_key,
                Body=lines_ndjson_content.encode("utf-8"),
                ContentType="application/x-ndjson",
            )
            print(
                f"   ✅ Exported {len(line_rows)} lines to s3://{artifacts_bucket}/{lines_key}"
            )

        # Upload words NDJSON
        if word_rows:
            words_ndjson_content = "\n".join(
                json.dumps(row, default=str) for row in word_rows
            )
            s3_client.put_object(
                Bucket=artifacts_bucket,
                Key=words_key,
                Body=words_ndjson_content.encode("utf-8"),
                ContentType="application/x-ndjson",
            )
            print(
                f"   ✅ Exported {len(word_rows)} words to s3://{artifacts_bucket}/{words_key}"
            )

    except Exception as e:
        print(f"⚠️  Error exporting NDJSON for receipt {receipt_id}: {e}")
        import traceback

        traceback.print_exc()
