"""Lambda handler serving per-epoch checkpoint-evaluation results from S3.

Reads per-epoch eval artifacts from EITHER location:
  - ``epoch-eval/<job>/`` — written by the standalone ``eval-checkpoints``
    SageMaker Processing job (retro-eval of a finished run).
  - ``runs/<job>/`` — written live DURING training by the in-trainer held-out
    eval callback (no separate job). Same ``epochs.json`` shape.

    <prefix>/<job>/epochs.json
    <prefix>/<job>/receipts/epoch-<n>/receipt-<image_id>-<receipt_id>.json

The standalone copy is preferred when both exist.

Query parameters:
    (none)              -> list job names that have an epochs.json
    ?job=<name>         -> that job's epochs.json (the held-out F1 curve)
    ?job=<name>&receipt_path=receipts/epoch-5/receipt-...json
                        -> a single per-epoch showcase receipt record
"""

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_CACHE_BUCKET = os.environ["S3_CACHE_BUCKET"]
# Standalone Processing-job evals, preferred when both exist; then live
# in-training evals written under each run's prefix.
PREFIXES = ("epoch-eval/", "runs/")

s3_client = boto3.client("s3")

_CORS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}
_CACHE = {
    "Cache-Control": (
        "public, max-age=60, s-maxage=60, stale-while-revalidate=300"
    )
}


def _resp(status, body, extra_headers=None):
    headers = dict(_CORS)
    if extra_headers:
        headers.update(extra_headers)
    return {
        "statusCode": status,
        "body": json.dumps(body, default=str),
        "headers": headers,
    }


def _job_prefix(job):
    """Return the prefix holding this job's epochs.json (standalone preferred
    over the in-training live copy), or None if neither exists."""
    for pfx in PREFIXES:
        try:
            s3_client.head_object(
                Bucket=S3_CACHE_BUCKET, Key=f"{pfx}{job}/epochs.json"
            )
            return pfx
        except ClientError:
            continue
    return None


def _list_jobs():
    """Return job names that have an epochs.json under either prefix."""
    jobs = {}
    paginator = s3_client.get_paginator("list_objects_v2")
    for pfx in PREFIXES:
        for page in paginator.paginate(
            Bucket=S3_CACHE_BUCKET, Prefix=pfx, Delimiter="/"
        ):
            for cp in page.get("CommonPrefixes", []) or []:
                job = cp["Prefix"][len(pfx):].rstrip("/")
                if job in jobs:
                    continue  # already found (standalone wins)
                try:
                    s3_client.head_object(
                        Bucket=S3_CACHE_BUCKET,
                        Key=f"{pfx}{job}/epochs.json",
                    )
                    jobs[job] = pfx
                except ClientError:
                    continue
    return sorted(jobs)


def _get_json(key):
    obj = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
    return json.loads(obj["Body"].read().decode("utf-8"))


def handler(event, _context):
    http_method = (
        event.get("requestContext", {})
        .get("http", {})
        .get("method", "GET")
        .upper()
    )
    if http_method != "GET":
        return _resp(405, {"error": f"Method {http_method} not allowed"})

    params = event.get("queryStringParameters") or {}
    job = params.get("job")
    receipt_path = params.get("receipt_path")

    try:
        if not job:
            return _resp(200, {"jobs": _list_jobs()}, _CACHE)

        prefix = _job_prefix(job)
        if prefix is None:
            return _resp(404, {"error": f"Not found for job '{job}'"})

        # Guard against path traversal: confine reads to this job's prefix.
        if receipt_path:
            if ".." in receipt_path or receipt_path.startswith("/"):
                return _resp(400, {"error": "invalid receipt_path"})
            key = f"{prefix}{job}/{receipt_path}"
        else:
            key = f"{prefix}{job}/epochs.json"

        return _resp(200, _get_json(key), _CACHE)

    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        if code in ("NoSuchKey", "404", "NotFound"):
            return _resp(404, {"error": f"Not found for job '{job}'"})
        logger.error("S3 error: %s", e)
        return _resp(500, {"error": "Failed to retrieve epoch evaluation"})
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Unexpected error: %s", e, exc_info=True)
        return _resp(500, {"error": "Internal error"})
