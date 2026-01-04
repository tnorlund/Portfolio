"""Cache generator for geometric anomaly visualization.

This Lambda scans the label evaluator batch bucket for recent executions,
finds receipts with interesting geometric anomalies, and caches them
in a visualization-friendly format.
"""

import json
import logging
import math
import os
import random
import re
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
LABEL_EVALUATOR_BATCH_BUCKET = os.environ.get("LABEL_EVALUATOR_BATCH_BUCKET")
CACHE_PREFIX = "geometric-anomaly-cache/receipts/"
MAX_CACHED_RECEIPTS = 50  # Max number of receipts to keep in cache

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")
if not LABEL_EVALUATOR_BATCH_BUCKET:
    logger.error("LABEL_EVALUATOR_BATCH_BUCKET environment variable not set")

# Initialize S3 client
s3_client = boto3.client("s3")


def _list_recent_executions() -> list[str]:
    """List recent execution IDs from the batch bucket.

    Returns:
        List of execution IDs (folder prefixes) sorted by recency
    """
    execution_ids = set()
    try:
        # List objects with common prefixes (execution IDs are folders)
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=LABEL_EVALUATOR_BATCH_BUCKET,
            Prefix="results/",
            Delimiter="/",
        ):
            for prefix in page.get("CommonPrefixes", []):
                # Extract execution ID from "results/exec_id/"
                execution_id = prefix.get("Prefix", "").split("/")[1]
                if execution_id:
                    execution_ids.add(execution_id)
    except ClientError:
        logger.exception("Error listing executions")
    # Sort by name descending to get most recent first (names include timestamps)
    return sorted(execution_ids, reverse=True)


def _list_result_files(execution_id: str) -> list[str]:
    """List result files for a given execution.

    Returns:
        List of S3 keys for result files
    """
    keys = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        prefix = f"results/{execution_id}/"
        for page in paginator.paginate(
            Bucket=LABEL_EVALUATOR_BATCH_BUCKET, Prefix=prefix
        ):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if key.endswith(".json"):
                    keys.append(key)
    except ClientError:
        logger.exception("Error listing result files for %s", execution_id)
    return keys


def _load_json_from_batch_bucket(key: str) -> dict[str, Any] | None:
    """Load JSON from the batch bucket."""
    try:
        response = s3_client.get_object(
            Bucket=LABEL_EVALUATOR_BATCH_BUCKET, Key=key
        )
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError:
        logger.exception("Error loading %s", key)
        return None


def _find_receipts_with_anomalies(
    execution_id: str, max_count: int = 10
) -> list[dict[str, Any]]:
    """Find receipts with geometric anomalies in an execution.

    Args:
        execution_id: Execution ID to scan
        max_count: Maximum number of receipts to return

    Returns:
        List of receipt info dicts with anomaly details
    """
    result_files = _list_result_files(execution_id)
    receipts_with_anomalies = []

    for result_key in result_files:
        result_data = _load_json_from_batch_bucket(result_key)
        if not result_data:
            continue

        issues = result_data.get("issues", [])
        if not issues:
            continue

        # Look for geometric anomaly issues
        geometric_issues = [
            issue
            for issue in issues
            if issue.get("type")
            in (
                "geometric_anomaly",
                "position_anomaly",
                "constellation_anomaly",
            )
        ]

        if geometric_issues:
            # Extract image_id and receipt_id from filename
            # Format: results/{exec_id}/{image_id}_{receipt_id}.json
            filename = result_key.split("/")[-1].replace(".json", "")
            parts = filename.rsplit("_", 1)
            if len(parts) == 2:
                image_id, receipt_id_str = parts
                try:
                    receipt_id = int(receipt_id_str)
                except ValueError:
                    continue

                receipts_with_anomalies.append({
                    "execution_id": execution_id,
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "result_key": result_key,
                    "issues": geometric_issues,
                    "all_issues": issues,
                })

        if len(receipts_with_anomalies) >= max_count:
            break

    return receipts_with_anomalies


def _parse_geometric_reasoning(reasoning: str) -> dict[str, Any]:
    """Parse expected/actual values from geometric anomaly reasoning string.

    The reasoning format is:
    "'{word}' labeled {label} has unusual geometric relationship with {other_label}.
    Expected position ({mean_dx}, {mean_dy}), actual ({actual_dx}, {actual_dy}),
    deviation {deviation} (adaptive threshold: {threshold_std}σ = {threshold_value})."

    Returns:
        Dict with reference_label, expected, actual, z_score, threshold
    """
    result = {
        "reference_label": None,
        "current_label": None,
        "expected": {"dx": 0, "dy": 0},
        "actual": {"dx": 0, "dy": 0},
        "z_score": 2.5,
        "threshold": 2.0,
    }

    # Extract current label and reference label
    # Pattern: "labeled {label} has unusual geometric relationship with {other_label}"
    label_match = re.search(
        r"labeled\s+(\w+)\s+has unusual geometric relationship with\s+(\w+)",
        reasoning,
    )
    if label_match:
        result["current_label"] = label_match.group(1)
        result["reference_label"] = label_match.group(2)

    # Extract expected position: "Expected position (x, y)"
    expected_match = re.search(
        r"Expected position\s*\(([+-]?\d*\.?\d+),\s*([+-]?\d*\.?\d+)\)",
        reasoning,
    )
    if expected_match:
        result["expected"] = {
            "dx": float(expected_match.group(1)),
            "dy": float(expected_match.group(2)),
        }

    # Extract actual position: "actual (x, y)"
    actual_match = re.search(
        r"actual\s*\(([+-]?\d*\.?\d+),\s*([+-]?\d*\.?\d+)\)",
        reasoning,
    )
    if actual_match:
        result["actual"] = {
            "dx": float(actual_match.group(1)),
            "dy": float(actual_match.group(2)),
        }

    # Extract deviation and threshold: "deviation 0.172 (adaptive threshold: 1.5σ = 0.084)"
    # or "deviation 0.765 (threshold: 2.0σ = 0.367)"
    deviation_match = re.search(
        r"deviation\s+([+-]?\d*\.?\d+)\s*\([^)]*?(\d+\.?\d*)σ",
        reasoning,
    )
    if deviation_match:
        deviation = float(deviation_match.group(1))
        threshold_sigma = float(deviation_match.group(2))
        result["threshold"] = threshold_sigma
        # Calculate z-score from deviation and threshold
        threshold_value_match = re.search(r"=\s*([+-]?\d*\.?\d+)\)", reasoning)
        if threshold_value_match:
            threshold_value = float(threshold_value_match.group(1))
            if threshold_value > 0:
                std_deviation = threshold_value / threshold_sigma
                result["z_score"] = (
                    deviation / std_deviation if std_deviation > 0 else 2.5
                )

    return result


def _parse_constellation_reasoning(reasoning: str) -> dict[str, Any]:
    """Parse expected/actual values from constellation anomaly reasoning string.

    The reasoning format is:
    "'{word}' labeled {label} has unusual position within constellation
    [{constellation_str}]. Expected offset ({mean_dx}, {mean_dy}) from cluster
    center, actual ({actual_dx}, {actual_dy}). Deviation {deviation} exceeds
    {threshold_std}σ threshold ({threshold_value})."

    Returns:
        Dict with constellation_labels, expected, actual, deviation, threshold
    """
    result = {
        "labels": [],
        "flagged_label": None,
        "expected": {"dx": 0, "dy": 0},
        "actual": {"dx": 0, "dy": 0},
        "deviation": 0,
        "threshold": 2.0,
    }

    # Extract flagged label: "labeled {label} has unusual position"
    label_match = re.search(r"labeled\s+(\w+)\s+has unusual position", reasoning)
    if label_match:
        result["flagged_label"] = label_match.group(1)

    # Extract constellation labels: "within constellation [{labels}]"
    constellation_match = re.search(r"within constellation\s*\[([^\]]+)\]", reasoning)
    if constellation_match:
        labels_str = constellation_match.group(1)
        # Split by " + " to get individual labels
        result["labels"] = [lbl.strip() for lbl in labels_str.split("+")]

    # Extract expected offset: "Expected offset (x, y)"
    expected_match = re.search(
        r"Expected offset\s*\(([+-]?\d*\.?\d+),\s*([+-]?\d*\.?\d+)\)",
        reasoning,
    )
    if expected_match:
        result["expected"] = {
            "dx": float(expected_match.group(1)),
            "dy": float(expected_match.group(2)),
        }

    # Extract actual offset: "actual (x, y)"
    actual_match = re.search(
        r"actual\s*\(([+-]?\d*\.?\d+),\s*([+-]?\d*\.?\d+)\)",
        reasoning,
    )
    if actual_match:
        result["actual"] = {
            "dx": float(actual_match.group(1)),
            "dy": float(actual_match.group(2)),
        }

    # Extract deviation: "Deviation {value}"
    deviation_match = re.search(r"Deviation\s+([+-]?\d*\.?\d+)", reasoning)
    if deviation_match:
        result["deviation"] = float(deviation_match.group(1))

    # Extract threshold: "{sigma}σ threshold"
    threshold_match = re.search(r"(\d+\.?\d*)σ\s+threshold", reasoning)
    if threshold_match:
        result["threshold"] = float(threshold_match.group(1))

    return result


def _convert_polar_to_cartesian(angle: float, distance: float) -> tuple[float, float]:
    """Convert polar coordinates to Cartesian.

    Args:
        angle: Angle in degrees (0° = right, 90° = down)
        distance: Distance (normalized 0-1)

    Returns:
        (dx, dy) tuple
    """
    radians = math.radians(angle)
    dx = distance * math.cos(radians)
    dy = distance * math.sin(radians)
    return (dx, dy)


def _build_visualization_data(
    execution_id: str,
    image_id: str,
    receipt_id: int,
    result_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Build visualization-ready data for a receipt.

    Args:
        execution_id: Execution ID
        image_id: Image ID
        receipt_id: Receipt ID
        result_data: Raw result data from evaluation

    Returns:
        Formatted visualization data or None if data is incomplete
    """
    # Load receipt data (words)
    data_key = f"data/{execution_id}/{image_id}_{receipt_id}.json"
    receipt_data = _load_json_from_batch_bucket(data_key)
    if not receipt_data:
        logger.warning("Could not load receipt data: %s", data_key)
        return None

    # Load patterns
    # Try to find patterns file by scanning patterns folder
    patterns_data = None
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=LABEL_EVALUATOR_BATCH_BUCKET,
            Prefix=f"patterns/{execution_id}/",
        ):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if key.endswith(".json"):
                    patterns_data = _load_json_from_batch_bucket(key)
                    break
            if patterns_data:
                break
    except ClientError:
        logger.warning("Could not find patterns for execution %s", execution_id)

    # Extract words
    words_raw = receipt_data.get("words", [])
    labels_raw = receipt_data.get("labels", [])
    place = receipt_data.get("place", {})
    merchant_name = place.get("merchant_name", "Unknown") if place else "Unknown"

    # Build label lookup by word position
    labels_by_word: dict[tuple[int, int], dict[str, Any]] = {}
    for label in labels_raw:
        key = (label.get("line_id", 0), label.get("word_id", 0))
        # Keep most recent label (or first if no timestamp)
        if key not in labels_by_word:
            labels_by_word[key] = label

    # Find flagged word IDs from issues
    flagged_word_ids = set()
    flagged_issues_by_word: dict[tuple[int, int], dict[str, Any]] = {}
    for issue in result_data.get("issues", []):
        word_id = issue.get("word_id")
        line_id = issue.get("line_id")
        if word_id is not None and line_id is not None:
            key = (line_id, word_id)
            flagged_word_ids.add(key)
            flagged_issues_by_word[key] = issue

    # Transform words for visualization
    words_viz = []
    for word in words_raw:
        line_id = word.get("line_id", 0)
        word_id = word.get("word_id", 0)
        key = (line_id, word_id)
        bbox = word.get("bounding_box", {})

        label_info = labels_by_word.get(key, {})
        is_flagged = key in flagged_word_ids
        issue_info = flagged_issues_by_word.get(key, {})

        words_viz.append({
            "word_id": word_id,
            "line_id": line_id,
            "text": word.get("text", ""),
            "x": bbox.get("x", 0),
            "y": bbox.get("y", 0),
            "width": bbox.get("width", 0),
            "height": bbox.get("height", 0),
            "label": label_info.get("label"),
            "is_flagged": is_flagged,
            "anomaly_type": issue_info.get("type") if is_flagged else None,
            "reasoning": issue_info.get("reasoning") if is_flagged else None,
        })

    # Build patterns data for visualization
    patterns_viz = {"label_pairs": []}

    if patterns_data and patterns_data.get("patterns"):
        raw_patterns = patterns_data["patterns"]
        label_pair_geometry = raw_patterns.get("label_pair_geometry", [])

        # Handle both list format (serialized) and dict format (legacy)
        if isinstance(label_pair_geometry, list):
            # New format: list of objects with "labels" key
            for geometry in label_pair_geometry:
                labels = geometry.get("labels", [])
                if len(labels) != 2:
                    continue
                from_label, to_label = labels

                mean_dx = geometry.get("mean_dx", 0)
                mean_dy = geometry.get("mean_dy", 0)
                # Compute std_deviation from std_dx/std_dy if not present
                std_deviation = geometry.get("std_deviation")
                if std_deviation is None:
                    std_dx = geometry.get("std_dx", 0)
                    std_dy = geometry.get("std_dy", 0)
                    std_deviation = math.sqrt(std_dx**2 + std_dy**2)

                patterns_viz["label_pairs"].append({
                    "from_label": from_label,
                    "to_label": to_label,
                    "observations": [],  # Not stored in serialized form
                    "mean": {"dx": mean_dx, "dy": mean_dy},
                    "std_deviation": std_deviation,
                })
        else:
            # Legacy format: dict with pair_key -> geometry
            for pair_key, geometry in label_pair_geometry.items():
                if isinstance(pair_key, str):
                    try:
                        pair_key = eval(pair_key)  # noqa: S307
                    except Exception:
                        continue
                from_label, to_label = pair_key

                mean_dx = geometry.get("mean_dx", 0)
                mean_dy = geometry.get("mean_dy", 0)
                std_deviation = geometry.get("std_deviation", 0)

                patterns_viz["label_pairs"].append({
                    "from_label": from_label,
                    "to_label": to_label,
                    "observations": [],
                    "mean": {"dx": mean_dx, "dy": mean_dy},
                    "std_deviation": std_deviation,
                })

    # Extract constellation geometry from patterns file
    constellation_viz = None
    if patterns_data and patterns_data.get("patterns"):
        raw_patterns = patterns_data["patterns"]
        constellation_geometry = raw_patterns.get("constellation_geometry", [])
        if constellation_geometry:
            # Take the first constellation as the main one to visualize
            for cg in constellation_geometry:
                labels = cg.get("labels", [])
                if len(labels) >= 3:
                    relative_positions = cg.get("relative_positions", {})
                    constellation_viz = {
                        "labels": labels,
                        "observation_count": cg.get("observation_count", 0),
                        "members": [],
                    }
                    for lbl in labels:
                        rel_pos = relative_positions.get(lbl, {})
                        constellation_viz["members"].append({
                            "label": lbl,
                            "expected": {
                                "dx": rel_pos.get("mean_dx", 0),
                                "dy": rel_pos.get("mean_dy", 0),
                            },
                            "std_deviation": rel_pos.get("std_deviation", 0.1),
                        })
                    break

    # Find the first flagged word with geometric details for highlighting
    flagged_word_detail = None
    constellation_anomaly_detail = None

    for issue in result_data.get("issues", []):
        issue_type = issue.get("type")

        # Handle geometric_anomaly
        if issue_type == "geometric_anomaly" and flagged_word_detail is None:
            word_id = issue.get("word_id")
            line_id = issue.get("line_id")
            reasoning = issue.get("reasoning", "")
            if word_id is not None and line_id is not None:
                # Parse expected/actual values from the reasoning string
                parsed = _parse_geometric_reasoning(reasoning)
                flagged_word_detail = {
                    "word_id": word_id,
                    "line_id": line_id,
                    "current_label": parsed["current_label"],
                    "reference_label": parsed["reference_label"],
                    "expected": parsed["expected"],
                    "actual": parsed["actual"],
                    "z_score": parsed["z_score"],
                    "threshold": parsed["threshold"],
                    "reasoning": reasoning,
                }

                # Ensure the flagged word's pattern exists in patterns_viz
                ref_label = parsed["reference_label"]
                cur_label = parsed["current_label"]
                if ref_label and cur_label:
                    pattern_exists = any(
                        p["from_label"] == ref_label and p["to_label"] == cur_label
                        for p in patterns_viz["label_pairs"]
                    )
                    if not pattern_exists:
                        threshold_match = re.search(
                            r"(\d+\.?\d*)σ\s*=\s*(\d+\.?\d+)",
                            reasoning,
                        )
                        std_dev = 0.1
                        if threshold_match:
                            threshold_sigma = float(threshold_match.group(1))
                            threshold_value = float(threshold_match.group(2))
                            if threshold_sigma > 0:
                                std_dev = threshold_value / threshold_sigma

                        patterns_viz["label_pairs"].insert(0, {
                            "from_label": ref_label,
                            "to_label": cur_label,
                            "observations": [],
                            "mean": parsed["expected"],
                            "std_deviation": std_dev,
                        })

        # Handle constellation_anomaly
        elif issue_type == "constellation_anomaly" and constellation_anomaly_detail is None:
            word_id = issue.get("word_id")
            line_id = issue.get("line_id")
            reasoning = issue.get("reasoning", "")
            if word_id is not None and line_id is not None:
                parsed = _parse_constellation_reasoning(reasoning)
                constellation_anomaly_detail = {
                    "word_id": word_id,
                    "line_id": line_id,
                    "labels": parsed["labels"],
                    "flagged_label": parsed["flagged_label"],
                    "expected": parsed["expected"],
                    "actual": parsed["actual"],
                    "deviation": parsed["deviation"],
                    "threshold": parsed["threshold"],
                    "reasoning": reasoning,
                }

                # If we have constellation anomaly data but no viz, create one
                if constellation_viz is None and parsed["labels"]:
                    constellation_viz = {
                        "labels": parsed["labels"],
                        "observation_count": 0,
                        "members": [],
                    }
                    for lbl in parsed["labels"]:
                        is_flagged = lbl == parsed["flagged_label"]
                        constellation_viz["members"].append({
                            "label": lbl,
                            "expected": parsed["expected"] if is_flagged else {"dx": 0, "dy": 0},
                            "actual": parsed["actual"] if is_flagged else {"dx": 0, "dy": 0},
                            "is_flagged": is_flagged,
                            "std_deviation": 0.1,
                        })

    return {
        "receipt": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "words": words_viz,
        },
        "patterns": patterns_viz,
        "flagged_word": flagged_word_detail,
        "constellation": constellation_viz,
        "constellation_anomaly": constellation_anomaly_detail,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "execution_id": execution_id,
    }


def _save_to_cache(receipt_data: dict[str, Any]) -> bool:
    """Save receipt data to the cache bucket.

    Args:
        receipt_data: Formatted visualization data

    Returns:
        True if successful
    """
    image_id = receipt_data.get("receipt", {}).get("image_id", "unknown")
    receipt_id = receipt_data.get("receipt", {}).get("receipt_id", 0)
    key = f"{CACHE_PREFIX}receipt-{image_id}-{receipt_id}.json"

    try:
        s3_client.put_object(
            Bucket=S3_CACHE_BUCKET,
            Key=key,
            Body=json.dumps(receipt_data, default=str),
            ContentType="application/json",
        )
        logger.info("Cached receipt to %s", key)
        return True
    except ClientError:
        logger.exception("Error saving to cache: %s", key)
        return False


def _cleanup_old_cache() -> int:
    """Remove old cache entries if over limit.

    Returns:
        Number of entries removed
    """
    try:
        # List all cached entries
        response = s3_client.list_objects_v2(
            Bucket=S3_CACHE_BUCKET, Prefix=CACHE_PREFIX
        )
        objects = response.get("Contents", [])

        if len(objects) <= MAX_CACHED_RECEIPTS:
            return 0

        # Sort by last modified, oldest first
        objects.sort(key=lambda x: x.get("LastModified", datetime.min))

        # Delete oldest entries
        to_delete = objects[: len(objects) - MAX_CACHED_RECEIPTS]
        for obj in to_delete:
            s3_client.delete_object(Bucket=S3_CACHE_BUCKET, Key=obj["Key"])

        logger.info("Cleaned up %d old cache entries", len(to_delete))
        return len(to_delete)
    except ClientError:
        logger.exception("Error cleaning up cache")
        return 0


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Generate geometric anomaly cache.

    Scans recent label evaluator executions for receipts with geometric
    anomalies and caches them in visualization-ready format.
    """
    logger.info("Starting geometric anomaly cache generation")

    if not S3_CACHE_BUCKET or not LABEL_EVALUATOR_BATCH_BUCKET:
        return {
            "statusCode": 500,
            "error": "Missing environment variables",
        }

    # List recent executions
    execution_ids = _list_recent_executions()
    if not execution_ids:
        logger.warning("No executions found in batch bucket")
        return {
            "statusCode": 200,
            "message": "No executions found",
            "cached_count": 0,
        }

    logger.info("Found %d executions", len(execution_ids))

    # Find receipts with anomalies (limit to 3 most recent executions)
    all_anomaly_receipts = []
    for execution_id in execution_ids[:3]:
        anomaly_receipts = _find_receipts_with_anomalies(execution_id, max_count=20)
        all_anomaly_receipts.extend(anomaly_receipts)
        logger.info(
            "Found %d receipts with anomalies in execution %s",
            len(anomaly_receipts),
            execution_id,
        )

    if not all_anomaly_receipts:
        logger.warning("No receipts with geometric anomalies found")
        return {
            "statusCode": 200,
            "message": "No geometric anomalies found",
            "cached_count": 0,
        }

    # Sample if we have too many
    if len(all_anomaly_receipts) > MAX_CACHED_RECEIPTS:
        all_anomaly_receipts = random.sample(
            all_anomaly_receipts, MAX_CACHED_RECEIPTS
        )

    # Build and cache visualization data
    cached_count = 0
    for receipt_info in all_anomaly_receipts:
        # Load result data
        result_data = _load_json_from_batch_bucket(receipt_info["result_key"])
        if not result_data:
            continue

        # Build visualization data
        viz_data = _build_visualization_data(
            execution_id=receipt_info["execution_id"],
            image_id=receipt_info["image_id"],
            receipt_id=receipt_info["receipt_id"],
            result_data=result_data,
        )

        if viz_data and _save_to_cache(viz_data):
            cached_count += 1

    # Cleanup old cache entries
    cleaned_count = _cleanup_old_cache()

    logger.info(
        "Cache generation complete: %d cached, %d cleaned",
        cached_count,
        cleaned_count,
    )

    return {
        "statusCode": 200,
        "message": "Cache generation complete",
        "executions_scanned": len(execution_ids[:3]),
        "anomalies_found": len(all_anomaly_receipts),
        "cached_count": cached_count,
        "cleaned_count": cleaned_count,
    }
