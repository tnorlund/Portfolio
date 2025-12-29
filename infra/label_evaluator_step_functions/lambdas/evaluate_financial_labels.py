"""Validate financial math relationships with LLM.

This handler runs AFTER currency/metadata reviews to validate:
- GRAND_TOTAL = SUBTOTAL + TAX
- SUBTOTAL = sum(LINE_TOTAL)
- QTY × UNIT_PRICE = LINE_TOTAL

Key difference from currency/metadata handlers:
- Re-fetches labels from DynamoDB (not S3) to get corrected values
- This ensures we validate math on up-to-date labels

Runs sequentially after ParallelReview in the Step Function.
"""

# pylint: disable=import-outside-toplevel,wrong-import-position
# Lambda handlers delay imports until runtime for cold start optimization

import logging
import os
import sys
import time
from typing import Any

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
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lambdas",
            "utils",
        ),
    )
    from tracing import (
        TRACING_VERSION,
        child_trace,
        flush_langsmith_traces,
        receipt_state_trace,
    )

    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lambdas",
        ),
    )
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
    Validate financial math after currency/metadata corrections.

    Runs AFTER ParallelReview to benefit from corrected labels.
    Re-fetches labels from DynamoDB to get the latest corrections.

    Input:
    {
        "data_s3_key": "data/{exec}/{image_id}_{receipt_id}.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Wild Fork",
        "dry_run": false,
        "receipt_trace_id": "..."
    }

    Output:
    {
        "status": "completed",
        "image_id": "img1",
        "receipt_id": 1,
        "math_issues_found": 2,
        "decisions": {"VALID": 5, "INVALID": 2, "NEEDS_REVIEW": 0},
        "results_s3_key": "financial/{exec}/{image_id}_{receipt_id}.json"
    }
    """
    logger.info(
        "[ValidateFinancialMath] Tracing module loaded: version=%s, source=%s",
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
    receipt_trace_id = event.get("receipt_trace_id", "")
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
    with receipt_state_trace(
        execution_arn=execution_arn,
        image_id="",
        receipt_id=0,
        state_name="ValidateFinancialMath",
        trace_id=receipt_trace_id,
        root_run_id=receipt_trace_id,
        root_dotted_order=None,
        inputs={
            "data_s3_key": data_s3_key,
            "merchant_name": merchant_name,
            "dry_run": dry_run,
        },
        metadata={
            "merchant_name": merchant_name,
            "execution_id": execution_id,
        },
        tags=["financial-validation", "llm"],
        enable_tracing=enable_tracing,
    ) as trace_ctx:

        try:
            # Import utilities
            from utils.serialization import deserialize_word

            # 1. Load receipt data from S3 (words only - we'll re-fetch labels)
            with child_trace("load_receipt_data", trace_ctx):
                logger.info(
                    "Loading receipt data from s3://%s/%s",
                    batch_bucket,
                    data_s3_key,
                )
                target_data = load_json_from_s3(
                    s3, batch_bucket, data_s3_key, logger=logger
                )

                if target_data is None:
                    raise ValueError(f"Receipt data not found at {data_s3_key}")

                image_id = target_data.get("image_id")
                receipt_id = target_data.get("receipt_id")

                if not image_id or receipt_id is None:
                    raise ValueError("image_id and receipt_id are required in data")

                # Deserialize words
                words = [deserialize_word(w) for w in target_data.get("words", [])]

                logger.info(
                    "Loaded %s words for %s#%s",
                    len(words),
                    image_id,
                    receipt_id,
                )

            # 2. Re-fetch labels from DynamoDB to get corrected values
            # This is the KEY DIFFERENCE from currency/metadata handlers
            with child_trace("fetch_labels_from_dynamo", trace_ctx):
                from receipt_dynamo import DynamoClient

                dynamo_table = os.environ.get("DYNAMODB_TABLE_NAME") or os.environ.get(
                    "RECEIPT_AGENT_DYNAMO_TABLE_NAME"
                )

                if not dynamo_table:
                    raise ValueError("DYNAMODB_TABLE_NAME not configured")

                dynamo_client = DynamoClient(table_name=dynamo_table)

                # Fetch all labels for this receipt
                labels = []
                page, lek = dynamo_client.list_receipt_word_labels_for_receipt(
                    image_id=image_id, receipt_id=receipt_id
                )
                labels.extend(page or [])
                while lek:
                    page, lek = dynamo_client.list_receipt_word_labels_for_receipt(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        last_evaluated_key=lek,
                    )
                    labels.extend(page or [])

                logger.info(
                    "Fetched %s labels from DynamoDB (includes corrections)",
                    len(labels),
                )

            # 3. Build visual lines from words and labels
            with child_trace("build_visual_lines", trace_ctx):
                from receipt_agent.agents.label_evaluator.word_context import (
                    assemble_visual_lines,
                    build_word_contexts,
                )

                word_contexts = build_word_contexts(words, labels)
                visual_lines = assemble_visual_lines(word_contexts)

                logger.info("Built %s visual lines", len(visual_lines))

            # 4. Create LLM instance and run financial validation
            with child_trace(
                "llm_financial_validation",
                trace_ctx,
                metadata={
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "word_count": len(words),
                },
            ):
                from receipt_agent.utils import create_production_invoker

                llm_invoker = create_production_invoker(
                    temperature=0.0,
                    timeout=120,
                    circuit_breaker_threshold=5,
                    max_jitter_seconds=0.25,
                )

                # Run financial validation
                from receipt_agent.agents.label_evaluator.financial_subagent import (
                    evaluate_financial_math,
                )

                decisions = evaluate_financial_math(
                    visual_lines=visual_lines,
                    llm=llm_invoker,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    merchant_name=merchant_name,
                )

                logger.info(
                    "Evaluated financial math, %s values involved", len(decisions)
                )

            # Count decisions
            decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
            for d in decisions:
                decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
                if decision in decision_counts:
                    decision_counts[decision] += 1
                else:
                    decision_counts["NEEDS_REVIEW"] += 1

            # Count unique math issues
            issue_types = set()
            for d in decisions:
                issue_type = d.get("issue", {}).get("issue_type")
                if issue_type:
                    issue_types.add(issue_type)

            logger.info("Decisions: %s", decision_counts)
            logger.info("Issue types: %s", issue_types)

            # 5. Apply decisions to DynamoDB (if not dry run)
            applied_stats = None
            if not dry_run and decisions:
                # Filter to only INVALID decisions
                invalid_decisions = [
                    d
                    for d in decisions
                    if d.get("llm_review", {}).get("decision") == "INVALID"
                ]

                if invalid_decisions:
                    with child_trace(
                        "apply_decisions",
                        trace_ctx,
                        metadata={
                            "invalid_count": len(invalid_decisions),
                        },
                    ):
                        from receipt_agent.agents.label_evaluator.llm_review import (
                            apply_llm_decisions,
                        )

                        applied_stats = apply_llm_decisions(
                            reviewed_issues=invalid_decisions,
                            dynamo_client=dynamo_client,
                            execution_id=f"financial-{execution_id}",
                        )
                        logger.info("Applied decisions: %s", applied_stats)

            # 6. Upload results to S3
            with child_trace("upload_results", trace_ctx):
                results_s3_key = (
                    f"financial/{execution_id}/{image_id}_{receipt_id}.json"
                )
                results_data = {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "merchant_name": merchant_name,
                    "math_issues_found": len(issue_types),
                    "values_evaluated": len(decisions),
                    "decisions": decision_counts,
                    "issue_types": list(issue_types),
                    "all_decisions": decisions,
                    "applied_stats": applied_stats,
                    "dry_run": dry_run,
                    "duration_seconds": time.time() - start_time,
                }

                upload_json_to_s3(s3, batch_bucket, results_s3_key, results_data)
                logger.info(
                    "Uploaded results to s3://%s/%s",
                    batch_bucket,
                    results_s3_key,
                )

            result = {
                "status": "completed",
                "image_id": image_id,
                "receipt_id": receipt_id,
                "math_issues_found": len(issue_types),
                "values_evaluated": len(decisions),
                "decisions": decision_counts,
                "results_s3_key": results_s3_key,
                "applied_stats": applied_stats,
            }

            trace_ctx.set_outputs(result)

        except Exception as e:
            logger.exception("Financial validation failed")
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
