"""Search and query tools for question-answering agent.

These tools enable the agent to:
1. Search ChromaDB for relevant receipt lines/words
2. Fetch receipt details from DynamoDB
3. Find prices and amounts on receipts
4. Submit answers with evidence
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
    """Context for the current question being answered.

    This dataclass holds the question and accumulated results
    during the agent's search process.
    """

    question: str = ""
    search_results: list[dict] = field(default_factory=list)
    receipt_details: list[dict] = field(default_factory=list)


def _assemble_visual_lines(words, labels) -> list[list[dict]]:
    """Group words into visual lines by y-coordinate proximity.

    This is used to find prices on the same visual line as a product.
    Reused from the milk cache generator pattern.
    """
    if not words:
        return []

    # Build label lookup
    labels_by_word = defaultdict(list)
    for label in labels:
        key = (label.line_id, label.word_id)
        labels_by_word[key].append(label)

    def get_valid_label(line_id, word_id):
        history = labels_by_word.get((line_id, word_id), [])
        valid = [lbl for lbl in history if lbl.validation_status == "VALID"]
        if valid:
            valid.sort(key=lambda lbl: str(lbl.timestamp_added), reverse=True)
            return valid[0]
        return None

    # Build word contexts with centroids
    word_contexts = []
    for word in words:
        centroid = word.calculate_centroid()
        label = get_valid_label(word.line_id, word.word_id)
        word_contexts.append({
            "word": word,
            "label": label,
            "y": centroid[1],
            "x": centroid[0],
        })

    # Sort by y descending
    sorted_contexts = sorted(word_contexts, key=lambda c: -c["y"])

    # Calculate tolerance
    heights = [
        w["word"].bounding_box.get("height", 0.02)
        for w in sorted_contexts
        if w["word"].bounding_box.get("height")
    ]
    if heights:
        y_tolerance = max(0.01, statistics.median(heights) * 0.75)
    else:
        y_tolerance = 0.015

    # Group by y-proximity
    visual_lines = []
    current_words = [sorted_contexts[0]]
    current_y = sorted_contexts[0]["y"]

    for ctx in sorted_contexts[1:]:
        if abs(ctx["y"] - current_y) <= y_tolerance:
            current_words.append(ctx)
            current_y = sum(c["y"] for c in current_words) / len(current_words)
        else:
            current_words.sort(key=lambda c: c["x"])
            visual_lines.append(current_words)
            current_words = [ctx]
            current_y = ctx["y"]

    current_words.sort(key=lambda c: c["x"])
    visual_lines.append(current_words)

    return visual_lines


def _find_price_on_visual_line(
    target_line_id: int,
    words: list,
    labels: list,
) -> tuple[Optional[str], Optional[str]]:
    """Find LINE_TOTAL or UNIT_PRICE on the same visual line."""
    visual_lines = _assemble_visual_lines(words, labels)

    # Find visual line containing target
    target_visual_line = None
    for vl in visual_lines:
        for ctx in vl:
            if ctx["word"].line_id == target_line_id:
                target_visual_line = vl
                break
        if target_visual_line:
            break

    if not target_visual_line:
        return None, None

    # Look for price labels
    line_total = None
    unit_price = None

    for ctx in target_visual_line:
        if ctx["label"]:
            if ctx["label"].label == "LINE_TOTAL":
                line_total = ctx["word"].text
            elif ctx["label"].label == "UNIT_PRICE":
                unit_price = ctx["word"].text

    return line_total, unit_price


def _find_labeled_amounts(
    words: list,
    labels: list,
    target_labels: list[str],
) -> list[dict]:
    """Find all words with specific labels and their amounts.

    Args:
        words: List of receipt words
        labels: List of word labels
        target_labels: Labels to search for (e.g., ["TAX", "GRAND_TOTAL"])

    Returns:
        List of dicts with label, text, and parsed amount
    """
    results = []

    # Build label lookup
    labels_by_word = defaultdict(list)
    for label in labels:
        key = (label.line_id, label.word_id)
        labels_by_word[key].append(label)

    for word in words:
        key = (word.line_id, word.word_id)
        word_labels = labels_by_word.get(key, [])

        for lbl in word_labels:
            if lbl.label in target_labels and lbl.validation_status == "VALID":
                # Try to parse as amount
                text = word.text
                amount = None
                try:
                    amount = float(text.replace("$", "").replace(",", ""))
                except (ValueError, AttributeError):
                    pass

                results.append({
                    "label": lbl.label,
                    "text": text,
                    "amount": amount,
                    "line_id": word.line_id,
                    "word_id": word.word_id,
                })

    return results


def create_qa_tools(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
) -> tuple[list, dict]:
    """Create tools for the question-answering agent.

    Args:
        dynamo_client: DynamoDB client for receipt data
        chroma_client: ChromaDB client for similarity search
        embed_fn: Function to generate embeddings

    Returns:
        (tools, state_holder) - List of tools and state dict
    """
    state_holder: dict[str, Any] = {
        "context": None,
        "answer": None,
    }

    @tool
    def search_lines_by_text(
        search_text: str,
        exclude_terms: Optional[list[str]] = None,
        limit: int = 50,
    ) -> dict:
        """Search ChromaDB lines collection for lines containing specific text.

        Use this to find receipts containing specific products or text.

        Args:
            search_text: Text to search for (e.g., "COFFEE", "MILK", "TAX")
            exclude_terms: Optional terms to exclude from results
            limit: Maximum number of results to return

        Returns:
            Dict with matching lines and their metadata
        """
        try:
            # Get the lines collection
            lines_collection = chroma_client.get_collection("lines")

            # Search using where_document filter
            results = lines_collection.get(
                where_document={"$contains": search_text.upper()},
                include=["metadatas"],
            )

            # Filter results
            matching_lines = []
            exclude_upper = [t.upper() for t in (exclude_terms or [])]

            for id_, meta in zip(results["ids"], results["metadatas"]):
                text = meta.get("text", "")
                text_upper = text.upper()

                # Check exclusions
                if exclude_upper and any(t in text_upper for t in exclude_upper):
                    continue

                matching_lines.append({
                    "id": id_,
                    "text": text,
                    "image_id": meta.get("image_id"),
                    "receipt_id": meta.get("receipt_id"),
                    "line_id": meta.get("line_id"),
                })

                if len(matching_lines) >= limit:
                    break

            # Deduplicate by image_id
            unique_receipts = {}
            for line in matching_lines:
                img_id = line["image_id"]
                if img_id not in unique_receipts:
                    unique_receipts[img_id] = line

            return {
                "total_matches": len(matching_lines),
                "unique_receipts": len(unique_receipts),
                "results": list(unique_receipts.values())[:limit],
            }

        except Exception as e:
            logger.error("Error searching lines: %s", e)
            return {"error": str(e), "results": []}

    @tool
    def search_words_by_label(
        label: str,
        limit: int = 50,
    ) -> dict:
        """Search ChromaDB words collection for words with a specific label.

        Use this to find specific labeled items like TAX, GRAND_TOTAL, etc.

        Args:
            label: Label to search for (e.g., "TAX", "GRAND_TOTAL", "SUBTOTAL")
            limit: Maximum number of results to return

        Returns:
            Dict with matching words and their metadata
        """
        try:
            # Get the words collection
            words_collection = chroma_client.get_collection("words")

            # Search using metadata filter
            results = words_collection.get(
                where={"label": label},
                include=["metadatas"],
            )

            matching_words = []
            for id_, meta in zip(results["ids"], results["metadatas"]):
                matching_words.append({
                    "id": id_,
                    "text": meta.get("text", ""),
                    "label": meta.get("label"),
                    "image_id": meta.get("image_id"),
                    "receipt_id": meta.get("receipt_id"),
                    "line_id": meta.get("line_id"),
                    "word_id": meta.get("word_id"),
                })

                if len(matching_words) >= limit:
                    break

            return {
                "total_matches": len(matching_words),
                "results": matching_words[:limit],
            }

        except Exception as e:
            logger.error("Error searching words: %s", e)
            return {"error": str(e), "results": []}

    @tool
    def get_receipt_with_price(
        image_id: str,
        receipt_id: int,
        target_line_id: int,
    ) -> dict:
        """Get receipt details and find the price for a specific line.

        Use this after finding matching lines to get the price/amount.

        Args:
            image_id: The image ID
            receipt_id: The receipt ID
            target_line_id: The line ID to find the price for

        Returns:
            Dict with receipt details and price information
        """
        try:
            details = dynamo_client.get_receipt_details(image_id, receipt_id)

            # Find price on the same visual line
            line_total, unit_price = _find_price_on_visual_line(
                target_line_id,
                details.words,
                details.labels,
            )

            # Get merchant info
            merchant = "Unknown"
            if details.place:
                merchant = details.place.merchant_name

            # Find the target line text
            target_text = ""
            for line in details.lines:
                if line.line_id == target_line_id:
                    target_text = line.text
                    break

            price = line_total or unit_price
            amount = None
            if price:
                try:
                    amount = float(price.replace("$", "").replace(",", ""))
                except (ValueError, AttributeError):
                    pass

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant": merchant,
                "line_text": target_text,
                "line_total": line_total,
                "unit_price": unit_price,
                "amount": amount,
            }

        except Exception as e:
            logger.error("Error getting receipt details: %s", e)
            return {"error": str(e)}

    @tool
    def get_labeled_amounts(
        image_id: str,
        receipt_id: int,
        labels: list[str],
    ) -> dict:
        """Get all amounts with specific labels from a receipt.

        Use this to find TAX, SUBTOTAL, GRAND_TOTAL, etc.

        Args:
            image_id: The image ID
            receipt_id: The receipt ID
            labels: List of labels to search for

        Returns:
            Dict with found amounts by label
        """
        try:
            details = dynamo_client.get_receipt_details(image_id, receipt_id)

            # Find labeled amounts
            amounts = _find_labeled_amounts(
                details.words,
                details.labels,
                labels,
            )

            # Get merchant info
            merchant = "Unknown"
            if details.place:
                merchant = details.place.merchant_name

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant": merchant,
                "amounts": amounts,
            }

        except Exception as e:
            logger.error("Error getting labeled amounts: %s", e)
            return {"error": str(e)}

    @tool
    def submit_answer(
        answer: str,
        total_amount: Optional[float] = None,
        receipt_count: int = 0,
        evidence: Optional[list[dict]] = None,
    ) -> dict:
        """Submit the final answer to the question.

        ALWAYS call this at the end to provide the answer.

        Args:
            answer: Natural language answer to the question
            total_amount: Total dollar amount if applicable
            receipt_count: Number of receipts found
            evidence: List of evidence dicts supporting the answer

        Returns:
            Confirmation that the answer was submitted
        """
        state_holder["answer"] = {
            "answer": answer,
            "total_amount": total_amount,
            "receipt_count": receipt_count,
            "evidence": evidence or [],
        }

        logger.info("Answer submitted: %s", answer[:100])
        return {"status": "submitted", "answer": answer}

    tools = [
        search_lines_by_text,
        search_words_by_label,
        get_receipt_with_price,
        get_labeled_amounts,
        submit_answer,
    ]

    return tools, state_holder
