"""Enhanced QA tools for ReAct RAG workflow.

This module provides a consolidated tool set for the QA agent:
1. search_receipts - unified search (text, label, or semantic)
2. semantic_search - explicit embedding-based similarity search
3. get_receipt - full receipt with formatted text and inline labels
4. signal_retrieval_complete - explicit signal to move to context shaping
5. aggregate_amounts - helper for "how much" questions
6. submit_answer - submit final answer (for backwards compatibility)
7. list_merchants - list all merchants with receipt counts
8. get_receipts_by_merchant - get receipts for a specific merchant
9. search_product_lines - search product lines with prices
10. get_receipt_summaries - pre-computed aggregates with filtering
11. list_categories - list merchant categories

The key insight is that showing the receipt with inline labels like:
    Line 5: ORGANIC[PRODUCT_NAME] COFFEE[PRODUCT_NAME] 12.99[LINE_TOTAL]

Allows the LLM to extract prices, amounts, and relationships without
needing separate tools for each query type.
"""

import logging
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
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

    def _fetch_receipt_details(image_id: str, receipt_id: int) -> Optional[dict]:
        """Fetch receipt details and format them. Returns None on error."""
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

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant": merchant,
                "formatted_receipt": formatted_receipt,
                "amounts": amounts,
            }

        except Exception as e:
            logger.error("Error fetching receipt %s:%s: %s", image_id, receipt_id, e)
            return None

    def _store_receipt(result: dict) -> None:
        """Store a receipt result in state_holder, avoiding duplicates."""
        if "retrieved_receipts" not in state_holder:
            state_holder["retrieved_receipts"] = []

        existing = [
            r for r in state_holder["retrieved_receipts"]
            if r.get("image_id") == result.get("image_id")
            and r.get("receipt_id") == result.get("receipt_id")
        ]
        if not existing:
            state_holder["retrieved_receipts"].append(result)

    @tool
    def search_receipts(
        query: str,
        search_type: str = "text",
        limit: int = 20,
        auto_fetch: int = 5,
    ) -> dict:
        """Search for receipts by text content, label type, or semantic similarity.

        Automatically fetches and stores the top results for use by other tools.

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
            auto_fetch: Number of top results to auto-fetch full details for (default 5)

        Returns:
            Dict with matching receipts (image_id, receipt_id, preview text)

        Examples:
            search_receipts("COFFEE", "text")  -> finds receipts with coffee
            search_receipts("TAX", "label")    -> finds receipts with TAX labels
            search_receipts("PRODUCT_NAME", "label_lines") -> finds rows with
                                                              product labels
            search_receipts("coffee purchase", "semantic") -> semantic search
        """
        search_result = None
        unique_receipts = {}

        try:
            if search_type == "label":
                # Search words collection by label
                words_collection = chroma_client.get_collection("words")
                results = words_collection.get(
                    where={"label": query.upper()},
                    include=["metadatas"],
                )

                # Dedupe by receipt
                for id_, meta in zip(results["ids"], results["metadatas"]):
                    key = (meta.get("image_id"), meta.get("receipt_id"))
                    if key not in unique_receipts:
                        unique_receipts[key] = {
                            "image_id": meta.get("image_id"),
                            "receipt_id": meta.get("receipt_id"),
                            "matched_text": meta.get("text"),
                            "matched_label": query.upper(),
                        }

                search_result = {
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
                for id_, meta in zip(results["ids"], results["metadatas"]):
                    key = (meta.get("image_id"), meta.get("receipt_id"))
                    if key not in unique_receipts:
                        unique_receipts[key] = {
                            "image_id": meta.get("image_id"),
                            "receipt_id": meta.get("receipt_id"),
                            "matched_row": meta.get("text", "")[:100],
                            "matched_label": query.upper(),
                        }

                search_result = {
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

                search_result = {
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
                for id_, meta in zip(results["ids"], results["metadatas"]):
                    key = (meta.get("image_id"), meta.get("receipt_id"))
                    if key not in unique_receipts:
                        unique_receipts[key] = {
                            "image_id": meta.get("image_id"),
                            "receipt_id": meta.get("receipt_id"),
                            "matched_line": meta.get("text", "")[:100],
                        }

                search_result = {
                    "search_type": "text",
                    "query": query,
                    "total_matches": len(results["ids"]),
                    "unique_receipts": len(unique_receipts),
                    "results": list(unique_receipts.values())[:limit],
                }

            # Auto-fetch top N receipts and store them for shape node
            if search_result and auto_fetch > 0:
                fetched_count = 0
                for receipt_info in search_result.get("results", [])[:auto_fetch]:
                    image_id = receipt_info.get("image_id")
                    receipt_id = receipt_info.get("receipt_id")
                    if image_id and receipt_id is not None:
                        details = _fetch_receipt_details(image_id, receipt_id)
                        if details:
                            _store_receipt(details)
                            fetched_count += 1

                search_result["auto_fetched"] = fetched_count
                logger.info(
                    "Search found %d receipts, auto-fetched %d",
                    len(unique_receipts),
                    fetched_count,
                )

            return search_result

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
        result = _fetch_receipt_details(image_id, receipt_id)
        if result is None:
            return {"error": f"Failed to fetch receipt {image_id}:{receipt_id}"}

        _store_receipt(result)
        return result

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

    @tool
    def list_merchants() -> dict:
        """List all merchants with receipt counts.

        Returns merchants sorted by receipt count (descending).
        Use this to see which stores appear most frequently in receipts.

        Returns:
            Dict with total_merchants count and list of {merchant, receipt_count}

        Example:
            list_merchants() -> {"merchants": [
                {"merchant": "Sprouts", "receipt_count": 45},
                {"merchant": "Costco", "receipt_count": 12},
            ]}
        """
        try:
            merchant_counts: dict[str, int] = defaultdict(int)
            last_key = None

            while True:
                places, last_key = dynamo_client.list_receipt_places(
                    limit=1000,
                    last_evaluated_key=last_key,
                )

                for place in places:
                    if place.merchant_name:
                        merchant_counts[place.merchant_name] += 1

                if last_key is None:
                    break

            sorted_merchants = sorted(
                merchant_counts.items(),
                key=lambda x: x[1],
                reverse=True,
            )

            return {
                "total_merchants": len(sorted_merchants),
                "merchants": [
                    {"merchant": name, "receipt_count": count}
                    for name, count in sorted_merchants
                ],
            }

        except Exception as e:
            logger.error("Error listing merchants: %s", e)
            return {"error": str(e)}

    @tool
    def get_receipts_by_merchant(merchant_name: str) -> dict:
        """Get all receipt IDs for a specific merchant.

        Use this after list_merchants to drill down into a specific store.

        Args:
            merchant_name: Exact merchant name from list_merchants

        Returns:
            Dict with merchant, count, and list of [image_id, receipt_id] pairs

        Example:
            get_receipts_by_merchant("Sprouts Farmers Market")
            -> {"merchant": "...", "count": 45, "receipts": [[img_id, receipt_id], ...]}
        """
        try:
            all_places = []
            last_key = None

            while True:
                places, last_key = dynamo_client.get_receipt_places_by_merchant(
                    merchant_name=merchant_name,
                    limit=1000,
                    last_evaluated_key=last_key,
                )
                all_places.extend(places)

                if last_key is None:
                    break

            receipts = [
                [place.image_id, place.receipt_id]
                for place in all_places
            ]

            return {
                "merchant": merchant_name,
                "count": len(receipts),
                "receipts": receipts,
            }

        except Exception as e:
            logger.error("Error getting receipts by merchant: %s", e)
            return {"error": str(e)}

    @tool
    def search_product_lines(
        query: str,
        search_type: str = "text",
        limit: int = 100,
    ) -> dict:
        """Search for product lines and return prices for spending analysis.

        Use this to answer spending questions like "how much did I spend on X?"

        Args:
            query: Product term or natural language description
            search_type: "text" for exact match, "semantic" for meaning-based
            limit: Maximum results to return

        Returns:
            Dict with items containing text, price, merchant, and receipt IDs

        Examples:
            search_product_lines("MILK", "text") -> exact matches for MILK
            search_product_lines("dairy products", "semantic") -> milk, cheese, etc.
        """
        try:
            lines_collection = chroma_client.get_collection("lines")

            def extract_price(text: str) -> Optional[float]:
                matches = re.findall(r'\d+\.\d{2}', text)
                if matches:
                    return float(matches[-1])
                return None

            if search_type == "semantic":
                query_embeddings = _embed_fn([query])
                if not query_embeddings or not query_embeddings[0]:
                    return {"error": "Failed to generate embedding"}

                results = lines_collection.query(
                    query_embeddings=query_embeddings,
                    n_results=limit * 3,
                    include=["metadatas", "distances"],
                )

                if not results["ids"] or not results["ids"][0]:
                    return {
                        "query": query,
                        "search_type": "semantic",
                        "total_matches": 0,
                        "items": [],
                    }

                items = []
                seen = set()

                for idx, (id_, meta) in enumerate(
                    zip(results["ids"][0], results["metadatas"][0])
                ):
                    text = meta.get("text", "")
                    image_id = meta.get("image_id")
                    receipt_id = meta.get("receipt_id")

                    key = (image_id, receipt_id, text)
                    if key in seen:
                        continue
                    seen.add(key)

                    distance = (
                        results["distances"][0][idx]
                        if results["distances"]
                        else 1.0
                    )
                    similarity = max(0.0, 1.0 - distance)

                    if similarity < 0.25:
                        continue

                    items.append({
                        "text": text,
                        "price": extract_price(text),
                        "similarity": round(similarity, 3),
                        "has_price_label": meta.get("label_LINE_TOTAL", False),
                        "merchant": meta.get("merchant_name", "Unknown"),
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                    })

                items.sort(key=lambda x: -x.get("similarity", 0))
                items = items[:limit]

                total = sum(
                    item["price"] for item in items if item["price"] is not None
                )

                # Auto-fetch unique receipts for context
                unique_receipt_keys = set()
                for item in items[:10]:  # Limit to top 10
                    key = (item.get("image_id"), item.get("receipt_id"))
                    if key[0] and key[1] is not None:
                        unique_receipt_keys.add(key)

                fetched_count = 0
                for image_id, receipt_id in list(unique_receipt_keys)[:5]:
                    details = _fetch_receipt_details(image_id, receipt_id)
                    if details:
                        _store_receipt(details)
                        fetched_count += 1

                if fetched_count:
                    logger.info(
                        "search_product_lines auto-fetched %d receipts",
                        fetched_count,
                    )

                return {
                    "query": query,
                    "search_type": "semantic",
                    "total_matches": len(results["ids"][0]),
                    "unique_items": len(items),
                    "items": items,
                    "raw_total": round(total, 2),
                    "auto_fetched": fetched_count,
                    "note": "Review items for relevance before summing prices.",
                }

            else:
                # Text search
                results = lines_collection.get(
                    where_document={"$contains": query.upper()},
                    include=["metadatas"],
                )

                if not results["ids"]:
                    return {
                        "query": query,
                        "search_type": "text",
                        "total_matches": 0,
                        "items": [],
                    }

                items = []
                seen = set()

                for id_, meta in zip(results["ids"], results["metadatas"]):
                    text = meta.get("text", "")
                    image_id = meta.get("image_id")
                    receipt_id = meta.get("receipt_id")

                    key = (image_id, receipt_id, text)
                    if key in seen:
                        continue
                    seen.add(key)

                    items.append({
                        "text": text,
                        "price": extract_price(text),
                        "has_price_label": meta.get("label_LINE_TOTAL", False),
                        "merchant": meta.get("merchant_name", "Unknown"),
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                    })

                items.sort(key=lambda x: (x["price"] is None, -(x["price"] or 0)))
                items = items[:limit]

                total = sum(
                    item["price"] for item in items if item["price"] is not None
                )

                # Auto-fetch unique receipts for context
                unique_receipt_keys = set()
                for item in items[:10]:  # Limit to top 10
                    key = (item.get("image_id"), item.get("receipt_id"))
                    if key[0] and key[1] is not None:
                        unique_receipt_keys.add(key)

                fetched_count = 0
                for image_id, receipt_id in list(unique_receipt_keys)[:5]:
                    details = _fetch_receipt_details(image_id, receipt_id)
                    if details:
                        _store_receipt(details)
                        fetched_count += 1

                if fetched_count:
                    logger.info(
                        "search_product_lines auto-fetched %d receipts",
                        fetched_count,
                    )

                return {
                    "query": query,
                    "search_type": "text",
                    "total_matches": len(results["ids"]),
                    "unique_items": len(items),
                    "items": items,
                    "raw_total": round(total, 2),
                    "auto_fetched": fetched_count,
                    "note": "Exclude false positives before reporting total.",
                }

        except Exception as e:
            logger.error("Error searching product lines: %s", e)
            return {"error": str(e)}

    @tool
    def get_receipt_summaries(
        merchant_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 1000,
    ) -> dict:
        """Get pre-computed summaries for receipts with totals, tax, dates.

        Use this for aggregation questions like:
        - "What was my total spending at Costco?" (merchant_filter="Costco")
        - "How much did I spend on groceries?" (category_filter="grocery")
        - "How much tax did I pay last month?" (use date filters)
        - "What's my average grocery bill?"

        Args:
            merchant_filter: Filter by merchant name (partial match)
            category_filter: Filter by category (grocery, restaurant, gas_station)
            start_date: Filter receipts on/after this date (YYYY-MM-DD)
            end_date: Filter receipts on/before this date (YYYY-MM-DD)
            limit: Maximum receipts to return

        Returns:
            Dict with aggregates (total_spending, total_tax, average_receipt)
            and individual receipt summaries
        """
        try:
            # Parse date filters
            start_dt = None
            end_dt = None
            if start_date:
                try:
                    start_dt = datetime.fromisoformat(
                        start_date.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass
            if end_date:
                try:
                    end_dt = datetime.fromisoformat(
                        end_date.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            # Load all summaries from DynamoDB
            all_summaries = []
            last_key = None
            while True:
                records, last_key = dynamo_client.list_receipt_summaries(
                    limit=1000,
                    last_evaluated_key=last_key,
                )
                all_summaries.extend(records)
                if last_key is None:
                    break

            # Load ReceiptPlace for category info
            places_by_key: dict[str, Any] = {}
            last_key = None
            while True:
                places, last_key = dynamo_client.list_receipt_places(
                    limit=1000,
                    last_evaluated_key=last_key,
                )
                for place in places:
                    key = f"{place.image_id}_{place.receipt_id}"
                    places_by_key[key] = place
                if last_key is None:
                    break

            # Filter in memory
            filtered = []
            for record in all_summaries:
                key = f"{record.image_id}_{record.receipt_id}"
                place = places_by_key.get(key)
                merchant_category = place.merchant_category if place else ""

                # Merchant filter
                if merchant_filter:
                    if not record.merchant_name:
                        continue
                    if merchant_filter.lower() not in record.merchant_name.lower():
                        continue

                # Category filter
                if category_filter:
                    category_match = False
                    if (
                        merchant_category
                        and category_filter.lower() in merchant_category.lower()
                    ):
                        category_match = True
                    if place and place.merchant_types:
                        for t in place.merchant_types:
                            if category_filter.lower() in t.lower():
                                category_match = True
                                break
                    if not category_match:
                        continue

                # Date filter
                if start_dt and record.date:
                    if record.date < start_dt:
                        continue
                if end_dt and record.date:
                    if record.date > end_dt:
                        continue

                summary_dict = record.to_dict()
                summary_dict["merchant_category"] = merchant_category
                filtered.append(summary_dict)

                if len(filtered) >= limit:
                    break

            # Calculate aggregates
            total_spending = sum(s["grand_total"] or 0 for s in filtered)
            total_tax = sum(s["tax"] or 0 for s in filtered)
            total_tip = sum(s["tip"] or 0 for s in filtered)
            receipts_with_totals = sum(1 for s in filtered if s["grand_total"])

            # Auto-fetch a few sample receipts for context
            fetched_count = 0
            for summary in filtered[:5]:
                image_id = summary.get("image_id")
                receipt_id = summary.get("receipt_id")
                if image_id and receipt_id is not None:
                    details = _fetch_receipt_details(image_id, receipt_id)
                    if details:
                        _store_receipt(details)
                        fetched_count += 1

            if fetched_count:
                logger.info(
                    "get_receipt_summaries auto-fetched %d sample receipts",
                    fetched_count,
                )

            return {
                "count": len(filtered),
                "total_spending": round(total_spending, 2),
                "total_tax": round(total_tax, 2),
                "total_tip": round(total_tip, 2),
                "receipts_with_totals": receipts_with_totals,
                "average_receipt": (
                    round(total_spending / receipts_with_totals, 2)
                    if receipts_with_totals > 0
                    else None
                ),
                "filters": {
                    "merchant": merchant_filter,
                    "category": category_filter,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                "summaries": filtered,
                "auto_fetched": fetched_count,
            }

        except Exception as e:
            logger.error("Error getting receipt summaries: %s", e)
            return {"error": str(e)}

    @tool
    def list_categories() -> dict:
        """List all merchant categories with receipt counts.

        Returns categories from Google Places data, sorted by receipt count.
        Use this to discover available categories for filtering.

        Returns:
            Dict with categories like grocery_store, restaurant, gas_station

        Example:
            list_categories() -> {"categories": [
                {"category": "grocery_store", "receipt_count": 241},
                {"category": "restaurant", "receipt_count": 47},
            ]}
        """
        try:
            category_counts: dict[str, int] = defaultdict(int)
            last_key = None

            while True:
                places, last_key = dynamo_client.list_receipt_places(
                    limit=1000,
                    last_evaluated_key=last_key,
                )

                for place in places:
                    if place.merchant_category:
                        category_counts[place.merchant_category] += 1

                if last_key is None:
                    break

            sorted_categories = sorted(
                category_counts.items(),
                key=lambda x: x[1],
                reverse=True,
            )

            return {
                "total_categories": len(sorted_categories),
                "categories": [
                    {"category": cat, "receipt_count": count}
                    for cat, count in sorted_categories
                ],
            }

        except Exception as e:
            logger.error("Error listing categories: %s", e)
            return {"error": str(e)}

    return [
        search_receipts,
        semantic_search,
        get_receipt,
        signal_retrieval_complete,
        aggregate_amounts,
        submit_answer,
        list_merchants,
        get_receipts_by_merchant,
        search_product_lines,
        get_receipt_summaries,
        list_categories,
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

3. **search_product_lines(query, search_type, limit)** - Search with prices
   - Returns product lines with extracted prices
   - Use for "how much did I spend on X?" questions
   - search_type="text" or "semantic"

### Retrieval Tools

4. **get_receipt(image_id, receipt_id)** - Full receipt details
   - Returns formatted text with inline labels:
     ```
     Line 0: TRADER[MERCHANT_NAME] JOE'S[MERCHANT_NAME]
     Line 5: ORGANIC[PRODUCT_NAME] COFFEE[PRODUCT_NAME] 12.99[LINE_TOTAL]
     Line 8: TAX 0.84[TAX]
     Line 9: TOTAL 13.83[GRAND_TOTAL]
     ```
   - [LINE_TOTAL] = item price, [TAX]/[GRAND_TOTAL]/[SUBTOTAL] = summary amounts

5. **signal_retrieval_complete(reason, receipts_found, confidence)** - Signal done retrieving
   - Call when you have gathered sufficient context
   - Triggers context shaping phase

### Aggregation Tools

6. **aggregate_amounts(label_type, filter_text)** - Sum amounts across receipts
   - Aggregates LINE_TOTAL, TAX, GRAND_TOTAL across retrieved receipts
   - Use filter_text to limit (e.g., filter_text="COFFEE" for coffee items only)

7. **get_receipt_summaries(merchant_filter, category_filter, start_date, end_date)** - Pre-computed aggregates
   - Fast aggregation for total spending, tax, tips
   - Filter by merchant name, category, or date range
   - Categories: grocery_store, restaurant, gas_station, pharmacy, coffee_shop
   - Date format: YYYY-MM-DD

### Merchant/Category Tools

8. **list_merchants()** - List all merchants with receipt counts
   - Shows which stores appear most frequently

9. **get_receipts_by_merchant(merchant_name)** - Get receipts for a merchant
   - Returns all receipt IDs for drilling down

10. **list_categories()** - List merchant categories with counts
    - Shows available categories for filtering

### Answer Tool

11. **submit_answer(answer, total_amount, receipt_count, evidence)** - Submit final answer
    - ALWAYS call at the end
    - Include total_amount for "how much" questions
    - Include evidence [{image_id, receipt_id, item, amount}]

## ReAct Strategy

**Think → Act → Observe → Repeat**

1. **Plan**: Understand the question type
   - Specific item query: "How much for coffee?" → search_product_lines or search + get_receipt
   - Aggregation: "Total spending at Costco?" → get_receipt_summaries(merchant_filter="Costco")
   - Time-based: "Spending last month?" → get_receipt_summaries with date filters
   - Category: "Grocery spending?" → get_receipt_summaries(category_filter="grocery")
   - List query: "Show all dairy" → multiple searches + compile list

2. **Search**: Find relevant receipts
   - For spending questions: try get_receipt_summaries first (fast aggregation)
   - For product search: use search_product_lines
   - Fall back to semantic_search if sparse results

3. **Retrieve**: Get full receipt details if needed
   - Use get_receipt for each relevant match
   - Look for [LINE_TOTAL] on same line as products

4. **Aggregate** (if needed): Use aggregate_amounts or get_receipt_summaries

5. **Signal**: Call signal_retrieval_complete when done gathering

6. **Answer**: Call submit_answer with evidence

## Examples

### "How much did I spend on coffee?"
1. search_product_lines("COFFEE", "text")
2. Review items, filter false positives
3. submit_answer("$45.67 on coffee", total_amount=45.67, receipt_count=8, evidence=[...])

### "What was my total spending at Costco?"
1. get_receipt_summaries(merchant_filter="Costco")
2. submit_answer("$1,234.56 at Costco across 12 receipts", total_amount=1234.56, receipt_count=12)

### "How much did I spend on groceries last month?"
1. get_receipt_summaries(category_filter="grocery", start_date="2025-12-01", end_date="2025-12-31")
2. submit_answer("$456.78 on groceries in December", total_amount=456.78, receipt_count=15)

### "Which stores do I shop at most?"
1. list_merchants()
2. submit_answer("Top stores: Sprouts (45 visits), Costco (12 visits)...")

## Rules
- ALWAYS end with submit_answer
- For aggregation questions, try get_receipt_summaries FIRST (it's faster)
- For product-specific spending, use search_product_lines
- Call signal_retrieval_complete before answering if you did detailed retrieval
- Try semantic_search if text search returns < 3 results
"""
