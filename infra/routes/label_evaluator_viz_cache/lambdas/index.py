"""Lambda handler for serving label evaluator visualization cache.

Uses seed-based deterministic pagination for consistent random ordering.
Client generates a seed on mount, then paginates through shuffled results.

Query Parameters:
- batch_size: Number of receipts to return (default: 20, max: 50)
- seed: Random seed for deterministic shuffle (default: random)
- offset: Starting position in shuffled list (default: 0)
"""

import json
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
DEFAULT_BATCH_SIZE = 20
MAX_BATCH_SIZE = 50
RECEIPT_HEALTH_LEDGER_KEY = "receipt-health/issues/ledger.json"
RECEIPT_HEALTH_ELIGIBLE_KEY = "receipt-health/issues/eligible.json"
MAX_ISSUE_BATCH_SIZE = 100

# Map viz_type (last path segment) to S3 prefix
VIZ_TYPE_PREFIXES = {
    "financial_math": "financial-math/",
    "within_receipt": "within-receipt/",
    "receipt_health": "receipt-health/",
}

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")

# Initialize S3 client
s3_client = boto3.client("s3")


def _list_cached_receipts(prefix: str) -> list[str]:
    """List all cached receipt keys from S3 using paginator.

    Args:
        prefix: S3 key prefix to list under.

    Returns:
        List of S3 keys for cached receipt files.
    """
    keys: list[str] = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_CACHE_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                relative_key = (
                    key[len(prefix) :] if key.startswith(prefix) else key
                )
                is_top_level = "/" not in relative_key
                if (
                    is_top_level
                    and key.endswith(".json")
                    and relative_key != "metadata.json"
                ):
                    keys.append(key)
    except ClientError:
        logger.exception("Error listing cached receipts")
    return keys


def _fetch_receipt(key: str) -> dict[str, Any] | None:
    """Fetch a single receipt from S3.

    Args:
        key: S3 key for the receipt file.

    Returns:
        Receipt data dict, or None if fetch failed.
    """
    try:
        response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError:
        logger.exception("Error fetching receipt %s", key)
        return None


def _fetch_metadata(prefix: str) -> dict[str, Any]:
    """Fetch pool metadata from S3.

    Args:
        prefix: S3 key prefix (e.g. "financial-math/") to read metadata from.

    Returns:
        Metadata dict with version, execution_id, total_receipts, etc.
        Empty dict if metadata file not found.
    """
    key = f"{prefix}metadata.json"
    try:
        response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError:
        logger.warning("Could not fetch %s", key)
        return {}


def _fetch_json_key(key: str) -> dict[str, Any]:
    """Fetch a JSON object from the cache bucket."""
    try:
        response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError:
        logger.warning("Could not fetch %s", key)
        return {}


def _write_json_key(key: str, data: dict[str, Any]) -> None:
    """Write a JSON object to the cache bucket."""
    s3_client.put_object(
        Bucket=S3_CACHE_BUCKET,
        Key=key,
        Body=json.dumps(data, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def _response(
    status_code: int,
    body: dict[str, Any],
    *,
    cache_control: str | None = None,
) -> dict[str, Any]:
    """Build a JSON API Gateway response."""
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
    }
    if cache_control:
        headers["Cache-Control"] = cache_control
    return {
        "statusCode": status_code,
        "body": json.dumps(body, default=str),
        "headers": headers,
    }


def _parse_limit(
    query_params: dict[str, str],
    *,
    default: int,
    maximum: int,
) -> int:
    """Parse bounded integer limit from query params."""
    try:
        limit = int(query_params.get("limit", default))
    except (ValueError, TypeError):
        limit = default
    return max(1, min(limit, maximum))


def _ledger_issue_is_eligible(
    issue: dict[str, Any], max_attempts: int
) -> bool:
    """Return true when an automated routine may attempt an issue."""
    if issue.get("state") != "open":
        return False
    if int(issue.get("attempt_count") or 0) >= max_attempts:
        return False

    next_retry_after = issue.get("next_retry_after")
    if next_retry_after:
        try:
            retry_at = datetime.fromisoformat(
                str(next_retry_after).replace("Z", "+00:00")
            )
            if retry_at > datetime.now(timezone.utc):
                return False
        except ValueError:
            pass
    return True


def _filter_ledger_issues(
    ledger: dict[str, Any],
    query_params: dict[str, str],
) -> list[dict[str, Any]]:
    """Filter issue ledger entries for API consumers."""
    state = query_params.get("state", "eligible")
    check_id = query_params.get("check_id")
    image_id = query_params.get("image_id")
    max_attempts = int(ledger.get("max_attempts") or 2)

    issues = []
    for issue in ledger.get("issues", []):
        if state == "eligible":
            if not _ledger_issue_is_eligible(issue, max_attempts):
                continue
        elif state != "all" and issue.get("state") != state:
            continue

        if check_id and issue.get("check_id") != check_id:
            continue
        if image_id and str(issue.get("image_id")) != image_id:
            continue
        issues.append(issue)

    issues.sort(
        key=lambda issue: (
            int(issue.get("attempt_count") or 0),
            str(issue.get("last_seen_at") or ""),
            str(issue.get("merchant_name") or ""),
            str(issue.get("issue_id") or ""),
        )
    )
    return issues


def _summarize_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    """Recalculate compact ledger counts after an API update."""
    by_state: dict[str, int] = {}
    by_check: dict[str, int] = {}
    max_attempts = int(ledger.get("max_attempts") or 2)
    issues = ledger.get("issues", [])

    for issue in issues:
        state = str(issue.get("state") or "open")
        check_id = str(issue.get("check_id") or "unknown")
        by_state[state] = by_state.get(state, 0) + 1
        by_check[check_id] = by_check.get(check_id, 0) + 1

    return {
        "total_issues": len(issues),
        "by_state": dict(sorted(by_state.items())),
        "by_check": dict(sorted(by_check.items())),
        "eligible_issues": sum(
            1
            for issue in issues
            if _ledger_issue_is_eligible(issue, max_attempts)
        ),
    }


def _handle_receipt_health_issues_get(
    query_params: dict[str, str],
) -> dict[str, Any]:
    """Serve current ledger issues or immutable issues for one execution."""
    execution_id = query_params.get("execution_id")
    if execution_id:
        key = f"receipt-health/runs/{execution_id}/issues.json"
        run_issues = _fetch_json_key(key)
        if not run_issues:
            return _response(
                404,
                {
                    "error": "No receipt health issues found for execution",
                    "execution_id": execution_id,
                },
            )
        return _response(
            200,
            {
                **run_issues,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
            cache_control="public, max-age=60, s-maxage=60, stale-while-revalidate=300",
        )

    ledger = _fetch_json_key(RECEIPT_HEALTH_LEDGER_KEY)
    if not ledger:
        return _response(
            404,
            {
                "error": "No receipt health issue ledger found",
                "message": "Run the label evaluator to generate the ledger.",
            },
        )

    limit = _parse_limit(
        query_params,
        default=10,
        maximum=MAX_ISSUE_BATCH_SIZE,
    )
    issues = _filter_ledger_issues(ledger, query_params)[:limit]
    return _response(
        200,
        {
            "issues": issues,
            "count": len(issues),
            "limit": limit,
            "state": query_params.get("state", "eligible"),
            "summary": ledger.get("summary", {}),
            "latest_execution_id": ledger.get("latest_execution_id"),
            "updated_at": ledger.get("updated_at"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        cache_control="no-store",
    )


def _append_attempt(
    issue: dict[str, Any],
    *,
    attempted_at: str,
    body: dict[str, Any],
) -> list[dict[str, Any]]:
    """Append one cleanup attempt to an issue ledger entry."""
    attempts = list(issue.get("attempts") or [])
    attempt = {
        "attempted_at": attempted_at,
        "agent": body.get("agent", "receipt-label-fixer"),
        "agent_run_id": body.get("agent_run_id"),
        "execution_id": body.get("execution_id")
        or issue.get("last_seen_execution_id"),
        "summary": body.get("attempt_summary") or body.get("summary"),
        "actions": body.get("actions") or [],
    }
    attempts.append(attempt)
    return attempts[-10:]


def _apply_issue_update(
    ledger: dict[str, Any],
    body: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    """Apply a claim/attempt/manual update to one ledger issue."""
    issue_id = body.get("issue_id")
    if not issue_id:
        return ledger, None, "issue_id is required"

    action = body.get("action", "mark_attempted")
    now_iso = datetime.now(timezone.utc).isoformat()
    updated_issue: dict[str, Any] | None = None
    issues = []

    for issue in ledger.get("issues", []):
        if issue.get("issue_id") != issue_id:
            issues.append(issue)
            continue

        next_issue = dict(issue)
        if action == "claim":
            next_issue["state"] = "claimed"
            next_issue["claimed_at"] = now_iso
            next_issue["claimed_by"] = body.get("agent", "receipt-label-fixer")
        elif action == "mark_attempted":
            next_issue["state"] = "awaiting_validation"
            next_issue["last_attempted_at"] = now_iso
            next_issue["last_attempted_execution_id"] = body.get(
                "execution_id"
            ) or issue.get("last_seen_execution_id")
            next_issue["last_attempt_summary"] = body.get(
                "attempt_summary"
            ) or body.get("summary")
            next_issue["attempt_count"] = (
                int(issue.get("attempt_count") or 0) + 1
            )
            next_issue["attempts"] = _append_attempt(
                issue,
                attempted_at=now_iso,
                body=body,
            )
        elif action == "manual_review":
            next_issue["state"] = "manual_review"
            next_issue["manual_review_reason"] = body.get("reason")
            next_issue["manual_review_at"] = now_iso
        elif action == "blocked":
            next_issue["state"] = "blocked"
            next_issue["blocked_reason"] = body.get("reason")
            next_issue["blocked_at"] = now_iso
        else:
            return ledger, None, f"Unsupported action: {action}"

        updated_issue = next_issue
        issues.append(next_issue)

    if updated_issue is None:
        return ledger, None, f"Issue not found: {issue_id}"

    ledger = {
        **ledger,
        "updated_at": now_iso,
        "issues": issues,
    }
    ledger["summary"] = _summarize_ledger(ledger)
    return ledger, updated_issue, None


def _handle_receipt_health_issues_post(
    event: dict[str, Any],
) -> dict[str, Any]:
    """Update one receipt health ledger issue after an agent action."""
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    ledger = _fetch_json_key(RECEIPT_HEALTH_LEDGER_KEY)
    if not ledger:
        return _response(
            404,
            {
                "error": "No receipt health issue ledger found",
                "message": "Run the label evaluator to generate the ledger.",
            },
        )

    ledger, issue, error = _apply_issue_update(ledger, body)
    if error:
        return _response(400, {"error": error})

    _write_json_key(RECEIPT_HEALTH_LEDGER_KEY, ledger)
    _write_json_key(
        RECEIPT_HEALTH_ELIGIBLE_KEY,
        {
            "execution_id": ledger.get("latest_execution_id"),
            "cached_at": ledger.get("updated_at"),
            "summary": ledger.get("summary", {}),
            "issues": _filter_ledger_issues(ledger, {"state": "eligible"}),
        },
    )
    return _response(
        200,
        {
            "issue": issue,
            "summary": ledger.get("summary", {}),
            "updated_at": ledger.get("updated_at"),
        },
        cache_control="no-store",
    )


def _calculate_aggregate_stats(
    receipts: list[dict[str, Any]], pool_size: int
) -> dict[str, Any]:
    """Calculate aggregate statistics across the fetched batch.

    Args:
        receipts: List of receipt dicts in the current batch.
        pool_size: Total number of receipts in the cache pool.

    Returns:
        Dict with aggregate statistics.
    """
    if not receipts:
        return {"total_receipts_in_pool": pool_size, "batch_size": 0}

    if receipts and "overall_status" in receipts[0]:
        statuses = [
            str(r.get("overall_status") or "not_applicable") for r in receipts
        ]
        issue_counts = [
            int((r.get("summary") or {}).get("issue_count") or 0)
            for r in receipts
        ]
        return {
            "total_receipts_in_pool": pool_size,
            "batch_size": len(receipts),
            "passed": sum(1 for s in statuses if s == "pass"),
            "needs_review": sum(1 for s in statuses if s == "review"),
            "failed": sum(1 for s in statuses if s == "fail"),
            "not_applicable": sum(
                1 for s in statuses if s == "not_applicable"
            ),
            "receipts_with_issues": sum(
                1 for count in issue_counts if count > 0
            ),
            "total_issues": sum(issue_counts),
        }

    issues = [r.get("issues_found", 0) for r in receipts]
    return {
        "total_receipts_in_pool": pool_size,
        "batch_size": len(receipts),
        "avg_issues": sum(issues) / len(issues) if issues else 0,
        "max_issues": max(issues) if issues else 0,
        "receipts_with_issues": sum(1 for i in issues if i > 0),
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Handle API Gateway requests for label evaluator visualization cache.

    Uses seed-based deterministic pagination. Client generates a seed on mount,
    then paginates through the shuffled results with consistent ordering.

    Query Parameters:
        batch_size: Number of receipts to return (default: 20, max: 50)
        seed: Random seed for deterministic shuffle (default: server generates one)
        offset: Starting position in shuffled list (default: 0)

    Response structure:
    {
        "receipts": [...],
        "total_count": int,      # Total receipts in cache
        "offset": int,           # Current offset
        "has_more": bool,        # Whether more pages exist
        "seed": int,             # Seed used (return to client for pagination)
        "aggregate_stats": {...},
        "execution_id": str | null,
        "cached_at": str | null,
        "fetched_at": str
    }
    """
    logger.info("Received event: %s", event)

    # Handle API Gateway v2 event format
    try:
        http_method = event["requestContext"]["http"]["method"].upper()
    except (KeyError, TypeError):
        logger.exception("Invalid event structure")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid event structure"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    # Determine S3 prefix from the API path (last segment)
    path = event["requestContext"]["http"]["path"]
    viz_type = path.rstrip("/").split("/")[-1]

    if http_method not in {"GET", "POST"}:
        return {
            "statusCode": 405,
            "body": json.dumps({"error": f"Method {http_method} not allowed"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    if http_method == "POST" and viz_type != "receipt_health_issues":
        return {
            "statusCode": 405,
            "body": json.dumps({"error": f"Method {http_method} not allowed"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    if not S3_CACHE_BUCKET:
        logger.error("S3_CACHE_BUCKET environment variable not set")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {"error": "Configuration error: S3_CACHE_BUCKET not set"}
            ),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    if viz_type == "receipt_health_issues":
        if http_method == "POST":
            return _handle_receipt_health_issues_post(event)
        return _handle_receipt_health_issues_get(
            event.get("queryStringParameters") or {}
        )

    if http_method != "GET":
        return {
            "statusCode": 405,
            "body": json.dumps({"error": f"Method {http_method} not allowed"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    prefix = VIZ_TYPE_PREFIXES.get(viz_type, "receipts/")

    try:
        # Parse query params
        query_params = event.get("queryStringParameters") or {}

        # batch_size: number of receipts per page
        try:
            batch_size = int(
                query_params.get("batch_size", DEFAULT_BATCH_SIZE)
            )
            batch_size = max(1, min(batch_size, MAX_BATCH_SIZE))
        except (ValueError, TypeError):
            batch_size = DEFAULT_BATCH_SIZE

        # seed: for deterministic shuffle (client sends same seed for pagination)
        try:
            seed = int(query_params.get("seed", random.randint(0, 2**31 - 1)))
        except (ValueError, TypeError):
            seed = random.randint(0, 2**31 - 1)

        # offset: starting position in shuffled list
        try:
            offset = int(query_params.get("offset", 0))
            offset = max(0, offset)
        except (ValueError, TypeError):
            offset = 0

        logger.info(
            "batch_size=%d, seed=%d, offset=%d", batch_size, seed, offset
        )

        # Filter by image_id if provided
        filter_image_id = query_params.get("image_id")

        # List all cached receipts
        cached_keys = _list_cached_receipts(prefix)
        if not cached_keys:
            logger.warning("No cached receipts found in %s", prefix)
            return {
                "statusCode": 404,
                "body": json.dumps(
                    {
                        "error": "No cached receipts found",
                        "message": "Run the label evaluator with analytics enabled to generate the cache.",
                    }
                ),
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            }

        # If image_id filter provided, return only matching receipts
        if filter_image_id:
            cached_keys = [
                k
                for k in cached_keys
                if k.split("/")[-1].startswith(filter_image_id)
            ]
            if not cached_keys:
                return {
                    "statusCode": 404,
                    "body": json.dumps(
                        {
                            "error": f"No cached receipts found for image_id {filter_image_id}"
                        }
                    ),
                    "headers": {
                        "Content-Type": "application/json",
                        "Access-Control-Allow-Origin": "*",
                    },
                }

        # Sort keys first for consistency, then shuffle with seed
        total_count = len(cached_keys)
        sorted_keys = sorted(cached_keys)

        # Deterministic shuffle using the provided seed
        rng = random.Random(seed)
        rng.shuffle(sorted_keys)

        # Slice based on offset
        end_offset = min(offset + batch_size, total_count)
        selected_keys = sorted_keys[offset:end_offset]
        has_more = end_offset < total_count

        logger.info(
            "Returning receipts [%d:%d] of %d (has_more=%s)",
            offset,
            end_offset,
            total_count,
            has_more,
        )

        # Fetch selected receipts in parallel for better latency
        # Use dict to map results back to original key order (as_completed returns in completion order)
        max_workers = min(len(selected_keys), 10)
        key_to_receipt: dict[str, dict[str, Any] | None] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_fetch_receipt, key): key
                for key in selected_keys
            }
            for future in as_completed(futures):
                key = futures[future]
                key_to_receipt[key] = future.result()

        # Rebuild list in original selected_keys order to preserve seed-based deterministic ordering
        receipts: list[dict[str, Any]] = [
            key_to_receipt[key]
            for key in selected_keys
            if key_to_receipt.get(key)
        ]

        # Get metadata and build response
        metadata = _fetch_metadata(prefix)
        aggregate_stats = _calculate_aggregate_stats(receipts, total_count)

        response_data = {
            "receipts": receipts,
            "total_count": total_count,
            "offset": offset,
            "has_more": has_more,
            "seed": seed,
            "aggregate_stats": aggregate_stats,
            "execution_id": metadata.get("execution_id"),
            "cached_at": metadata.get("cached_at"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "Returning %d receipts from execution %s",
            len(receipts),
            metadata.get("execution_id", "unknown"),
        )

        return {
            "statusCode": 200,
            "body": json.dumps(response_data, default=str),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=60, s-maxage=60, stale-while-revalidate=300",
            },
        }

    except ClientError:
        logger.exception("S3 error")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "S3 error occurred"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }
    except Exception:
        logger.exception("Unexpected error")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }
