"""Serve deterministic receipt sections and decoded line items to the UI."""

import json
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    OperationError,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
DEFAULT_BATCH_SIZE = 5
MAX_BATCH_SIZE = 10
CANDIDATE_SAMPLE_SIZE = 500

IMAGE_FIELDS = (
    "image_id",
    "receipt_id",
    "width",
    "height",
    "cdn_s3_bucket",
    "cdn_s3_key",
    "cdn_webp_s3_key",
    "cdn_avif_s3_key",
    "cdn_thumbnail_s3_key",
    "cdn_thumbnail_webp_s3_key",
    "cdn_thumbnail_avif_s3_key",
    "cdn_small_s3_key",
    "cdn_small_webp_s3_key",
    "cdn_small_avif_s3_key",
    "cdn_medium_s3_key",
    "cdn_medium_webp_s3_key",
    "cdn_medium_avif_s3_key",
)


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build an API Gateway response with consistent JSON headers."""
    return {
        "statusCode": status_code,
        "body": json.dumps(body, default=str),
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
    }


def _batch_size(event: dict[str, Any]) -> int:
    """Read the optional batch-size query parameter."""
    params = event.get("queryStringParameters") or {}
    raw_value = params.get("batch_size", params.get("limit"))
    if raw_value is None:
        return DEFAULT_BATCH_SIZE
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("batch_size must be an integer") from exc
    if not 1 <= value <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    return value


def _candidate_receipts(client: DynamoClient) -> list[tuple[str, int]]:
    """Return randomized receipt IDs known to have decoded line items."""
    line_items, _ = client.list_receipt_line_items(limit=CANDIDATE_SAMPLE_SIZE)
    candidates = list(
        {
            (line_item.image_id, line_item.receipt_id)
            for line_item in line_items
        }
    )
    random.shuffle(candidates)
    return candidates


def _image_payload(receipt: Any) -> dict[str, Any]:
    """Return the image-reference shape consumed by getBestImageUrl."""
    return {
        field: getattr(receipt, field)
        for field in IMAGE_FIELDS
        if getattr(receipt, field, None) is not None
    }


def _line_payload(line: Any) -> dict[str, Any]:
    """Return OCR text with normalized and quadrilateral geometry."""
    return {
        "line_id": line.line_id,
        "text": line.text,
        "bounding_box": line.bounding_box,
        "top_left": line.top_left,
        "top_right": line.top_right,
        "bottom_left": line.bottom_left,
        "bottom_right": line.bottom_right,
    }


def _receipt_payload(client: DynamoClient, image_id: str, receipt_id: int):
    """Hydrate one receipt's deterministic decode from normalized rows."""
    details = client.get_receipt_details(image_id, receipt_id)
    sections = client.get_receipt_sections_from_receipt(image_id, receipt_id)
    line_items = client.get_receipt_line_items_from_receipt(
        image_id, receipt_id
    )
    image = _image_payload(details.receipt)
    if (
        not line_items
        or not sections
        or not details.lines
        or not image.get("cdn_s3_key")
    ):
        return None

    try:
        summary = client.get_receipt_summary(image_id, receipt_id)
    except EntityNotFoundError:
        summary = None

    summary_merchant = summary.merchant_name if summary else None
    place_merchant = (
        details.place.merchant_name if details.place is not None else None
    )
    item_merchant = next(
        (item.merchant_name for item in line_items if item.merchant_name),
        None,
    )

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": (
            summary_merchant or place_merchant or item_merchant or "Unknown"
        ),
        "image": image,
        "lines": [
            _line_payload(line)
            for line in sorted(details.lines, key=lambda value: value.line_id)
        ],
        "sections": [
            {
                "section_type": str(section.section_type),
                "line_ids": sorted(set(section.line_ids)),
            }
            for section in sections
        ],
        "line_items": [
            {
                "name": item.name,
                "price": item.price,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "is_discount": item.is_discount,
                "line_ids": item.line_ids,
                "reconciliation_status": item.reconciliation_status,
            }
            for item in sorted(line_items, key=lambda value: value.item_index)
        ],
        "printed_subtotal": summary.subtotal if summary else None,
    }


def handle_get_request(event: dict[str, Any]) -> dict[str, Any]:
    """Return a randomized batch of receipts that have line items."""
    try:
        batch_size = _batch_size(event)
    except ValueError as exc:
        return _response(400, {"error": str(exc)})

    try:
        client = DynamoClient(DYNAMODB_TABLE_NAME)
        candidates = _candidate_receipts(client)
        receipts = []
        for image_id, receipt_id in candidates:
            if len(receipts) >= batch_size:
                break
            try:
                payload = _receipt_payload(client, image_id, receipt_id)
            except (EntityNotFoundError, ValueError) as exc:
                logger.warning(
                    "Skipping incomplete line-item receipt %s/%s: %s",
                    image_id,
                    receipt_id,
                    exc,
                )
                continue
            if payload is not None:
                receipts.append(payload)

        return _response(
            200,
            {
                "receipts": receipts,
                "batch_size": len(receipts),
                "candidate_count": len(candidates),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except OperationError as exc:
        logger.error("Database operation failed: %s", exc)
        return _response(500, {"error": "Database operation failed"})
    except Exception:
        logger.exception("Unexpected line-item decode route failure")
        return _response(500, {"error": "Internal server error"})


def handler(event, _context):
    """Handle API Gateway requests for the geometric-reader data."""
    logger.info("Received event: %s", event)
    try:
        method = event["requestContext"]["http"]["method"].upper()
    except (KeyError, TypeError):
        return _response(400, {"error": "Invalid event structure"})

    if method != "GET":
        return _response(405, {"error": f"Method {method} not allowed"})
    return handle_get_request(event)
