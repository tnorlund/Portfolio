"""Unified receipt evaluator with concurrent LLM calls.

This handler consolidates all receipt evaluation steps into a single Lambda:
- Phase 1 (concurrent): Currency, Metadata, and Geometric evaluations
- Phase 2 (sequential): Financial validation (needs corrected labels)
- Phase 3 (conditional): LLM review of flagged issues

Uses asyncio.gather() for true concurrent LLM calls, eliminating Step Function
orchestration overhead and reducing cold starts from 5 lambdas to 1.
"""

# pylint: disable=import-outside-toplevel,wrong-import-position
# Lambda handlers delay imports until runtime for cold start optimization

import asyncio
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
        TraceContext,
        child_trace,
        create_receipt_trace,
        end_child_trace,
        end_receipt_trace,
        flush_langsmith_traces,
        start_child_trace,
    )

    from utils.s3_helpers import (
        download_chromadb_snapshot,
        get_merchant_hash,
        load_json_from_s3,
        upload_json_to_s3,
    )

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
        TraceContext,
        child_trace,
        create_receipt_trace,
        end_child_trace,
        end_receipt_trace,
        flush_langsmith_traces,
        start_child_trace,
    )

    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lambdas",
        ),
    )
    from utils.s3_helpers import (
        download_chromadb_snapshot,
        get_merchant_hash,
        load_json_from_s3,
        upload_json_to_s3,
    )

    _tracing_import_source = "local"


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client("s3")


async def unified_receipt_evaluator(
    event: dict[str, Any], _context: Any
) -> dict[str, Any]:
    """
    Unified receipt evaluation with concurrent LLM calls.

    Input:
    {
        "data_s3_key": "data/{exec}/{image_id}_{receipt_id}.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Wild Fork",
        "dry_run": false,
        "enable_tracing": true,
        "receipt_trace_id": "...",
        "execution_arn": "arn:aws:states:..."
    }

    Output:
    {
        "status": "completed",
        "image_id": "img1",
        "receipt_id": 1,
        "issues_found": 3,
        "currency_words_evaluated": 12,
        "metadata_words_evaluated": 15,
        "financial_values_evaluated": 8,
        "decisions": {...}
    }
    """
    logger.info(
        "[UnifiedReceiptEvaluator] Tracing module loaded: version=%s, source=%s",
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
    receipt_trace = None  # Will hold the ReceiptTraceInfo

    try:
        # Import utilities
        from utils.serialization import (
            deserialize_label,
            deserialize_place,
            deserialize_patterns,
            deserialize_word,
        )

        # Initialize dynamo_table early for use across all phases
        dynamo_table = os.environ.get(
            "DYNAMODB_TABLE_NAME"
        ) or os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME")

        # 1. Load receipt data from S3 FIRST (before creating trace)
        # We need image_id and receipt_id to create the deterministic trace ID
        logger.info(
            "Loading receipt data from s3://%s/%s",
            batch_bucket,
            data_s3_key,
        )
        target_data = load_json_from_s3(
            s3, batch_bucket, data_s3_key, logger=logger
        )

        if target_data is None:
            raise ValueError(
                f"Receipt data not found at {data_s3_key}"
            )

        image_id = target_data.get("image_id")
        receipt_id = target_data.get("receipt_id")

        if not image_id or receipt_id is None:
            raise ValueError(
                "image_id and receipt_id are required in data"
            )

        # Deserialize entities
        words = [
            deserialize_word(w) for w in target_data.get("words", [])
        ]
        labels = [
            deserialize_label(label_data)
            for label_data in target_data.get("labels", [])
        ]
        place = deserialize_place(target_data.get("place"))

        logger.info(
            "Loaded %s words, %s labels for %s#%s",
            len(words),
            len(labels),
            image_id,
            receipt_id,
        )

        # 2. Create the ROOT receipt trace (one per receipt)
        # This is the parent trace that all child operations will link to
        # NOTE: Must use "ReceiptEvaluation" name for Spark analytics compatibility
        receipt_trace = create_receipt_trace(
            execution_arn=execution_arn,
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=merchant_name,
            name="ReceiptEvaluation",
            inputs={
                "data_s3_key": data_s3_key,
                "merchant_name": merchant_name,
                "dry_run": dry_run,
                "word_count": len(words),
                "label_count": len(labels),
            },
            metadata={
                "merchant_name": merchant_name,
                "execution_id": execution_id,
            },
            tags=["unified-evaluation", "llm", "per-receipt"],
            enable_tracing=enable_tracing,
        )

        # Verify trace ID matches what FetchReceiptData generated
        if receipt_trace_id and receipt_trace_id != receipt_trace.trace_id:
            logger.warning(
                "TRACE ID MISMATCH: FetchReceiptData=%s, UnifiedEvaluator=%s",
                receipt_trace_id[:8],
                receipt_trace.trace_id[:8],
            )

        # Create TraceContext for child_trace() calls
        trace_ctx = TraceContext(
            run_tree=receipt_trace.run_tree,
            headers=(
                receipt_trace.run_tree.to_headers()
                if receipt_trace.run_tree
                else None
            ),
            trace_id=receipt_trace.trace_id,
            root_run_id=receipt_trace.root_run_id,
        )

        # 3. Load patterns
        patterns = None
        line_item_patterns = None
        merchant_hash = get_merchant_hash(merchant_name)
        patterns_s3_key = event.get("patterns_s3_key") or (
            f"patterns/{execution_id}/{merchant_hash}.json"
        )
        line_item_patterns_s3_key = event.get(
            "line_item_patterns_s3_key"
        ) or (f"line_item_patterns/{merchant_hash}.json")

        with child_trace("load_patterns", trace_ctx):
            # Load geometric patterns
            patterns_data = load_json_from_s3(
                s3,
                batch_bucket,
                patterns_s3_key,
                logger=logger,
                allow_missing=True,
            )
            if patterns_data:
                patterns = deserialize_patterns(patterns_data)

            # Load line item patterns
            line_item_patterns_data = load_json_from_s3(
                s3,
                batch_bucket,
                line_item_patterns_s3_key,
                logger=logger,
                allow_missing=True,
            )
            if line_item_patterns_data:
                if "patterns" in line_item_patterns_data:
                    line_item_patterns = line_item_patterns_data["patterns"]
                else:
                    line_item_patterns = line_item_patterns_data

        # 4. Build visual lines (shared across all evaluations)
        from receipt_agent.agents.label_evaluator.word_context import (
            assemble_visual_lines,
            build_word_contexts,
        )

        with child_trace("build_visual_lines", trace_ctx):
            word_contexts = build_word_contexts(words, labels)
            visual_lines = assemble_visual_lines(word_contexts)

            logger.info("Built %s visual lines", len(visual_lines))

        # 5. Create shared LLM invoker (used by all evaluations)
        with child_trace("setup_llm", trace_ctx):
            from receipt_agent.utils import create_production_invoker

            llm_invoker = create_production_invoker(
                temperature=0.0,
                timeout=120,
                circuit_breaker_threshold=5,
                max_jitter_seconds=0.25,
            )

        # 6. Phase 1: Run currency, metadata, and geometric evaluations concurrently
        # Each evaluation gets its own child trace for visibility in LangSmith
        from receipt_agent.agents.label_evaluator.currency_subagent import (
            evaluate_currency_labels_async,
        )
        from receipt_agent.agents.label_evaluator.metadata_subagent import (
            evaluate_metadata_labels_async,
        )
        from receipt_agent.agents.label_evaluator import (
            create_compute_only_graph,
            run_compute_only_sync,
        )
        from receipt_agent.agents.label_evaluator.state import (
            EvaluatorState,
        )

        # Create child traces for parallel evaluations (visible as siblings in LangSmith)
        currency_trace_ctx = start_child_trace(
            "currency_evaluation",
            trace_ctx,
            metadata={"image_id": image_id, "receipt_id": receipt_id},
            inputs={"merchant_name": merchant_name, "num_visual_lines": len(visual_lines)},
        )
        metadata_trace_ctx = start_child_trace(
            "metadata_evaluation",
            trace_ctx,
            metadata={"image_id": image_id, "receipt_id": receipt_id},
            inputs={"merchant_name": merchant_name, "has_place": place is not None},
        )
        geometric_trace_ctx = start_child_trace(
            "geometric_evaluation",
            trace_ctx,
            metadata={"image_id": image_id, "receipt_id": receipt_id},
            inputs={"num_words": len(words), "num_labels": len(labels)},
        )

        try:
            # Run currency and metadata concurrently (both use LLM)
            currency_task = evaluate_currency_labels_async(
                visual_lines=visual_lines,
                patterns=line_item_patterns,
                llm=llm_invoker,
                image_id=image_id,
                receipt_id=receipt_id,
                merchant_name=merchant_name,
                trace_ctx=currency_trace_ctx,
            )

            metadata_task = evaluate_metadata_labels_async(
                visual_lines=visual_lines,
                place=place,
                llm=llm_invoker,
                image_id=image_id,
                receipt_id=receipt_id,
                merchant_name=merchant_name,
                trace_ctx=metadata_trace_ctx,
            )

            # Geometric evaluation is sync (no LLM) - run in parallel with LLM calls
            # Create EvaluatorState for geometric evaluation
            geometric_state = EvaluatorState(
                image_id=image_id,
                receipt_id=receipt_id,
                words=words,
                labels=labels,
                place=place,
                other_receipt_data=[],
                merchant_patterns=patterns,
                skip_llm_review=True,
            )

            # Run geometric evaluation (sync, but runs concurrently with async tasks)
            geometric_graph = create_compute_only_graph()
            geometric_result = run_compute_only_sync(
                geometric_graph, geometric_state
            )

            # Wait for concurrent LLM calls
            currency_result, metadata_result = await asyncio.gather(
                currency_task, metadata_task
            )

            logger.info(
                "Phase 1 complete: currency=%d, metadata=%d, geometric issues=%d",
                len(currency_result),
                len(metadata_result),
                geometric_result.get("issues_found", 0),
            )
        finally:
            # End child traces with outputs
            end_child_trace(currency_trace_ctx, outputs={
                "decisions_count": len(currency_result) if 'currency_result' in dir() else 0,
            })
            end_child_trace(metadata_trace_ctx, outputs={
                "decisions_count": len(metadata_result) if 'metadata_result' in dir() else 0,
            })
            end_child_trace(geometric_trace_ctx, outputs={
                "issues_found": geometric_result.get("issues_found", 0) if 'geometric_result' in dir() else 0,
            })

        # 7. Apply Phase 1 corrections to DynamoDB
        applied_stats_currency = None
        applied_stats_metadata = None

        if not dry_run and dynamo_table:
            with child_trace("apply_phase1_corrections", trace_ctx):
                from receipt_agent.agents.label_evaluator.llm_review import (
                    apply_llm_decisions,
                )
                from receipt_dynamo import DynamoClient

                if dynamo_table:
                    dynamo_client = DynamoClient(table_name=dynamo_table)

                    # Apply currency corrections
                    invalid_currency = [
                        d
                        for d in currency_result
                        if d.get("llm_review", {}).get("decision")
                        == "INVALID"
                    ]
                    if invalid_currency:
                        applied_stats_currency = apply_llm_decisions(
                            reviewed_issues=invalid_currency,
                            dynamo_client=dynamo_client,
                            execution_id=f"currency-{execution_id}",
                        )

                    # Apply metadata corrections
                    invalid_metadata = [
                        d
                        for d in metadata_result
                        if d.get("llm_review", {}).get("decision")
                        == "INVALID"
                    ]
                    if invalid_metadata:
                        applied_stats_metadata = apply_llm_decisions(
                            reviewed_issues=invalid_metadata,
                            dynamo_client=dynamo_client,
                            execution_id=f"metadata-{execution_id}",
                        )

        # 8. Phase 2: Financial validation (needs corrected labels from Phase 1)
        # Always run evaluation for diagnostics; only skip writes in dry_run mode
        financial_result = None
        if dynamo_table:
            with child_trace("phase2_financial_validation", trace_ctx) as financial_ctx:
                # Re-fetch labels from DynamoDB to get corrections
                from receipt_dynamo import DynamoClient

                dynamo_client = DynamoClient(table_name=dynamo_table)

                # Fetch fresh labels
                fresh_labels = []
                page, lek = (
                    dynamo_client.list_receipt_word_labels_for_receipt(
                        image_id=image_id, receipt_id=receipt_id
                    )
                )
                fresh_labels.extend(page or [])
                while lek:
                    page, lek = (
                        dynamo_client.list_receipt_word_labels_for_receipt(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            last_evaluated_key=lek,
                        )
                    )
                    fresh_labels.extend(page or [])

                logger.info(
                    "Fetched %s fresh labels from DynamoDB",
                    len(fresh_labels),
                )

                # Rebuild visual lines with fresh labels
                fresh_word_contexts = build_word_contexts(
                    words, fresh_labels
                )
                fresh_visual_lines = assemble_visual_lines(
                    fresh_word_contexts
                )

                # Run financial validation
                from receipt_agent.agents.label_evaluator.financial_subagent import (
                    evaluate_financial_math_async,
                )

                financial_result = (
                    await evaluate_financial_math_async(
                        visual_lines=fresh_visual_lines,
                        llm=llm_invoker,
                        image_id=image_id,
                        receipt_id=receipt_id,
                        merchant_name=merchant_name,
                        trace_ctx=financial_ctx,
                    )
                )

                # Apply financial corrections (only when not dry_run)
                if not dry_run and financial_result:
                    from receipt_agent.agents.label_evaluator.llm_review import (
                        apply_llm_decisions,
                    )

                    invalid_financial = [
                        d
                        for d in financial_result
                        if d.get("llm_review", {}).get("decision")
                        == "INVALID"
                    ]
                    if invalid_financial:
                        apply_llm_decisions(
                            reviewed_issues=invalid_financial,
                            dynamo_client=dynamo_client,
                            execution_id=f"financial-{execution_id}",
                        )

        # 9. Phase 3: LLM review of flagged geometric issues (if any)
        llm_review_result = None
        issues_found = geometric_result.get("issues_found", 0)

        if issues_found > 0:
            with child_trace("phase3_llm_review", trace_ctx):
                # Setup ChromaDB if available
                chromadb_bucket = os.environ.get("CHROMADB_BUCKET")
                chroma_client = None

                if chromadb_bucket:
                    try:
                        chroma_path = os.environ.get(
                            "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY",
                            "/tmp/chromadb",
                        )
                        download_chromadb_snapshot(
                            s3, chromadb_bucket, "words", chroma_path
                        )
                        os.environ[
                            "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"
                        ] = chroma_path

                        from receipt_chroma import ChromaClient

                        chroma_client = ChromaClient(
                            persist_directory=chroma_path
                        )
                    except Exception as e:
                        logger.warning(
                            "Could not initialize ChromaDB: %s", e
                        )

                # Get issues from geometric result
                geometric_issues = geometric_result.get("issues", [])

                if geometric_issues and chroma_client:
                    from receipt_agent.agents.label_evaluator.llm_review import (
                        assemble_receipt_text,
                    )
                    from receipt_agent.prompts.label_evaluator import (
                        build_receipt_context_prompt,
                        parse_batched_llm_response,
                    )
                    from receipt_agent.utils.chroma_helpers import (
                        compute_label_consensus,
                        format_label_evidence_for_prompt,
                        query_label_evidence,
                    )
                    from langchain_core.messages import HumanMessage

                    # Gather context for issues using targeted boolean queries
                    issues_with_context = []
                    for issue in geometric_issues[:15]:  # Limit to 15
                        word_text = issue.get("word_text", "")
                        line_id = issue.get("line_id", 0)
                        word_id = issue.get("word_id", 0)
                        current_label = issue.get("current_label", "")

                        try:
                            # Use targeted query for the specific label being reviewed
                            if current_label and current_label != "O":
                                label_evidence = query_label_evidence(
                                    chroma_client=chroma_client,
                                    image_id=image_id,
                                    receipt_id=receipt_id,
                                    line_id=line_id,
                                    word_id=word_id,
                                    target_label=current_label,
                                    target_merchant=merchant_name,
                                    n_results_per_query=15,
                                    min_similarity=0.70,
                                )

                                # Format evidence for prompt
                                evidence_text = format_label_evidence_for_prompt(
                                    label_evidence,
                                    target_label=current_label,
                                    max_positive=5,
                                    max_negative=3,
                                )

                                # Compute consensus for decision support
                                consensus, pos_count, neg_count = (
                                    compute_label_consensus(label_evidence)
                                )
                            else:
                                label_evidence = []
                                evidence_text = "No evidence needed for O labels."
                                consensus, pos_count, neg_count = 0.0, 0, 0

                            issues_with_context.append(
                                {
                                    "issue": issue,
                                    "label_evidence": label_evidence,
                                    "evidence_text": evidence_text,
                                    "consensus": consensus,
                                    "positive_count": pos_count,
                                    "negative_count": neg_count,
                                }
                            )
                        except Exception as e:
                            logger.warning(
                                "Error gathering context for %s: %s",
                                current_label,
                                e,
                            )
                            issues_with_context.append(
                                {
                                    "issue": issue,
                                    "label_evidence": [],
                                    "evidence_text": f"Error: {e}",
                                    "consensus": 0.0,
                                    "positive_count": 0,
                                    "negative_count": 0,
                                }
                            )

                    if issues_with_context:
                        # Build prompt and call LLM
                        highlight_words = [
                            (
                                item["issue"].get("line_id"),
                                item["issue"].get("word_id"),
                            )
                            for item in issues_with_context
                        ]

                        receipt_text = assemble_receipt_text(
                            words=words,
                            labels=labels,
                            highlight_words=highlight_words,
                            max_lines=60,
                        )

                        prompt = build_receipt_context_prompt(
                            receipt_text=receipt_text,
                            issues_with_context=issues_with_context,
                            merchant_name=merchant_name,
                            merchant_receipt_count=0,  # Not available here
                            line_item_patterns=line_item_patterns,
                        )

                        # Make async LLM call
                        response = await llm_invoker.ainvoke(
                            [HumanMessage(content=prompt)]
                        )

                        # Parse response
                        response_text = (
                            response.content
                            if hasattr(response, "content")
                            else str(response)
                        )
                        chunk_reviews = parse_batched_llm_response(
                            response_text.strip(),
                            expected_count=len(issues_with_context),
                            raise_on_parse_error=False,
                        )

                        # Format results
                        llm_review_result = []
                        for i, review_result in enumerate(chunk_reviews):
                            meta = issues_with_context[i]
                            llm_review_result.append(
                                {
                                    "image_id": image_id,
                                    "receipt_id": receipt_id,
                                    "issue": meta["issue"],
                                    "llm_review": review_result,
                                }
                            )

                        # Apply LLM review decisions
                        if not dry_run and dynamo_table:
                            invalid_reviewed = [
                                d
                                for d in llm_review_result
                                if d.get("llm_review", {}).get("decision")
                                == "INVALID"
                            ]
                            if invalid_reviewed:
                                dynamo_client = DynamoClient(
                                    table_name=dynamo_table
                                )
                                apply_llm_decisions(
                                    reviewed_issues=invalid_reviewed,
                                    dynamo_client=dynamo_client,
                                    execution_id=execution_id,
                                )

        # 10. Aggregate results
        decision_counts = {
            "currency": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
            "metadata": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
            "financial": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
        }

        for d in currency_result:
            decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
            if decision in decision_counts["currency"]:
                decision_counts["currency"][decision] += 1

        for d in metadata_result:
            decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
            if decision in decision_counts["metadata"]:
                decision_counts["metadata"][decision] += 1

        if financial_result:
            for d in financial_result:
                decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
                if decision in decision_counts["financial"]:
                    decision_counts["financial"][decision] += 1

        # 11. Upload results to S3
        with child_trace("upload_results", trace_ctx):
            results_s3_key = (
                f"unified/{execution_id}/{image_id}_{receipt_id}.json"
            )
            results_data = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant_name": merchant_name,
                "issues_found": issues_found,
                "currency_words_evaluated": len(currency_result),
                "metadata_words_evaluated": len(metadata_result),
                "financial_values_evaluated": (
                    len(financial_result) if financial_result else 0
                ),
                "decisions": decision_counts,
                "applied_stats": {
                    "currency": applied_stats_currency,
                    "metadata": applied_stats_metadata,
                },
                "dry_run": dry_run,
                "duration_seconds": time.time() - start_time,
            }

            upload_json_to_s3(
                s3, batch_bucket, results_s3_key, results_data
            )
            logger.info(
                "Uploaded results to s3://%s/%s",
                batch_bucket,
                results_s3_key,
            )

        result = {
            "status": "completed",
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "issues_found": issues_found,
            "currency_words_evaluated": len(currency_result),
            "metadata_words_evaluated": len(metadata_result),
            "financial_values_evaluated": (
                len(financial_result) if financial_result else 0
            ),
            "decisions": decision_counts,
            "results_s3_key": results_s3_key,
        }

    except Exception as e:
        from receipt_agent.utils.llm_factory import AllProvidersFailedError
        from receipt_agent.utils.ollama_rate_limit import (
            OllamaRateLimitError,
        )

        if isinstance(e, (OllamaRateLimitError, AllProvidersFailedError)):
            logger.error(
                "Rate limit error, propagating for Step Function retry: %s",
                e,
            )
            flush_langsmith_traces()
            raise

        logger.exception("Unified evaluation failed")
        result = {
            "status": "failed",
            "error": str(e),
            "image_id": image_id or event.get("image_id"),
            "receipt_id": receipt_id or event.get("receipt_id"),
            "merchant_name": merchant_name,
            "issues_found": 0,  # Default to 0 on error
            "currency_words_evaluated": 0,
            "metadata_words_evaluated": 0,
            "financial_values_evaluated": 0,
            "decisions": {
                "currency": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
                "metadata": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
                "financial": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
            },
        }

    # Close the receipt trace (created with create_receipt_trace)
    if receipt_trace is not None:
        end_receipt_trace(
            receipt_trace,
            outputs={
                "status": result.get("status", "unknown") if result else "unknown",
                "issues_found": result.get("issues_found", 0) if result else 0,
            },
        )

    # Flush LangSmith traces
    flush_langsmith_traces()

    return result


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point - runs the async handler."""
    return asyncio.run(unified_receipt_evaluator(event, context))
