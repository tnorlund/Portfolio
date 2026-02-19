"""Neo4j-backed QA tools for ReAct workflow.

Provides the same 9-tool interface as search.py, but routes 7 tools
through Neo4j Cypher queries instead of DynamoDB scans + ChromaDB.

Tools moved to Neo4j:
1. list_merchants       — Cypher aggregation
2. list_categories      — Cypher aggregation
3. get_receipts_by_merchant — Cypher traversal
4. get_receipt_summaries — Cypher with filters
5. search_receipts (text) — Full-text index
6. aggregate_amounts    — Cypher SUM with filters
7. search_product_lines — Full-text index + prices

Tools staying in existing backends:
8. get_receipt          — DynamoDB (word-level OCR detail)
9. semantic_search      — ChromaDB (embedding similarity)
"""

import logging
import re
import statistics
from typing import Any, Callable, Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def create_neo4j_qa_tools(
    neo4j_client: Any,
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
) -> tuple[list, dict]:
    """Create tools for QA agent backed by Neo4j.

    7 tools use Neo4j for structured queries. get_receipt stays
    in DynamoDB for word-level detail. semantic_search stays in
    ChromaDB for embedding similarity.

    Args:
        neo4j_client: ReceiptGraphClient instance.
        dynamo_client: DynamoDB client for get_receipt.
        chroma_client: ChromaDB client for semantic_search.
        embed_fn: Embedding function for semantic_search.

    Returns:
        (tools, state_holder) — list of tools and state dict.
    """
    _embed_fn = embed_fn

    state_holder: dict[str, Any] = {
        "searches": [],
        "retrieved_receipts": [],
        "summary_receipts": [],
        "_summary_keys": set(),
        "fetched_receipt_keys": set(),
        "aggregates": [],
    }

    # ------------------------------------------------------------------
    # Helper: fetch receipt details from DynamoDB (unchanged)
    # ------------------------------------------------------------------

    def _get_effective_label(
        labels: list,
        line_id: int,
        word_id: int,
    ) -> Optional[str]:
        """Get effective label for a word."""
        matching = [
            lb
            for lb in labels
            if lb.line_id == line_id and lb.word_id == word_id
        ]
        if not matching:
            return None
        valid = [
            lb
            for lb in matching
            if lb.validation_status == "VALID"
        ]
        if valid:
            valid.sort(
                key=lambda lb: str(lb.timestamp_added or ""),
                reverse=True,
            )
            return valid[0].label
        matching.sort(
            key=lambda lb: str(lb.timestamp_added or ""),
            reverse=True,
        )
        return matching[0].label

    def _fetch_receipt_details(
        image_id: str, receipt_id: int
    ) -> Optional[dict]:
        """Fetch receipt details from DynamoDB."""
        key = (image_id, receipt_id)
        if key in state_holder["fetched_receipt_keys"]:
            for r in state_holder["retrieved_receipts"]:
                if (
                    r.get("image_id") == image_id
                    and r.get("receipt_id") == receipt_id
                ):
                    return r
            return None

        try:
            details = dynamo_client.get_receipt_details(
                image_id, receipt_id
            )
            merchant = "Unknown"
            if details.place:
                merchant = (
                    details.place.merchant_name or "Unknown"
                )

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
                state_holder["retrieved_receipts"].append(
                    result
                )
                return result

            sorted_words = sorted(
                word_contexts, key=lambda w: -w["y"]
            )
            heights = [
                w["bounding_box"].get("height", 0.02)
                for w in sorted_words
                if w["bounding_box"]
                and w["bounding_box"].get("height")
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
                    current_y = sum(
                        c["y"] for c in current_line
                    ) / len(current_line)
                else:
                    current_line.sort(key=lambda c: c["x"])
                    visual_lines.append(current_line)
                    current_line = [w]
                    current_y = w["y"]

            current_line.sort(key=lambda c: c["x"])
            visual_lines.append(current_line)

            words_by_line: dict[int, list[dict]] = {}
            for line_idx, line_words in enumerate(
                visual_lines
            ):
                words_by_line[line_idx] = [
                    {
                        "text": w["text"],
                        "label": w["label"],
                        "word_id": w["word_id"],
                        "x": w["x"],
                    }
                    for w in line_words
                ]

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
                                w["text"]
                                .replace("$", "")
                                .replace(",", "")
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

            formatted_lines = []
            for line_idx, line_words in words_by_line.items():
                line_parts = []
                for w in line_words:
                    if w["label"]:
                        line_parts.append(
                            f"{w['text']}[{w['label']}]"
                        )
                    else:
                        line_parts.append(w["text"])
                formatted_lines.append(
                    f"Line {line_idx}: "
                    f"{' '.join(line_parts)}"
                )

            result = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant": merchant,
                "words_by_line": words_by_line,
                "amounts": amounts,
                "formatted_receipt": "\n".join(
                    formatted_lines
                ),
            }
            state_holder["fetched_receipt_keys"].add(key)
            state_holder["retrieved_receipts"].append(result)
            return result

        except Exception as e:
            logger.error(
                "Error fetching receipt %s:%s: %s",
                image_id,
                receipt_id,
                e,
            )
            return None

    def _track_search(
        query: str, search_type: str, result_count: int
    ) -> None:
        state_holder["searches"].append(
            {
                "query": query,
                "type": search_type,
                "result_count": result_count,
            }
        )

    # ------------------------------------------------------------------
    # Tool 1: list_merchants (Neo4j)
    # ------------------------------------------------------------------

    @tool
    def list_merchants() -> dict:
        """List all merchants with receipt counts.

        Returns merchants sorted by receipt count (descending).

        Returns:
            Dict with total_merchants and list of
            {merchant, receipt_count}
        """
        try:
            rows = neo4j_client.list_merchants()
            return {
                "total_merchants": len(rows),
                "merchants": [
                    {
                        "merchant": r["name"],
                        "receipt_count": r["receipt_count"],
                    }
                    for r in rows
                ],
            }
        except Exception as e:
            logger.error("list_merchants error: %s", e)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Tool 2: list_categories (Neo4j)
    # ------------------------------------------------------------------

    @tool
    def list_categories() -> dict:
        """List all merchant categories with receipt counts.

        Returns:
            Dict with categories like grocery_store, restaurant
        """
        try:
            rows = neo4j_client.list_categories()
            return {
                "total_categories": len(rows),
                "categories": [
                    {
                        "category": r["name"],
                        "receipt_count": r["receipt_count"],
                    }
                    for r in rows
                ],
            }
        except Exception as e:
            logger.error("list_categories error: %s", e)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Tool 3: get_receipts_by_merchant (Neo4j)
    # ------------------------------------------------------------------

    @tool
    def get_receipts_by_merchant(merchant_name: str) -> dict:
        """Get all receipt IDs for a specific merchant.

        Args:
            merchant_name: Exact merchant name

        Returns:
            Dict with merchant, count, and receipt pairs
        """
        try:
            return neo4j_client.get_receipts_by_merchant(
                merchant_name
            )
        except Exception as e:
            logger.error(
                "get_receipts_by_merchant error: %s", e
            )
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Tool 4: get_receipt_summaries (Neo4j)
    # ------------------------------------------------------------------

    @tool
    def get_receipt_summaries(
        merchant_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 1000,
    ) -> dict:
        """Get pre-computed summaries with totals, tax, dates.

        Use for aggregation questions like:
        - "Total spending at Costco?" (merchant_filter)
        - "How much on groceries?" (category_filter)
        - "Tax paid last month?" (date filters)

        Args:
            merchant_filter: Partial merchant name match
            category_filter: Category name (grocery_store, etc.)
            start_date: YYYY-MM-DD, inclusive
            end_date: YYYY-MM-DD, inclusive
            limit: Maximum receipts

        Returns:
            Dict with aggregates and individual summaries
        """
        try:
            result = neo4j_client.get_receipt_summaries(
                merchant_filter=merchant_filter,
                category_filter=category_filter,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )

            # Store summaries for the shape node
            for s in result.get("summaries", []):
                key = (
                    s.get("image_id"),
                    s.get("receipt_id"),
                )
                if key not in state_holder["_summary_keys"]:
                    state_holder["_summary_keys"].add(key)
                    state_holder["summary_receipts"].append(s)

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
                    "count": result["count"],
                    "total_spending": result[
                        "total_spending"
                    ],
                    "total_tax": result["total_tax"],
                    "total_tip": result["total_tip"],
                    "receipts_with_totals": result[
                        "receipts_with_totals"
                    ],
                    "average_receipt": result[
                        "average_receipt"
                    ],
                }
            )

            # Auto-fetch a few sample receipts
            fetched_count = 0
            for summary in result.get("summaries", [])[:5]:
                img_id = summary.get("image_id")
                rcpt_id = summary.get("receipt_id")
                if img_id and rcpt_id is not None:
                    details = _fetch_receipt_details(
                        img_id, rcpt_id
                    )
                    if details:
                        fetched_count += 1

            result["auto_fetched"] = fetched_count
            return result

        except Exception as e:
            logger.error(
                "get_receipt_summaries error: %s", e
            )
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Tool 5: search_receipts (Neo4j full-text + ChromaDB)
    # ------------------------------------------------------------------

    @tool
    def search_receipts(
        query: str,
        search_type: str = "text",
        limit: int = 20,
        auto_fetch: int = 5,
    ) -> dict:
        """Search for receipts by text, label, or semantic similarity.

        Args:
            query: What to search for
            search_type: "text" (full-text), "semantic", "label"
            limit: Maximum results
            auto_fetch: Top results to auto-fetch details for

        Returns:
            Dict with matching receipts
        """
        try:
            if search_type == "text":
                # Use Neo4j full-text index
                rows = neo4j_client.search_receipts_text(
                    query, limit=limit
                )
                unique_receipts: dict[tuple, dict] = {}
                for r in rows:
                    rkey = (
                        r["image_id"],
                        r["receipt_id"],
                    )
                    if rkey not in unique_receipts:
                        unique_receipts[rkey] = {
                            "image_id": r["image_id"],
                            "receipt_id": r["receipt_id"],
                            "matched_text": r.get(
                                "product_name", ""
                            ),
                            "merchant": r.get(
                                "merchant_name"
                            ),
                            "score": r.get("score"),
                        }

                search_result = {
                    "search_type": "text",
                    "query": query,
                    "total_matches": len(rows),
                    "unique_receipts": len(unique_receipts),
                    "results": list(
                        unique_receipts.values()
                    )[:limit],
                }

            elif search_type == "semantic":
                # Stays in ChromaDB
                lines_collection = (
                    chroma_client.get_collection("lines")
                )
                query_embeddings = _embed_fn([query])
                if (
                    not query_embeddings
                    or not query_embeddings[0]
                ):
                    return {
                        "error": "Failed to generate embedding",
                        "results": [],
                    }

                results = lines_collection.query(
                    query_embeddings=query_embeddings,
                    n_results=limit * 2,
                    include=["metadatas", "distances"],
                )

                unique_receipts = {}
                if (
                    results["ids"]
                    and results["ids"][0]
                ):
                    for idx, (id_, meta) in enumerate(
                        zip(
                            results["ids"][0],
                            results["metadatas"][0],
                        )
                    ):
                        rkey = (
                            meta.get("image_id"),
                            meta.get("receipt_id"),
                        )
                        distance = (
                            results["distances"][0][idx]
                            if results["distances"]
                            else None
                        )
                        if rkey not in unique_receipts:
                            unique_receipts[rkey] = {
                                "image_id": meta.get(
                                    "image_id"
                                ),
                                "receipt_id": meta.get(
                                    "receipt_id"
                                ),
                                "matched_row": meta.get(
                                    "text", ""
                                )[:100],
                                "similarity_distance": distance,
                            }

                search_result = {
                    "search_type": "semantic",
                    "query": query,
                    "total_matches": (
                        len(results["ids"][0])
                        if results["ids"]
                        else 0
                    ),
                    "unique_receipts": len(unique_receipts),
                    "results": list(
                        unique_receipts.values()
                    )[:limit],
                }

            elif search_type == "label":
                # Label search stays in ChromaDB
                words_collection = (
                    chroma_client.get_collection("words")
                )
                results = words_collection.get(
                    where={"label": query.upper()},
                    include=["metadatas"],
                )
                unique_receipts = {}
                for id_, meta in zip(
                    results["ids"], results["metadatas"]
                ):
                    rkey = (
                        meta.get("image_id"),
                        meta.get("receipt_id"),
                    )
                    if rkey not in unique_receipts:
                        unique_receipts[rkey] = {
                            "image_id": meta.get(
                                "image_id"
                            ),
                            "receipt_id": meta.get(
                                "receipt_id"
                            ),
                            "matched_text": meta.get(
                                "text"
                            ),
                            "matched_label": query.upper(),
                        }

                search_result = {
                    "search_type": "label",
                    "query": query,
                    "total_matches": len(results["ids"]),
                    "unique_receipts": len(unique_receipts),
                    "results": list(
                        unique_receipts.values()
                    )[:limit],
                }
            else:
                return {
                    "error": f"Unknown search_type: {search_type}",
                    "results": [],
                }

            _track_search(
                query, search_type, len(unique_receipts)
            )

            # Auto-fetch top N receipts
            if search_result and auto_fetch > 0:
                fetched_count = 0
                for info in search_result.get(
                    "results", []
                )[:auto_fetch]:
                    img = info.get("image_id")
                    rcpt = info.get("receipt_id")
                    if img and rcpt is not None:
                        details = _fetch_receipt_details(
                            img, rcpt
                        )
                        if details:
                            fetched_count += 1
                search_result["auto_fetched"] = (
                    fetched_count
                )

            return search_result

        except Exception as e:
            logger.error("search_receipts error: %s", e)
            return {"error": str(e), "results": []}

    # ------------------------------------------------------------------
    # Tool 6: aggregate_amounts (Neo4j)
    # ------------------------------------------------------------------

    @tool
    def aggregate_amounts(
        label_type: str = "LINE_TOTAL",
        filter_text: Optional[str] = None,
    ) -> dict:
        """Aggregate amounts across receipts.

        Uses Neo4j for line-item aggregation when no receipts are
        pre-fetched, falls back to in-memory for fetched receipts.

        Args:
            label_type: Amount type (LINE_TOTAL, TAX, etc.)
            filter_text: Product name filter

        Returns:
            Dict with total, count, and breakdown
        """
        try:
            # Use Neo4j for global aggregation
            result = neo4j_client.aggregate_amounts(
                filter_text=filter_text,
            )
            return {
                "label_type": label_type,
                "filter_text": filter_text,
                "total": result["total"],
                "count": result["count"],
                "breakdown": result["breakdown"],
            }
        except Exception as e:
            logger.error("aggregate_amounts error: %s", e)
            return {
                "error": str(e),
                "total": 0.0,
                "count": 0,
            }

    # ------------------------------------------------------------------
    # Tool 7: search_product_lines (Neo4j)
    # ------------------------------------------------------------------

    @tool
    def search_product_lines(
        query: str,
        search_type: str = "text",
        limit: int = 100,
    ) -> dict:
        """Search for product lines with prices.

        Use for "how much did I spend on X?" questions.

        Args:
            query: Product term or description
            search_type: "text" (full-text) or "semantic"
            limit: Maximum results

        Returns:
            Dict with items, prices, and totals
        """
        try:
            if search_type == "semantic":
                # Semantic stays in ChromaDB
                lines_collection = (
                    chroma_client.get_collection("lines")
                )
                query_embeddings = _embed_fn([query])
                if (
                    not query_embeddings
                    or not query_embeddings[0]
                ):
                    return {
                        "error": "Failed to generate embedding"
                    }

                results = lines_collection.query(
                    query_embeddings=query_embeddings,
                    n_results=limit * 3,
                    include=["metadatas", "distances"],
                )

                if (
                    not results["ids"]
                    or not results["ids"][0]
                ):
                    _track_search(query, "semantic", 0)
                    return {
                        "query": query,
                        "search_type": "semantic",
                        "total_matches": 0,
                        "items": [],
                    }

                def extract_price(
                    text: str,
                ) -> Optional[float]:
                    matches = re.findall(
                        r"\d+\.\d{2}", text
                    )
                    return (
                        float(matches[-1])
                        if matches
                        else None
                    )

                items = []
                seen: set[tuple] = set()
                for idx, (id_, meta) in enumerate(
                    zip(
                        results["ids"][0],
                        results["metadatas"][0],
                    )
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
                            "similarity": round(
                                similarity, 3
                            ),
                            "merchant": meta.get(
                                "merchant_name", "Unknown"
                            ),
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                        }
                    )

                items.sort(
                    key=lambda x: -x.get("similarity", 0)
                )
                items = items[:limit]
                total = sum(
                    i["price"]
                    for i in items
                    if i["price"] is not None
                )

                _track_search(query, "semantic", len(items))
                return {
                    "query": query,
                    "search_type": "semantic",
                    "total_matches": len(
                        results["ids"][0]
                    ),
                    "unique_items": len(items),
                    "items": items,
                    "raw_total": round(total, 2),
                }

            else:
                # Use Neo4j full-text index
                rows = (
                    neo4j_client.search_product_lines(
                        query, limit=limit
                    )
                )

                items = []
                seen = set()
                for r in rows:
                    item_key = (
                        r.get("image_id"),
                        r.get("receipt_id"),
                        r.get("text"),
                    )
                    if item_key in seen:
                        continue
                    seen.add(item_key)

                    items.append(
                        {
                            "text": r.get("text", ""),
                            "price": r.get("price"),
                            "has_price_label": r.get("price")
                            is not None,
                            "merchant": r.get(
                                "merchant", "Unknown"
                            ),
                            "image_id": r.get("image_id"),
                            "receipt_id": r.get(
                                "receipt_id"
                            ),
                            "score": r.get("score"),
                        }
                    )

                total = sum(
                    i["price"]
                    for i in items
                    if i["price"] is not None
                )

                # Auto-fetch unique receipts
                unique_keys: set[tuple] = set()
                for item in items[:10]:
                    ikey = (
                        item.get("image_id"),
                        item.get("receipt_id"),
                    )
                    if ikey[0] and ikey[1] is not None:
                        unique_keys.add(ikey)

                fetched_count = 0
                for img_id, rcpt_id in list(
                    unique_keys
                )[:5]:
                    details = _fetch_receipt_details(
                        img_id, rcpt_id
                    )
                    if details:
                        fetched_count += 1

                _track_search(query, "text", len(items))
                return {
                    "query": query,
                    "search_type": "text",
                    "total_matches": len(rows),
                    "unique_items": len(items),
                    "items": items,
                    "raw_total": round(total, 2),
                    "auto_fetched": fetched_count,
                }

        except Exception as e:
            logger.error(
                "search_product_lines error: %s", e
            )
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Tool 8: get_receipt (DynamoDB — unchanged)
    # ------------------------------------------------------------------

    @tool
    def get_receipt(image_id: str, receipt_id: int) -> dict:
        """Get full receipt with formatted text and labels.

        Shows each line with words and labels inline.
        Use for seeing items, prices, tax, and totals.

        Args:
            image_id: Image ID from search results
            receipt_id: Receipt ID from search results

        Returns:
            Dict with merchant, formatted receipt text, amounts
        """
        result = _fetch_receipt_details(
            image_id, receipt_id
        )
        if result is None:
            return {
                "error": (
                    f"Failed to fetch receipt "
                    f"{image_id}:{receipt_id}"
                )
            }
        return result

    # ------------------------------------------------------------------
    # Tool 9: semantic_search (ChromaDB — unchanged)
    # ------------------------------------------------------------------

    @tool
    def semantic_search(
        query: str,
        limit: int = 20,
        min_similarity: float = 0.3,
    ) -> dict:
        """Perform semantic similarity search using embeddings.

        Use when text search returns no results or for natural
        language queries about conceptually similar items.

        Args:
            query: Natural language query
            limit: Maximum results
            min_similarity: Minimum similarity threshold

        Returns:
            Dict with receipts sorted by similarity
        """
        try:
            lines_collection = chroma_client.get_collection(
                "lines"
            )
            query_embeddings = _embed_fn([query])
            if (
                not query_embeddings
                or not query_embeddings[0]
            ):
                return {
                    "error": "Failed to generate embedding",
                    "results": [],
                }

            results = lines_collection.query(
                query_embeddings=query_embeddings,
                n_results=limit * 3,
                include=[
                    "metadatas",
                    "distances",
                    "documents",
                ],
            )

            unique_receipts: dict[tuple, dict] = {}
            if results["ids"] and results["ids"][0]:
                for idx, (id_, meta) in enumerate(
                    zip(
                        results["ids"][0],
                        results["metadatas"][0],
                    )
                ):
                    rkey = (
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
                        rkey not in unique_receipts
                        or similarity
                        > unique_receipts[rkey].get(
                            "similarity", 0
                        )
                    ):
                        unique_receipts[rkey] = {
                            "image_id": meta.get(
                                "image_id"
                            ),
                            "receipt_id": meta.get(
                                "receipt_id"
                            ),
                            "matched_text": meta.get(
                                "text", ""
                            )[:150],
                            "similarity": round(
                                similarity, 3
                            ),
                            "confidence": (
                                "high"
                                if similarity > 0.7
                                else "medium"
                                if similarity > 0.5
                                else "low"
                            ),
                        }

            _track_search(
                query, "semantic", len(unique_receipts)
            )

            sorted_results = sorted(
                unique_receipts.values(),
                key=lambda x: x.get("similarity", 0),
                reverse=True,
            )[:limit]

            return {
                "search_type": "semantic",
                "query": query,
                "total_matches": len(sorted_results),
                "min_similarity_used": min_similarity,
                "results": sorted_results,
            }

        except Exception as e:
            logger.error("semantic_search error: %s", e)
            return {"error": str(e), "results": []}

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


# System prompt — same as original, tools behave identically
NEO4J_SYSTEM_PROMPT = """You are a receipt analysis assistant.

## How This Works

You search for receipt information using the available tools. When you have
enough information to answer the question, simply respond with your findings.
DO NOT call any more tools - just write your answer as a normal response.

A separate step will format your answer with supporting receipt details.

## Available Tools

### Search Tools
- **search_receipts(query, search_type, limit)** - Find receipts
  - search_type="text": Full-text product search (e.g., "COFFEE", "MILK")
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
- **aggregate_amounts(label_type, filter_text)** - Sum amounts across receipts
- **get_receipt_summaries(merchant_filter, category_filter, start_date, end_date)** - Pre-computed totals
  - Fast for merchant/category/date aggregation

### Discovery Tools
- **list_merchants()** - List all merchants with receipt counts
- **get_receipts_by_merchant(merchant_name)** - Get receipts for a specific merchant
- **list_categories()** - List merchant categories

## Search Strategy

**For category queries** (coffee, dairy, snacks, etc.):
- Use BOTH text AND semantic search for complete coverage

**For specific products**:
- Use text search with exact product name

**For merchant/date aggregation**:
- Use get_receipt_summaries (pre-computed, fast)

## When to Stop

Stop calling tools and write your answer when:
- You have found the relevant receipts/items
- You have calculated any totals needed
- You have enough information to answer the question
"""
