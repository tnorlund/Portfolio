"""Compute merchant patterns with trace propagation.

This handler creates a child trace using deterministic UUIDs based on
execution ARN. The trace_id and root_run_id are passed from the upstream
DiscoverLineItemPatterns Lambda.
"""

# pylint: disable=import-outside-toplevel
# Lambda handlers delay imports until runtime for cold start optimization

import json
import logging
import os
import sys
import time
from typing import TYPE_CHECKING, Any

import boto3

# Import tracing utilities - works in both container and local environments
try:
    # Container environment: tracing.py is in same directory
    from tracing import child_trace, flush_langsmith_traces, state_trace

    from utils.s3_helpers import get_merchant_hash
except ImportError:
    # Local/development environment: use path relative to this file
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lambdas",
            "utils",
        ),
    )
    from s3_helpers import get_merchant_hash
    from tracing import child_trace, flush_langsmith_traces, state_trace

if TYPE_CHECKING:
    from handlers.evaluator_types import ComputePatternsOutput

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> "ComputePatternsOutput":
    """
    Compute merchant patterns from training receipts with trace propagation.

    Input:
    {
        "execution_id": "abc123",
        "execution_arn": "arn:aws:states:...",  # From $$.Execution.Id
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "max_training_receipts": 50,
        "trace_id": "...",       # From upstream Lambda
        "root_run_id": "..."     # From upstream Lambda
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
    execution_arn = event.get("execution_arn", f"local:{execution_id}")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name")
    # Handle merchant_name from merchant object (Map state)
    if not merchant_name and "merchant" in event:
        merchant_name = event["merchant"].get("merchant_name")
    max_training_receipts = event.get("max_training_receipts", 50)

    # Get trace IDs from upstream Lambda
    trace_id = event.get("trace_id", "")
    root_run_id = event.get("root_run_id", "")
    root_dotted_order = event.get("root_dotted_order")
    enable_tracing = event.get("enable_tracing", False)

    if not merchant_name:
        raise ValueError("merchant_name is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    start_time = time.time()

    # Initialize result for error cases
    result = {
        "patterns_s3_key": "",
        "merchant_name": merchant_name,
        "receipt_count": 0,
        "pattern_stats": None,
    }

    # Create a child trace using deterministic UUID
    with state_trace(
        execution_arn=execution_arn,
        state_name="ComputePatterns",
        trace_id=trace_id,
        root_run_id=root_run_id,
        root_dotted_order=root_dotted_order,
        inputs={
            "merchant_name": merchant_name,
            "max_training_receipts": max_training_receipts,
        },
        metadata={
            "merchant_name": merchant_name,
            "execution_id": execution_id,
        },
        tags=["compute-patterns"],
        enable_tracing=enable_tracing,
    ) as trace_ctx:

        logger.info(
            "Computing patterns for merchant '%s' (max_receipts=%s)",
            merchant_name,
            max_training_receipts,
        )

        # Import DynamoDB client
        from receipt_dynamo import DynamoClient

        table_name = os.environ.get("DYNAMODB_TABLE_NAME")
        if not table_name:
            raise ValueError(
                "DYNAMODB_TABLE_NAME environment variable not set"
            )

        dynamo = DynamoClient(table_name=table_name)

        # Create child trace for data loading
        with child_trace(
            "load_training_data",
            trace_ctx,
            metadata={
                "merchant_name": merchant_name,
            },
        ):
            # Load training receipts
            from receipt_agent.agents.label_evaluator.state import (
                OtherReceiptData,
            )

            other_receipt_data: list[OtherReceiptData] = []
            last_key = None

            while len(other_receipt_data) < max_training_receipts:
                places, last_key = dynamo.get_receipt_places_by_merchant(
                    merchant_name,
                    limit=min(
                        100, max_training_receipts - len(other_receipt_data)
                    ),
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
                        labels, _ = (
                            dynamo.list_receipt_word_labels_for_receipt(
                                place.image_id, place.receipt_id
                            )
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

            logger.info("Loaded %s training receipts", len(other_receipt_data))

        if not other_receipt_data:
            # No training data - save empty patterns
            patterns_s3_key = f"patterns/{execution_id}/{get_merchant_hash(merchant_name)}.json"
            s3.put_object(
                Bucket=batch_bucket,
                Key=patterns_s3_key,
                Body=json.dumps(
                    {"patterns": None, "merchant_name": merchant_name}
                ),
                ContentType="application/json",
            )

            result = {
                "patterns_s3_key": patterns_s3_key,
                "merchant_name": merchant_name,
                "receipt_count": 0,
                "pattern_stats": None,
            }
            trace_ctx.set_outputs(result)
            flush_langsmith_traces()
            return result

        # Create child trace for pattern computation
        with child_trace(
            "compute_merchant_patterns",
            trace_ctx,
            metadata={
                "receipt_count": len(other_receipt_data),
            },
        ):
            logger.info("Computing merchant patterns...")
            compute_start = time.time()

            from receipt_agent.agents.label_evaluator.patterns import (
                compute_merchant_patterns,
            )

            patterns = compute_merchant_patterns(
                other_receipt_data,
                merchant_name,
                max_pair_patterns=4,
                max_relationship_dimension=3,  # Enable constellation (n-tuple) patterns
            )

            compute_time = time.time() - compute_start
            logger.info("Pattern computation completed in %.2fs", compute_time)

        # Serialize patterns to S3
        patterns_s3_key = (
            f"patterns/{execution_id}/{get_merchant_hash(merchant_name)}.json"
        )
        patterns_data = _serialize_patterns(patterns, merchant_name)

        s3.put_object(
            Bucket=batch_bucket,
            Key=patterns_s3_key,
            Body=json.dumps(patterns_data, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

        total_time = time.time() - start_time
        logger.info(
            "Saved patterns to s3://%s/%s (total time: %.2fs)",
            batch_bucket,
            patterns_s3_key,
            total_time,
        )

        pattern_stats = None
        if patterns:
            pattern_stats = {
                "label_positions": len(patterns.label_positions),
                "label_pairs": len(patterns.label_pair_geometry),
                "observed_pairs": len(patterns.all_observed_pairs),
                "receipt_count": patterns.receipt_count,
            }

        result = {
            "patterns_s3_key": patterns_s3_key,
            "merchant_name": merchant_name,
            "receipt_count": len(other_receipt_data),
            "pattern_stats": pattern_stats,
            "compute_time_seconds": round(compute_time, 2),
        }

        trace_ctx.set_outputs(result)

    # Flush traces before Lambda exits
    flush_langsmith_traces()

    return result


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
