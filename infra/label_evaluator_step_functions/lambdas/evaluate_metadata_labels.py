"""Evaluate metadata labels with LLM.

This handler evaluates metadata labels (MERCHANT_NAME, ADDRESS_LINE, PHONE_NUMBER,
WEBSITE, DATE, TIME, etc.) using ReceiptPlace data and pattern validation.

Runs in parallel with EvaluateLabels and EvaluateCurrencyLabels.
"""

# pylint: disable=import-outside-toplevel,wrong-import-position
# Lambda handlers delay imports until runtime for cold start optimization

import logging
import os
import sys
import time
from typing import TYPE_CHECKING, Any

import boto3

# Import tracing utilities - works in both container and local environments
try:
    # Container environment: tracing.py is in same directory
    from tracing import (
        TRACING_VERSION,
        child_trace,
        flush_langsmith_traces,
        receipt_state_trace,
    )
    from utils.s3_helpers import load_json_from_s3, upload_json_to_s3
    _tracing_import_source = "container"
except ImportError:
    # Local/development environment: use path relative to this file
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "lambdas", "utils"
    ))
    from tracing import (
        TRACING_VERSION,
        child_trace,
        flush_langsmith_traces,
        receipt_state_trace,
    )
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "lambdas"
    ))
    from utils.s3_helpers import load_json_from_s3, upload_json_to_s3
    _tracing_import_source = "local"


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Evaluate metadata labels using LLM and ReceiptPlace data.

    Runs in parallel with EvaluateLabels and EvaluateCurrencyLabels.
    Joins the receipt-level trace using receipt_trace_id from FetchReceiptData.

    Input:
    {
        "data_s3_key": "data/{exec}/{image_id}_{receipt_id}.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Wild Fork",
        "dry_run": false,
        # Receipt-level trace_id from FetchReceiptData
        "receipt_trace_id": "..."
    }

    Output:
    {
        "status": "completed",
        "image_id": "img1",
        "receipt_id": 1,
        "metadata_words_evaluated": 15,
        "decisions": {"VALID": 10, "INVALID": 3, "NEEDS_REVIEW": 2},
        "results_s3_key": "metadata/{exec}/{image_id}_{receipt_id}.json"
    }
    """
    logger.info(
        "[EvaluateMetadataLabels] Tracing module loaded: version=%s, source=%s",
        TRACING_VERSION,
        _tracing_import_source,
    )

    # Allow runtime override of LangSmith project via Step Function input
    langchain_project = event.get("langchain_project")
    if langchain_project:
        os.environ["LANGCHAIN_PROJECT"] = langchain_project
        logger.info("LangSmith project set to: %s", langchain_project)

    data_s3_key = event.get("data_s3_key")
    execution_id = event.get("execution_id", "unknown")
    execution_arn = event.get("execution_arn", f"local:{execution_id}")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "unknown")
    dry_run = event.get("dry_run", False)

    # Receipt-level trace_id from FetchReceiptData
    # This ensures all parallel evaluators (EvaluateLabels, Currency, Metadata)
    # share the same receipt trace. For receipt traces, root_run_id == trace_id.
    receipt_trace_id = event.get("receipt_trace_id", "")
    # Note: We don't have root_dotted_order since EvaluateLabels creates it simultaneously.
    # The trace will still link correctly via trace_id and parent_run_id.
    enable_tracing = event.get("enable_tracing", True)

    if not data_s3_key:
        raise ValueError("data_s3_key is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    start_time = time.time()

    # Initialize for error handling
    image_id = None
    receipt_id = None
    result = None

    # Join the receipt-level trace as a child
    # For receipt traces, root_run_id == trace_id (by design)
    with receipt_state_trace(
        execution_arn=execution_arn,
        image_id="",  # Will be updated after loading
        receipt_id=0,
        state_name="EvaluateMetadataLabels",
        trace_id=receipt_trace_id,
        root_run_id=receipt_trace_id,  # For receipt traces, root_run_id == trace_id
        root_dotted_order=None,  # Not available - EvaluateLabels creates it simultaneously
        inputs={
            "data_s3_key": data_s3_key,
            "merchant_name": merchant_name,
            "dry_run": dry_run,
        },
        metadata={
            "merchant_name": merchant_name,
            "execution_id": execution_id,
        },
        tags=["metadata-evaluation", "llm"],
        enable_tracing=enable_tracing,
    ) as trace_ctx:

        try:
            # Import utilities
            from utils.serialization import (
                deserialize_label,
                deserialize_word,
                deserialize_place,
            )

            # 1. Load receipt data from S3
            with child_trace("load_receipt_data", trace_ctx):
                logger.info(
                    "Loading receipt data from s3://%s/%s",
                    batch_bucket,
                    data_s3_key,
                )
                target_data = load_json_from_s3(s3, batch_bucket, data_s3_key)

                if target_data is None:
                    raise ValueError(f"Receipt data not found at {data_s3_key}")

                image_id = target_data.get("image_id")
                receipt_id = target_data.get("receipt_id")

                if not image_id or receipt_id is None:
                    raise ValueError("image_id and receipt_id are required in data")

                # Deserialize entities
                words = [deserialize_word(w) for w in target_data.get("words", [])]
                labels = [
                    deserialize_label(label_data)
                    for label_data in target_data.get("labels", [])
                ]

                # Deserialize place if present
                place_data = target_data.get("place")
                place = deserialize_place(place_data) if place_data else None

                logger.info(
                    "Loaded %s words, %s labels, place=%s for %s#%s",
                    len(words),
                    len(labels),
                    place is not None,
                    image_id,
                    receipt_id,
                )

            # 2. Build visual lines from words and labels
            with child_trace("build_visual_lines", trace_ctx):
                from receipt_agent.agents.label_evaluator.word_context import (
                    build_word_contexts,
                    assemble_visual_lines,
                )

                word_contexts = build_word_contexts(words, labels)
                visual_lines = assemble_visual_lines(word_contexts)

                logger.info("Built %s visual lines", len(visual_lines))

            # 3. Create LLM instance and run evaluation
            with child_trace("llm_metadata_evaluation", trace_ctx, metadata={
                "image_id": image_id,
                "receipt_id": receipt_id,
                "word_count": len(words),
                "has_place": place is not None,
            }):
                from langchain_ollama import ChatOllama

                ollama_api_key = os.environ.get("OLLAMA_API_KEY")
                if not ollama_api_key:
                    raise ValueError("OLLAMA_API_KEY environment variable is required")

                llm = ChatOllama(
                    model=os.environ.get("OLLAMA_MODEL", "gpt-oss:120b-cloud"),
                    base_url=os.environ.get("OLLAMA_BASE_URL", "https://ollama.com"),
                    api_key=ollama_api_key,
                    temperature=0.0,
                )

                # Run metadata evaluation
                from receipt_agent.agents.label_evaluator.metadata_subagent import (
                    evaluate_metadata_labels,
                )

                decisions = evaluate_metadata_labels(
                    visual_lines=visual_lines,
                    place=place,
                    llm=llm,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    merchant_name=merchant_name,
                )

                logger.info("Evaluated %s metadata words", len(decisions))

            # Count decisions
            decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
            for d in decisions:
                decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
                if decision in decision_counts:
                    decision_counts[decision] += 1
                else:
                    decision_counts["NEEDS_REVIEW"] += 1

            logger.info("Decisions: %s", decision_counts)

            # 4. Apply decisions to DynamoDB (if not dry run)
            applied_stats = None
            if not dry_run and decisions:
                # Filter to only INVALID decisions
                invalid_decisions = [
                    d for d in decisions
                    if d.get("llm_review", {}).get("decision") == "INVALID"
                ]

                if invalid_decisions:
                    with child_trace("apply_decisions", trace_ctx, metadata={
                        "invalid_count": len(invalid_decisions),
                    }):
                        from receipt_dynamo import DynamoClient
                        from receipt_agent.agents.label_evaluator.llm_review import (
                            apply_llm_decisions,
                        )

                        dynamo_table = os.environ.get("DYNAMODB_TABLE_NAME") or os.environ.get(
                            "RECEIPT_AGENT_DYNAMO_TABLE_NAME"
                        )
                        if dynamo_table:
                            dynamo_client = DynamoClient(table_name=dynamo_table)
                            applied_stats = apply_llm_decisions(
                                reviewed_issues=invalid_decisions,
                                dynamo_client=dynamo_client,
                                execution_id=f"metadata-{execution_id}",
                            )
                            logger.info("Applied decisions: %s", applied_stats)

            # 5. Upload results to S3
            with child_trace("upload_results", trace_ctx):
                results_s3_key = f"metadata/{execution_id}/{image_id}_{receipt_id}.json"
                results_data = {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "merchant_name": merchant_name,
                    "metadata_words_evaluated": len(decisions),
                    "decisions": decision_counts,
                    "all_decisions": decisions,
                    "applied_stats": applied_stats,
                    "dry_run": dry_run,
                    "has_place": place is not None,
                    "duration_seconds": time.time() - start_time,
                }

                upload_json_to_s3(s3, batch_bucket, results_s3_key, results_data)
                logger.info("Uploaded results to s3://%s/%s", batch_bucket, results_s3_key)

            result = {
                "status": "completed",
                "image_id": image_id,
                "receipt_id": receipt_id,
                "metadata_words_evaluated": len(decisions),
                "decisions": decision_counts,
                "results_s3_key": results_s3_key,
                "applied_stats": applied_stats,
            }

            trace_ctx.set_outputs(result)

        except Exception as e:
            logger.exception("Metadata evaluation failed")
            result = {
                "status": "failed",
                "error": str(e),
                "image_id": image_id or event.get("image_id"),
                "receipt_id": receipt_id or event.get("receipt_id"),
            }
            trace_ctx.set_outputs(result)

    # Flush LangSmith traces
    flush_langsmith_traces()

    return result
