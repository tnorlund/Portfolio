"""Compute merchant patterns with timing metadata for per-receipt tracing.

This handler computes geometric patterns from training receipts and stores
timing metadata alongside the patterns in S3. The timing is used by
evaluate_labels.py to create child spans in each receipt's trace.

Timing metadata stored:
- computation_start_time: ISO timestamp when computation started
- computation_end_time: ISO timestamp when computation completed
- computation_duration_seconds: Total duration in seconds
- load_data_duration_seconds: Time to load training data
- compute_patterns_duration_seconds: Time to compute patterns
"""

# pylint: disable=import-outside-toplevel
# Lambda handlers delay imports until runtime for cold start optimization

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import boto3

# Import utilities - works in both container and local environments
try:
    # Container environment
    from utils.s3_helpers import get_merchant_hash
except ImportError:
    # Local/development environment
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lambdas",
            "utils",
        ),
    )
    from s3_helpers import get_merchant_hash

if TYPE_CHECKING:
    from handlers.evaluator_types import ComputePatternsOutput

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> "ComputePatternsOutput":
    """
    Compute merchant patterns from training receipts.

    Tracks timing metadata that will be used by evaluate_labels.py to create
    ComputePatterns child spans in each receipt's trace.

    Input:
    {
        "execution_id": "abc123",
        "execution_arn": "arn:aws:states:...",  # From $$.Execution.Id
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "max_training_receipts": 50
    }

    Output:
    {
        "patterns_s3_key": "patterns/{exec}/{hash}.json",
        "merchant_name": "Sprouts Farmers Market",
        "receipt_count": 45
    }
    """
    # Allow runtime override of LangSmith project via Step Function input
    langchain_project = event.get("langchain_project")
    if langchain_project:
        os.environ["LANGCHAIN_PROJECT"] = langchain_project
        logger.info("LangSmith project set to: %s", langchain_project)

    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name")
    # Handle merchant_name from merchant object (Map state)
    if not merchant_name and "merchant" in event:
        merchant_name = event["merchant"].get("merchant_name")
    max_training_receipts = event.get("max_training_receipts", 50)

    if not merchant_name:
        raise ValueError("merchant_name is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    # Track timing for the entire computation process
    computation_start = time.time()
    computation_start_time = datetime.now(timezone.utc).isoformat()

    logger.info(
        "Computing patterns for merchant '%s' (max_receipts=%s)",
        merchant_name,
        max_training_receipts,
    )

    # Import DynamoDB client
    from receipt_dynamo import DynamoClient

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    dynamo = DynamoClient(table_name=table_name)

    # Load training receipts
    load_start = time.time()
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
            except Exception:
                logger.exception(
                    "Error loading receipt %s#%s",
                    place.image_id,
                    place.receipt_id,
                )
                continue

        if not last_key:
            break

    load_duration = time.time() - load_start
    logger.info(
        "Loaded %s training receipts in %.2fs",
        len(other_receipt_data),
        load_duration,
    )

    if not other_receipt_data:
        # No training data - save empty patterns with timing metadata
        computation_end_time = datetime.now(timezone.utc).isoformat()
        computation_duration = time.time() - computation_start

        patterns_s3_key = (
            f"patterns/{execution_id}/{get_merchant_hash(merchant_name)}.json"
        )
        patterns_data = {
            "patterns": None,
            "merchant_name": merchant_name,
            "_trace_metadata": {
                "computation_start_time": computation_start_time,
                "computation_end_time": computation_end_time,
                "computation_duration_seconds": round(computation_duration, 3),
                "computation_status": "no_training_data",
                "load_data_duration_seconds": round(load_duration, 3),
                "training_receipt_count": 0,
            },
        }
        s3.put_object(
            Bucket=batch_bucket,
            Key=patterns_s3_key,
            Body=json.dumps(patterns_data, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

        return {
            "patterns_s3_key": patterns_s3_key,
            "merchant_name": merchant_name,
            "receipt_count": 0,
            "pattern_stats": None,
        }

    # Compute patterns
    compute_start = time.time()
    logger.info("Computing merchant patterns...")

    from receipt_agent.agents.label_evaluator.patterns import (
        compute_merchant_patterns,
    )

    patterns = compute_merchant_patterns(
        other_receipt_data,
        merchant_name,
        max_pair_patterns=4,
        max_relationship_dimension=3,  # Enable constellation (n-tuple) patterns
    )

    compute_duration = time.time() - compute_start
    logger.info("Pattern computation completed in %.2fs", compute_duration)

    # Serialize patterns to S3 with timing metadata
    computation_end_time = datetime.now(timezone.utc).isoformat()
    computation_duration = time.time() - computation_start

    patterns_s3_key = (
        f"patterns/{execution_id}/{get_merchant_hash(merchant_name)}.json"
    )
    patterns_data = _serialize_patterns(patterns, merchant_name)

    # Add timing metadata for per-receipt tracing
    patterns_data["_trace_metadata"] = {
        "computation_start_time": computation_start_time,
        "computation_end_time": computation_end_time,
        "computation_duration_seconds": round(computation_duration, 3),
        "computation_status": "success",
        "load_data_duration_seconds": round(load_duration, 3),
        "compute_patterns_duration_seconds": round(compute_duration, 3),
        "training_receipt_count": len(other_receipt_data),
    }

    s3.put_object(
        Bucket=batch_bucket,
        Key=patterns_s3_key,
        Body=json.dumps(patterns_data, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    logger.info(
        "Saved patterns to s3://%s/%s (total time: %.2fs)",
        batch_bucket,
        patterns_s3_key,
        computation_duration,
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
        "compute_time_seconds": round(compute_duration, 2),
    }


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
            std_y = (
                statistics.stdev(y_positions) if len(y_positions) > 1 else 0.0
            )
            label_position_stats[label] = {
                "mean_y": mean_y,
                "std_y": std_y,
                "count": len(y_positions),
            }

    # Serialize label pair geometry
    label_pair_geometry_list = []
    for pair_tuple, geom in patterns.label_pair_geometry.items():
        label_pair_geometry_list.append(
            {
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
            }
        )

    # Serialize constellation geometry
    constellation_geometry_list = []
    for labels_tuple, cg in patterns.constellation_geometry.items():
        relative_positions_dict = {}
        for label, rel_pos in cg.relative_positions.items():
            relative_positions_dict[label] = {
                "mean_dx": rel_pos.mean_dx,
                "mean_dy": rel_pos.mean_dy,
                "std_dx": rel_pos.std_dx,
                "std_dy": rel_pos.std_dy,
            }
        constellation_geometry_list.append(
            {
                "labels": list(labels_tuple),
                "observation_count": cg.observation_count,
                "relative_positions": relative_positions_dict,
            }
        )

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
            "batch_classification": dict(patterns.batch_classification),
            "labels_with_same_line_multiplicity": list(
                patterns.labels_with_same_line_multiplicity
            ),
            "labels_with_receipt_multiplicity": list(
                patterns.labels_with_receipt_multiplicity
            ),
        },
    }
