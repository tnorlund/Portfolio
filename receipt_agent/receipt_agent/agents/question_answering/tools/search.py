"""QA tools for ReAct workflow.

This module provides tools for the receipt QA agent:
1. search_receipts - unified search (text, label, or semantic)
2. semantic_search - explicit embedding-based similarity search
3. get_receipt - full receipt with formatted text and inline labels
4. aggregate_amounts - helper for "how much" questions
5. list_merchants - list all merchants with receipt counts
6. get_receipts_by_merchant - get receipts for a specific merchant
7. search_product_lines - search product lines with prices
8. get_receipt_summaries - pre-computed aggregates with filtering
9. list_categories - list merchant categories

The agent uses these tools in a ReAct loop, then stops calling tools
when it has enough information. A synthesize node formats the final answer.
"""

import logging
import re
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def create_qa_tools(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
) -> tuple[list, dict]:
    """Create tools for QA agent.

    Args:
        dynamo_client: DynamoDB client for receipt data
        chroma_client: ChromaDB client for similarity search
        embed_fn: Function to generate embeddings for semantic search

    Returns:
        (tools, state_holder) - List of tools and state dict for tracking
    """
    _embed_fn = embed_fn

    # State holder tracks searches and retrieved receipts
    # This helps the agent know what it's already searched/fetched
    state_holder: dict[str, Any] = {
        "searches": [],  # [{query, type, result_count}, ...]
        "retrieved_receipts": [],  # Full receipt details for synthesize
        "fetched_receipt_keys": set(),  # (image_id, receipt_id) to avoid re-fetching
    }

    def _get_effective_label(
        labels: list,
        line_id: int,
        word_id: int,
    ) -> Optional[str]:
        """Get the effective label for a word, preferring VALID status.

        A word can have multiple ReceiptWordLabel records (audit trail).
        Priority: VALID status, then most recent by timestamp.
        """
        matching = [
            lb
            for lb in labels
            if lb.line_id == line_id and lb.word_id == word_id
        ]
        if not matching:
            return None

        # Prefer VALID labels
        valid = [lb for lb in matching if lb.validation_status == "VALID"]
        if valid:
            valid.sort(
                key=lambda lb: str(lb.timestamp_added or ""), reverse=True
            )
            return valid[0].label

        # Fall back to most recent
        matching.sort(
            key=lambda lb: str(lb.timestamp_added or ""), reverse=True
        )
        return matching[0].label

    def _fetch_receipt_details(
        image_id: str, receipt_id: int
    ) -> Optional[dict]:
        """Fetch receipt details with structured word/label data.

        Returns dict with:
        - image_id, receipt_id, merchant
        - words_by_line: dict[line_id] -> list of {text, label, x, y, word_id}
        - amounts: list of {label, text, amount, line_id, word_id}
        - formatted_receipt: text representation for LLM display
        """
        # Skip if already fetched
        key = (image_id, receipt_id)
        if key in state_holder["fetched_receipt_keys"]:
            # Return existing
            for r in state_holder["retrieved_receipts"]:
                if (
                    r.get("image_id") == image_id
                    and r.get("receipt_id") == receipt_id
                ):
                    return r
            return None

        try:
            details = dynamo_client.get_receipt_details(image_id, receipt_id)

            # Get merchant
            merchant = "Unknown"
            if details.place:
                merchant = details.place.merchant_name or "Unknown"

            # Build word contexts with positions and labels
            word_contexts = []
            for word in details.words:
                centroid = word.calculate_centroid()
                label = _get_effective_label(
                    details.labels, word.line_id, word.word_id
                )
                word_contexts.append(
                    {
                        "text": word.text,
                        "label": label,
                        "y": centroid[1],
                        "x": centroid[0],
                        "line_id": word.line_id,
                        "word_id": word.word_id,
                        "bounding_box": word.bounding_box,
                    }
                )

            if not word_contexts:
                result = {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "merchant": merchant,
                    "words_by_line": {},
                    "amounts": [],
                    "formatted_receipt": "(empty receipt)",
                }
                state_holder["fetched_receipt_keys"].add(key)
                state_holder["retrieved_receipts"].append(result)
                return result

            # Sort by y descending (top first)
            sorted_words = sorted(word_contexts, key=lambda w: -w["y"])

            # Group into visual lines based on y-position
            heights = [
                w["bounding_box"].get("height", 0.02)
                for w in sorted_words
                if w["bounding_box"] and w["bounding_box"].get("height")
            ]
            y_tolerance = (
                max(0.01, statistics.median(heights) * 0.75)
                if heights
                else 0.015
            )

            visual_lines: list[list[dict]] = []
            current_line = [sorted_words[0]]
            current_y = sorted_words[0]["y"]

            for w in sorted_words[1:]:
                if abs(w["y"] - current_y) <= y_tolerance:
                    current_line.append(w)
                    current_y = sum(c["y"] for c in current_line) / len(
                        current_line
                    )
                else:
                    current_line.sort(key=lambda c: c["x"])
                    visual_lines.append(current_line)
                    current_line = [w]
                    current_y = w["y"]

            current_line.sort(key=lambda c: c["x"])
            visual_lines.append(current_line)

            # Build structured words_by_line dict
            words_by_line: dict[int, list[dict]] = {}
            for line_idx, line_words in enumerate(visual_lines):
                words_by_line[line_idx] = [
                    {
                        "text": w["text"],
                        "label": w["label"],
                        "word_id": w["word_id"],
                        "x": w["x"],
                    }
                    for w in line_words
                ]

            # Extract amounts with line context
            amounts = []
            currency_labels = [
                "TAX",
                "SUBTOTAL",
                "GRAND_TOTAL",
                "LINE_TOTAL",
                "UNIT_PRICE",
            ]
            for line_idx, line_words in words_by_line.items():
                for w in line_words:
                    if w["label"] in currency_labels:
                        try:
                            amount = float(
                                w["text"].replace("$", "").replace(",", "")
                            )
                            amounts.append(
                                {
                                    "label": w["label"],
                                    "text": w["text"],
                                    "amount": amount,
                                    "line_idx": line_idx,
                                    "word_id": w["word_id"],
                                }
                            )
                        except ValueError:
                            pass

            # Format as text for LLM display (still useful for debugging/display)
            formatted_lines = []
            for line_idx, line_words in words_by_line.items():
                line_parts = []
                for w in line_words:
                    if w["label"]:
                        line_parts.append(f"{w['text']}[{w['label']}]")
                    else:
                        line_parts.append(w["text"])
                formatted_lines.append(
                    f"Line {line_idx}: {' '.join(line_parts)}"
                )

            formatted_receipt = "\n".join(formatted_lines)

            result = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant": merchant,
                "words_by_line": words_by_line,
                "amounts": amounts,
                "formatted_receipt": formatted_receipt,
            }

            # Track as fetched
            state_holder["fetched_receipt_keys"].add(key)
            state_holder["retrieved_receipts"].append(result)

            return result

        except Exception as e:
            logger.error(
                "Error fetching receipt %s:%s: %s", image_id, receipt_id, e
            )
            return None

    def _track_search(query: str, search_type: str, result_count: int) -> None:
        """Track a search to avoid redundant searches."""
        state_holder["searches"].append(
            {
                "query": query,
                "type": search_type,
                "result_count": result_count,
            }
        )

    @tool
    def search_receipts(
        query: str,
        search_type: str = "text",
        limit: int = 20,
        auto_fetch: int = 5,
    ) -> dict:
        """Search for receipts by text content, label type, or semantic similarity.

        Automatically fetches the top results so they're available for the final answer.

        Args:
            query: What to search for.
                - For text search: product name like "COFFEE", "MILK", "ORGANIC"
                - For label search: label type like "TAX", "GRAND_TOTAL", "SUBTOTAL"
                - For semantic search: natural language like "coffee purchase"
            search_type: Search method to use:
                - "text": Search line content for exact text match
                - "label": Search by label type (uses WORDS collection)
                - "label_lines": Search rows with specific label
                - "semantic": Semantic similarity search using embeddings
            limit: Maximum results to return
            auto_fetch: Number of top results to auto-fetch full details for (default 5)

        Returns:
            Dict with matching receipts (image_id, receipt_id, preview text)

        Examples:
            search_receipts("COFFEE", "text")  -> finds receipts with coffee
            search_receipts("TAX", "label")    -> finds receipts with TAX labels
            search_receipts("coffee purchase", "semantic") -> semantic search
        """
        search_result = None
        unique_receipts = {}

        try:
            if search_type == "label":
                words_collection = chroma_client.get_collection("words")
                results = words_collection.get(
                    where={"label": query.upper()},
                    include=["metadatas"],
                )

                for id_, meta in zip(results["ids"], results["metadatas"]):
                    receipt_key = (
                        meta.get("image_id"),
                        meta.get("receipt_id"),
                    )
                    if receipt_key not in unique_receipts:
                        unique_receipts[receipt_key] = {
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
                lines_collection = chroma_client.get_collection("lines")
                label_key = f"label_{query.upper()}"

                results = lines_collection.get(
                    where={label_key: True},
                    include=["metadatas"],
                )

                for id_, meta in zip(results["ids"], results["metadatas"]):
                    receipt_key = (
                        meta.get("image_id"),
                        meta.get("receipt_id"),
                    )
                    if receipt_key not in unique_receipts:
                        unique_receipts[receipt_key] = {
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
                lines_collection = chroma_client.get_collection("lines")
                query_embeddings = _embed_fn([query])
                if not query_embeddings or not query_embeddings[0]:
                    return {
                        "error": "Failed to generate query embedding",
                        "results": [],
                    }

                results = lines_collection.query(
                    query_embeddings=query_embeddings,
                    n_results=limit * 2,
                    include=["metadatas", "distances"],
                )

                if results["ids"] and results["ids"][0]:
                    for idx, (id_, meta) in enumerate(
                        zip(results["ids"][0], results["metadatas"][0])
                    ):
                        receipt_key = (
                            meta.get("image_id"),
                            meta.get("receipt_id"),
                        )
                        distance = (
                            results["distances"][0][idx]
                            if results["distances"]
                            else None
                        )
                        if receipt_key not in unique_receipts:
                            unique_receipts[receipt_key] = {
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
                # Default: text search
                lines_collection = chroma_client.get_collection("lines")
                results = lines_collection.get(
                    where_document={"$contains": query.upper()},
                    include=["metadatas"],
                )

                for id_, meta in zip(results["ids"], results["metadatas"]):
                    receipt_key = (
                        meta.get("image_id"),
                        meta.get("receipt_id"),
                    )
                    if receipt_key not in unique_receipts:
                        unique_receipts[receipt_key] = {
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

            # Track this search
            _track_search(query, search_type, len(unique_receipts))

            # Auto-fetch top N receipts
            if search_result and auto_fetch > 0:
                fetched_count = 0
                for receipt_info in search_result.get("results", [])[
                    :auto_fetch
                ]:
                    image_id = receipt_info.get("image_id")
                    receipt_id = receipt_info.get("receipt_id")
                    if image_id and receipt_id is not None:
                        details = _fetch_receipt_details(image_id, receipt_id)
                        if details:
                            fetched_count += 1

                search_result["auto_fetched"] = fetched_count
                logger.info(
                    "Search '%s' (%s) found %d receipts, auto-fetched %d",
                    query,
                    search_type,
                    len(unique_receipts),
                    fetched_count,
                )

            return search_result

        except Exception as e:
            logger.error("Search error: %s", e)
            return {"error": str(e), "results": []}

    @tool
    def get_receipt(image_id: str, receipt_id: int) -> dict:
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
            return {
                "error": f"Failed to fetch receipt {image_id}:{receipt_id}"
            }
        return result

    @tool
    def semantic_search(
        query: str,
        limit: int = 20,
        min_similarity: float = 0.3,
    ) -> dict:
        """Perform semantic similarity search using embeddings.

        Use this when:
        - Text search returned no results
        - You need to find conceptually similar items (not exact text match)
        - The user's query is natural language rather than exact product names

        Args:
            query: Natural language query (e.g., "coffee purchases", "dairy products")
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
            query_embeddings = _embed_fn([query])
            if not query_embeddings or not query_embeddings[0]:
                return {
                    "error": "Failed to generate query embedding",
                    "results": [],
                    "suggestion": "Try using search_receipts with text search instead",
                }

            results = lines_collection.query(
                query_embeddings=query_embeddings,
                n_results=limit * 3,
                include=["metadatas", "distances", "documents"],
            )

            unique_receipts: dict[tuple, dict] = {}
            if results["ids"] and results["ids"][0]:
                for idx, (id_, meta) in enumerate(
                    zip(results["ids"][0], results["metadatas"][0])
                ):
                    receipt_key = (
                        meta.get("image_id"),
                        meta.get("receipt_id"),
                    )
                    distance = (
                        results["distances"][0][idx]
                        if results["distances"]
                        else 1.0
                    )
                    similarity = max(0.0, 1.0 - distance)

                    if similarity < min_similarity:
                        continue

                    if (
                        receipt_key not in unique_receipts
                        or similarity
                        > unique_receipts[receipt_key].get("similarity", 0)
                    ):
                        unique_receipts[receipt_key] = {
                            "image_id": meta.get("image_id"),
                            "receipt_id": meta.get("receipt_id"),
                            "matched_text": meta.get("text", "")[:150],
                            "similarity": round(similarity, 3),
                            "confidence": (
                                "high"
                                if similarity > 0.7
                                else "medium" if similarity > 0.5 else "low"
                            ),
                        }

            # Track this search
            _track_search(query, "semantic", len(unique_receipts))

            sorted_results = sorted(
                unique_receipts.values(),
                key=lambda x: x.get("similarity", 0),
                reverse=True,
            )[:limit]

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
        retrieved = state_holder.get("retrieved_receipts", [])
        if not retrieved:
            return {
                "error": "No receipts retrieved yet",
                "suggestion": "Use search_receipts first",
                "total": 0.0,
                "count": 0,
            }

        total = 0.0
        count = 0
        breakdown = []

        for receipt in retrieved:
            amounts = receipt.get("amounts", [])
            words_by_line = receipt.get("words_by_line", {})

            for amt in amounts:
                if amt.get("label") != label_type:
                    continue

                # Check filter_text using structured data
                if filter_text:
                    line_idx = amt.get("line_idx")
                    if line_idx is None:
                        continue

                    # Get all text on this line
                    line_words = words_by_line.get(line_idx, [])
                    line_text = " ".join(w.get("text", "") for w in line_words)

                    # Check if filter text appears on this line
                    if filter_text.upper() not in line_text.upper():
                        continue

                amount_value = amt.get("amount", 0.0)
                total += amount_value
                count += 1

                # Get product name from the line (for breakdown)
                line_idx = amt.get("line_idx")
                product_name = ""
                if line_idx is not None:
                    line_words = words_by_line.get(line_idx, [])
                    # Collect PRODUCT_NAME words or unlabeled words
                    product_words = [
                        w
                        for w in line_words
                        if w.get("label") == "PRODUCT_NAME"
                    ]
                    if product_words:
                        product_name = " ".join(
                            w["text"] for w in product_words
                        )
                    else:
                        # Use unlabeled words (excluding price)
                        price_word_id = amt.get("word_id")
                        unlabeled = [
                            w
                            for w in line_words
                            if w.get("word_id") != price_word_id
                            and w.get("label")
                            not in (
                                "TAX",
                                "SUBTOTAL",
                                "GRAND_TOTAL",
                                "LINE_TOTAL",
                                "UNIT_PRICE",
                            )
                        ]
                        product_name = " ".join(w["text"] for w in unlabeled)

                breakdown.append(
                    {
                        "image_id": receipt.get("image_id"),
                        "receipt_id": receipt.get("receipt_id"),
                        "merchant": receipt.get("merchant", "Unknown"),
                        "amount": amount_value,
                        "text": amt.get("text"),
                        "product": (
                            product_name.strip() if product_name else None
                        ),
                    }
                )

        return {
            "label_type": label_type,
            "filter_text": filter_text,
            "total": round(total, 2),
            "count": count,
            "breakdown": breakdown,
        }

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
                places, last_key = (
                    dynamo_client.get_receipt_places_by_merchant(
                        merchant_name=merchant_name,
                        limit=1000,
                        last_evaluated_key=last_key,
                    )
                )
                all_places.extend(places)

                if last_key is None:
                    break

            receipts = [
                [place.image_id, place.receipt_id] for place in all_places
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
                matches = re.findall(r"\d+\.\d{2}", text)
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
                    _track_search(query, "semantic", 0)
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

                    item_key = (image_id, receipt_id, text)
                    if item_key in seen:
                        continue
                    seen.add(item_key)

                    distance = (
                        results["distances"][0][idx]
                        if results["distances"]
                        else 1.0
                    )
                    similarity = max(0.0, 1.0 - distance)

                    if similarity < 0.25:
                        continue

                    items.append(
                        {
                            "text": text,
                            "price": extract_price(text),
                            "similarity": round(similarity, 3),
                            "has_price_label": meta.get(
                                "label_LINE_TOTAL", False
                            ),
                            "merchant": meta.get("merchant_name", "Unknown"),
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                        }
                    )

                items.sort(key=lambda x: -x.get("similarity", 0))
                items = items[:limit]

                total = sum(
                    item["price"]
                    for item in items
                    if item["price"] is not None
                )

                # Auto-fetch unique receipts
                unique_receipt_keys = set()
                for item in items[:10]:
                    item_key = (item.get("image_id"), item.get("receipt_id"))
                    if item_key[0] and item_key[1] is not None:
                        unique_receipt_keys.add(item_key)

                fetched_count = 0
                for img_id, rcpt_id in list(unique_receipt_keys)[:5]:
                    details = _fetch_receipt_details(img_id, rcpt_id)
                    if details:
                        fetched_count += 1

                _track_search(query, "semantic", len(items))

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
                    _track_search(query, "text", 0)
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

                    item_key = (image_id, receipt_id, text)
                    if item_key in seen:
                        continue
                    seen.add(item_key)

                    items.append(
                        {
                            "text": text,
                            "price": extract_price(text),
                            "has_price_label": meta.get(
                                "label_LINE_TOTAL", False
                            ),
                            "merchant": meta.get("merchant_name", "Unknown"),
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                        }
                    )

                items.sort(
                    key=lambda x: (x["price"] is None, -(x["price"] or 0))
                )
                items = items[:limit]

                total = sum(
                    item["price"]
                    for item in items
                    if item["price"] is not None
                )

                # Auto-fetch unique receipts
                unique_receipt_keys = set()
                for item in items[:10]:
                    item_key = (item.get("image_id"), item.get("receipt_id"))
                    if item_key[0] and item_key[1] is not None:
                        unique_receipt_keys.add(item_key)

                fetched_count = 0
                for img_id, rcpt_id in list(unique_receipt_keys)[:5]:
                    details = _fetch_receipt_details(img_id, rcpt_id)
                    if details:
                        fetched_count += 1

                _track_search(query, "text", len(items))

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

            places_by_key: dict[str, Any] = {}
            last_key = None
            while True:
                places, last_key = dynamo_client.list_receipt_places(
                    limit=1000,
                    last_evaluated_key=last_key,
                )
                for place in places:
                    place_key = f"{place.image_id}_{place.receipt_id}"
                    places_by_key[place_key] = place
                if last_key is None:
                    break

            filtered = []
            for record in all_summaries:
                place_key = f"{record.image_id}_{record.receipt_id}"
                place = places_by_key.get(place_key)
                merchant_category = place.merchant_category if place else ""

                if merchant_filter:
                    if not record.merchant_name:
                        continue
                    if (
                        merchant_filter.lower()
                        not in record.merchant_name.lower()
                    ):
                        continue

                if category_filter:
                    category_match = False
                    if (
                        merchant_category
                        and category_filter.lower()
                        in merchant_category.lower()
                    ):
                        category_match = True
                    if place and place.merchant_types:
                        for t in place.merchant_types:
                            if category_filter.lower() in t.lower():
                                category_match = True
                                break
                    if not category_match:
                        continue

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

            total_spending = sum(s["grand_total"] or 0 for s in filtered)
            total_tax = sum(s["tax"] or 0 for s in filtered)
            total_tip = sum(s["tip"] or 0 for s in filtered)
            receipts_with_totals = sum(1 for s in filtered if s["grand_total"])

            # Auto-fetch a few sample receipts
            fetched_count = 0
            for summary in filtered[:5]:
                img_id = summary.get("image_id")
                rcpt_id = summary.get("receipt_id")
                if img_id and rcpt_id is not None:
                    details = _fetch_receipt_details(img_id, rcpt_id)
                    if details:
                        fetched_count += 1

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
        aggregate_amounts,
        list_merchants,
        get_receipts_by_merchant,
        search_product_lines,
        get_receipt_summaries,
        list_categories,
    ], state_holder


# System prompt for ReAct workflow
SYSTEM_PROMPT = """You are a receipt analysis assistant.

## How This Works

You search for receipt information using the available tools. When you have
enough information to answer the question, simply respond with your findings.
DO NOT call any more tools - just write your answer as a normal response.

A separate step will format your answer with supporting receipt details.

## Available Tools

### Search Tools
- **search_receipts(query, search_type, limit)** - Find receipts
  - search_type="text": Exact match (e.g., "COFFEE", "MILK")
  - search_type="semantic": Meaning similarity (e.g., "coffee drinks")

- **semantic_search(query, limit)** - Embedding-based similarity search
  - Best for natural language queries and finding related items

- **search_product_lines(query, search_type, limit)** - Find products with prices
  - Returns line items with extracted prices
  - Use for "how much did I spend on X?" questions

### Retrieval Tools
- **get_receipt(image_id, receipt_id)** - Get full receipt details
  - Shows formatted text with labels like [LINE_TOTAL], [TAX], [GRAND_TOTAL]

### Aggregation Tools
- **aggregate_amounts(label_type, filter_text)** - Sum amounts across retrieved receipts
- **get_receipt_summaries(merchant_filter, category_filter, start_date, end_date)** - Pre-computed totals
  - Fast for merchant/category/date aggregation

### Discovery Tools
- **list_merchants()** - List all merchants with receipt counts
- **get_receipts_by_merchant(merchant_name)** - Get receipts for a specific merchant
- **list_categories()** - List merchant categories

## Search Strategy

**For category queries** (coffee, dairy, snacks, etc.):
- Use BOTH text AND semantic search for complete coverage
- Text search catches exact matches: "COFFEE", "FRENCH ROAST"
- Semantic search catches related items: cappuccino, latte, espresso
- You can call multiple tools in one response - they run in parallel

**For specific products**:
- Use text search with exact product name

**For merchant/date aggregation**:
- Use get_receipt_summaries (pre-computed, fast)

## When to Stop

Stop calling tools and write your answer when:
- You have found the relevant receipts/items
- You have calculated any totals needed
- You have enough information to answer the question

Just respond naturally with your findings. Include:
- The answer to the question
- Total amounts for "how much" questions
- Key items/receipts that support your answer

## Example

User: "How much did I spend on coffee?"

You would:
1. Call search_product_lines("COFFEE", "text") AND search_product_lines("coffee drinks", "semantic") in parallel
2. Review results, filter false positives, sum prices
3. Write your answer: "You spent $89.32 on coffee across 12 items from 10 receipts. This includes $45.99 in grocery store coffee and $43.33 in café purchases."

The system will then format this with the supporting receipt details.
"""
