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
from receipt_embeddings.backend import vector_search_client
from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
from receipt_embeddings.section_labels import (
    NON_ITEM_SECTION_LABELS,
    non_item_section_filter,
)
from receipt_embeddings.service_limits import LINE_INDEX, MAX_SEARCH_RESULTS
from receipt_embeddings.vector_client import VectorSearchClient

logger = logging.getLogger(__name__)


def _chroma_mode_unavailable(search_type: str, query: str) -> dict:
    """Structured result for retired Chroma-only search modes.

    On the dynamodb backend there is no Chroma client; the label /
    label_lines / text modes have no DynamoDB implementation, so they
    answer with a clear signal instead of raising.
    """
    return {
        "search_type": search_type,
        "query": query,
        "error": (
            f"search_type '{search_type}' is unavailable on the "
            "dynamodb backend (Chroma retired); use 'semantic' or the "
            "DynamoDB-backed tools instead"
        ),
        "total_matches": 0,
        "unique_receipts": 0,
        "results": [],
    }


# Every semantic n_results is trimmed to the 100-result SearchVectors cap
# (MAX_SEARCH_RESULTS). This deliberately cuts the old Chroma depth of up
# to 300 (spec §3.5 "Top-100 cap check"); Chroma accepts the smaller ask
# unchanged, so both backends stay within one call.


# ==============================================================================
# OCR Outlier Filtering
# ==============================================================================

# A dropped decimal point or a run-together digit turns "12.99" into
# "1299.00" or worse, and a single such misread swamps an aggregate. These
# ceilings sit well above any plausible value for the receipts in this
# dataset, so anything past them is a parse artifact rather than a purchase.
OCR_MAX_LINE_ITEM_AMOUNT = 10_000.0
OCR_MAX_RECEIPT_TOTAL = 50_000.0

# Secondary relative rule: an amount can be under the ceiling and still be
# obvious garbage next to its peers. Both conditions must hold, and only
# once there are enough samples for the median to mean anything.
OCR_OUTLIER_MEDIAN_RATIO = 100.0
OCR_OUTLIER_MIN_ABSOLUTE = 5_000.0
OCR_OUTLIER_MIN_SAMPLES = 8


def _coerce_amount(value: Any) -> Optional[float]:
    """Return value as a float, or None when it isn't a usable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def partition_ocr_outliers(
    records: list[dict],
    amount_key: str = "amount",
    ceiling: float = OCR_MAX_LINE_ITEM_AMOUNT,
) -> tuple[list[dict], list[dict]]:
    """Split records into (kept, outliers) by their amount field.

    Records with a missing or non-numeric amount are always kept — this
    filter targets OCR misreads, not incomplete data. Negative amounts are
    kept too, since discounts and refunds are legitimate.

    Args:
        records: Dicts carrying an amount under ``amount_key``.
        amount_key: Field holding the dollar value.
        ceiling: Absolute cutoff above which an amount is a misread.

    Returns:
        (kept, outliers) preserving the input order within each list.
    """
    amounts = [
        amount
        for record in records
        if (amount := _coerce_amount(record.get(amount_key))) is not None
        and amount > 0
    ]

    relative_threshold: Optional[float] = None
    if len(amounts) >= OCR_OUTLIER_MIN_SAMPLES:
        median = statistics.median(amounts)
        if median > 0:
            relative_threshold = max(
                median * OCR_OUTLIER_MEDIAN_RATIO, OCR_OUTLIER_MIN_ABSOLUTE
            )

    kept: list[dict] = []
    outliers: list[dict] = []
    for record in records:
        amount = _coerce_amount(record.get(amount_key))
        is_outlier = amount is not None and (
            amount > ceiling
            or (relative_threshold is not None and amount > relative_threshold)
        )
        (outliers if is_outlier else kept).append(record)

    if outliers:
        logger.info(
            "Dropped %d OCR outlier(s) from %d records (ceiling=%.2f)",
            len(outliers),
            len(records),
            ceiling,
        )

    return kept, outliers


def summarize_ocr_outliers(outliers: list[dict]) -> list[dict]:
    """Describe dropped outliers compactly for the LLM."""
    return [
        {
            "image_id": o.get("image_id"),
            "receipt_id": o.get("receipt_id"),
            "merchant": o.get("merchant") or o.get("merchant_name"),
            "amount": o.get("amount") or o.get("grand_total"),
            "reason": "Amount is implausibly large; likely an OCR misread",
        }
        for o in outliers[:10]
    ]


def create_qa_tools(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    *,
    vector_client: Optional[VectorSearchClient] = None,
) -> tuple[list, dict]:
    """Create tools for QA agent.

    Args:
        dynamo_client: DynamoDB client for receipt data
        chroma_client: ChromaDB client for similarity search
        embed_fn: Function to generate embeddings for semantic search
        vector_client: Optional injected similarity backend; defaults to
            the ``VECTOR_BACKEND`` selection (chroma unless set)

    Returns:
        (tools, state_holder) - List of tools and state dict for tracking
    """
    _embed_fn = embed_fn

    # Semantic retrieval goes through the shared VectorSearchClient seam
    # (VECTOR_BACKEND=chroma|dynamodb, default chroma). Resolution is lazy
    # and cached so the default path builds no AWS client, and any failure
    # degrades every semantic mode to empty results instead of hard-failing
    # the QA agent — search is its only discovery surface.
    _vector_client_cache: dict[str, Optional[VectorSearchClient]] = {}

    def _resolve_vector_client() -> Optional[VectorSearchClient]:
        if "client" not in _vector_client_cache:
            try:
                # Thread the session's configured Dynamo table (and its
                # low-level boto3 client) through, so a dynamodb backend
                # targets the SAME table as every other tool instead of
                # backend.py's environment fallback (E3 review P1-3).
                _vector_client_cache["client"] = vector_search_client(
                    chroma_client,
                    vector_client=vector_client,
                    dynamodb_client=getattr(dynamo_client, "_client", None),
                    table_name=getattr(dynamo_client, "table_name", None),
                )
            except Exception as exc:  # noqa: BLE001 - degrade, never fail
                logger.error("Vector search backend unavailable: %s", exc)
                _vector_client_cache["client"] = None
        return _vector_client_cache["client"]

    def _search_lines(
        query_embedding: list[float], top_k: int
    ) -> Optional[list]:
        """Line-index neighbors, or None when retrieval is degraded."""
        client = _resolve_vector_client()
        if client is None:
            return None
        try:
            return client.search(
                query_embedding,
                index=LINE_INDEX,
                top_k=max(1, min(top_k, MAX_SEARCH_RESULTS)),
            )
        except Exception as exc:  # noqa: BLE001 - throttle/missing index
            logger.error("Vector search failed, degrading to empty: %s", exc)
            return None

    # State holder tracks searches and retrieved receipts
    # This helps the agent know what it's already searched/fetched
    state_holder: dict[str, Any] = {
        "searches": [],  # [{query, type, result_count}, ...]
        "retrieved_receipts": [],  # Full receipt details for synthesize
        "summary_receipts": [],  # Lightweight summaries from get_receipt_summaries
        "_summary_keys": set(),  # Dedup keys for summary_receipts
        "fetched_receipt_keys": set(),  # (image_id, receipt_id) to avoid re-fetching
        "aggregates": [],  # Pre-computed aggregates from get_receipt_summaries
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

            # Extract amounts with line context. parse_receipt_amount
            # accepts the accounting negatives return receipts print
            # ("$16.25-" trailing minus) that a bare float() rejects,
            # and TIP is a money label like the others.
            from receipt_dynamo.amounts import parse_receipt_amount

            amounts = []
            currency_labels = [
                "TAX",
                "SUBTOTAL",
                "GRAND_TOTAL",
                "LINE_TOTAL",
                "UNIT_PRICE",
                "TIP",
            ]
            for line_idx, line_words in words_by_line.items():
                for w in line_words:
                    if w["label"] in currency_labels:
                        amount = parse_receipt_amount(w["text"])
                        if amount is not None:
                            amounts.append(
                                {
                                    "label": w["label"],
                                    "text": w["text"],
                                    "amount": amount,
                                    "line_idx": line_idx,
                                    "word_id": w["word_id"],
                                }
                            )

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
                if chroma_client is None:
                    _track_search(query, search_type, 0)
                    return _chroma_mode_unavailable("label", query)
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
                if chroma_client is None:
                    _track_search(query, search_type, 0)
                    return _chroma_mode_unavailable("label_lines", query)
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
                query_embeddings = _embed_fn([query])
                if not query_embeddings or not query_embeddings[0]:
                    return {
                        "error": "Failed to generate query embedding",
                        "results": [],
                    }

                neighbors = _search_lines(
                    query_embeddings[0], min(limit * 2, MAX_SEARCH_RESULTS)
                )
                if neighbors is None:
                    _track_search(query, search_type, 0)
                    return {
                        "search_type": "semantic",
                        "query": query,
                        "total_matches": 0,
                        "unique_receipts": 0,
                        "results": [],
                        "note": (
                            "Vector search is unavailable; reason logged. "
                            "Try text search instead."
                        ),
                    }

                for neighbor in neighbors:
                    meta = neighbor.metadata
                    receipt_key = (
                        meta.get("image_id"),
                        meta.get("receipt_id"),
                    )
                    if receipt_key not in unique_receipts:
                        unique_receipts[receipt_key] = {
                            "image_id": meta.get("image_id"),
                            "receipt_id": meta.get("receipt_id"),
                            "matched_row": str(meta.get("text", ""))[:100],
                            "similarity_distance": neighbor.distance,
                        }

                search_result = {
                    "search_type": "semantic",
                    "query": query,
                    "total_matches": len(neighbors),
                    "unique_receipts": len(unique_receipts),
                    "results": list(unique_receipts.values())[:limit],
                }

            else:
                # Default: text search
                if chroma_client is None:
                    _track_search(query, "text", 0)
                    return _chroma_mode_unavailable("text", query)
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
            query_embeddings = _embed_fn([query])
            if not query_embeddings or not query_embeddings[0]:
                return {
                    "error": "Failed to generate query embedding",
                    "results": [],
                    "suggestion": "Try using search_receipts with text search instead",
                }

            neighbors = _search_lines(
                query_embeddings[0], min(limit * 3, MAX_SEARCH_RESULTS)
            )
            if neighbors is None:
                _track_search(query, "semantic", 0)
                return {
                    "search_type": "semantic",
                    "query": query,
                    "total_matches": 0,
                    "min_similarity_used": min_similarity,
                    "results": [],
                    "suggestions": [
                        "Vector search is unavailable; use search_receipts "
                        "with text search instead"
                    ],
                }

            unique_receipts: dict[tuple, dict] = {}
            for neighbor in neighbors:
                meta = neighbor.metadata
                receipt_key = (
                    meta.get("image_id"),
                    meta.get("receipt_id"),
                )
                similarity = max(0.0, 1.0 - neighbor.distance)

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
                        "matched_text": str(meta.get("text", ""))[:150],
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

        # Drop OCR misreads before summing — one bad parse is enough to
        # make the reported total meaningless.
        ceiling = (
            OCR_MAX_RECEIPT_TOTAL
            if label_type in ("GRAND_TOTAL", "SUBTOTAL")
            else OCR_MAX_LINE_ITEM_AMOUNT
        )
        breakdown, outliers = partition_ocr_outliers(
            breakdown, ceiling=ceiling
        )

        total = sum(item["amount"] for item in breakdown)

        result = {
            "label_type": label_type,
            "filter_text": filter_text,
            "total": round(total, 2),
            "count": len(breakdown),
            "breakdown": breakdown,
        }
        if outliers:
            result["excluded_outliers"] = summarize_ocr_outliers(outliers)
            result["excluded_outlier_count"] = len(outliers)
        return result

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

            def extract_price(text: str) -> Optional[float]:
                matches = re.findall(r"\d+\.\d{2}", text)
                if matches:
                    return float(matches[-1])
                return None

            if search_type == "semantic":
                query_embeddings = _embed_fn([query])
                if not query_embeddings or not query_embeddings[0]:
                    return {"error": "Failed to generate embedding"}

                neighbors = _search_lines(
                    query_embeddings[0], min(limit * 3, MAX_SEARCH_RESULTS)
                )
                if neighbors is None:
                    _track_search(query, "semantic", 0)
                    return {
                        "query": query,
                        "search_type": "semantic",
                        "total_matches": 0,
                        "items": [],
                        "note": (
                            "Vector search is unavailable; reason logged. "
                            "Try text search instead."
                        ),
                    }

                if not neighbors:
                    _track_search(query, "semantic", 0)
                    return {
                        "query": query,
                        "search_type": "semantic",
                        "total_matches": 0,
                        "items": [],
                    }

                items = []
                seen = set()
                # label_LINE_TOTAL is Chroma line metadata; Dynamo line
                # items never carry it, so under the Dynamo backend the
                # flag is honestly "unknown" rather than a false False
                # (E3 review P2-5).
                label_flags_available = not isinstance(
                    _resolve_vector_client(), DynamoVectorSearchClient
                )
                # Chroma pre-filtered non-item sections inside the ANN
                # query ($nin); the seam takes equality filters only, so
                # the same exclusion is applied after retrieval. Rows with
                # no section label stay, like non_item_section_filter().
                non_item_sections = set(NON_ITEM_SECTION_LABELS)

                for neighbor in neighbors:
                    meta = neighbor.metadata
                    section = meta.get("section_label") or meta.get(
                        "section_type"
                    )
                    if section in non_item_sections:
                        continue
                    text = str(meta.get("text", ""))
                    image_id = meta.get("image_id")
                    receipt_id = meta.get("receipt_id")

                    item_key = (image_id, receipt_id, text)
                    if item_key in seen:
                        continue
                    seen.add(item_key)

                    similarity = max(0.0, 1.0 - neighbor.distance)

                    if similarity < 0.25:
                        continue

                    items.append(
                        {
                            "text": text,
                            "price": extract_price(text),
                            "similarity": round(similarity, 3),
                            "has_price_label": (
                                meta.get("label_LINE_TOTAL", False)
                                if label_flags_available
                                else "unknown"
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

                result = {
                    "query": query,
                    "search_type": "semantic",
                    "total_matches": len(neighbors),
                    "unique_items": len(items),
                    "items": items,
                    "auto_fetched": fetched_count,
                    "note": "Review items for relevance before summing prices.",
                }
                # A summable total next to nothing-but-noise invites the
                # model to report it. When even the best hit is weak, the
                # matches are almost certainly irrelevant — say so instead
                # of offering a number.
                max_sim = max(
                    (i.get("similarity", 0) for i in items), default=0
                )
                if max_sim >= 0.5:
                    result["raw_total"] = round(total, 2)
                else:
                    result["raw_total"] = None
                    result["note"] = (
                        f"All matches are weak (best similarity "
                        f"{max_sim:.2f}); they are likely irrelevant. "
                        "Do not sum or cite these prices."
                    )
                return result

            else:
                # Text search (direct Chroma substring scan; no DynamoDB
                # rewrite — unavailable once Chroma is retired)
                if chroma_client is None:
                    _track_search(query, "text", 0)
                    return _chroma_mode_unavailable("text", query)
                lines_collection = chroma_client.get_collection("lines")
                results = lines_collection.get(
                    where_document={"$contains": query.upper()},
                    where=non_item_section_filter(),
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
            undated_excluded = 0
            undated_spending = 0.0
            uncategorized_excluded = 0
            uncategorized_spending = 0.0
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
                    # A third of receipts have no category at all; count
                    # them out loud so percentages keep an honest
                    # denominator instead of silently vanishing.
                    if not merchant_category and not (
                        place and place.merchant_types
                    ):
                        uncategorized_excluded += 1
                        uncategorized_spending += record.grand_total or 0
                        continue
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

                # A date filter must not pass receipts with no date at
                # all — asserting "spent $X in July" while citing undated
                # receipts was the largest wrong-number source in the
                # 2026-07-29 scorecard. They are counted and reported
                # separately instead of silently included.
                if (start_dt or end_dt) and not record.date:
                    undated_excluded += 1
                    undated_spending += record.grand_total or 0
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

            # Drop receipts whose totals are OCR misreads before they can
            # skew the aggregate or reach the synthesizer as evidence.
            filtered, outliers = partition_ocr_outliers(
                filtered,
                amount_key="grand_total",
                ceiling=OCR_MAX_RECEIPT_TOTAL,
            )

            # Store all summary dicts for the shape node
            for s in filtered:
                key = (s.get("image_id"), s.get("receipt_id"))
                if key not in state_holder["_summary_keys"]:
                    state_holder["_summary_keys"].add(key)
                    state_holder["summary_receipts"].append(s)

            total_spending = sum(s["grand_total"] or 0 for s in filtered)
            total_tax = sum(s["tax"] or 0 for s in filtered)
            total_tip = sum(s["tip"] or 0 for s in filtered)
            receipts_with_totals = sum(1 for s in filtered if s["grand_total"])

            # Store aggregates for synthesizer
            state_holder["aggregates"].append(
                {
                    "source": (
                        f"get_receipt_summaries("
                        f"merchant={merchant_filter}, "
                        f"category={category_filter}, "
                        f"start={start_date}, "
                        f"end={end_date})"
                    ),
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
                    "excluded_outlier_count": len(outliers),
                    "undated_excluded": undated_excluded,
                    "uncategorized_excluded": uncategorized_excluded,
                }
            )

            # Auto-fetch a few sample receipts
            fetched_count = 0
            for summary in filtered[:5]:
                img_id = summary.get("image_id")
                rcpt_id = summary.get("receipt_id")
                if img_id and rcpt_id is not None:
                    details = _fetch_receipt_details(img_id, rcpt_id)
                    if details:
                        fetched_count += 1

            result = {
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
            if undated_excluded:
                result["undated_excluded"] = {
                    "count": undated_excluded,
                    "total_spending": round(undated_spending, 2),
                    "note": (
                        "Receipts with no date cannot satisfy a date "
                        "filter and are NOT included above. State this "
                        "coverage gap when answering."
                    ),
                }
            if uncategorized_excluded:
                result["uncategorized_excluded"] = {
                    "count": uncategorized_excluded,
                    "total_spending": round(uncategorized_spending, 2),
                    "note": (
                        "Receipts with no category are NOT included "
                        "above. Include this bucket in any percentage "
                        "or breakdown denominator."
                    ),
                }
            if outliers:
                result["excluded_outliers"] = summarize_ocr_outliers(outliers)
                result["excluded_outlier_count"] = len(outliers)
            return result

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
            categorized = 0
            last_key = None

            while True:
                places, last_key = dynamo_client.list_receipt_places(
                    limit=1000,
                    last_evaluated_key=last_key,
                )

                for place in places:
                    if place.merchant_category:
                        category_counts[place.merchant_category] += 1
                        categorized += 1

                if last_key is None:
                    break

            # Receipts without a category hold a third of all spend; a
            # breakdown that omits them reports percentages against a
            # fictional denominator.
            total_receipts = 0
            last_key = None
            while True:
                records, last_key = dynamo_client.list_receipt_summaries(
                    limit=1000,
                    last_evaluated_key=last_key,
                )
                total_receipts += len(records)
                if last_key is None:
                    break

            sorted_categories = sorted(
                category_counts.items(),
                key=lambda x: x[1],
                reverse=True,
            )

            return {
                "total_categories": len(sorted_categories),
                "total_receipts": total_receipts,
                "uncategorized_receipts": max(total_receipts - categorized, 0),
                "note": (
                    "Uncategorized receipts match NO category filter; "
                    "include them in any breakdown denominator."
                ),
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

**For merchant totals** ("total at Costco", "gas stations"):
- Merchant names have case and suffix variants ("Speedway"/"SPEEDWAY",
  three CVS spellings). Call list_merchants() FIRST and aggregate over
  EVERY variant that is the same real merchant — a single-pass search
  under one spelling badly undercounts.
- For merchant-type questions (gas stations, pharmacies), enumerate the
  merchants of that type from list_merchants(), then aggregate each.

**Partial data is not a refusal.** Many receipts have no date or no
category. If a question needs dates or categories, answer over the
receipts that HAVE them and state the coverage (e.g. "among the 680
dated receipts..."). Tool results report excluded undated/uncategorized
buckets — cite them. Never answer "cannot be determined" when a covered
subset supports a direct answer, and never assert a date range that the
cited receipts do not actually carry.

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
