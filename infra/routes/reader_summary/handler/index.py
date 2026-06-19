import json
import logging
import os
import re
import time
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ["READER_SUMMARY_TABLE_NAME"]
MINIMUM_SAMPLE_SIZE = int(os.environ.get("MINIMUM_SAMPLE_SIZE", "5"))
ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.environ.get(
        "ALLOWED_ORIGINS",
        ",".join(
            [
                "http://localhost:3000",
                "https://tylernorlund.com",
                "https://www.tylernorlund.com",
                "https://dev.tylernorlund.com",
            ]
        ),
    ).split(",")
    if origin.strip()
}
EVENT_TTL_SECONDS = 60 * 60 * 24 * 30
MAX_PATH_LENGTH = 180
MAX_ID_LENGTH = 120
MAX_TIME_TO_BOTTOM_MS = 60 * 60 * 1000
MIN_ACTIVE_SCROLL_MS = 5000

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body),
    }


def get_header(headers: Dict[str, Any], name: str) -> Optional[str]:
    target = name.lower()

    for key, value in headers.items():
        if key.lower() == target and value:
            return str(value)

    return None


def has_allowed_origin(event: Dict[str, Any]) -> bool:
    headers = event.get("headers") or {}
    origin = get_header(headers, "origin")

    if origin:
        return origin in ALLOWED_ORIGINS

    referer = get_header(headers, "referer")
    if not referer:
        return False

    return any(
        referer == allowed_origin
        or referer.startswith(f"{allowed_origin}/")
        for allowed_origin in ALLOWED_ORIGINS
    )


def decimal_to_int(value: Any) -> int:
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def decimal_to_float(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body") or "{}"

    if event.get("isBase64Encoded"):
        return {}

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}

    return parsed if isinstance(parsed, dict) else {}


def normalize_page_path(value: Any) -> str:
    page_path = str(value or "/").split("?", 1)[0].strip() or "/"

    if not page_path.startswith("/"):
        page_path = f"/{page_path}"

    page_path = re.sub(r"/{2,}", "/", page_path)

    if len(page_path) > MAX_PATH_LENGTH:
        page_path = page_path[:MAX_PATH_LENGTH]

    return page_path.rstrip("/") or "/"


def clean_id(value: Any, fallback: str) -> str:
    text = str(value or "").strip()

    if not text:
        text = fallback

    return re.sub(r"[^a-zA-Z0-9_.:-]", "_", text)[:MAX_ID_LENGTH]


def bounded_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
) -> Optional[int]:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None

    if parsed < minimum or parsed > maximum:
        return None

    return parsed


def bounded_float(
    value: Any,
    *,
    minimum: float,
    maximum: float,
) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    if parsed < minimum or parsed > maximum:
        return None

    return parsed


def get_summary(page_path: str) -> Dict[str, Any]:
    item = table.get_item(
        Key={
            "PK": f"READER_SUMMARY#PAGE#{page_path}",
            "SK": "SUMMARY",
        }
    ).get("Item", {})

    sample_size = decimal_to_int(item.get("sample_size"))
    total_time_to_bottom_ms = decimal_to_int(
        item.get("total_time_to_bottom_ms")
    )
    total_active_scroll_ms = decimal_to_int(
        item.get("total_active_scroll_ms")
    )
    total_screens_per_minute = decimal_to_float(
        item.get("total_screens_per_minute")
    )

    average_time_to_bottom_ms = (
        round(total_time_to_bottom_ms / sample_size)
        if sample_size > 0
        else None
    )
    average_active_scroll_ms = (
        round(total_active_scroll_ms / sample_size)
        if sample_size > 0
        else None
    )
    average_screens_per_minute = (
        round(total_screens_per_minute / sample_size, 2)
        if sample_size > 0
        else None
    )

    return {
        "sampleSize": sample_size,
        "averageTimeToBottomMs": average_time_to_bottom_ms,
        "averageActiveScrollMs": average_active_scroll_ms,
        "averageScreensPerMinute": average_screens_per_minute,
        "updatedAt": item.get("updated_at"),
    }


def get_reader_delta_percent(
    time_to_bottom_ms: int,
    baseline: Dict[str, Any],
) -> Optional[int]:
    average_time_to_bottom_ms = baseline.get("averageTimeToBottomMs")

    if (
        baseline.get("sampleSize", 0) < MINIMUM_SAMPLE_SIZE
        or not average_time_to_bottom_ms
    ):
        return None

    return round(
        ((average_time_to_bottom_ms - time_to_bottom_ms)
         / average_time_to_bottom_ms)
        * 100
    )


def validate_payload(
    payload: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    page_path = normalize_page_path(payload.get("page_path"))
    analytics_event_id = clean_id(
        payload.get("analytics_event_id"),
        f"missing-{int(time.time() * 1000)}",
    )
    time_to_bottom_ms = bounded_int(
        payload.get("time_to_bottom_ms"),
        minimum=1,
        maximum=MAX_TIME_TO_BOTTOM_MS,
    )
    active_scroll_ms = bounded_int(
        payload.get("active_scroll_ms"),
        minimum=0,
        maximum=MAX_TIME_TO_BOTTOM_MS,
    )
    scrollable_pixels = bounded_int(
        payload.get("scrollable_pixels"),
        minimum=0,
        maximum=2_000_000,
    )
    screens_per_minute = bounded_float(
        payload.get("screens_per_minute"),
        minimum=0,
        maximum=10_000,
    )
    quick_jump = bool(payload.get("quick_jump"))

    if time_to_bottom_ms is None or active_scroll_ms is None:
        return None, "Invalid reader timing values."

    if scrollable_pixels is None or screens_per_minute is None:
        return None, "Invalid page measurement values."

    if active_scroll_ms < MIN_ACTIVE_SCROLL_MS:
        quick_jump = True

    return {
        "page_path": page_path,
        "analytics_event_id": analytics_event_id,
        "time_to_bottom_ms": time_to_bottom_ms,
        "active_scroll_ms": active_scroll_ms,
        "scrollable_pixels": scrollable_pixels,
        "screens_per_minute": Decimal(str(round(screens_per_minute, 2))),
        "quick_jump": quick_jump,
    }, None


def update_summary(valid_payload: Dict[str, Any]) -> bool:
    if valid_payload["quick_jump"]:
        return False

    now = int(time.time())
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    page_key = f"READER_SUMMARY#PAGE#{valid_payload['page_path']}"
    event_key = (
        f"READER_SUMMARY#EVENT#{valid_payload['analytics_event_id']}"
    )

    try:
        table.put_item(
            Item={
                "PK": event_key,
                "SK": "DEDUP",
                "page_path": valid_payload["page_path"],
                "created_at": now_iso,
                "TYPE": "READER_SUMMARY_EVENT",
                "time_to_live": now + EVENT_TTL_SECONDS,
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise

    table.update_item(
        Key={
            "PK": page_key,
            "SK": "SUMMARY",
        },
        UpdateExpression=(
            "SET updated_at = :updated_at "
            "ADD sample_size :one, "
            "total_time_to_bottom_ms :time_to_bottom, "
            "total_active_scroll_ms :active_scroll, "
            "total_screens_per_minute :screens_per_minute"
        ),
        ExpressionAttributeValues={
            ":one": 1,
            ":time_to_bottom": valid_payload["time_to_bottom_ms"],
            ":active_scroll": valid_payload["active_scroll_ms"],
            ":screens_per_minute": valid_payload["screens_per_minute"],
            ":updated_at": now_iso,
        },
    )

    return True


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method")

    if method != "POST":
        return response(405, {"error": "Method not allowed"})

    if not has_allowed_origin(event):
        return response(403, {"error": "Forbidden"})

    payload, validation_error = validate_payload(parse_body(event))

    if validation_error or payload is None:
        return response(400, {"error": validation_error})

    baseline = get_summary(payload["page_path"])

    try:
        counted = update_summary(payload)
    except Exception:
        logger.exception("Failed to update reader summary")
        return response(500, {"error": "Failed to update reader summary."})

    updated_summary = get_summary(payload["page_path"])
    reader_delta_percent = get_reader_delta_percent(
        payload["time_to_bottom_ms"],
        baseline,
    )

    return response(
        200,
        {
            "accepted": True,
            "counted": counted,
            "quickJump": payload["quick_jump"],
            "pagePath": payload["page_path"],
            "minimumSampleSize": MINIMUM_SAMPLE_SIZE,
            "comparison": {
                "sampleSize": baseline["sampleSize"],
                "averageTimeToBottomMs": baseline[
                    "averageTimeToBottomMs"
                ],
                "readerDeltaPercent": reader_delta_percent,
            },
            "aggregate": updated_summary,
        },
    )
