"""Evaluate currency labels with LLM.

This handler evaluates currency labels on line item rows using the
line item patterns from DiscoverLineItemPatterns.

Runs in parallel with EvaluateLabels (deterministic checks).
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
    Evaluate currency labels using LLM and line item patterns.

    Runs in parallel with EvaluateLabels. Joins the execution-level trace
    from DiscoverLineItemPatterns/ComputePatterns.

    Input:
    {
        "data_s3_key": "data/{exec}/{image_id}_{receipt_id}.json",
        "line_item_patterns_s3_key": "line_item_patterns/{merchant_hash}.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Wild Fork",
        "dry_run": false,
        # Trace context (from execution-level trace)
        "trace_id": "...",
        "root_run_id": "...",
        "root_dotted_order": "..."
    }

    Output:
    {
        "status": "completed",
        "image_id": "img1",
        "receipt_id": 1,
        "currency_words_evaluated": 12,
        "decisions": {"VALID": 8, "INVALID": 4, "NEEDS_REVIEW": 0},
        "results_s3_key": "currency/{exec}/{image_id}_{receipt_id}.json"
    }
    """
    logger.info(
        "[EvaluateCurrencyLabels] Tracing module loaded: version=%s, source=%s",
        TRACING_VERSION,
        _tracing_import_source,
    )

    data_s3_key = event.get("data_s3_key")
    line_item_patterns_s3_key = event.get("line_item_patterns_s3_key")
    execution_id = event.get("execution_id", "unknown")
    execution_arn = event.get("execution_arn", f"local:{execution_id}")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "unknown")
    dry_run = event.get("dry_run", False)

    # Trace context from upstream (execution-level trace)
    trace_id = event.get("trace_id", "")
    root_run_id = event.get("root_run_id", "")
    root_dotted_order = event.get("root_dotted_order")
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

    # Join the execution-level trace as a child
    with receipt_state_trace(
        execution_arn=execution_arn,
        image_id="",  # Will be updated after loading
        receipt_id=0,
        state_name="EvaluateCurrencyLabels",
        trace_id=trace_id,
        root_run_id=root_run_id,
        root_dotted_order=root_dotted_order,
        inputs={
            "data_s3_key": data_s3_key,
            "merchant_name": merchant_name,
            "dry_run": dry_run,
        },
        metadata={
            "merchant_name": merchant_name,
            "execution_id": execution_id,
        },
        tags=["currency-evaluation", "llm"],
        enable_tracing=enable_tracing,
    ) as trace_ctx:

        try:
            # Import utilities
            from utils.serialization import (
                deserialize_label,
                deserialize_word,
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

                logger.info(
                    "Loaded %s words, %s labels for %s#%s",
                    len(words),
                    len(labels),
                    image_id,
                    receipt_id,
                )

            # 2. Load line item patterns from S3
            patterns = None
            if line_item_patterns_s3_key:
                with child_trace("load_patterns", trace_ctx):
                    logger.info(
                        "Loading line item patterns from s3://%s/%s",
                        batch_bucket,
                        line_item_patterns_s3_key,
                    )
                    patterns_data = load_json_from_s3(
                        s3, batch_bucket, line_item_patterns_s3_key
                    )
                    if patterns_data:
                        # DiscoverPatterns writes patterns dict directly to S3
                        # Support both formats for compatibility
                        if "patterns" in patterns_data:
                            patterns = patterns_data["patterns"]
                        else:
                            patterns = patterns_data
                        logger.info(
                            "Loaded patterns: item_structure=%s",
                            patterns.get("item_structure") if patterns else None,
                        )

            # 3. Build visual lines from words and labels
            with child_trace("build_visual_lines", trace_ctx):
                from receipt_agent.agents.label_evaluator.word_context import (
                    build_word_contexts,
                    assemble_visual_lines,
                )

                word_contexts = build_word_contexts(words, labels)
                visual_lines = assemble_visual_lines(word_contexts)

                logger.info("Built %s visual lines", len(visual_lines))

            # 4. Create LLM instance and run evaluation
            with child_trace("llm_currency_evaluation", trace_ctx, metadata={
                "image_id": image_id,
                "receipt_id": receipt_id,
                "word_count": len(words),
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

                # Run currency evaluation
                from receipt_agent.agents.label_evaluator.currency_subagent import (
                    evaluate_currency_labels,
                )

                decisions = evaluate_currency_labels(
                    visual_lines=visual_lines,
                    patterns=patterns,
                    llm=llm,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    merchant_name=merchant_name,
                )

                logger.info("Evaluated %s currency words", len(decisions))

            # Count decisions
            decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
            for d in decisions:
                decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
                if decision in decision_counts:
                    decision_counts[decision] += 1
                else:
                    decision_counts["NEEDS_REVIEW"] += 1

            logger.info("Decisions: %s", decision_counts)

            # 5. Apply decisions to DynamoDB (if not dry run)
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
                                execution_id=f"currency-{execution_id}",
                            )
                            logger.info("Applied decisions: %s", applied_stats)

            # 6. Upload results to S3
            with child_trace("upload_results", trace_ctx):
                results_s3_key = f"currency/{execution_id}/{image_id}_{receipt_id}.json"
                results_data = {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "merchant_name": merchant_name,
                    "currency_words_evaluated": len(decisions),
                    "decisions": decision_counts,
                    "all_decisions": decisions,
                    "applied_stats": applied_stats,
                    "dry_run": dry_run,
                    "duration_seconds": time.time() - start_time,
                }

                upload_json_to_s3(s3, batch_bucket, results_s3_key, results_data)
                logger.info("Uploaded results to s3://%s/%s", batch_bucket, results_s3_key)

            result = {
                "status": "completed",
                "image_id": image_id,
                "receipt_id": receipt_id,
                "currency_words_evaluated": len(decisions),
                "decisions": decision_counts,
                "results_s3_key": results_s3_key,
                "applied_stats": applied_stats,
            }

            trace_ctx.set_outputs(result)

        except Exception as e:
            logger.exception("Currency evaluation failed")
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
