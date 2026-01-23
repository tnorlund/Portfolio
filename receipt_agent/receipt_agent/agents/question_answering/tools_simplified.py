"""Enhanced QA tools for ReAct RAG workflow.

This module provides a consolidated tool set for the QA agent:
1. search_receipts - unified search (text, label, or semantic)
2. semantic_search - explicit embedding-based similarity search
3. get_receipt - full receipt with formatted text and inline labels
4. signal_retrieval_complete - explicit signal to move to context shaping
5. aggregate_amounts - helper for "how much" questions
6. submit_answer - submit final answer (for backwards compatibility)

The key insight is that showing the receipt with inline labels like:
    Line 5: ORGANIC[PRODUCT_NAME] COFFEE[PRODUCT_NAME] 12.99[LINE_TOTAL]

Allows the LLM to extract prices, amounts, and relationships without
needing separate tools for each query type.
"""

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@dataclass
class QuestionContext:
    """Context for the current question being answered."""

    question: str = ""
    search_results: list[dict] = field(default_factory=list)
    receipt_details: list[dict] = field(default_factory=list)


def create_simplified_qa_tools(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
) -> tuple[list, dict]:
    """Create simplified tools for QA agent.

    Args:
        dynamo_client: DynamoDB client for receipt data
        chroma_client: ChromaDB client for similarity search
        embed_fn: Function to generate embeddings for semantic search

    Returns:
        (tools, state_holder) - List of 3 tools and state dict
    """
    _embed_fn = embed_fn  # Used for semantic search

    state_holder: dict[str, Any] = {
        "context": None,
        "answer": None,
    }

    @tool
    def search_receipts(
        query: str,
        search_type: str = "text",
        limit: int = 20,
    ) -> dict:
        """Search for receipts by text content, label type, or semantic similarity.

        Args:
            query: What to search for.
                - For text search: product name like "COFFEE", "MILK", "ORGANIC"
                - For label search: label type like "TAX", "GRAND_TOTAL", "SUBTOTAL"
                - For semantic search: natural language like "coffee purchase"
            search_type: Search method to use:
                - "text": Search line content for exact text match
                - "label": Search by label type (uses WORDS collection)
                - "label_lines": Search rows with specific label (uses LINES
                   collection with aggregated labels)
                - "semantic": Semantic similarity search using embeddings
            limit: Maximum results to return

        Returns:
            Dict with matching receipts (image_id, receipt_id, preview text)

        Examples:
            search_receipts("COFFEE", "text")  -> finds receipts with coffee
            search_receipts("TAX", "label")    -> finds receipts with TAX labels
            search_receipts("PRODUCT_NAME", "label_lines") -> finds rows with
                                                              product labels
            search_receipts("coffee purchase", "semantic") -> semantic search
        """
        try:
            if search_type == "label":
                # Search words collection by label
                words_collection = chroma_client.get_collection("words")
                results = words_collection.get(
                    where={"label": query.upper()},
                    include=["metadatas"],
                )

                # Dedupe by receipt
                unique_receipts = {}
                for id_, meta in zip(results["ids"], results["metadatas"]):
                    key = (meta.get("image_id"), meta.get("receipt_id"))
                    if key not in unique_receipts:
                        unique_receipts[key] = {
                            "image_id": meta.get("image_id"),
                            "receipt_id": meta.get("receipt_id"),
                            "matched_text": meta.get("text"),
                            "matched_label": query.upper(),
                        }

                return {
                    "search_type": "label",
                    "query": query,
                    "total_matches": len(results["ids"]),
                    "unique_receipts": len(unique_receipts),
                    "results": list(unique_receipts.values())[:limit],
                }

            elif search_type == "label_lines":
                # Search lines collection by aggregated label metadata
                # This uses the row-based embeddings with label_* fields
                lines_collection = chroma_client.get_collection("lines")
                label_key = f"label_{query.upper()}"

                results = lines_collection.get(
                    where={label_key: True},
                    include=["metadatas"],
                )

                # Dedupe by receipt
                unique_receipts = {}
                for id_, meta in zip(results["ids"], results["metadatas"]):
                    key = (meta.get("image_id"), meta.get("receipt_id"))
                    if key not in unique_receipts:
                        unique_receipts[key] = {
                            "image_id": meta.get("image_id"),
                            "receipt_id": meta.get("receipt_id"),
                            "matched_row": meta.get("text", "")[:100],
                            "matched_label": query.upper(),
                        }

                return {
                    "search_type": "label_lines",
                    "query": query,
                    "total_matches": len(results["ids"]),
                    "unique_receipts": len(unique_receipts),
                    "results": list(unique_receipts.values())[:limit],
                }

            elif search_type == "semantic":
                # Semantic search using embeddings
                lines_collection = chroma_client.get_collection("lines")

                # Generate embedding for the query
                query_embeddings = _embed_fn([query])
                if not query_embeddings or not query_embeddings[0]:
                    return {"error": "Failed to generate query embedding", "results": []}

                # Perform similarity search
                results = lines_collection.query(
                    query_embeddings=query_embeddings,
                    n_results=limit * 2,  # Get extra to allow deduping
                    include=["metadatas", "distances"],
                )

                # Dedupe by receipt, keeping best match
                unique_receipts = {}
                if results["ids"] and results["ids"][0]:
                    for idx, (id_, meta) in enumerate(
                        zip(results["ids"][0], results["metadatas"][0])
                    ):
                        key = (meta.get("image_id"), meta.get("receipt_id"))
                        distance = (
                            results["distances"][0][idx]
                            if results["distances"]
                            else None
                        )
                        if key not in unique_receipts:
                            unique_receipts[key] = {
                                "image_id": meta.get("image_id"),
                                "receipt_id": meta.get("receipt_id"),
                                "matched_row": meta.get("text", "")[:100],
                                "similarity_distance": distance,
                            }

                return {
                    "search_type": "semantic",
                    "query": query,
                    "total_matches": (
                        len(results["ids"][0]) if results["ids"] else 0
                    ),
                    "unique_receipts": len(unique_receipts),
                    "results": list(unique_receipts.values())[:limit],
                }

            else:
                # Default: Search lines collection by text content
                lines_collection = chroma_client.get_collection("lines")
                results = lines_collection.get(
                    where_document={"$contains": query.upper()},
                    include=["metadatas"],
                )

                # Dedupe by receipt
                unique_receipts = {}
                for id_, meta in zip(results["ids"], results["metadatas"]):
                    key = (meta.get("image_id"), meta.get("receipt_id"))
                    if key not in unique_receipts:
                        unique_receipts[key] = {
                            "image_id": meta.get("image_id"),
                            "receipt_id": meta.get("receipt_id"),
                            "matched_line": meta.get("text", "")[:100],
                        }

                return {
                    "search_type": "text",
                    "query": query,
                    "total_matches": len(results["ids"]),
                    "unique_receipts": len(unique_receipts),
                    "results": list(unique_receipts.values())[:limit],
                }

        except Exception as e:
            logger.error("Search error: %s", e)
            return {"error": str(e), "results": []}

    @tool
    def get_receipt(
        image_id: str,
        receipt_id: int,
    ) -> dict:
        """Get full receipt with formatted text showing all words and labels.

        The receipt text shows each line with words and their labels inline:
            Line 0: TRADER[MERCHANT_NAME] JOE'S[MERCHANT_NAME]
            Line 5: ORGANIC[PRODUCT_NAME] COFFEE[PRODUCT_NAME] 12.99[LINE_TOTAL]
            Line 8: TAX 0.84[TAX]
            Line 9: TOTAL 13.83[GRAND_TOTAL]

        Use this to:
        - See what items are on a receipt
        - Find prices (look for [LINE_TOTAL] on same line as product)
        - Get tax and total amounts (look for [TAX], [GRAND_TOTAL])

        Args:
            image_id: The image ID from search results
            receipt_id: The receipt ID from search results

        Returns:
            Dict with merchant, formatted receipt text, and amounts summary
        """
        try:
            details = dynamo_client.get_receipt_details(image_id, receipt_id)

            # Get merchant
            merchant = "Unknown"
            if details.place:
                merchant = details.place.merchant_name or "Unknown"

            # Build label lookup
            labels_by_word: dict[tuple[int, int], list] = defaultdict(list)
            for label in details.labels:
                key = (label.line_id, label.word_id)
                labels_by_word[key].append(label)

            def get_valid_label(line_id: int, word_id: int) -> Optional[str]:
                history = labels_by_word.get((line_id, word_id), [])
                valid = [lb for lb in history if lb.validation_status == "VALID"]
                if valid:
                    valid.sort(key=lambda lb: str(lb.timestamp_added), reverse=True)
                    return valid[0].label
                return None

            # Build word contexts with positions
            word_contexts = []
            for word in details.words:
                centroid = word.calculate_centroid()
                label = get_valid_label(word.line_id, word.word_id)
                word_contexts.append({
                    "word": word,
                    "label": label,
                    "y": centroid[1],
                    "x": centroid[0],
                    "text": word.text,
                    "line_id": word.line_id,
                    "word_id": word.word_id,
                })

            if not word_contexts:
                return {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "merchant": merchant,
                    "formatted_receipt": "(empty receipt)",
                    "amounts": [],
                }

            # Sort by y descending (top first)
            sorted_words = sorted(word_contexts, key=lambda w: -w["y"])

            # Group into visual lines
            heights = [
                w["word"].bounding_box.get("height", 0.02)
                for w in sorted_words
                if w["word"].bounding_box.get("height")
            ]
            y_tolerance = (
                max(0.01, statistics.median(heights) * 0.75) if heights else 0.015
            )

            visual_lines = []
            current_line = [sorted_words[0]]
            current_y = sorted_words[0]["y"]

            for w in sorted_words[1:]:
                if abs(w["y"] - current_y) <= y_tolerance:
                    current_line.append(w)
                    current_y = sum(c["y"] for c in current_line) / len(current_line)
                else:
                    current_line.sort(key=lambda c: c["x"])
                    visual_lines.append(current_line)
                    current_line = [w]
                    current_y = w["y"]

            current_line.sort(key=lambda c: c["x"])
            visual_lines.append(current_line)

            # Format as text with inline labels
            formatted_lines = []
            for i, line in enumerate(visual_lines):
                line_parts = []
                for w in line:
                    if w["label"]:
                        line_parts.append(f"{w['text']}[{w['label']}]")
                    else:
                        line_parts.append(w["text"])
                formatted_lines.append(f"Line {i}: {' '.join(line_parts)}")

            formatted_receipt = "\n".join(formatted_lines)

            # Extract amounts summary
            amounts = []
            currency_labels = [
                "TAX",
                "SUBTOTAL",
                "GRAND_TOTAL",
                "LINE_TOTAL",
                "UNIT_PRICE",
            ]
            for w in sorted_words:
                if w["label"] in currency_labels:
                    try:
                        amount = float(w["text"].replace("$", "").replace(",", ""))
                        amounts.append({
                            "label": w["label"],
                            "text": w["text"],
                            "amount": amount,
                        })
                    except ValueError:
                        pass

            result = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant": merchant,
                "formatted_receipt": formatted_receipt,
                "amounts": amounts,
            }

            # Track retrieved receipt for aggregate_amounts
            if "retrieved_receipts" not in state_holder:
                state_holder["retrieved_receipts"] = []

            # Avoid duplicates
            existing = [
                r for r in state_holder["retrieved_receipts"]
                if r.get("image_id") == image_id
                and r.get("receipt_id") == receipt_id
            ]
            if not existing:
                state_holder["retrieved_receipts"].append(result)

            return result

        except Exception as e:
            logger.error("Error getting receipt: %s", e)
            return {"error": str(e)}

    @tool
    def semantic_search(
        query: str,
        limit: int = 20,
        min_similarity: float = 0.3,
    ) -> dict:
        """Perform explicit semantic similarity search using embeddings.

        Use this when:
        - Text search returned no results
        - You need to find conceptually similar items (not exact text match)
        - The user's query is natural language rather than exact product names

        Args:
            query: Natural language query (e.g., "coffee purchases", "grocery items")
            limit: Maximum results to return
            min_similarity: Minimum similarity threshold (0-1, default 0.3)

        Returns:
            Dict with matching receipts sorted by similarity score

        Examples:
            semantic_search("coffee purchases") -> finds receipts about coffee
            semantic_search("dairy products") -> finds milk, cheese, yogurt receipts
        """
        try:
            lines_collection = chroma_client.get_collection("lines")

            # Generate embedding for the query
            query_embeddings = _embed_fn([query])
            if not query_embeddings or not query_embeddings[0]:
                return {
                    "error": "Failed to generate query embedding",
                    "results": [],
                    "suggestion": "Try using search_receipts with text search instead",
                }

            # Perform similarity search
            results = lines_collection.query(
                query_embeddings=query_embeddings,
                n_results=limit * 3,  # Get extra to allow deduping and filtering
                include=["metadatas", "distances", "documents"],
            )

            # Process results with similarity scoring
            unique_receipts: dict[tuple, dict] = {}
            if results["ids"] and results["ids"][0]:
                for idx, (id_, meta) in enumerate(
                    zip(results["ids"][0], results["metadatas"][0])
                ):
                    key = (meta.get("image_id"), meta.get("receipt_id"))
                    distance = (
                        results["distances"][0][idx]
                        if results["distances"]
                        else 1.0
                    )
                    # Convert distance to similarity (assuming cosine distance)
                    similarity = max(0.0, 1.0 - distance)

                    # Filter by minimum similarity
                    if similarity < min_similarity:
                        continue

                    # Keep best match per receipt
                    if key not in unique_receipts or similarity > unique_receipts[key].get(
                        "similarity", 0
                    ):
                        unique_receipts[key] = {
                            "image_id": meta.get("image_id"),
                            "receipt_id": meta.get("receipt_id"),
                            "matched_text": meta.get("text", "")[:150],
                            "similarity": round(similarity, 3),
                            "confidence": (
                                "high" if similarity > 0.7
                                else "medium" if similarity > 0.5
                                else "low"
                            ),
                        }

                    # Track in state for retrieval metrics
                    if "searches" not in state_holder:
                        state_holder["searches"] = []
                    state_holder["searches"].append({
                        "type": "semantic",
                        "query": query,
                        "results_count": len(unique_receipts),
                    })

            # Sort by similarity
            sorted_results = sorted(
                unique_receipts.values(),
                key=lambda x: x.get("similarity", 0),
                reverse=True,
            )[:limit]

            # Provide suggestions if results are sparse
            suggestions = []
            if len(sorted_results) < 3:
                suggestions.append(
                    "Try search_receipts with text search for exact product names"
                )
            if all(r.get("confidence") == "low" for r in sorted_results):
                suggestions.append(
                    "Results have low confidence - consider refining your query"
                )

            return {
                "search_type": "semantic",
                "query": query,
                "total_matches": len(sorted_results),
                "min_similarity_used": min_similarity,
                "results": sorted_results,
                "suggestions": suggestions if suggestions else None,
            }

        except Exception as e:
            logger.error("Semantic search error: %s", e)
            return {"error": str(e), "results": []}

    @tool
    def signal_retrieval_complete(
        reason: str,
        receipts_found: int,
        confidence: str = "high",
    ) -> dict:
        """Signal that retrieval is complete and ready for context shaping.

        Call this when you have gathered sufficient receipt data and are ready
        to have the context shaped and filtered before answer synthesis.

        Args:
            reason: Why retrieval is complete (e.g., "Found all matching receipts")
            receipts_found: Number of unique receipts retrieved
            confidence: Confidence level in retrieval completeness (high/medium/low)

        Returns:
            Confirmation and summary of retrieval phase

        Example:
            After searching and getting receipt details:
            signal_retrieval_complete(
                reason="Found 3 receipts with coffee purchases",
                receipts_found=3,
                confidence="high"
            )
        """
        # Record completion signal in state
        state_holder["retrieval_complete"] = True
        state_holder["retrieval_summary"] = {
            "reason": reason,
            "receipts_found": receipts_found,
            "confidence": confidence,
        }

        logger.info(
            "Retrieval complete: %s (%d receipts, %s confidence)",
            reason,
            receipts_found,
            confidence,
        )

        return {
            "status": "retrieval_complete",
            "reason": reason,
            "receipts_found": receipts_found,
            "confidence": confidence,
            "next_step": "Context will be shaped and filtered for answer synthesis",
        }

    @tool
    def aggregate_amounts(
        label_type: str = "LINE_TOTAL",
        filter_text: Optional[str] = None,
    ) -> dict:
        """Aggregate amounts across all retrieved receipt contexts.

        Use this for "how much" questions after retrieving relevant receipts.
        Sums amounts with the specified label type across all retrieved contexts.

        Args:
            label_type: Type of amount to aggregate (LINE_TOTAL, TAX, GRAND_TOTAL, etc.)
            filter_text: Optional text filter to only include amounts from lines
                        containing this text (e.g., "COFFEE" to sum only coffee items)

        Returns:
            Dict with total, count, and breakdown by receipt

        Example:
            # After getting receipts with coffee:
            aggregate_amounts("LINE_TOTAL", filter_text="COFFEE")
            # Returns: {"total": 23.98, "count": 2, "breakdown": [...]}
        """
        # Get retrieved receipts from state
        retrieved = state_holder.get("retrieved_receipts", [])
        if not retrieved:
            return {
                "error": "No receipts retrieved yet",
                "suggestion": "Use search_receipts and get_receipt first",
                "total": 0.0,
                "count": 0,
            }

        total = 0.0
        count = 0
        breakdown = []

        for receipt in retrieved:
            amounts = receipt.get("amounts", [])
            for amt in amounts:
                if amt.get("label") != label_type:
                    continue

                # Apply text filter if specified
                if filter_text:
                    formatted_receipt = receipt.get("formatted_receipt", "")
                    # Check if filter text appears on same line as amount
                    lines = formatted_receipt.split("\n")
                    amount_found_with_filter = False
                    for line in lines:
                        if (
                            filter_text.upper() in line.upper()
                            and amt.get("text", "") in line
                        ):
                            amount_found_with_filter = True
                            break
                    if not amount_found_with_filter:
                        continue

                amount_value = amt.get("amount", 0.0)
                total += amount_value
                count += 1
                breakdown.append({
                    "image_id": receipt.get("image_id"),
                    "receipt_id": receipt.get("receipt_id"),
                    "merchant": receipt.get("merchant", "Unknown"),
                    "amount": amount_value,
                    "text": amt.get("text"),
                })

        return {
            "label_type": label_type,
            "filter_text": filter_text,
            "total": round(total, 2),
            "count": count,
            "breakdown": breakdown,
        }

    @tool
    def submit_answer(
        answer: str,
        total_amount: Optional[float] = None,
        receipt_count: int = 0,
        evidence: Optional[list[dict]] = None,
    ) -> dict:
        """Submit your final answer. ALWAYS call this at the end.

        Args:
            answer: Natural language answer to the question
            total_amount: Total dollar amount if the question asks "how much"
            receipt_count: Number of receipts involved
            evidence: List of {image_id, receipt_id, item, amount} for each receipt

        Returns:
            Confirmation that answer was submitted
        """
        state_holder["answer"] = {
            "answer": answer,
            "total_amount": total_amount,
            "receipt_count": receipt_count,
            "evidence": evidence or [],
        }
        logger.info("Answer submitted: %s", answer[:100])
        return {"status": "submitted", "answer": answer}

    return [
        search_receipts,
        semantic_search,
        get_receipt,
        signal_retrieval_complete,
        aggregate_amounts,
        submit_answer,
    ], state_holder


# Enhanced system prompt for ReAct RAG workflow
SIMPLIFIED_SYSTEM_PROMPT = """You are a receipt analysis assistant using a ReAct RAG workflow.

## Available Tools

### Search Tools

1. **search_receipts(query, search_type, limit)** - Structured search
   - search_type="text": Exact text match (e.g., "COFFEE", "MILK")
   - search_type="label": Word labels (e.g., "TAX", "GRAND_TOTAL")
   - search_type="label_lines": Row-level labels (faster for aggregation)
   - search_type="semantic": Meaning similarity (e.g., "coffee purchase")

2. **semantic_search(query, limit, min_similarity)** - Embedding-based similarity
   - Best for natural language queries
   - Use when text search fails or returns sparse results
   - Returns confidence scores (high/medium/low)

### Retrieval Tools

3. **get_receipt(image_id, receipt_id)** - Full receipt details
   - Returns formatted text with inline labels:
     ```
     Line 0: TRADER[MERCHANT_NAME] JOE'S[MERCHANT_NAME]
     Line 5: ORGANIC[PRODUCT_NAME] COFFEE[PRODUCT_NAME] 12.99[LINE_TOTAL]
     Line 8: TAX 0.84[TAX]
     Line 9: TOTAL 13.83[GRAND_TOTAL]
     ```
   - [LINE_TOTAL] = item price, [TAX]/[GRAND_TOTAL]/[SUBTOTAL] = summary amounts

4. **signal_retrieval_complete(reason, receipts_found, confidence)** - Signal done retrieving
   - Call when you have gathered sufficient context
   - Triggers context shaping phase

### Aggregation Tools

5. **aggregate_amounts(label_type, filter_text)** - Sum amounts across receipts
   - Aggregates LINE_TOTAL, TAX, GRAND_TOTAL across retrieved receipts
   - Use filter_text to limit (e.g., filter_text="COFFEE" for coffee items only)

### Answer Tool

6. **submit_answer(answer, total_amount, receipt_count, evidence)** - Submit final answer
   - ALWAYS call at the end
   - Include total_amount for "how much" questions
   - Include evidence [{image_id, receipt_id, item, amount}]

## ReAct Strategy

**Think → Act → Observe → Repeat**

1. **Plan**: Understand the question type
   - Specific item query: "How much for coffee?" → search + get_receipt
   - Aggregation: "Total spending?" → search labels + aggregate_amounts
   - List query: "Show all dairy" → multiple searches + compile list

2. **Search**: Find relevant receipts
   - Start with text search for known terms
   - Fall back to semantic_search if sparse results
   - Try query variations if initial search fails

3. **Retrieve**: Get full receipt details
   - Use get_receipt for each relevant match
   - Look for [LINE_TOTAL] on same line as products

4. **Aggregate** (if needed): Use aggregate_amounts for "how much" questions

5. **Signal**: Call signal_retrieval_complete when done gathering

6. **Answer**: Call submit_answer with evidence

## Examples

### "How much did I spend on coffee?"
1. search_receipts("COFFEE", "text")
2. get_receipt(image_id_1, receipt_id_1) → find COFFEE[PRODUCT_NAME] 5.99[LINE_TOTAL]
3. get_receipt(image_id_2, receipt_id_2) → find COLD BREW[PRODUCT_NAME] 4.99[LINE_TOTAL]
4. aggregate_amounts("LINE_TOTAL", filter_text="COFFEE")
5. signal_retrieval_complete("Found all coffee purchases", receipts_found=2)
6. submit_answer("$10.98 on coffee", total_amount=10.98, receipt_count=2, evidence=[...])

### "Show me all receipts with dairy products"
1. search_receipts("MILK", "text")
2. search_receipts("CHEESE", "text")
3. semantic_search("dairy products yogurt cream")
4. get_receipt for each unique match
5. signal_retrieval_complete("Found dairy receipts", receipts_found=5)
6. submit_answer("Found 5 receipts with dairy...", receipt_count=5, evidence=[...])

## Rules
- ALWAYS end with submit_answer
- Call signal_retrieval_complete before answering if you did retrieval
- Try semantic_search if text search returns < 3 results
- Look for [LINE_TOTAL] on same line as product to find prices
"""
