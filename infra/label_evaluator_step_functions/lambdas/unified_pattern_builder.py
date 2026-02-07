"""Unified pattern builder combining LLM discovery and geometric computation.

This handler consolidates LearnLineItemPatterns and BuildMerchantPatterns into
a single Lambda, reducing cold starts and simplifying the Step Function.

Phase 1 operations (sequential):
1. Discover line item patterns using LLM (from sample receipts)
2. Compute geometric patterns from training receipts

Both pattern types are saved to S3 with timing metadata for per-receipt tracing.
"""

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
    from utils.s3_helpers import get_merchant_hash, upload_json_to_s3
    from utils.tracing import (
        MerchantTraceInfo,
        TraceContext,
        child_trace,
        create_merchant_trace,
        end_merchant_trace,
        flush_langsmith_traces,
        state_trace,
    )
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
    from s3_helpers import get_merchant_hash, upload_json_to_s3
    from tracing import (
        MerchantTraceInfo,
        TraceContext,
        child_trace,
        create_merchant_trace,
        end_merchant_trace,
        flush_langsmith_traces,
        state_trace,
    )

if TYPE_CHECKING:
    pass

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Unified pattern builder for a merchant.

    Combines LearnLineItemPatterns (LLM discovery) and BuildMerchantPatterns
    (geometric computation) into a single Lambda invocation.

    Input:
    {
        "execution_id": "abc123",
        "execution_arn": "arn:aws:states:...",
        "batch_bucket": "bucket-name",
        "merchant_name": "Home Depot",
        "max_training_receipts": 50,
        "langchain_project": "label-evaluator-{timestamp}"
    }

    Output:
    {
        "execution_id": "abc123",
        "merchant_name": "Home Depot",
        "line_item_patterns_s3_key": "line_item_patterns/{hash}.json",
        "patterns_s3_key": "patterns/{exec}/{hash}.json",
        "receipt_count": 45,
        "pattern_stats": {...}
    }
    """
    from receipt_dynamo import DynamoClient

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
    execution_arn = event.get("execution_arn", "")

    if not merchant_name:
        raise ValueError("merchant_name is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    merchant_hash = get_merchant_hash(merchant_name)
    line_item_patterns_s3_key = f"line_item_patterns/{execution_id}/{merchant_hash}.json"
    patterns_s3_key = f"patterns/{execution_id}/{merchant_hash}.json"

    # Create root merchant trace for Phase 1 (pattern computation)
    merchant_trace = create_merchant_trace(
        execution_arn=execution_arn,
        merchant_name=merchant_name,
        name="UnifiedPatternBuilder",
        inputs={
            "merchant_name": merchant_name,
            "max_training_receipts": max_training_receipts,
        },
        metadata={"execution_id": execution_id, "phase": "pattern_learning"},
        tags=["phase-1", "per-merchant", "unified"],
        enable_tracing=True,
    )

    trace_ctx = TraceContext(
        run_tree=merchant_trace.run_tree,
        headers=(
            merchant_trace.run_tree.to_headers()
            if merchant_trace.run_tree
            else None
        ),
        trace_id=merchant_trace.trace_id,
        root_run_id=merchant_trace.root_run_id,
    )

    # Initialize DynamoDB client
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable")
    dynamo_client = DynamoClient(table_name=table_name)

    # Track overall timing
    total_start = time.time()

    # =========================================================================
    # Step 1: Discover Line Item Patterns (LLM)
    # =========================================================================
    discovery_start = time.time()
    discovery_start_time = datetime.now(timezone.utc).isoformat()
    line_item_patterns = None
    discovery_error = None

    try:
        with child_trace(
            "LearnLineItemPatterns",
            trace_ctx,
            run_type="chain",
            inputs={"merchant_name": merchant_name},
            metadata={"step": "discovery"},
        ):
            from receipt_agent.agents.label_evaluator.pattern_discovery import (
                PatternDiscoveryConfig,
                build_discovery_prompt,
                build_receipt_structure,
                discover_patterns_with_llm,
                get_default_patterns,
            )

            config = PatternDiscoveryConfig.from_env()

            # Load sample receipts for LLM discovery (typically 3)
            load_start = time.time()
            receipts_data = build_receipt_structure(
                dynamo_client, merchant_name, limit=3
            )
            load_duration = time.time() - load_start

            if not receipts_data:
                logger.warning("No receipt data found for %s", merchant_name)
                line_item_patterns = get_default_patterns(
                    merchant_name, reason="no_receipt_data"
                )
            else:
                # Build prompt and call LLM
                prompt_start = time.time()
                prompt = build_discovery_prompt(merchant_name, receipts_data)
                prompt_duration = time.time() - prompt_start

                llm_start = time.time()
                patterns = discover_patterns_with_llm(
                    prompt, config, trace_ctx=trace_ctx
                )
                llm_duration = time.time() - llm_start

                if not patterns:
                    logger.warning(
                        "LLM pattern discovery failed for %s", merchant_name
                    )
                    line_item_patterns = get_default_patterns(
                        merchant_name, reason="llm_discovery_failed"
                    )
                else:
                    line_item_patterns = patterns
                    line_item_patterns["discovered_from_receipts"] = len(
                        receipts_data
                    )
                    line_item_patterns["auto_generated"] = False

    except Exception as e:
        from receipt_agent.utils.llm_factory import LLMRateLimitError

        if isinstance(e, LLMRateLimitError):
            logger.error("Rate limit error in discovery: %s", e)
            raise

        logger.error("Error in pattern discovery: %s", e, exc_info=True)
        discovery_error = str(e)
        # Use default patterns on error
        from receipt_agent.agents.label_evaluator.pattern_discovery import (
            get_default_patterns,
        )

        line_item_patterns = get_default_patterns(
            merchant_name, reason="discovery_error"
        )

    discovery_end_time = datetime.now(timezone.utc).isoformat()
    discovery_duration = time.time() - discovery_start

    # Add timing metadata to line item patterns
    if line_item_patterns:
        line_item_patterns["_trace_metadata"] = {
            "discovery_start_time": discovery_start_time,
            "discovery_end_time": discovery_end_time,
            "discovery_duration_seconds": round(discovery_duration, 3),
            "discovery_status": "error" if discovery_error else "success",
        }
        if discovery_error:
            line_item_patterns["_trace_metadata"]["error"] = discovery_error

    # Save line item patterns to S3
    upload_json_to_s3(
        s3, batch_bucket, line_item_patterns_s3_key, line_item_patterns
    )
    logger.info(
        "Saved line item patterns to s3://%s/%s (%.2fs)",
        batch_bucket,
        line_item_patterns_s3_key,
        discovery_duration,
    )

    # =========================================================================
    # Step 2: Compute Geometric Patterns
    # =========================================================================
    computation_start = time.time()
    computation_start_time = datetime.now(timezone.utc).isoformat()
    geometric_patterns = None
    pattern_stats = None
    receipt_count = 0

    try:
        with child_trace(
            "BuildMerchantPatterns",
            trace_ctx,
            run_type="chain",
            inputs={
                "merchant_name": merchant_name,
                "max_receipts": max_training_receipts,
            },
            metadata={"step": "geometric"},
        ):
            from receipt_agent.agents.label_evaluator.state import (
                OtherReceiptData,
            )

            # Load training receipts for geometric patterns
            other_receipt_data: list[OtherReceiptData] = []
            last_key = None
            load_start = time.time()

            while len(other_receipt_data) < max_training_receipts:
                places, last_key = (
                    dynamo_client.get_receipt_places_by_merchant(
                        merchant_name,
                        limit=min(
                            100,
                            max_training_receipts - len(other_receipt_data),
                        ),
                        last_evaluated_key=last_key,
                    )
                )

                if not places:
                    break

                for place in places:
                    if len(other_receipt_data) >= max_training_receipts:
                        break

                    try:
                        words = dynamo_client.list_receipt_words_from_receipt(
                            place.image_id, place.receipt_id
                        )
                        labels, _ = (
                            dynamo_client.list_receipt_word_labels_for_receipt(
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

            load_duration = time.time() - load_start
            receipt_count = len(other_receipt_data)
            logger.info(
                "Loaded %s training receipts in %.2fs",
                receipt_count,
                load_duration,
            )

            if other_receipt_data:
                # Compute geometric patterns
                compute_start = time.time()
                from receipt_agent.agents.label_evaluator.patterns import (
                    compute_merchant_patterns,
                )

                geometric_patterns = compute_merchant_patterns(
                    other_receipt_data,
                    merchant_name,
                    max_pair_patterns=4,
                    max_relationship_dimension=3,
                )
                compute_duration = time.time() - compute_start
                logger.info(
                    "Pattern computation completed in %.2fs", compute_duration
                )

                if geometric_patterns:
                    pattern_stats = {
                        "label_positions": len(
                            geometric_patterns.label_positions
                        ),
                        "label_pairs": len(
                            geometric_patterns.label_pair_geometry
                        ),
                        "observed_pairs": len(
                            geometric_patterns.all_observed_pairs
                        ),
                        "receipt_count": geometric_patterns.receipt_count,
                    }

    except Exception as e:
        logger.error(
            "Error in geometric pattern computation: %s", e, exc_info=True
        )
        # Continue without geometric patterns - not a fatal error

    computation_end_time = datetime.now(timezone.utc).isoformat()
    computation_duration = time.time() - computation_start

    # Serialize and save geometric patterns to S3
    patterns_data = _serialize_patterns(geometric_patterns, merchant_name)
    patterns_data["_trace_metadata"] = {
        "computation_start_time": computation_start_time,
        "computation_end_time": computation_end_time,
        "computation_duration_seconds": round(computation_duration, 3),
        "computation_status": "success" if geometric_patterns else "no_data",
        "training_receipt_count": receipt_count,
    }

    s3.put_object(
        Bucket=batch_bucket,
        Key=patterns_s3_key,
        Body=json.dumps(patterns_data, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(
        "Saved geometric patterns to s3://%s/%s (%.2fs)",
        batch_bucket,
        patterns_s3_key,
        computation_duration,
    )

    # =========================================================================
    # Finalize
    # =========================================================================
    total_duration = time.time() - total_start

    # End the merchant trace
    end_merchant_trace(
        merchant_trace,
        outputs={
            "line_item_patterns_discovered": line_item_patterns is not None,
            "geometric_patterns_computed": geometric_patterns is not None,
            "receipt_count": receipt_count,
            "total_duration_seconds": round(total_duration, 2),
        },
    )
    flush_langsmith_traces()

    return {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "line_item_patterns_s3_key": line_item_patterns_s3_key,
        "patterns_s3_key": patterns_s3_key,
        "receipt_count": receipt_count,
        "pattern_stats": pattern_stats,
        "discovery_duration_seconds": round(discovery_duration, 2),
        "computation_duration_seconds": round(computation_duration, 2),
        "total_duration_seconds": round(total_duration, 2),
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
