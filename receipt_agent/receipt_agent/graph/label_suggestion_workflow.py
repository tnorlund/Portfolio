"""
Label Suggestion Workflow

This workflow finds unlabeled words on a receipt and suggests labels using
ChromaDB similarity search, minimizing LLM calls.
"""

import logging
import time
from typing import Any, Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.tools.label_suggestion_tools import (
    ReceiptContext,
    create_label_suggestion_tools,
)

logger = logging.getLogger(__name__)

# CORE_LABELS definitions (same as validation agent)
CORE_LABELS = {
    "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
    "STORE_HOURS": "Printed business hours or opening times for the merchant.",
    "PHONE_NUMBER": "Telephone number printed on the receipt (store's main line).",
    "WEBSITE": "Web or email address printed on the receipt (e.g., sprouts.com).",
    "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
    "ADDRESS_LINE": "Full address line (street + city etc.) printed on the receipt.",
    "DATE": "Calendar date of the transaction.",
    "TIME": "Time of the transaction.",
    "PAYMENT_METHOD": "Payment instrument summary (e.g., VISA ••••1234, CASH).",
    "COUPON": "Coupon code or description that reduces price.",
    "DISCOUNT": "Any non-coupon discount line item (e.g., '10% member discount').",
    "PRODUCT_NAME": "Name of a product or item being purchased.",
    "QUANTITY": "Number of units purchased (e.g., '2', '1.5 lbs').",
    "UNIT_PRICE": "Price per unit of the product.",
    "LINE_TOTAL": "Total price for a line item (quantity × unit_price).",
    "SUBTOTAL": "Subtotal before tax and discounts.",
    "TAX": "Tax amount (sales tax, VAT, etc.).",
    "GRAND_TOTAL": "Final total amount paid (after all discounts and taxes).",
}

LABEL_SUGGESTION_PROMPT = """You are a label suggestion agent for receipt processing.

Your task is to analyze unlabeled words on a receipt and suggest appropriate CORE_LABEL types
using ChromaDB similarity search results.

## Strategy

1. **Use ChromaDB results first**: The `search_label_candidates` tool provides similarity search
   results showing which label types similar words have. Use this as primary evidence.

2. **Minimize LLM calls**: Only use LLM reasoning when:
   - Multiple label candidates have similar confidence scores
   - ChromaDB results are ambiguous or conflicting
   - Word context needs interpretation beyond similarity scores

3. **High confidence cases**: If ChromaDB shows a clear winner (high confidence, many matches),
   suggest that label directly without LLM reasoning.

## CORE_LABELS Definitions

{core_labels_definitions}

## Decision Logic

For each unlabeled word:

1. **High Confidence (≥0.75)**: If top candidate has confidence ≥0.75 and ≥5 matches:
   - Suggest the label directly
   - Reasoning: "Found {count} similar words with VALID {label_type} labels (avg similarity: {avg_sim})"

2. **Medium Confidence (0.60-0.75)**: If top candidate has confidence ≥0.60 and ≥3 matches:
   - Suggest the label directly
   - Reasoning: "Found {count} similar words with VALID {label_type} labels"

3. **Multiple Candidates**: If multiple candidates have similar confidence (within 0.15):
   - Use LLM to choose based on word context and similar examples
   - Consider which label type makes most sense given the word's position and surrounding text

4. **Low Confidence (<0.60)**: If confidence is low but some matches exist:
   - Use LLM to evaluate if the word should be labeled at all
   - Consider word context, position, and whether it's likely to be a meaningful label

5. **No Matches**: If no similar words found:
   - Skip this word (don't suggest a label)
   - The validator/harmonizer can handle it later

## Important Rules

1. **Don't suggest labels for all words**: Only suggest when ChromaDB provides strong evidence
2. **Respect existing labels**: Don't suggest labels for words that already have labels
3. **Filter noise**: Words marked as `is_noise` are already excluded
4. **Be conservative**: It's better to skip uncertain cases than create incorrect labels
5. **Batch suggestions**: Use `submit_label_suggestions` to submit multiple suggestions at once

## Workflow

1. Call `get_receipt_context` to see unlabeled words
2. For each unlabeled word, call `search_label_candidates` to get ChromaDB results
3. Analyze results and decide which words to suggest labels for
4. Use `submit_label_suggestions` to create PENDING labels

Start by getting the receipt context."""


async def suggest_labels_for_receipt(
    image_id: str,
    receipt_id: int,
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    llm: Optional[Any] = None,
    settings: Optional[Settings] = None,
    max_llm_calls: int = 10,
    dry_run: bool = False,
) -> dict:
    """
    Suggest labels for unlabeled words on a receipt.

    This function processes all unlabeled words and suggests labels using
    ChromaDB similarity search, with minimal LLM usage.

    Args:
        image_id: Image ID
        receipt_id: Receipt ID
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client
        embed_fn: Embedding function
        llm: Optional LLM (if None, will only use ChromaDB results)
        settings: Optional settings
        max_llm_calls: Maximum number of LLM calls to make (default: 10)

    Returns:
        Dictionary with suggestions summary
    """
    if settings is None:
        settings = get_settings()

    # Create tools
    tools, state_holder = create_label_suggestion_tools(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
    )

    # Get receipt metadata to determine merchant receipt count
    metadata = dynamo_client.get_receipt_metadata(image_id, receipt_id)
    merchant_name = metadata.merchant_name if metadata else None

    # Count receipts for this merchant (query ChromaDB to count unique receipt_ids)
    merchant_receipt_count = None
    if merchant_name and chroma_client:
        try:
            # Query ChromaDB with merchant filter to count receipts
            # We'll do a small query to estimate
            test_query = chroma_client.query(
                collection_name="words",
                query_embeddings=[[0.0] * 1536],  # Dummy embedding (text-embedding-3-small dimension)
                n_results=100,
                where={"merchant_name": {"$eq": merchant_name.strip().title()}},
                include=["metadatas"],
            )
            if test_query and test_query.get("metadatas"):
                # Count unique receipt_ids
                receipt_ids = set()
                for meta_list in test_query["metadatas"]:
                    for meta in meta_list:
                        if meta and "receipt_id" in meta:
                            receipt_ids.add(str(meta["receipt_id"]))
                merchant_receipt_count = len(receipt_ids)
        except Exception as e:
            logger.debug(f"Could not count merchant receipts: {e}")
            merchant_receipt_count = None

    # Set context in state holder
    ctx = ReceiptContext(
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name=merchant_name,
        merchant_receipt_count=merchant_receipt_count,
    )
    state_holder["context"] = ctx

    # Get receipt context
    get_context_tool = tools[0]  # get_receipt_context
    context_result = get_context_tool.invoke({})

    if "error" in context_result:
        return {"error": context_result["error"]}

    unlabeled_words = context_result.get("unlabeled_words", [])
    if not unlabeled_words:
        return {
            "status": "no_unlabeled_words",
            "message": "All words on this receipt already have labels",
        }

    logger.info(f"Found {len(unlabeled_words)} unlabeled words")

    # Process each unlabeled word
    search_tool = tools[1]  # search_label_candidates
    suggestions = []
    llm_calls = 0
    skipped_no_candidates = 0
    skipped_low_confidence = 0
    skipped_words_details = []  # Track details of skipped words

    # Timing metrics
    start_time = time.time()
    dynamo_get_times = []
    dynamo_list_times = []
    chroma_query_times = []
    chroma_get_times = []
    embedding_times = []
    total_processing_time = None

    for word_info in unlabeled_words:
        word_start = time.time()
        word_id = word_info["word_id"]
        line_id = word_info["line_id"]
        word_text = word_info["text"]

        # Search for label candidates
        search_start = time.time()
        candidates_result = search_tool.invoke({
            "word_id": word_id,
            "line_id": line_id,
            "n_results": 50,
        })
        search_time = time.time() - search_start

        # Always record search time
        if not chroma_query_times:
            chroma_query_times = []
        chroma_query_times.append(search_time)

        # Extract detailed timing from result if available
        timing = candidates_result.get("timing", {})
        if timing:
            if not dynamo_get_times:
                dynamo_get_times = []
            if not dynamo_list_times:
                dynamo_list_times = []
            if not chroma_get_times:
                chroma_get_times = []
            if not embedding_times:
                embedding_times = []
            dynamo_get_times.append(timing.get("dynamo_get_word_seconds", 0))
            dynamo_list_times.append(timing.get("dynamo_list_words_seconds", 0))
            chroma_get_times.append(timing.get("chroma_get_seconds", 0))
            embedding_times.append(timing.get("embedding_seconds", 0))
            # Override with actual query time from timing if available
            query_time_from_timing = timing.get("chroma_query_seconds", 0)
            if query_time_from_timing > 0:
                chroma_query_times[-1] = query_time_from_timing

        if "error" in candidates_result:
            logger.debug(f"Error searching candidates for word '{word_text}': {candidates_result['error']}")
            continue

        candidates = candidates_result.get("candidates", [])
        top_candidate = candidates_result.get("top_candidate")

        if not top_candidate:
            # No candidates found - skip
            skipped_no_candidates += 1
            total_matches = candidates_result.get("total_matches", 0)
            words_with_valid = candidates_result.get("words_with_valid_labels", 0)
            words_without_valid = candidates_result.get("words_without_valid_labels", 0)

            skipped_words_details.append({
                "word_text": word_text,
                "word_id": word_id,
                "line_id": line_id,
                "total_similar_words": total_matches,
                "words_with_valid_labels": words_with_valid,
                "words_without_valid_labels": words_without_valid,
                "reason": "no_validated_labels" if total_matches > 0 else "no_similar_words",
            })
            logger.debug(f"No candidates found for word '{word_text}' (word_id: {word_id})")
            continue

        # Decision logic (minimize LLM calls, prioritize similarity)
        confidence = top_candidate["confidence"]
        match_count = top_candidate["match_count"]
        avg_similarity = top_candidate["avg_similarity"]
        label_type = top_candidate["label_type"]

        logger.debug(
            f"Word '{word_text}': top candidate {label_type} "
            f"(similarity: {avg_similarity:.2f}, confidence: {confidence:.2f}, matches: {match_count}, candidates: {len(candidates)})"
        )

        # CASE 1: High similarity - suggest directly (NO LLM)
        # Prioritize similarity over count for suggestions (will be validated later)
        avg_similarity = top_candidate["avg_similarity"]
        if avg_similarity >= 0.80 and match_count >= 2:
            # High similarity with just 2+ matches is reliable enough to suggest
            suggestions.append({
                "word_id": word_id,
                "line_id": line_id,
                "label_type": label_type,
                "confidence": confidence,
                "reasoning": (
                    f"Found {match_count} similar words with VALID {label_type} labels "
                    f"(avg similarity: {avg_similarity:.2f})"
                ),
            })
            logger.debug(f"High similarity suggestion: {word_text} -> {label_type} (similarity: {avg_similarity:.2f}, matches: {match_count})")
            continue

        # CASE 2: Medium-high similarity or good confidence - suggest directly (NO LLM)
        if (avg_similarity >= 0.75 and match_count >= 3) or (confidence >= 0.65 and match_count >= 3):
            suggestions.append({
                "word_id": word_id,
                "line_id": line_id,
                "label_type": label_type,
                "confidence": confidence,
                "reasoning": (
                    f"Found {match_count} similar words with VALID {label_type} labels "
                    f"(avg similarity: {avg_similarity:.2f})"
                ),
            })
            logger.debug(f"Medium-high suggestion: {word_text} -> {label_type} (similarity: {avg_similarity:.2f}, confidence: {confidence:.2f}, matches: {match_count})")
            continue

        # CASE 2b: Single candidate with decent similarity - suggest directly (NO LLM)
        if len(candidates) == 1 and avg_similarity >= 0.70 and match_count >= 2:
            suggestions.append({
                "word_id": word_id,
                "line_id": line_id,
                "label_type": label_type,
                "confidence": confidence,
                "reasoning": f"Found {match_count} similar words with VALID {label_type} labels (avg similarity: {avg_similarity:.2f})",
            })
            continue

        # CASE 3: Multiple candidates or ambiguous - use LLM (if available)
        if llm and llm_calls < max_llm_calls:
            # Check if there are multiple strong candidates
            has_multiple_candidates = (
                len(candidates) >= 2 and
                candidates[0]["avg_similarity"] >= 0.65 and
                candidates[1]["avg_similarity"] >= 0.60 and
                (candidates[0]["avg_similarity"] - candidates[1]["avg_similarity"]) < 0.10
            )

            # Use LLM for ambiguous cases: multiple candidates OR medium similarity
            if has_multiple_candidates or (avg_similarity >= 0.65 and avg_similarity < 0.75 and match_count >= 2):
                llm_calls += 1

                # Build prompt for LLM
                core_labels_text = "\n".join(
                    f"- **{label}**: {definition}"
                    for label, definition in CORE_LABELS.items()
                )

                candidates_text = "\n".join(
                    f"- **{c['label_type']}**: confidence={c['confidence']:.2f}, "
                    f"matches={c['match_count']}, avg_sim={c['avg_similarity']:.2f}"
                    for c in candidates[:3]
                )

                prompt = f"""Word: "{word_text}"
Line ID: {line_id}

ChromaDB found these label candidates:
{candidates_text}

Which CORE_LABEL type should this word have? Consider:
1. The word text itself
2. The confidence scores and match counts
3. Which label type makes most sense

Respond with JSON:
{{
  "label_type": "LABEL_TYPE or null",
  "confidence": 0.0-1.0,
  "reasoning": "explanation"
}}"""

                try:
                    response = await llm.ainvoke([HumanMessage(content=prompt)])
                    # Parse response (simplified - in production, use structured output)
                    content = response.content if hasattr(response, 'content') else str(response)

                    # Try to extract JSON from response
                    import json
                    import re
                    json_match = re.search(r'\{[^}]+\}', content, re.DOTALL)
                    if json_match:
                        llm_result = json.loads(json_match.group())
                        if llm_result.get("label_type") and llm_result["label_type"] in CORE_LABELS:
                            suggestions.append({
                                "word_id": word_id,
                                "line_id": line_id,
                                "label_type": llm_result["label_type"],
                                "confidence": llm_result.get("confidence", confidence),
                                "reasoning": llm_result.get("reasoning", "LLM decision based on ChromaDB results"),
                            })
                except Exception as e:
                    logger.debug(f"LLM call failed for word '{word_text}': {e}")
                    # Fall back to top candidate if LLM fails and similarity is decent
                    if avg_similarity >= 0.70 and match_count >= 2:
                        suggestions.append({
                            "word_id": word_id,
                            "line_id": line_id,
                            "label_type": label_type,
                            "confidence": confidence,
                            "reasoning": f"Fallback: Found {match_count} similar words with VALID {label_type} labels (avg similarity: {avg_similarity:.2f})",
                        })

        # Track low confidence skips (only skip if similarity is truly low)
        if avg_similarity < 0.65 or match_count < 2:
            skipped_low_confidence += 1
            logger.debug(
                f"Skipped '{word_text}': similarity={avg_similarity:.2f} < 0.65 or matches={match_count} < 2"
            )

        word_time = time.time() - word_start
        if word_time > 1.0:  # Log slow words
            logger.debug(f"Slow word '{word_text}': {word_time:.2f}s")

    total_processing_time = time.time() - start_time

    # Submit suggestions
    if suggestions:
        if dry_run:
            # Dry run - don't actually create labels
            submit_result = {
                "status": "dry_run",
                "created_count": len(suggestions),
                "error_count": 0,
                "created_labels": [
                    {
                        "word_id": s["word_id"],
                        "line_id": s["line_id"],
                        "label_type": s["label_type"],
                        "confidence": s["confidence"],
                    }
                    for s in suggestions
                ],
                "errors": [],
                "message": "DRY RUN - labels were not actually created",
            }
        else:
            submit_tool = tools[2]  # submit_label_suggestions
            submit_result = submit_tool.invoke({"suggestions": suggestions})

        return {
            "status": "success",
            "unlabeled_words_count": len(unlabeled_words),
            "suggestions_count": len(suggestions),
            "llm_calls": llm_calls,
            "submit_result": submit_result,
            "skipped_no_candidates": skipped_no_candidates,
            "skipped_low_confidence": skipped_low_confidence,
            "skipped_words_details": skipped_words_details,
            "performance": {
                "total_time_seconds": total_processing_time,
                "time_per_word_seconds": total_processing_time / len(unlabeled_words) if unlabeled_words else 0,
                "avg_chroma_query_time": sum(chroma_query_times) / len(chroma_query_times) if chroma_query_times else 0,
                "total_chroma_queries": len(chroma_query_times),
            },
        }
    else:
        return {
            "status": "no_suggestions",
            "unlabeled_words_count": len(unlabeled_words),
            "message": "No labels suggested (insufficient ChromaDB evidence)",
            "skipped_no_candidates": skipped_no_candidates,
            "skipped_low_confidence": skipped_low_confidence,
            "skipped_words_details": skipped_words_details,
            "performance": {
                "total_time_seconds": total_processing_time,
                "time_per_word_seconds": total_processing_time / len(unlabeled_words) if unlabeled_words else 0,
                "avg_chroma_query_time": sum(chroma_query_times) / len(chroma_query_times) if chroma_query_times else 0,
                "total_chroma_queries": len(chroma_query_times),
            },
        }

