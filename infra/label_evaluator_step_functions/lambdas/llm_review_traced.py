"""LLM Review with trace propagation.

This handler creates a child trace using deterministic UUIDs based on
execution ARN and llm_batch_index. The trace_id and root_run_id are passed
from the upstream DiscoverLineItemPatterns Lambda.

LangChain's ChatOllama calls will appear as children in the unified trace.
"""

import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any, Optional

import boto3
from botocore.config import Config

# Import tracing utilities
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lambdas", "utils"
))
from tracing import child_trace, flush_langsmith_traces, state_trace

# Import from the original llm_review module for utility functions
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lambdas"
))

from receipt_agent import (
    OllamaCircuitBreaker,
    OllamaRateLimitError,
    RateLimitedLLMInvoker,
)
from receipt_agent.agents.label_evaluator import apply_llm_decisions
from receipt_agent.constants import CORE_LABELS, CORE_LABELS_SET
from receipt_agent.utils.chroma_helpers import build_word_chroma_id

if TYPE_CHECKING:
    from handlers.evaluator_types import (
        LabelDistributionStats,
        LLMDecision,
        LLMReviewBatchOutput,
        MerchantBreakdown,
        SimilarityDistribution,
        SimilarWordEvidence,
    )

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client(
    "s3",
    config=Config(connect_timeout=10, read_timeout=120),
)


# Import utility functions from the original llm_review module
from llm_review import (
    assemble_receipt_text,
    build_receipt_context_prompt,
    compute_label_distribution,
    compute_merchant_breakdown,
    compute_similarity_distribution,
    download_chromadb_snapshot,
    enrich_evidence_with_dynamo_reasoning,
    load_json_from_s3,
    parse_batched_llm_response,
    query_similar_words,
    upload_json_to_s3,
)


def handler(event: dict[str, Any], _context: Any) -> "LLMReviewBatchOutput":
    """
    Review a batch of flagged issues using LLM with trace propagation.

    Input:
    {
        "execution_id": "abc123",
        "execution_arn": "arn:aws:states:...",  # From $$.Execution.Id
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "merchant_receipt_count": 50,
        "batch_s3_key": "batches/{exec}/{merchant_hash}_0.json",
        "batch_index": 0,
        "llm_batch_index": 0,    # From Map state
        "dry_run": false,
        "trace_id": "...",       # From upstream Lambda
        "root_run_id": "..."     # From upstream Lambda
    }

    Output:
    {
        "status": "completed",
        "execution_id": "abc123",
        "merchant_name": "Sprouts Farmers Market",
        "batch_index": 0,
        "total_issues": 50,
        "issues_reviewed": 50,
        "decisions": {"VALID": 10, "INVALID": 25, "NEEDS_REVIEW": 15},
        "reviewed_issues_s3_key": "reviewed/{exec}/{merchant_hash}_0.json"
    }
    """
    execution_id = event.get("execution_id", "unknown")
    execution_arn = event.get("execution_arn", f"local:{execution_id}")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "Unknown")
    merchant_receipt_count = event.get("merchant_receipt_count", 0)
    batch_index = event.get("batch_index", 0)
    llm_batch_index = event.get("llm_batch_index", 0)
    dry_run = event.get("dry_run", False)
    line_item_patterns_s3_key = event.get("line_item_patterns_s3_key")

    # Get trace IDs from upstream Lambda
    trace_id = event.get("trace_id", "")
    root_run_id = event.get("root_run_id", "")
    root_dotted_order = event.get("root_dotted_order")

    # Support both batch format and legacy format
    batch_s3_key = event.get("batch_s3_key")
    issues_s3_key = event.get("issues_s3_key")
    data_s3_key = batch_s3_key or issues_s3_key

    # Rate limiting configuration
    circuit_breaker_threshold = int(
        os.environ.get("CIRCUIT_BREAKER_THRESHOLD", "5")
    )
    max_jitter_seconds = float(
        os.environ.get("LLM_MAX_JITTER_SECONDS", "0.25")
    )

    chromadb_bucket = os.environ.get("CHROMADB_BUCKET")
    table_name = os.environ.get(
        "DYNAMODB_TABLE_NAME",
        os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
    )

    if not batch_bucket:
        raise ValueError("batch_bucket is required")
    if not data_s3_key:
        raise ValueError("batch_s3_key or issues_s3_key is required")

    start_time = time.time()

    # Create a child trace using deterministic UUID
    with state_trace(
        execution_arn=execution_arn,
        state_name="LLMReviewBatch",
        trace_id=trace_id,
        root_run_id=root_run_id,
        root_dotted_order=root_dotted_order,
        map_index=llm_batch_index,
        inputs={
            "merchant_name": merchant_name,
            "batch_index": batch_index,
            "merchant_receipt_count": merchant_receipt_count,
        },
        metadata={
            "merchant_name": merchant_name,
            "batch_index": batch_index,
            "llm_batch_index": llm_batch_index,
        },
        tags=["llm-review", "llm"],
    ) as trace_ctx:

        try:
            # 1. Load collected issues from S3
            with child_trace("load_issues", trace_ctx):
                logger.info(f"Loading issues from s3://{batch_bucket}/{data_s3_key}")
                issues_data = load_json_from_s3(batch_bucket, data_s3_key)
                collected_issues = issues_data.get("issues", [])

            total_issues = len(collected_issues)
            if total_issues == 0:
                logger.info("No issues to review")
                result = {
                    "status": "skipped",
                    "execution_id": execution_id,
                    "merchant_name": merchant_name,
                    "total_issues": 0,
                    "issues_reviewed": 0,
                    "decisions": {},
                }
                trace_ctx.set_outputs(result)
                flush_langsmith_traces()
                return result

            logger.info(
                f"Reviewing {total_issues} issues for {merchant_name} "
                f"({merchant_receipt_count} receipts)"
            )

            # 2. Load line item patterns (if available)
            line_item_patterns = None
            if line_item_patterns_s3_key:
                with child_trace("load_line_item_patterns", trace_ctx):
                    try:
                        logger.info(
                            f"Loading line item patterns from "
                            f"s3://{batch_bucket}/{line_item_patterns_s3_key}"
                        )
                        line_item_patterns = load_json_from_s3(
                            batch_bucket, line_item_patterns_s3_key
                        )
                        logger.info(
                            f"Loaded patterns: {line_item_patterns.get('item_structure', 'unknown')} "
                            f"structure"
                        )
                    except Exception as e:
                        logger.warning(f"Could not load line item patterns: {e}")

            # 3. Setup ChromaDB
            chroma_client = None
            if chromadb_bucket:
                with child_trace("setup_chromadb", trace_ctx):
                    try:
                        chroma_path = os.environ.get(
                            "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY", "/tmp/chromadb"
                        )
                        download_chromadb_snapshot(
                            chromadb_bucket, "words", chroma_path
                        )
                        os.environ["RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"] = (
                            chroma_path
                        )

                        from receipt_chroma import ChromaClient

                        chroma_client = ChromaClient(persist_directory=chroma_path)
                        logger.info("ChromaDB client initialized")
                    except Exception as e:
                        logger.warning(f"Could not initialize ChromaDB: {e}")

            # 4. Setup DynamoDB client
            dynamo_client = None
            if table_name:
                try:
                    from receipt_dynamo import DynamoClient

                    dynamo_client = DynamoClient(table_name=table_name)
                    logger.info("DynamoDB client initialized")
                except Exception as e:
                    logger.warning(f"Could not initialize DynamoDB: {e}")

            # 5. Setup Ollama LLM with LangSmith tracing context
            with child_trace("setup_llm", trace_ctx):
                ollama_api_key = os.environ.get("RECEIPT_AGENT_OLLAMA_API_KEY")
                ollama_base_url = os.environ.get(
                    "RECEIPT_AGENT_OLLAMA_BASE_URL", "https://ollama.com"
                )
                ollama_model = os.environ.get(
                    "RECEIPT_AGENT_OLLAMA_MODEL", "gpt-oss:20b-cloud"
                )

                if not ollama_api_key:
                    raise ValueError("RECEIPT_AGENT_OLLAMA_API_KEY not set")

                from langchain_ollama import ChatOllama

                base_llm = ChatOllama(
                    model=ollama_model,
                    base_url=ollama_base_url,
                    client_kwargs={
                        "headers": {"Authorization": f"Bearer {ollama_api_key}"},
                        "timeout": 120,
                    },
                    temperature=0,
                )

                # Wrap LLM with rate limiting and circuit breaker
                circuit_breaker = OllamaCircuitBreaker(
                    threshold=circuit_breaker_threshold
                )
                llm_invoker = RateLimitedLLMInvoker(
                    llm=base_llm,
                    circuit_breaker=circuit_breaker,
                    max_jitter_seconds=max_jitter_seconds,
                )

                logger.info(
                    f"LLM initialized: {ollama_model} (max jitter: {max_jitter_seconds}s)"
                )

            # 6. Process issues by receipt with tracing
            max_issues_per_call = int(
                os.environ.get("MAX_ISSUES_PER_LLM_CALL", "15")
            )

            decisions: Counter = Counter()
            reviewed_issues: list[dict[str, Any]] = []
            similar_cache: dict[str, list] = {}

            # Group issues by receipt
            issues_by_receipt: dict[str, list[dict]] = defaultdict(list)
            for collected in collected_issues:
                image_id = collected.get("image_id")
                receipt_id = collected.get("receipt_id")
                receipt_key = f"{image_id}:{receipt_id}"
                issues_by_receipt[receipt_key].append(collected)

            logger.info(
                f"Processing {total_issues} issues across "
                f"{len(issues_by_receipt)} receipts"
            )

            from langchain_core.messages import HumanMessage

            llm_call_count = 0

            # Process each receipt
            for receipt_key, receipt_issues in issues_by_receipt.items():
                image_id, receipt_id_str = receipt_key.split(":", 1)
                receipt_id = int(receipt_id_str)

                with child_trace(
                    f"process_receipt:{image_id[:8]}#{receipt_id}",
                    trace_ctx,
                    metadata={
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "issue_count": len(receipt_issues),
                    },
                ):
                    # Load receipt data
                    receipt_data_key = (
                        f"data/{execution_id}/{image_id}_{receipt_id}.json"
                    )
                    try:
                        receipt_data = load_json_from_s3(
                            batch_bucket, receipt_data_key
                        )
                        receipt_words = receipt_data.get("words", [])
                        receipt_labels = receipt_data.get("labels", [])
                    except Exception as e:
                        logger.exception(
                            "Could not load receipt data for %s",
                            receipt_key,
                        )
                        for collected in receipt_issues:
                            issue = collected.get("issue", {})
                            decisions["NEEDS_REVIEW"] += 1
                            reviewed_issues.append(
                                {
                                    "image_id": image_id,
                                    "receipt_id": receipt_id,
                                    "issue": issue,
                                    "llm_review": {
                                        "decision": "NEEDS_REVIEW",
                                        "reasoning": f"Receipt data unavailable: {e}",
                                        "suggested_label": None,
                                        "confidence": "low",
                                    },
                                    "data_load_error": str(e),
                                }
                            )
                        continue

                    # Process issues in chunks
                    for chunk_start in range(
                        0, len(receipt_issues), max_issues_per_call
                    ):
                        chunk_end = min(
                            chunk_start + max_issues_per_call, len(receipt_issues)
                        )
                        chunk_issues = receipt_issues[chunk_start:chunk_end]

                        # Gather context for each issue
                        issues_with_context = []
                        chunk_metadata = []

                        for collected in chunk_issues:
                            issue = collected.get("issue", {})
                            word_text = issue.get("word_text", "")
                            word_id = issue.get("word_id", 0)
                            line_id = issue.get("line_id", 0)

                            chunk_metadata.append(
                                {
                                    "image_id": image_id,
                                    "receipt_id": receipt_id,
                                    "issue": issue,
                                }
                            )

                            try:
                                # Query similar words (with caching)
                                cache_key = (
                                    f"{image_id}:{receipt_id}:{line_id}:{word_id}"
                                )
                                if cache_key in similar_cache:
                                    similar_evidence = similar_cache[cache_key]
                                elif chroma_client:
                                    similar_evidence = query_similar_words(
                                        chroma_client=chroma_client,
                                        word_text=word_text,
                                        image_id=image_id,
                                        receipt_id=receipt_id,
                                        line_id=line_id,
                                        word_id=word_id,
                                        target_merchant=merchant_name,
                                    )

                                    if dynamo_client and similar_evidence:
                                        similar_evidence = (
                                            enrich_evidence_with_dynamo_reasoning(
                                                similar_evidence,
                                                dynamo_client,
                                                limit=15,
                                            )
                                        )

                                    similar_cache[cache_key] = similar_evidence
                                else:
                                    similar_evidence = []

                                issues_with_context.append(
                                    {
                                        "issue": issue,
                                        "similar_evidence": similar_evidence,
                                    }
                                )

                            except Exception as e:
                                logger.warning(
                                    f"Error gathering context for issue: {e}"
                                )
                                issues_with_context.append(
                                    {
                                        "issue": issue,
                                        "similar_evidence": [],
                                        "context_error": str(e),
                                    }
                                )

                        # Build prompt and make LLM call with child trace
                        try:
                            prompt = build_receipt_context_prompt(
                                receipt_words=receipt_words,
                                receipt_labels=receipt_labels,
                                issues_with_context=issues_with_context,
                                merchant_name=merchant_name,
                                merchant_receipt_count=merchant_receipt_count,
                                line_item_patterns=line_item_patterns,
                            )

                            # LLM call with child trace
                            with child_trace(
                                f"llm_call:{len(issues_with_context)}_issues",
                                trace_ctx,
                                run_type="llm",
                                metadata={
                                    "issue_count": len(issues_with_context),
                                    "prompt_length": len(prompt),
                                },
                            ):
                                response = llm_invoker.invoke(
                                    [HumanMessage(content=prompt)]
                                )
                                llm_call_count += 1

                            chunk_reviews = parse_batched_llm_response(
                                response.content.strip(),
                                expected_count=len(issues_with_context),
                            )

                            # Store results
                            for i, review_result in enumerate(chunk_reviews):
                                meta = chunk_metadata[i]
                                ctx = issues_with_context[i]

                                decisions[review_result["decision"]] += 1

                                reviewed_issues.append(
                                    {
                                        "image_id": meta["image_id"],
                                        "receipt_id": meta["receipt_id"],
                                        "issue": meta["issue"],
                                        "llm_review": review_result,
                                        "similar_word_count": len(
                                            ctx.get("similar_evidence", [])
                                        ),
                                    }
                                )

                        except OllamaRateLimitError:
                            logger.warning(
                                f"Rate limit hit after {len(reviewed_issues)}/{total_issues} "
                                f"issues. Saving partial progress."
                            )
                            raise  # Re-raise for Step Function retry

                        except Exception as e:
                            logger.exception(
                                "LLM call failed for receipt %s", receipt_key
                            )
                            for i, meta in enumerate(chunk_metadata):
                                ctx = (
                                    issues_with_context[i]
                                    if i < len(issues_with_context)
                                    else {}
                                )
                                decisions["NEEDS_REVIEW"] += 1
                                reviewed_issues.append(
                                    {
                                        "image_id": meta["image_id"],
                                        "receipt_id": meta["receipt_id"],
                                        "issue": meta["issue"],
                                        "llm_review": {
                                            "decision": "NEEDS_REVIEW",
                                            "reasoning": f"LLM call failed: {e}",
                                            "suggested_label": None,
                                            "confidence": "low",
                                        },
                                        "similar_word_count": len(
                                            ctx.get("similar_evidence", [])
                                        ),
                                        "error": str(e),
                                    }
                                )

            logger.info(
                f"Reviewed {total_issues} issues in {llm_call_count} LLM calls: "
                f"{dict(decisions)}"
            )

            # 7. Upload reviewed results to S3
            with child_trace("upload_results", trace_ctx):
                merchant_hash = merchant_name.lower().replace(" ", "_")[:30]
                reviewed_s3_key = (
                    f"reviewed/{execution_id}/{merchant_hash}_{batch_index}.json"
                )

                rate_limit_stats = llm_invoker.get_stats()

                reviewed_data = {
                    "execution_id": execution_id,
                    "merchant_name": merchant_name,
                    "merchant_receipt_count": merchant_receipt_count,
                    "batch_index": batch_index,
                    "total_issues": total_issues,
                    "issues_reviewed": len(reviewed_issues),
                    "decisions": dict(decisions),
                    "issues": reviewed_issues,
                    "rate_limit_stats": rate_limit_stats,
                }

                upload_json_to_s3(batch_bucket, reviewed_s3_key, reviewed_data)
                logger.info(
                    f"Uploaded reviewed results to s3://{batch_bucket}/{reviewed_s3_key}"
                )

            # 8. Apply decisions to DynamoDB (if not dry_run)
            apply_stats = None
            if not dry_run and dynamo_client and reviewed_issues:
                with child_trace("apply_decisions", trace_ctx):
                    logger.info(
                        f"Applying {len(reviewed_issues)} LLM decisions to DynamoDB..."
                    )
                    apply_stats = apply_llm_decisions(
                        reviewed_issues=reviewed_issues,
                        dynamo_client=dynamo_client,
                        execution_id=execution_id,
                    )
                    logger.info(f"Applied decisions: {apply_stats}")
            elif dry_run:
                logger.info("Skipping DynamoDB writes (dry_run=true)")

            # 9. Log metrics
            from utils.emf_metrics import emf_metrics

            processing_time = time.time() - start_time
            metrics = {
                "IssuesReviewed": total_issues,
                "DecisionsValid": decisions.get("VALID", 0),
                "DecisionsInvalid": decisions.get("INVALID", 0),
                "DecisionsNeedsReview": decisions.get("NEEDS_REVIEW", 0),
                "ProcessingTimeSeconds": round(processing_time, 2),
                "LLMCallCount": llm_call_count,
            }
            if apply_stats:
                metrics["LabelsConfirmed"] = apply_stats.get("labels_confirmed", 0)
                metrics["LabelsInvalidated"] = apply_stats.get("labels_invalidated", 0)
                metrics["LabelsCreated"] = apply_stats.get("labels_created", 0)

            emf_metrics.log_metrics(
                metrics=metrics,
                dimensions={"Merchant": merchant_name[:50]},
                properties={"execution_id": execution_id, "dry_run": dry_run},
                units={"ProcessingTimeSeconds": "Seconds"},
            )

            result = {
                "status": "completed",
                "execution_id": execution_id,
                "merchant_name": merchant_name,
                "batch_index": batch_index,
                "total_issues": total_issues,
                "issues_reviewed": len(reviewed_issues),
                "decisions": dict(decisions),
                "reviewed_issues_s3_key": reviewed_s3_key,
                "rate_limit_stats": rate_limit_stats,
                "dry_run": dry_run,
            }
            if apply_stats:
                result["apply_stats"] = apply_stats

            trace_ctx.set_outputs(result)

        except Exception as e:
            logger.error(f"Error in LLM review batch: {e}", exc_info=True)

            from utils.emf_metrics import emf_metrics

            processing_time = time.time() - start_time
            emf_metrics.log_metrics(
                metrics={
                    "LLMReviewBatchFailed": 1,
                    "ProcessingTimeSeconds": round(processing_time, 2),
                },
                dimensions={"Merchant": merchant_name[:50]},
                properties={"execution_id": execution_id, "error": str(e)},
                units={"ProcessingTimeSeconds": "Seconds"},
            )

            result = {
                "status": "error",
                "execution_id": execution_id,
                "merchant_name": merchant_name,
                "batch_index": batch_index,
                "total_issues": 0,
                "issues_reviewed": 0,
                "decisions": {},
                "reviewed_issues_s3_key": None,
                "error": str(e),
            }
            trace_ctx.set_outputs(result)

    # Flush LangSmith traces
    flush_langsmith_traces()

    return result
