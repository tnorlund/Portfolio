"""Lambda handler for serving QA Agent visualization cache.

Serves per-question trace data and metadata from S3 cache.

API:
    GET /qa/visualization             → metadata.json
    GET /qa/visualization?index=3     → single question's trace JSON
    GET /qa/visualization?all=true    → all questions (for SSG/build-time)
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
QUESTIONS_PREFIX = "questions/"

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")

s3_client = boto3.client("s3")


def _cors_response(status_code: int, body: Any) -> dict:
    """Build an API Gateway v2 response with CORS headers."""
    return {
        "statusCode": status_code,
        "body": json.dumps(body, default=str),
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
    }


def _fetch_metadata() -> dict[str, Any]:
    """Fetch metadata.json from S3."""
    try:
        response = s3_client.get_object(
            Bucket=S3_CACHE_BUCKET, Key="metadata.json"
        )
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError:
        logger.warning("Could not fetch metadata.json")
        return {}


def _fetch_question(index: int) -> dict[str, Any] | None:
    """Fetch a single question cache file from S3."""
    key = f"{QUESTIONS_PREFIX}question-{index}.json"
    try:
        response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError:
        logger.warning("Could not fetch %s", key)
        return None


def _list_question_keys() -> list[str]:
    """List all cached question keys from S3."""
    keys: list[str] = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=S3_CACHE_BUCKET, Prefix=QUESTIONS_PREFIX
        ):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if key.endswith(".json"):
                    keys.append(key)
    except ClientError:
        logger.exception("Error listing question files")
    return sorted(keys)


def _fetch_all_questions() -> list[dict[str, Any]]:
    """Fetch all question cache files from S3 in parallel."""
    keys = _list_question_keys()
    if not keys:
        return []

    questions: list[dict[str, Any]] = []
    max_workers = min(len(keys), 10)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for key in keys:
            futures[executor.submit(
                lambda k: json.loads(
                    s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=k)["Body"]
                    .read()
                    .decode("utf-8")
                ),
                key,
            )] = key

        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    questions.append(result)
            except Exception:
                logger.exception("Failed to fetch %s", futures[future])

    # Sort by questionIndex
    questions.sort(key=lambda q: q.get("questionIndex", 0))
    return questions


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Handle API Gateway requests for QA visualization cache.

    Query Parameters:
        index: Question index (0-31) → returns single question trace
        all: If 'true', returns all questions (for build-time fetch)
        (none): Returns metadata only

    Response:
        {
            "questions": [...],     # when index or all specified
            "metadata": {...},      # always included
            "fetched_at": "..."     # timestamp
        }
    """
    logger.info("Received event: %s", json.dumps(event, default=str)[:500])

    # Handle API Gateway v2 event format
    try:
        http_method = event["requestContext"]["http"]["method"].upper()
    except (KeyError, TypeError):
        return _cors_response(400, {"error": "Invalid event structure"})

    if http_method != "GET":
        return _cors_response(405, {"error": f"Method {http_method} not allowed"})

    if not S3_CACHE_BUCKET:
        return _cors_response(500, {"error": "S3_CACHE_BUCKET not configured"})

    try:
        query_params = event.get("queryStringParameters") or {}
        metadata = _fetch_metadata()

        # Single question by index
        question_index = query_params.get("index") or query_params.get("question_index")
        if question_index is not None:
            try:
                idx = int(question_index)
            except (ValueError, TypeError):
                return _cors_response(400, {"error": "Invalid question index"})

            question = _fetch_question(idx)
            if not question:
                return _cors_response(404, {"error": f"Question {idx} not found"})

            return _cors_response(200, {
                "questions": [question],
                "metadata": metadata,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })

        # All questions
        if query_params.get("all", "").lower() == "true":
            questions = _fetch_all_questions()
            return _cors_response(200, {
                "questions": questions,
                "metadata": metadata,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })

        # Metadata only (default)
        return _cors_response(200, {
            "metadata": metadata,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })

    except ClientError:
        logger.exception("S3 error")
        return _cors_response(500, {"error": "S3 error occurred"})
    except Exception:
        logger.exception("Unexpected error")
        return _cors_response(500, {"error": "Internal server error"})
