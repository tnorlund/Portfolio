"""Discover line item patterns with trace propagation.

This handler CREATES the root trace since it's the first container-based Lambda
in the traced Step Function. Uses deterministic UUIDs based on execution ARN
to ensure proper trace hierarchy across all Lambda invocations.

The trace_id and root_run_id are returned in the output and propagated
through the Step Function to all downstream Lambdas.
"""

import json
import logging
import os
import sys
from typing import Any

import boto3

# Import tracing utilities - works in both container and local environments
try:
    # Container environment: tracing.py is in same directory
    from tracing import (
        TraceContext,
        child_trace,
        create_execution_trace,
        end_execution_trace,
        flush_langsmith_traces,
    )
except ImportError:
    # Local/development environment: use path relative to this file
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "lambdas", "utils"
    ))
    from tracing import (
        TraceContext,
        child_trace,
        create_execution_trace,
        end_execution_trace,
        flush_langsmith_traces,
    )

# Import pattern discovery from receipt_agent
from receipt_agent.agents.label_evaluator.pattern_discovery import (
    PatternDiscoveryConfig,
    build_discovery_prompt,
    build_receipt_structure,
    discover_patterns_with_llm,
    get_default_patterns,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def upload_json_to_s3(bucket: str, key: str, data: Any) -> None:
    """Upload JSON data to S3."""
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def get_merchant_hash(merchant_name: str) -> str:
    """Create a short, stable hash for merchant name S3 keys."""
    import hashlib

    return hashlib.sha256(merchant_name.encode("utf-8")).hexdigest()[:12]


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Discover line item patterns for a merchant with trace propagation.

    This is the FIRST container-based Lambda, so it CREATES the root trace
    using deterministic UUIDs based on execution ARN.

    Input:
    {
        "execution_id": "abc123",
        "execution_arn": "arn:aws:states:...",  # From $$.Execution.Id
        "batch_bucket": "bucket-name",
        "merchant_name": "Home Depot"
    }

    Output:
    {
        "execution_id": "abc123",
        "merchant_name": "Home Depot",
        "patterns_s3_key": "patterns/home_depot.json",
        "patterns": { ... discovered patterns ... },
        "error": null or "error message",
        "trace_id": "...",       # For downstream Lambdas
        "root_run_id": "..."     # For downstream Lambdas
    }
    """
    from receipt_dynamo import DynamoClient

    execution_id = event.get("execution_id", "unknown")
    execution_arn = event.get("execution_arn", f"local:{execution_id}")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "Unknown")
    enable_tracing = event.get("enable_tracing", False)

    if not batch_bucket:
        return {
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "patterns_s3_key": None,
            "patterns": None,
            "error": "batch_bucket is required",
        }

    merchant_hash = get_merchant_hash(merchant_name)
    patterns_s3_key = f"line_item_patterns/{merchant_hash}.json"

    # CREATE the root trace using deterministic UUIDs
    # This ensures all Lambdas in this execution share the same trace
    trace_info = create_execution_trace(
        execution_arn=execution_arn,
        name=f"label_evaluator:{merchant_name[:20]}",
        inputs={
            "execution_id": execution_id,
            "merchant_name": merchant_name,
        },
        metadata={
            "execution_id": execution_id,
            "execution_arn": execution_arn,
            "merchant_name": merchant_name,
        },
        tags=["label-evaluator", "discover-patterns", "llm"],
        enable_tracing=enable_tracing,
    )

    # Create a TraceContext for child_trace compatibility
    trace_ctx = TraceContext(
        run_tree=trace_info.run_tree,
        headers=trace_info.run_tree.to_headers() if trace_info.run_tree else None,
        trace_id=trace_info.trace_id,
        root_run_id=trace_info.root_run_id,
    )

    # Get pattern discovery config from environment
    config = PatternDiscoveryConfig.from_env()

    result = None

    try:
        # Always run pattern discovery (no caching)
        logger.info(f"Discovering patterns for {merchant_name}")
        table_name = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable")
        dynamo_client = DynamoClient(table_name=table_name)

        with child_trace("load_sample_receipts", trace_ctx):
            receipts_data = build_receipt_structure(
                dynamo_client, merchant_name, limit=3
            )

        if not receipts_data:
            logger.warning(f"No receipt data found for {merchant_name}")
            default_patterns = get_default_patterns(
                merchant_name, reason="no_receipt_data"
            )
            upload_json_to_s3(batch_bucket, patterns_s3_key, default_patterns)
            result = {
                "execution_id": execution_id,
                "merchant_name": merchant_name,
                "patterns_s3_key": patterns_s3_key,
                "patterns": default_patterns,
                "error": None,
            }
        else:
            # Build prompt and call LLM
            with child_trace("build_prompt", trace_ctx):
                prompt = build_discovery_prompt(merchant_name, receipts_data)

            patterns = discover_patterns_with_llm(prompt, config, trace_ctx)

            if not patterns:
                logger.warning(
                    f"LLM pattern discovery failed for {merchant_name}"
                )
                default_patterns = get_default_patterns(
                    merchant_name, reason="llm_discovery_failed"
                )
                upload_json_to_s3(
                    batch_bucket, patterns_s3_key, default_patterns
                )
                result = {
                    "execution_id": execution_id,
                    "merchant_name": merchant_name,
                    "patterns_s3_key": patterns_s3_key,
                    "patterns": default_patterns,
                    "error": "LLM discovery failed, using defaults",
                }
            else:
                # Add metadata
                patterns["discovered_from_receipts"] = len(receipts_data)
                patterns["auto_generated"] = False

                # Store patterns
                with child_trace("upload_patterns", trace_ctx):
                    upload_json_to_s3(batch_bucket, patterns_s3_key, patterns)
                    logger.info(
                        f"Stored patterns for {merchant_name} at {patterns_s3_key}"
                    )

                result = {
                    "execution_id": execution_id,
                    "merchant_name": merchant_name,
                    "patterns_s3_key": patterns_s3_key,
                    "patterns": patterns,
                    "error": None,
                }

    finally:
        # End the root trace and flush
        end_execution_trace(trace_info, outputs=result)
        flush_langsmith_traces()

    # Add trace IDs to output for downstream Lambdas
    # Always include all three fields (even if null) for Step Function JSONPath
    result["trace_id"] = trace_info.trace_id
    result["root_run_id"] = trace_info.root_run_id
    result["root_dotted_order"] = trace_info.root_dotted_order  # Can be None

    return result
