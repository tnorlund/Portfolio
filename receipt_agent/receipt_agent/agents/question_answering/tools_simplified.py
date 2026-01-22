"""Simplified QA tools - 3 tools instead of 6.

This module provides a streamlined tool set for the QA agent:
1. search_receipts - unified search (text or label)
2. get_receipt - full receipt with formatted text and inline labels
3. submit_answer - submit final answer

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
                    return {
                        "error": "Failed to generate query embedding",
                        "results": [],
                    }

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
                valid = [
                    lb for lb in history if lb.validation_status == "VALID"
                ]
                if valid:
                    valid.sort(
                        key=lambda lb: str(lb.timestamp_added), reverse=True
                    )
                    return valid[0].label
                return None

            # Build word contexts with positions
            word_contexts = []
            for word in details.words:
                centroid = word.calculate_centroid()
                label = get_valid_label(word.line_id, word.word_id)
                word_contexts.append(
                    {
                        "word": word,
                        "label": label,
                        "y": centroid[1],
                        "x": centroid[0],
                        "text": word.text,
                        "line_id": word.line_id,
                        "word_id": word.word_id,
                    }
                )

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
                max(0.01, statistics.median(heights) * 0.75)
                if heights
                else 0.015
            )

            visual_lines = []
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
                        amount = float(
                            w["text"].replace("$", "").replace(",", "")
                        )
                        amounts.append(
                            {
                                "label": w["label"],
                                "text": w["text"],
                                "amount": amount,
                            }
                        )
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
            logger.error("Error getting receipt: %s", e)
            return {"error": str(e)}

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

    return [search_receipts, get_receipt, submit_answer], state_holder


# Simplified system prompt
SIMPLIFIED_SYSTEM_PROMPT = """You are a receipt analysis assistant. Answer questions about receipts by searching and reading them.

## Tools

1. **search_receipts(query, search_type)** - Find receipts
   - search_type="text": Find receipts containing exact text (e.g., "COFFEE", "MILK")
   - search_type="label": Find receipts with specific word labels (e.g., "TAX", "GRAND_TOTAL")
   - search_type="label_lines": Find receipt rows with specific labels (faster for aggregated results)
   - search_type="semantic": Find receipts by meaning similarity (e.g., "coffee purchase")

2. **get_receipt(image_id, receipt_id)** - Get full receipt details
   - Returns formatted text showing every word with its label:
     ```
     Line 0: TRADER[MERCHANT_NAME] JOE'S[MERCHANT_NAME]
     Line 5: ORGANIC[PRODUCT_NAME] COFFEE[PRODUCT_NAME] 12.99[LINE_TOTAL]
     Line 8: TAX 0.84[TAX]
     Line 9: TOTAL 13.83[GRAND_TOTAL]
     ```
   - [LINE_TOTAL] = price for that line item
   - [TAX], [GRAND_TOTAL], [SUBTOTAL] = summary amounts

3. **submit_answer(answer, total_amount, receipt_count, evidence)** - Submit final answer
   - ALWAYS call this at the end
   - Include total_amount for "how much" questions
   - Include evidence showing which receipts support your answer

## Strategy

1. Search for relevant receipts
2. Get full receipt to see items and prices
3. Extract the information you need from the formatted text
4. Submit your answer with evidence

## Example: "How much did I spend on coffee?"

1. search_receipts("COFFEE", "text") → finds 2 receipts
2. get_receipt(image_id_1, receipt_id_1) → see formatted text, find COFFEE line with [LINE_TOTAL]
3. get_receipt(image_id_2, receipt_id_2) → same for second receipt
4. submit_answer("You spent $23.98 on coffee", total_amount=23.98, receipt_count=2, evidence=[...])

## Search Type Guide
- Use "text" for exact product names you know (COFFEE, MILK, etc.)
- Use "label" for finding specific labeled amounts (TAX, GRAND_TOTAL)
- Use "label_lines" to quickly find all rows with a label type
- Use "semantic" for natural language queries or when exact text is unknown

## Rules
- ALWAYS end with submit_answer
- Look for [LINE_TOTAL] on the same line as product names to find prices
- If you can't find information, say so in your answer
"""
