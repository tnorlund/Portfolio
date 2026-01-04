"""Discover line item patterns with timing metadata for per-receipt tracing.

This handler discovers line item patterns using LLM and stores timing metadata
alongside the patterns in S3. The timing is used by evaluate_labels.py to create
child spans in each receipt's trace, making the traces look like production
(full pipeline per receipt) while still benefiting from shared pattern computation.

Timing metadata stored:
- discovery_start_time: ISO timestamp when discovery started
- discovery_end_time: ISO timestamp when discovery completed
- discovery_duration_seconds: Total duration in seconds
- discovery_llm_model: Model used for pattern discovery
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import boto3

# Import tracing utilities - works in both container and local environments
try:
    # Container environment: tracing.py is in same directory
    from utils.s3_helpers import get_merchant_hash, upload_json_to_s3
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
    from s3_helpers import get_merchant_hash, upload_json_to_s3

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


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Discover line item patterns for a merchant.

    Tracks timing metadata that will be used by evaluate_labels.py to create
    DiscoverPatterns child spans in each receipt's trace.

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
        "patterns_s3_key": "line_item_patterns/home_depot.json",
        "patterns": { ... discovered patterns with timing metadata ... },
        "error": null or "error message"
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
    merchant_name = event.get("merchant_name", "Unknown")

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

    # Get pattern discovery config from environment
    config = PatternDiscoveryConfig.from_env()

    # Track timing for the entire discovery process
    discovery_start = time.time()
    discovery_start_time = datetime.now(timezone.utc).isoformat()

    # Initialize result with default structure for error cases
    result = {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "patterns_s3_key": None,
        "patterns": None,
        "error": None,
    }

    try:
        # Always run pattern discovery (no caching)
        logger.info("Discovering patterns for %s", merchant_name)
        table_name = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable")
        dynamo_client = DynamoClient(table_name=table_name)

        # Load sample receipts
        load_start = time.time()
        receipts_data = build_receipt_structure(
            dynamo_client, merchant_name, limit=3
        )
        load_duration = time.time() - load_start

        if not receipts_data:
            logger.warning("No receipt data found for %s", merchant_name)
            default_patterns = get_default_patterns(
                merchant_name, reason="no_receipt_data"
            )
            # Add timing metadata
            discovery_end_time = datetime.now(timezone.utc).isoformat()
            discovery_duration = time.time() - discovery_start
            default_patterns["_trace_metadata"] = {
                "discovery_start_time": discovery_start_time,
                "discovery_end_time": discovery_end_time,
                "discovery_duration_seconds": round(discovery_duration, 3),
                "discovery_status": "no_receipt_data",
                "load_receipts_duration_seconds": round(load_duration, 3),
            }
            upload_json_to_s3(
                s3, batch_bucket, patterns_s3_key, default_patterns
            )
            result = {
                "execution_id": execution_id,
                "merchant_name": merchant_name,
                "patterns_s3_key": patterns_s3_key,
                "patterns": default_patterns,
                "error": None,
            }
        else:
            # Build prompt
            prompt_start = time.time()
            prompt = build_discovery_prompt(merchant_name, receipts_data)
            prompt_duration = time.time() - prompt_start

            # Call LLM for pattern discovery
            llm_start = time.time()
            patterns = discover_patterns_with_llm(prompt, config, trace_ctx=None)
            llm_duration = time.time() - llm_start

            if not patterns:
                logger.warning(
                    f"LLM pattern discovery failed for {merchant_name}"
                )
                default_patterns = get_default_patterns(
                    merchant_name, reason="llm_discovery_failed"
                )
                # Add timing metadata
                discovery_end_time = datetime.now(timezone.utc).isoformat()
                discovery_duration = time.time() - discovery_start
                default_patterns["_trace_metadata"] = {
                    "discovery_start_time": discovery_start_time,
                    "discovery_end_time": discovery_end_time,
                    "discovery_duration_seconds": round(discovery_duration, 3),
                    "discovery_status": "llm_failed",
                    "load_receipts_duration_seconds": round(load_duration, 3),
                    "build_prompt_duration_seconds": round(prompt_duration, 3),
                    "llm_call_duration_seconds": round(llm_duration, 3),
                    "llm_model": config.ollama_model,
                }
                upload_json_to_s3(
                    s3, batch_bucket, patterns_s3_key, default_patterns
                )
                result = {
                    "execution_id": execution_id,
                    "merchant_name": merchant_name,
                    "patterns_s3_key": patterns_s3_key,
                    "patterns": default_patterns,
                    "error": "LLM discovery failed, using defaults",
                }
            else:
                # Add standard metadata
                patterns["discovered_from_receipts"] = len(receipts_data)
                patterns["auto_generated"] = False

                # Add timing metadata for per-receipt tracing
                discovery_end_time = datetime.now(timezone.utc).isoformat()
                discovery_duration = time.time() - discovery_start
                patterns["_trace_metadata"] = {
                    "discovery_start_time": discovery_start_time,
                    "discovery_end_time": discovery_end_time,
                    "discovery_duration_seconds": round(discovery_duration, 3),
                    "discovery_status": "success",
                    "load_receipts_duration_seconds": round(load_duration, 3),
                    "build_prompt_duration_seconds": round(prompt_duration, 3),
                    "llm_call_duration_seconds": round(llm_duration, 3),
                    "llm_model": config.ollama_model,
                    "sample_receipt_count": len(receipts_data),
                }

                # Store patterns with timing metadata
                upload_json_to_s3(
                    s3, batch_bucket, patterns_s3_key, patterns
                )
                logger.info(
                    "Stored patterns for %s at %s (discovery took %.2fs)",
                    merchant_name,
                    patterns_s3_key,
                    discovery_duration,
                )

                result = {
                    "execution_id": execution_id,
                    "merchant_name": merchant_name,
                    "patterns_s3_key": patterns_s3_key,
                    "patterns": patterns,
                    "error": None,
                }

    except Exception as e:
        logger.error("Error discovering patterns: %s", e, exc_info=True)
        # Add timing metadata even on error
        discovery_end_time = datetime.now(timezone.utc).isoformat()
        discovery_duration = time.time() - discovery_start
        result = {
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "patterns_s3_key": None,
            "patterns": None,
            "error": str(e),
            "_trace_metadata": {
                "discovery_start_time": discovery_start_time,
                "discovery_end_time": discovery_end_time,
                "discovery_duration_seconds": round(discovery_duration, 3),
                "discovery_status": "error",
                "error": str(e),
            },
        }

    return result
