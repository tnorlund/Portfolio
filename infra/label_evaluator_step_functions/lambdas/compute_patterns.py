"""Compute merchant patterns Lambda handler.

This Lambda computes spatial patterns for a merchant from training receipts
and saves them to S3. Patterns are computed ONCE per Step Function execution
and shared across all receipt evaluations.

This is the memory-intensive step - give it 10GB and longer timeout.
Receipt evaluation Lambdas then just load the pre-computed patterns.
"""

import json
import logging
import os
import time
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Compute merchant patterns from training receipts.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "max_training_receipts": 50
    }

    Output:
    {
        "patterns_s3_key": "patterns/{exec}/{merchant_hash}.json",
        "merchant_name": "Sprouts Farmers Market",
        "receipt_count": 45,
        "pattern_stats": {
            "label_positions": 12,
            "label_pairs": 8,
            "constellations": 3
        }
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name")
    max_training_receipts = event.get("max_training_receipts", 50)

    if not merchant_name:
        raise ValueError("merchant_name is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    start_time = time.time()
    logger.info(
        f"Computing patterns for merchant '{merchant_name}' "
        f"(max_receipts={max_training_receipts})"
    )

    # Import DynamoDB client
    from receipt_dynamo import DynamoClient

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    dynamo = DynamoClient(table_name=table_name)

    # Load training receipts
    from receipt_agent.agents.label_evaluator.state import OtherReceiptData

    other_receipt_data: list[OtherReceiptData] = []
    last_key = None

    while len(other_receipt_data) < max_training_receipts:
        places, last_key = dynamo.get_receipt_places_by_merchant(
            merchant_name,
            limit=min(100, max_training_receipts - len(other_receipt_data)),
            last_evaluated_key=last_key,
        )

        if not places:
            break

        for place in places:
            if len(other_receipt_data) >= max_training_receipts:
                break

            try:
                words = dynamo.list_receipt_words_from_receipt(
                    place.image_id, place.receipt_id
                )
                labels, _ = dynamo.list_receipt_word_labels_for_receipt(
                    place.image_id, place.receipt_id
                )

                other_receipt_data.append(
                    OtherReceiptData(
                        place=place,
                        words=words,
                        labels=labels,
                    )
                )
            except Exception as e:
                logger.warning(
                    f"Error loading receipt {place.image_id}#{place.receipt_id}: {e}"
                )
                continue

        if not last_key:
            break

    logger.info(f"Loaded {len(other_receipt_data)} training receipts")

    if not other_receipt_data:
        # No training data - save empty patterns
        patterns_s3_key = f"patterns/{execution_id}/{_hash_merchant(merchant_name)}.json"
        s3.put_object(
            Bucket=batch_bucket,
            Key=patterns_s3_key,
            Body=json.dumps({"patterns": None, "merchant_name": merchant_name}),
            ContentType="application/json",
        )
        return {
            "patterns_s3_key": patterns_s3_key,
            "merchant_name": merchant_name,
            "receipt_count": 0,
            "pattern_stats": None,
        }

    # Compute patterns
    logger.info("Computing merchant patterns...")
    compute_start = time.time()

    from receipt_agent.agents.label_evaluator.helpers import compute_merchant_patterns

    patterns = compute_merchant_patterns(
        other_receipt_data,
        merchant_name,
        max_pair_patterns=4,
        max_relationship_dimension=2,
    )

    compute_time = time.time() - compute_start
    logger.info(f"Pattern computation completed in {compute_time:.2f}s")

    # Serialize patterns to S3
    patterns_s3_key = f"patterns/{execution_id}/{_hash_merchant(merchant_name)}.json"
    patterns_data = _serialize_patterns(patterns, merchant_name)

    s3.put_object(
        Bucket=batch_bucket,
        Key=patterns_s3_key,
        Body=json.dumps(patterns_data, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )

    total_time = time.time() - start_time
    logger.info(
        f"Saved patterns to s3://{batch_bucket}/{patterns_s3_key} "
        f"(total time: {total_time:.2f}s)"
    )

    pattern_stats = None
    if patterns:
        pattern_stats = {
            "label_positions": len(patterns.label_positions),
            "label_pairs": len(patterns.label_pair_geometry),
            "observed_pairs": len(patterns.all_observed_pairs),
            "receipt_count": patterns.receipt_count,
        }

    return {
        "patterns_s3_key": patterns_s3_key,
        "merchant_name": merchant_name,
        "receipt_count": len(other_receipt_data),
        "pattern_stats": pattern_stats,
        "compute_time_seconds": round(compute_time, 2),
    }


def _hash_merchant(merchant_name: str) -> str:
    """Create a short hash for merchant name (for S3 key)."""
    import hashlib

    return hashlib.md5(merchant_name.encode()).hexdigest()[:12]


def _serialize_patterns(patterns, merchant_name: str) -> dict[str, Any]:
    """Serialize MerchantPatterns to JSON-safe dict."""
    import statistics

    if patterns is None:
        return {"patterns": None, "merchant_name": merchant_name}

    # Compute label position statistics from raw y-values
    label_position_stats = {}
    for label, y_positions in patterns.label_positions.items():
        if y_positions:
            mean_y = statistics.mean(y_positions)
            std_y = statistics.stdev(y_positions) if len(y_positions) > 1 else 0.0
            label_position_stats[label] = {
                "mean_y": mean_y,
                "std_y": std_y,
                "count": len(y_positions),
            }

    # Serialize label pair geometry (Dict[tuple, LabelPairGeometry])
    label_pair_geometry_list = []
    for pair_tuple, geom in patterns.label_pair_geometry.items():
        label_pair_geometry_list.append({
            "labels": list(pair_tuple),
            "mean_angle": geom.mean_angle,
            "std_angle": geom.std_angle,
            "mean_distance": geom.mean_distance,
            "std_distance": geom.std_distance,
            "mean_dx": geom.mean_dx,
            "mean_dy": geom.mean_dy,
            "std_dx": geom.std_dx,
            "std_dy": geom.std_dy,
            "count": len(geom.observations) if geom.observations else 0,
        })

    # Serialize constellation geometry (Dict[Tuple[str, ...], ConstellationGeometry])
    constellation_geometry_list = []
    for labels_tuple, cg in patterns.constellation_geometry.items():
        # Serialize relative positions
        relative_positions_dict = {}
        for label, rel_pos in cg.relative_positions.items():
            relative_positions_dict[label] = {
                "mean_dx": rel_pos.mean_dx,
                "mean_dy": rel_pos.mean_dy,
                "std_dx": rel_pos.std_dx,
                "std_dy": rel_pos.std_dy,
            }
        constellation_geometry_list.append({
            "labels": list(labels_tuple),
            "observation_count": cg.observation_count,
            "relative_positions": relative_positions_dict,
        })

    return {
        "merchant_name": merchant_name,
        "patterns": {
            "merchant_name": patterns.merchant_name,
            "receipt_count": patterns.receipt_count,
            "label_positions": label_position_stats,
            "label_pair_geometry": label_pair_geometry_list,
            "all_observed_pairs": [
                list(pair) for pair in patterns.all_observed_pairs
            ],
            "constellation_geometry": constellation_geometry_list,
            # Include batch classification for weighted threshold selection
            "batch_classification": dict(patterns.batch_classification),
            # Include same-line multiplicity labels
            "labels_with_same_line_multiplicity": list(
                patterns.labels_with_same_line_multiplicity
            ),
        },
    }
