"""Handler for finding items that need embeddings.

Pure business logic - no Lambda-specific code.
"""

import os
from typing import Any, Dict

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import ReceiptLine

from embedding_ingest import write_ndjson
import utils.logging

get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """Find receipt lines without embeddings and prepare batches.

    Args:
        event: Lambda event (unused in current implementation)
        context: Lambda context (unused)

    Returns:
        Dictionary containing batches ready for processing

    Raises:
        RuntimeError: If there's an error processing
    """
    logger.info("Starting find_unembedded_lines handler")

    try:
        # Get S3 bucket from environment
        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise ValueError("S3_BUCKET environment variable not set")

        dynamo_client = DynamoClient(os.environ.get("DYNAMODB_TABLE_NAME"))

        # Find lines without embeddings
        if not hasattr(dynamo_client, "list_receipt_lines_with_no_embeddings"):
            logger.warning(
                "Dynamo client missing list_receipt_lines_with_no_embeddings; skipping"
            )
            return {"batches": []}

        lines_without_embeddings = dynamo_client.list_receipt_lines_with_no_embeddings()  # type: ignore[attr-defined]
        logger.info(
            "Found lines without embeddings",
            count=len(lines_without_embeddings),
        )

        # Chunk into batches
        batches = _chunk_into_line_embedding_batches(lines_without_embeddings)
        logger.info("Chunked into batches", count=len(batches))

        # Serialize and upload to S3
        uploaded = _upload_serialized_lines(
            _serialize_receipt_lines(batches), bucket
        )
        logger.info("Uploaded files", count=len(uploaded))

        # Format response
        cleaned = [
            {
                "s3_key": e["s3_key"],
                "s3_bucket": e["s3_bucket"],
                "image_id": e["image_id"],
                "receipt_id": e["receipt_id"],
            }
            for e in uploaded
        ]

        return {"batches": cleaned}

    except Exception as e:
        logger.error("Error finding unembedded lines", error=str(e))
        raise RuntimeError(f"Error finding unembedded lines: {str(e)}") from e


def _chunk_into_line_embedding_batches(
    lines: list[ReceiptLine], batch_size: int = 50
) -> list[list[ReceiptLine]]:
    """
    Chunk ReceiptLines into batches. This mirrors the lightweight logic from
    receipt_label without pulling that dependency.
    """
    batches: list[list[ReceiptLine]] = []
    current: list[ReceiptLine] = []
    for line in lines:
        current.append(line)
        if len(current) >= batch_size:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches


def _serialize_receipt_lines(
    batches: list[list[ReceiptLine]],
) -> list[dict]:
    """Serialize batches of ReceiptLine entities to NDJSON-ready dicts."""
    serialized: list[dict] = []
    for batch in batches:
        if not batch:
            continue
        image_id = batch[0].image_id
        receipt_id = batch[0].receipt_id
        ndjson_path = f"/tmp/lines-{image_id}-{receipt_id}.ndjson"
        rows = []
        for line in batch:
            rows.append(
                {
                    "receipt_id": line.receipt_id,
                    "image_id": line.image_id,
                    "line_id": line.line_id,
                    "text": line.text,
                    "bounding_box": line.bounding_box,
                    "top_right": line.top_right,
                    "top_left": line.top_left,
                    "bottom_right": line.bottom_right,
                    "bottom_left": line.bottom_left,
                    "angle_degrees": line.angle_degrees,
                    "angle_radians": line.angle_radians,
                    "confidence": line.confidence,
                    "embedding_status": line.embedding_status,
                }
            )
        write_ndjson(Path(ndjson_path), rows)
        serialized.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "ndjson_path": ndjson_path,
            }
        )
    return serialized


def _upload_serialized_lines(
    serialized_lines: list[dict], s3_bucket: str, prefix: str = "line_embeddings"
) -> list[dict]:
    """Upload serialized line NDJSON files to S3."""
    s3 = boto3.client("s3")
    for entry in serialized_lines:
        key = f"{prefix}/{Path(entry['ndjson_path']).name}"
        s3.upload_file(entry["ndjson_path"], s3_bucket, key)
        entry["s3_key"] = key
        entry["s3_bucket"] = s3_bucket
    return serialized_lines
