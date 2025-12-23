"""
LLM Review Module for Label Evaluator

Core logic for LLM-based review of receipt labeling issues.
Moved from infra/label_evaluator_step_functions/lambdas/llm_review.py.

This module provides functions that can be used by both:
- The LangGraph-based label evaluator
- The Lambda-based Step Function workflow
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from langchain_core.messages import HumanMessage

from receipt_agent.prompts.label_evaluator import (
    build_batched_review_prompt,
    build_receipt_context_prompt,
    build_review_prompt,
    parse_batched_llm_response,
    parse_llm_response,
)
from receipt_agent.utils.chroma_helpers import (
    SimilarWordEvidence,
    compute_label_distribution,
    compute_merchant_breakdown,
    compute_similarity_distribution,
    enrich_evidence_with_dynamo_reasoning,
    query_similar_words,
)

from .helpers import (
    assemble_receipt_text,
    extract_receipt_currency_context,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from receipt_agent.utils.ollama_rate_limit import RateLimitedLLMInvoker

logger = logging.getLogger(__name__)


# Default batch size for LLM calls
DEFAULT_ISSUES_PER_LLM_CALL = 10


# =============================================================================
# Core Review Functions
# =============================================================================


def gather_evidence_for_issue(
    issue: dict[str, Any],
    merchant_name: str,
    chroma_client: Optional[Any] = None,
    dynamo_client: Optional[Any] = None,
    n_similar_results: int = 100,
) -> dict[str, Any]:
    """
    Gather all evidence needed for LLM review of a single issue.

    Args:
        issue: The issue dict with word_text, line_id, word_id, image_id, etc.
        merchant_name: Merchant name for same-merchant filtering
        chroma_client: Optional ChromaDB client for similar word queries
        dynamo_client: Optional DynamoDB client for reasoning enrichment
        n_similar_results: Number of similar words to query

    Returns:
        Dict with issue and all gathered evidence
    """
    evidence: dict[str, Any] = {
        "issue": issue,
        "similar_evidence": [],
        "similarity_dist": {"very_high": 0, "high": 0, "medium": 0, "low": 0},
        "label_dist": {},
        "merchant_breakdown": [],
        "currency_context": [],
    }

    if not chroma_client:
        return evidence

    # Query for similar words
    try:
        similar_evidence = query_similar_words(
            chroma_client=chroma_client,
            word_text=issue.get("word_text", ""),
            image_id=issue.get("image_id", ""),
            receipt_id=issue.get("receipt_id", 0),
            line_id=issue.get("line_id", 0),
            word_id=issue.get("word_id", 0),
            target_merchant=merchant_name,
            n_results=n_similar_results,
        )

        # Enrich with DynamoDB reasoning if available
        if dynamo_client and similar_evidence:
            similar_evidence = enrich_evidence_with_dynamo_reasoning(
                similar_evidence, dynamo_client, limit=20
            )

        evidence["similar_evidence"] = similar_evidence
        evidence["similarity_dist"] = compute_similarity_distribution(
            similar_evidence
        )
        evidence["label_dist"] = compute_label_distribution(similar_evidence)
        evidence["merchant_breakdown"] = compute_merchant_breakdown(
            similar_evidence
        )

    except Exception as e:
        logger.warning("Error gathering evidence for issue: %s", e)

    return evidence


def review_single_issue(
    issue: dict[str, Any],
    similar_evidence: list[SimilarWordEvidence],
    merchant_name: str,
    merchant_receipt_count: int,
    llm: "BaseChatModel",
    currency_context: Optional[list[dict]] = None,
    line_item_patterns: Optional[dict] = None,
    rate_limiter: Optional["RateLimitedLLMInvoker"] = None,
) -> dict[str, Any]:
    """
    Review a single issue with LLM.

    Args:
        issue: The issue dict
        similar_evidence: List of similar word evidence
        merchant_name: Merchant name
        merchant_receipt_count: Number of receipts for this merchant
        llm: LangChain chat model
        currency_context: Optional currency context from receipt
        line_item_patterns: Optional line item patterns for merchant
        rate_limiter: Optional rate limiter wrapper

    Returns:
        Dict with decision, reasoning, suggested_label, confidence
    """
    # Compute distributions from evidence
    similarity_dist = compute_similarity_distribution(similar_evidence)
    label_dist = compute_label_distribution(similar_evidence)
    merchant_breakdown = compute_merchant_breakdown(similar_evidence)

    # Build prompt
    prompt = build_review_prompt(
        issue=issue,
        similar_evidence=similar_evidence,
        similarity_dist=similarity_dist,
        label_dist=label_dist,
        merchant_breakdown=merchant_breakdown,
        merchant_name=merchant_name,
        merchant_receipt_count=merchant_receipt_count,
        currency_context=currency_context,
        line_item_patterns=line_item_patterns,
    )

    # Call LLM (with optional rate limiting)
    try:
        if rate_limiter:
            response = rate_limiter.invoke([HumanMessage(content=prompt)])
        else:
            response = llm.invoke([HumanMessage(content=prompt)])

        response_text = response.content.strip()
        return parse_llm_response(response_text)

    except Exception as e:
        logger.error("LLM review failed: %s", e)
        return {
            "decision": "NEEDS_REVIEW",
            "reasoning": f"LLM review failed: {e}",
            "suggested_label": None,
            "confidence": "low",
        }


def review_issues_batch(
    issues_with_context: list[dict[str, Any]],
    merchant_name: str,
    merchant_receipt_count: int,
    llm: "BaseChatModel",
    line_item_patterns: Optional[dict] = None,
    rate_limiter: Optional["RateLimitedLLMInvoker"] = None,
) -> list[dict[str, Any]]:
    """
    Review multiple issues in a single LLM call.

    Args:
        issues_with_context: List of dicts, each containing:
            - issue: The issue dict
            - similar_evidence: List of SimilarWordEvidence
            - similarity_dist: SimilarityDistribution (optional, will compute)
            - label_dist: Label distribution (optional, will compute)
            - merchant_breakdown: Merchant breakdown (optional, will compute)
            - currency_context: Currency amounts from receipt
        merchant_name: Merchant name
        merchant_receipt_count: Number of receipts for this merchant
        llm: LangChain chat model
        line_item_patterns: Optional line item patterns for merchant
        rate_limiter: Optional rate limiter wrapper

    Returns:
        List of dicts, one per issue, each with:
            decision, reasoning, suggested_label, confidence
    """
    if not issues_with_context:
        return []

    # Ensure each item has computed distributions
    for item in issues_with_context:
        similar_evidence = item.get("similar_evidence", [])
        if "similarity_dist" not in item:
            item["similarity_dist"] = compute_similarity_distribution(
                similar_evidence
            )
        if "label_dist" not in item:
            item["label_dist"] = compute_label_distribution(similar_evidence)
        if "merchant_breakdown" not in item:
            item["merchant_breakdown"] = compute_merchant_breakdown(
                similar_evidence
            )

    # Build batched prompt
    prompt = build_batched_review_prompt(
        issues_with_context=issues_with_context,
        merchant_name=merchant_name,
        merchant_receipt_count=merchant_receipt_count,
        line_item_patterns=line_item_patterns,
    )

    # Call LLM
    try:
        if rate_limiter:
            response = rate_limiter.invoke([HumanMessage(content=prompt)])
        else:
            response = llm.invoke([HumanMessage(content=prompt)])

        response_text = response.content.strip()
        return parse_batched_llm_response(
            response_text, len(issues_with_context)
        )

    except Exception as e:
        logger.error("Batched LLM review failed: %s", e)
        # Return fallback for all issues
        return [
            {
                "decision": "NEEDS_REVIEW",
                "reasoning": f"Batched LLM review failed: {e}",
                "suggested_label": None,
                "confidence": "low",
            }
            for _ in issues_with_context
        ]


def review_issues_with_receipt_context(
    receipt_words: list[dict],
    receipt_labels: list[dict],
    issues_with_context: list[dict[str, Any]],
    merchant_name: str,
    merchant_receipt_count: int,
    llm: "BaseChatModel",
    line_item_patterns: Optional[dict] = None,
    rate_limiter: Optional["RateLimitedLLMInvoker"] = None,
    max_lines: int = 50,
) -> list[dict[str, Any]]:
    """
    Review issues with full receipt context visible in prompt.

    This provides more context by showing the full receipt text with
    the issue words highlighted, then details each issue.

    Args:
        receipt_words: All words from the receipt (as dicts)
        receipt_labels: All labels for the receipt (as dicts)
        issues_with_context: List of issue dicts with similar_evidence
        merchant_name: Merchant name
        merchant_receipt_count: Number of receipts for this merchant
        llm: LangChain chat model
        line_item_patterns: Optional line item patterns
        rate_limiter: Optional rate limiter
        max_lines: Maximum receipt lines to show

    Returns:
        List of review decision dicts
    """
    if not issues_with_context:
        return []

    # Build list of highlight positions
    highlight_words = []
    for item in issues_with_context:
        issue = item.get("issue", {})
        line_id = issue.get("line_id")
        word_id = issue.get("word_id")
        if line_id is not None and word_id is not None:
            highlight_words.append((line_id, word_id))

    # Assemble receipt text with highlighted words
    receipt_text = assemble_receipt_text(
        words=receipt_words,
        labels=receipt_labels,
        highlight_words=highlight_words,
        max_lines=max_lines,
    )

    # Build prompt
    prompt = build_receipt_context_prompt(
        receipt_text=receipt_text,
        issues_with_context=issues_with_context,
        merchant_name=merchant_name,
        merchant_receipt_count=merchant_receipt_count,
        line_item_patterns=line_item_patterns,
    )

    # Call LLM
    try:
        if rate_limiter:
            response = rate_limiter.invoke([HumanMessage(content=prompt)])
        else:
            response = llm.invoke([HumanMessage(content=prompt)])

        response_text = response.content.strip()
        return parse_batched_llm_response(
            response_text, len(issues_with_context)
        )

    except Exception as e:
        logger.error("Receipt context LLM review failed: %s", e)
        return [
            {
                "decision": "NEEDS_REVIEW",
                "reasoning": f"LLM review failed: {e}",
                "suggested_label": None,
                "confidence": "low",
            }
            for _ in issues_with_context
        ]


# =============================================================================
# High-Level Review Orchestration
# =============================================================================


def review_all_issues(
    issues: list[dict[str, Any]],
    receipt_words: list[dict],
    receipt_labels: list[dict],
    merchant_name: str,
    merchant_receipt_count: int,
    llm: "BaseChatModel",
    chroma_client: Optional[Any] = None,
    dynamo_client: Optional[Any] = None,
    rate_limiter: Optional["RateLimitedLLMInvoker"] = None,
    line_item_patterns: Optional[dict] = None,
    max_issues_per_call: int = DEFAULT_ISSUES_PER_LLM_CALL,
    use_receipt_context: bool = True,
) -> list[dict[str, Any]]:
    """
    Review all issues with batching and optional receipt context.

    This is the main entry point for LLM review. It:
    1. Gathers evidence for each issue
    2. Extracts currency context from the receipt
    3. Batches issues for efficient LLM calls
    4. Returns review decisions

    Args:
        issues: List of issue dicts to review
        receipt_words: All words from the receipt (as dicts)
        receipt_labels: All labels for the receipt (as dicts)
        merchant_name: Merchant name
        merchant_receipt_count: Number of receipts for this merchant
        llm: LangChain chat model
        chroma_client: Optional ChromaDB client
        dynamo_client: Optional DynamoDB client
        rate_limiter: Optional rate limiter
        line_item_patterns: Optional line item patterns
        max_issues_per_call: Maximum issues per LLM call
        use_receipt_context: Whether to include full receipt context

    Returns:
        List of dicts, one per issue, each containing:
            - issue: Original issue dict
            - decision: VALID, INVALID, or NEEDS_REVIEW
            - reasoning: LLM's reasoning
            - suggested_label: Suggested label if INVALID
            - confidence: low, medium, or high
    """
    if not issues:
        return []

    # Gather evidence for each issue
    logger.info("Gathering evidence for %d issues", len(issues))
    issues_with_context = []

    for issue in issues:
        evidence = gather_evidence_for_issue(
            issue=issue,
            merchant_name=merchant_name,
            chroma_client=chroma_client,
            dynamo_client=dynamo_client,
        )
        issues_with_context.append(evidence)

    # Extract currency context from receipt
    currency_context = extract_receipt_currency_context(
        receipt_words, receipt_labels
    )

    # Add currency context to each issue
    for item in issues_with_context:
        item["currency_context"] = currency_context

    # Review in batches
    all_results = []
    for i in range(0, len(issues_with_context), max_issues_per_call):
        batch = issues_with_context[i : i + max_issues_per_call]

        logger.info(
            "Reviewing batch %d-%d of %d issues",
            i,
            i + len(batch),
            len(issues_with_context),
        )

        if use_receipt_context:
            batch_results = review_issues_with_receipt_context(
                receipt_words=receipt_words,
                receipt_labels=receipt_labels,
                issues_with_context=batch,
                merchant_name=merchant_name,
                merchant_receipt_count=merchant_receipt_count,
                llm=llm,
                line_item_patterns=line_item_patterns,
                rate_limiter=rate_limiter,
            )
        else:
            batch_results = review_issues_batch(
                issues_with_context=batch,
                merchant_name=merchant_name,
                merchant_receipt_count=merchant_receipt_count,
                llm=llm,
                line_item_patterns=line_item_patterns,
                rate_limiter=rate_limiter,
            )

        # Combine issue with its review result
        for item, result in zip(batch, batch_results, strict=True):
            all_results.append(
                {
                    "issue": item["issue"],
                    "decision": result["decision"],
                    "reasoning": result["reasoning"],
                    "suggested_label": result.get("suggested_label"),
                    "confidence": result.get("confidence", "medium"),
                }
            )

    logger.info("Completed LLM review of %d issues", len(all_results))
    return all_results


# =============================================================================
# Apply Decisions to DynamoDB
# =============================================================================


def apply_llm_decisions(
    reviewed_issues: list[dict[str, Any]],
    dynamo_client: Any,
    execution_id: str,
) -> dict[str, int]:
    """
    Apply LLM review decisions to DynamoDB.

    For each reviewed issue:
    - VALID: Confirm the current label, invalidate conflicting labels
    - INVALID: Invalidate the current label, confirm/add suggested label
    - NEEDS_REVIEW: No changes

    Args:
        reviewed_issues: List of reviewed issue dicts with llm_review results.
            Each dict should have:
            - image_id: str
            - receipt_id: int
            - issue: dict with line_id, word_id, current_label
            - llm_review: dict with decision, reasoning, suggested_label, confidence
        dynamo_client: DynamoClient instance
        execution_id: Execution ID for audit trail

    Returns:
        Dict with counts of actions taken:
        - labels_confirmed: Labels marked as VALID
        - labels_invalidated: Labels marked as INVALID
        - labels_created: New labels created
        - conflicts_resolved: Conflicting labels invalidated
        - skipped_needs_review: Issues left for human review
        - errors: Number of errors encountered
    """
    from datetime import datetime, timezone

    from receipt_dynamo import ReceiptWordLabel

    stats = {
        "labels_confirmed": 0,
        "labels_invalidated": 0,
        "labels_created": 0,
        "conflicts_resolved": 0,
        "skipped_needs_review": 0,
        "errors": 0,
    }

    for item in reviewed_issues:
        try:
            image_id = item.get("image_id")
            receipt_id = item.get("receipt_id")
            issue = item.get("issue", {})
            llm_review = item.get("llm_review", {})

            line_id = issue.get("line_id")
            word_id = issue.get("word_id")
            current_label = issue.get("current_label")
            decision = llm_review.get("decision")
            reasoning = llm_review.get("reasoning", "")
            suggested_label = llm_review.get("suggested_label")
            confidence = llm_review.get("confidence", "medium")

            if not all(
                [image_id, receipt_id is not None, line_id, word_id, current_label]
            ):
                logger.warning("Missing required fields for issue: %s", item)
                stats["errors"] += 1
                continue

            # Build audit reasoning
            audit_reasoning = (
                f"[label-evaluator {execution_id}] "
                f"Decision: {decision} ({confidence}). {reasoning[:200]}"
            )
            timestamp = datetime.now(timezone.utc).isoformat()

            if decision == "VALID":
                # 1. Confirm the current label (update reasoning if desired)
                # 2. Invalidate any conflicting labels on the same word

                # Get all labels for this word (returns tuple: list, last_key)
                all_labels, _ = dynamo_client.list_receipt_word_labels_for_word(
                    image_id, receipt_id, line_id, word_id
                )

                for label in all_labels:
                    if label.label == current_label:
                        # This is the confirmed label - already VALID, count it
                        stats["labels_confirmed"] += 1
                    elif label.validation_status == "VALID":
                        # Conflicting VALID label - invalidate it
                        updated_label = ReceiptWordLabel(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=line_id,
                            word_id=word_id,
                            label=label.label,
                            reasoning=(
                                f"Invalidated: {current_label} confirmed. "
                                f"{audit_reasoning}"
                            ),
                            timestamp_added=label.timestamp_added,
                            validation_status="INVALID",
                            label_proposed_by=label.label_proposed_by,
                            label_consolidated_from=label.label_consolidated_from,
                        )
                        dynamo_client.update_receipt_word_label(updated_label)
                        stats["labels_invalidated"] += 1
                        stats["conflicts_resolved"] += 1
                        logger.info(
                            "Invalidated conflicting %s on %s:%s:%s:%s",
                            label.label,
                            image_id,
                            receipt_id,
                            line_id,
                            word_id,
                        )

            elif decision == "INVALID":
                # 1. Invalidate the current label
                # 2. If suggested_label provided, confirm or create it

                # Get all labels for this word (returns tuple: list, last_key)
                all_labels, _ = dynamo_client.list_receipt_word_labels_for_word(
                    image_id, receipt_id, line_id, word_id
                )

                # Find and invalidate the current label
                for label in all_labels:
                    if label.label == current_label:
                        updated_label = ReceiptWordLabel(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=line_id,
                            word_id=word_id,
                            label=label.label,
                            reasoning=audit_reasoning,
                            timestamp_added=label.timestamp_added,
                            validation_status="INVALID",
                            label_proposed_by=label.label_proposed_by,
                            label_consolidated_from=label.label_consolidated_from,
                        )
                        dynamo_client.update_receipt_word_label(updated_label)
                        stats["labels_invalidated"] += 1
                        logger.info(
                            "Invalidated %s on %s:%s:%s:%s",
                            current_label,
                            image_id,
                            receipt_id,
                            line_id,
                            word_id,
                        )
                        break

                # Handle suggested label
                if suggested_label:
                    # Check if suggested label already exists
                    existing_suggested = None
                    for label in all_labels:
                        if label.label == suggested_label:
                            existing_suggested = label
                            break

                    if existing_suggested:
                        # Confirm the existing suggested label
                        if existing_suggested.validation_status != "VALID":
                            updated_label = ReceiptWordLabel(
                                image_id=image_id,
                                receipt_id=receipt_id,
                                line_id=line_id,
                                word_id=word_id,
                                label=suggested_label,
                                reasoning=(
                                    f"Confirmed as replacement. {audit_reasoning}"
                                ),
                                timestamp_added=existing_suggested.timestamp_added,
                                validation_status="VALID",
                                label_proposed_by=existing_suggested.label_proposed_by,
                                label_consolidated_from=(
                                    existing_suggested.label_consolidated_from
                                ),
                            )
                            dynamo_client.update_receipt_word_label(updated_label)
                            stats["labels_confirmed"] += 1
                    else:
                        # Create new label
                        new_label = ReceiptWordLabel(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=line_id,
                            word_id=word_id,
                            label=suggested_label,
                            reasoning=f"Added by LLM review. {audit_reasoning}",
                            timestamp_added=timestamp,
                            validation_status="VALID",
                            label_proposed_by="label-evaluator-llm",
                            label_consolidated_from=None,
                        )
                        dynamo_client.add_receipt_word_label(new_label)
                        stats["labels_created"] += 1
                        logger.info(
                            "Created new %s on %s:%s:%s:%s",
                            suggested_label,
                            image_id,
                            receipt_id,
                            line_id,
                            word_id,
                        )

            elif decision == "NEEDS_REVIEW":
                # No changes - leave for human review
                stats["skipped_needs_review"] += 1

        except Exception as e:
            logger.error("Error applying decision: %s", e)
            stats["errors"] += 1

    logger.info("Applied decisions: %s", stats)
    return stats
