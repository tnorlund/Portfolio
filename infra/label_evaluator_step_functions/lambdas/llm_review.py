"""
LLM Review Lambda Handler - Batched Per-Merchant Processing

Reviews flagged label issues using Ollama LLM with ChromaDB for semantic
similarity search. Processes all issues for a merchant in a single invocation
for efficient ChromaDB and DynamoDB access.

Key Features:
- Batched processing per merchant (one Lambda invocation per merchant)
- Semantic similarity search using ChromaDB word embeddings
- Rich context including validation history with reasoning from DynamoDB
- Dynamic merchant scoping (same-merchant vs cross-merchant based on data)
- Full similarity distribution shown to LLM (no artificial limits)

Environment Variables:
- BATCH_BUCKET: S3 bucket for data and results
- CHROMADB_BUCKET: S3 bucket with ChromaDB snapshots
- DYNAMODB_TABLE_NAME: DynamoDB table for label history
- RECEIPT_AGENT_OLLAMA_API_KEY: Ollama Cloud API key
- RECEIPT_AGENT_OLLAMA_BASE_URL: Ollama API base URL
- RECEIPT_AGENT_OLLAMA_MODEL: Ollama model name
- LANGCHAIN_API_KEY: LangSmith API key
- LANGCHAIN_TRACING_V2: Enable tracing
- LANGCHAIN_PROJECT: LangSmith project name
"""

import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any, Optional

import boto3
from botocore.config import Config
from receipt_agent import (
    OllamaCircuitBreaker,
    OllamaRateLimitError,
    RateLimitedLLMInvoker,
)
from receipt_agent.agents.label_evaluator import apply_llm_decisions
from receipt_agent.constants import CORE_LABELS, CORE_LABELS_SET

from utils.tracing import flush_langsmith_traces

if TYPE_CHECKING:
    from evaluator_types import (
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


# =============================================================================
# S3 Utilities
# =============================================================================


def download_chromadb_snapshot(
    bucket: str, collection: str, cache_path: str
) -> str:
    """Download ChromaDB snapshot from S3 using atomic pointer pattern."""
    chroma_db_file = os.path.join(cache_path, "chroma.sqlite3")
    if os.path.exists(chroma_db_file):
        logger.info(f"ChromaDB already cached at {cache_path}")
        return cache_path

    logger.info(f"Downloading ChromaDB from s3://{bucket}/{collection}/")

    pointer_key = f"{collection}/snapshot/latest-pointer.txt"
    try:
        response = s3.get_object(Bucket=bucket, Key=pointer_key)
        timestamp = response["Body"].read().decode().strip()
        logger.info(f"Latest snapshot: {timestamp}")
    except Exception:
        logger.exception("Failed to get pointer")
        raise

    prefix = f"{collection}/snapshot/timestamped/{timestamp}/"
    paginator = s3.get_paginator("list_objects_v2")

    os.makedirs(cache_path, exist_ok=True)
    downloaded = 0

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            relative_path = key[len(prefix) :]
            if not relative_path or key.endswith(".snapshot_hash"):
                continue

            local_path = os.path.join(cache_path, relative_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            s3.download_file(bucket, key, local_path)
            downloaded += 1

    logger.info(f"Downloaded {downloaded} files to {cache_path}")
    return cache_path


def load_json_from_s3(bucket: str, key: str) -> dict[str, Any]:
    """Load JSON data from S3."""
    response = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def upload_json_to_s3(bucket: str, key: str, data: Any) -> None:
    """Upload JSON data to S3."""
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )


# =============================================================================
# Text Pattern Detection
# =============================================================================


def detect_text_pattern(text: str) -> Optional[str]:
    """Detect common text patterns for context."""
    text = text.strip()

    # Currency patterns
    if re.match(r"^\$?\d+\.\d{2}$", text):
        return "currency"
    if re.match(r"^\d+\.\d{2}$", text):
        return "decimal-number"

    # Date patterns
    if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", text):
        return "date-slash"
    if re.match(r"^\d{1,2}-\d{1,2}-\d{2,4}$", text):
        return "date-dash"

    # Time patterns
    if re.match(r"^\d{1,2}:\d{2}(:\d{2})?\s*(AM|PM|am|pm)?$", text):
        return "time"

    # Phone patterns
    if re.match(r"^\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$", text):
        return "phone-number"

    # Zip code
    if re.match(r"^\d{5}(-\d{4})?$", text):
        return "zip-code"

    # Pure digits
    if re.match(r"^\d+$", text):
        n = len(text)
        if n <= 2:
            return "1-2-digit-number"
        elif n <= 4:
            return "3-4-digit-number"
        elif n == 5:
            return "5-digit-number"
        else:
            return f"{n}-digit-number"

    # All caps
    if text.isupper() and len(text) > 2:
        return "all-caps"

    return None


def is_currency_amount(text: str) -> bool:
    """Check if text looks like a currency amount."""
    text = text.strip()
    # Match $X.XX, X.XX, or X.XX patterns (with optional $ and commas)
    return bool(re.match(r"^\$?\d{1,3}(,\d{3})*\.\d{2}$", text))


def parse_currency_value(text: str) -> Optional[float]:
    """Parse currency text to float value."""
    text = text.strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


# Currency-related labels that should be shown in receipt context
CURRENCY_LABELS = {
    "LINE_TOTAL",
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",
    "TENDER",
    "CHANGE",
    "DISCOUNT",
    "SAVINGS",
    "CASH_BACK",
    "REFUND",
    "UNIT_PRICE",
}


# =============================================================================
# Receipt Text Assembly
# =============================================================================


def _group_words_into_ocr_lines(
    words: list[dict],
) -> list[dict]:
    """
    Group words by line_id into OCR lines with computed geometry.

    Returns list of line dicts with:
    - line_id: OCR line ID
    - words: list of words in this line (sorted by x)
    - centroid_y: average Y of word centroids
    - top_y: max top_left Y (top of line)
    - bottom_y: min bottom_left Y (bottom of line)
    - min_x: leftmost X
    """
    lines_by_id: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        lines_by_id[w.get("line_id", 0)].append(w)

    ocr_lines = []
    for line_id, line_words in lines_by_id.items():
        # Sort words by X position
        line_words.sort(key=lambda w: w.get("top_left", {}).get("x", 0))

        # Compute geometry from word corners
        top_ys = []
        bottom_ys = []
        centroid_ys = []

        for w in line_words:
            tl = w.get("top_left", {})
            bl = w.get("bottom_left", {})
            if tl.get("y") is not None:
                top_ys.append(tl["y"])
            if bl.get("y") is not None:
                bottom_ys.append(bl["y"])
            # Centroid Y is average of top and bottom
            if tl.get("y") is not None and bl.get("y") is not None:
                centroid_ys.append((tl["y"] + bl["y"]) / 2)

        ocr_lines.append(
            {
                "line_id": line_id,
                "words": line_words,
                "centroid_y": (
                    sum(centroid_ys) / len(centroid_ys) if centroid_ys else 0
                ),
                "top_y": max(top_ys) if top_ys else 0,
                "bottom_y": min(bottom_ys) if bottom_ys else 0,
                "min_x": min(
                    w.get("top_left", {}).get("x", 0) for w in line_words
                ),
            }
        )

    return ocr_lines


def assemble_receipt_text(
    words: list[dict],
    labels: list[dict],
    highlight_words: Optional[list[tuple[int, int]]] = None,
    max_lines: int = 60,
) -> str:
    """
    Reassemble receipt text in reading order with labels.

    Uses the same logic as format_receipt_text_receipt_space:
    - Sort OCR lines by centroid Y descending (top first, Y=0 is bottom)
    - Merge lines whose centroid falls within previous line's vertical span
    - Sort words within each visual line by X (left to right)

    Args:
        words: List of word dicts with text, line_id, word_id, corners
        labels: List of label dicts with line_id, word_id, label, validation_status
        highlight_words: Optional list of (line_id, word_id) tuples to mark with []
        max_lines: Maximum visual lines to include (truncate middle if needed)

    Returns:
        Receipt text in reading order, with labels shown inline
    """
    # Debug: Log input types
    if words is None:
        logger.warning("assemble_receipt_text: words is None")
        return "(empty receipt - words is None)"
    if not isinstance(words, list):
        logger.warning(
            f"assemble_receipt_text: words is {type(words).__name__}, not list"
        )
        return f"(invalid receipt - words is {type(words).__name__})"
    if not words:
        return "(empty receipt)"

    # Build label lookup: (line_id, word_id) -> label info
    label_map: dict[tuple[int, int], dict] = {}
    for lbl in labels:
        key = (lbl.get("line_id"), lbl.get("word_id"))
        # Keep only VALID labels, or the most recent if no VALID
        if key not in label_map or lbl.get("validation_status") == "VALID":
            label_map[key] = lbl

    # Build highlight set
    highlight_set = set(highlight_words) if highlight_words else set()

    # Group words into OCR lines with geometry
    ocr_lines = _group_words_into_ocr_lines(words)
    if not ocr_lines:
        return "(empty receipt)"

    # Sort by centroid Y descending (top first, since Y=0 is bottom)
    ocr_lines.sort(key=lambda line: -line["centroid_y"])

    # Merge OCR lines into visual lines using vertical span logic
    # (same as format_receipt_text_receipt_space)
    # Track visual line geometry for proper merging after accumulation
    visual_lines: list[dict] = []  # Each has: words, top_y, bottom_y

    for ocr_line in ocr_lines:
        if visual_lines:
            prev = visual_lines[-1]
            centroid_y = ocr_line["centroid_y"]
            # Check if this line's centroid falls within prev visual line's span
            if prev["bottom_y"] < centroid_y < prev["top_y"]:
                # Merge with previous visual line
                prev["words"].extend(ocr_line["words"])
                # Update visual line geometry to encompass both
                prev["top_y"] = max(prev["top_y"], ocr_line["top_y"])
                prev["bottom_y"] = min(prev["bottom_y"], ocr_line["bottom_y"])
                continue

        # Start new visual line with this OCR line's geometry
        visual_lines.append(
            {
                "words": list(ocr_line["words"]),
                "top_y": ocr_line["top_y"],
                "bottom_y": ocr_line["bottom_y"],
            }
        )

    # Sort words within each visual line by X (left to right)
    for vl in visual_lines:
        vl["words"].sort(key=lambda w: w.get("top_left", {}).get("x", 0))

    # Truncate if too many lines (keep top and bottom, skip middle)
    if len(visual_lines) > max_lines:
        keep_top = max_lines // 2
        keep_bottom = max_lines - keep_top - 1
        omitted = len(visual_lines) - max_lines + 1
        placeholder = {
            "words": [
                {
                    "text": f"... ({omitted} lines omitted) ...",
                    "line_id": -1,
                    "word_id": -1,
                }
            ],
            "top_y": 0,
            "bottom_y": 0,
        }
        visual_lines = [
            *visual_lines[:keep_top],
            placeholder,
            *visual_lines[-keep_bottom:],
        ]

    # Format each visual line with labels
    formatted_lines = []
    for vl in visual_lines:
        line_words = vl["words"]
        line_parts = []
        line_labels = []

        for w in line_words:
            text = w.get("text", "")
            key = (w.get("line_id"), w.get("word_id"))

            # Mark highlighted words
            if key in highlight_set:
                text = f"[{text}]"

            line_parts.append(text)

            # Collect label if exists
            if key in label_map:
                lbl = label_map[key]
                label_name = lbl.get("label", "?")
                status = lbl.get("validation_status", "")
                if status == "INVALID":
                    line_labels.append(f"~~{label_name}~~")
                else:
                    line_labels.append(label_name)

        line_text = " ".join(line_parts)
        if line_labels:
            line_text += f"  ({', '.join(line_labels)})"

        formatted_lines.append(line_text)

    return "\n".join(formatted_lines)


def extract_receipt_currency_context(
    words: list[dict],
    labels: list[dict],
) -> list[dict]:
    """
    Extract all currency amounts from a receipt with their labels and context.

    Returns a list of dicts with:
    - amount: the numeric value
    - text: original text
    - label: current label (or None)
    - line_id: line number
    - word_id: word number
    - context: surrounding words on the same line
    """
    # Build label lookup
    label_map: dict[tuple[int, int], dict] = {}
    for lbl in labels:
        key = (lbl.get("line_id"), lbl.get("word_id"))
        label_map[key] = lbl

    # Group words by line for context
    lines: dict[int, list[dict]] = {}
    for word in words:
        line_id = word.get("line_id")
        if line_id not in lines:
            lines[line_id] = []
        lines[line_id].append(word)

    # Sort words within each line by x position
    for line_id in lines:
        lines[line_id].sort(
            key=lambda w: w.get("bounding_box", {}).get("x", 0)
        )

    # Extract currency amounts
    currency_items: list[dict] = []
    for word in words:
        text = word.get("text", "")
        if not is_currency_amount(text):
            continue

        amount = parse_currency_value(text)
        if amount is None:
            continue

        line_id = word.get("line_id")
        word_id = word.get("word_id")
        label_info = label_map.get((line_id, word_id))

        # Build context from same line
        line_words = lines.get(line_id, [])
        word_idx = next(
            (
                i
                for i, w in enumerate(line_words)
                if w.get("word_id") == word_id
            ),
            -1,
        )

        # Get words before this one on the same line
        context_before = []
        if word_idx > 0:
            for w in line_words[max(0, word_idx - 3) : word_idx]:
                context_before.append(w.get("text", ""))

        context = (
            " ".join(context_before) if context_before else "(start of line)"
        )

        currency_items.append(
            {
                "amount": amount,
                "text": text,
                "label": label_info.get("label") if label_info else None,
                "validation_status": (
                    label_info.get("validation_status") if label_info else None
                ),
                "line_id": line_id,
                "word_id": word_id,
                "context": context,
                "y_position": word.get("bounding_box", {}).get("y", 0.5),
            }
        )

    # Sort by position on receipt (top to bottom)
    currency_items.sort(key=lambda x: -x["y_position"])

    return currency_items


def format_currency_context_table(currency_items: list[dict]) -> str:
    """Format currency items as a markdown table for the LLM prompt.

    Note: We intentionally do NOT show validation_status here because we want
    the LLM to make an independent judgment based on mathematical relationships,
    not confirm/deny existing labels.
    """
    if not currency_items:
        return "No currency amounts found on this receipt."

    lines = ["| Amount | Current Label | Line | Preceding Text |"]
    lines.append("|--------|---------------|------|----------------|")

    for item in currency_items:
        amount = f"${item['amount']:.2f}"
        label = item["label"] or "(unlabeled)"
        line_id = item["line_id"]
        context = (
            item["context"][:25] + "..."
            if len(item["context"]) > 25
            else item["context"]
        )

        lines.append(f"| {amount} | {label} | {line_id} | {context} |")

    return "\n".join(lines)


def compute_currency_math_hints(currency_items: list[dict]) -> str:
    """
    Compute mathematical relationships between currency amounts.

    Returns hints like "LINE_TOTALs sum to $10.78" to help LLM reason.
    """
    hints = []

    # Group by label
    by_label: dict[str, list[float]] = {}
    for item in currency_items:
        label = item.get("label")
        if label:
            if label not in by_label:
                by_label[label] = []
            by_label[label].append(item["amount"])

    # Sum of LINE_TOTALs and UNIT_PRICEs (both represent item amounts)
    line_totals = by_label.get("LINE_TOTAL", [])
    unit_prices = by_label.get("UNIT_PRICE", [])
    item_amounts = line_totals + unit_prices

    if item_amounts:
        total = sum(item_amounts)
        label_desc = []
        if line_totals:
            label_desc.append(f"{len(line_totals)} LINE_TOTAL")
        if unit_prices:
            label_desc.append(f"{len(unit_prices)} UNIT_PRICE")
        hints.append(
            f"- Item amounts ({', '.join(label_desc)}): sum to ${total:.2f}"
        )

    # Check for GRAND_TOTAL match against item amounts
    grand_totals = by_label.get("GRAND_TOTAL", [])
    if grand_totals and item_amounts:
        items_sum = sum(item_amounts)
        for gt in set(grand_totals):
            if abs(gt - items_sum) < 0.01:
                hints.append(
                    f"- GRAND_TOTAL ${gt:.2f} equals sum of item amounts "
                    f"(${items_sum:.2f}) - mathematically consistent"
                )
            else:
                diff = gt - items_sum
                if diff > 0:
                    hints.append(
                        f"- GRAND_TOTAL ${gt:.2f} = items (${items_sum:.2f}) + "
                        f"${diff:.2f} (likely tax/fees)"
                    )
                else:
                    hints.append(
                        f"- GRAND_TOTAL ${gt:.2f} is ${abs(diff):.2f} less than "
                        f"items sum (${items_sum:.2f}) - possible discount"
                    )

    # Check for duplicate amounts
    amount_counts: dict[float, int] = {}
    for item in currency_items:
        amt = item["amount"]
        amount_counts[amt] = amount_counts.get(amt, 0) + 1

    duplicates = [(amt, cnt) for amt, cnt in amount_counts.items() if cnt > 1]
    if duplicates:
        for amt, cnt in duplicates:
            labels = [
                item["label"]
                for item in currency_items
                if item["amount"] == amt and item["label"]
            ]
            if labels:
                hints.append(
                    f"- ${amt:.2f} appears {cnt} times with labels: "
                    f"{', '.join(labels)}"
                )

    return "\n".join(hints) if hints else "No mathematical patterns detected."


def describe_position(x: float, y: float) -> str:
    """Describe position on receipt."""
    # Y: 0=bottom, 1=top in our normalized space
    if y > 0.7:
        v_pos = "top"
    elif y > 0.3:
        v_pos = "middle"
    else:
        v_pos = "bottom"

    if x < 0.33:
        h_pos = "left"
    elif x < 0.66:
        h_pos = "center"
    else:
        h_pos = "right"

    return f"{v_pos}-{h_pos}"


# =============================================================================
# ChromaDB Similar Word Query
# =============================================================================


def build_word_chroma_id(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    """Build ChromaDB ID for a word."""
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
    )


def query_similar_words(
    chroma_client: Any,
    word_text: str,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    target_merchant: str,
    n_results: int = 100,
) -> list["SimilarWordEvidence"]:
    """
    Query ChromaDB for semantically similar words.

    Returns ALL similar words above a minimum threshold, grouped by merchant.
    The LLM decides what evidence is sufficient.
    """
    word_chroma_id = build_word_chroma_id(
        image_id, receipt_id, line_id, word_id
    )

    try:
        logger.debug(
            "Querying similar words for '%s' (%s)", word_text, word_chroma_id
        )

        # First, get the target word's embedding
        word_result = chroma_client.get(
            collection_name="words",
            ids=[word_chroma_id],
            include=["embeddings"],
        )

        embeddings = word_result.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            logger.warning(f"No embedding found for {word_chroma_id}")
            return []

        embedding = embeddings[0]
        # Handle numpy array - convert to list for ChromaDB query
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()

        # Query for similar words (no merchant filter - we'll categorize after)
        results = chroma_client.query(
            collection_name="words",
            query_embeddings=[embedding],
            n_results=n_results,
            where={"label_status": {"$in": ["validated", "auto_suggested"]}},
            include=["metadatas", "distances"],
        )

        metadatas = results.get("metadatas")
        if metadatas is None or len(metadatas) == 0 or len(metadatas[0]) == 0:
            return []

        evidence_list: list["SimilarWordEvidence"] = []
        distances = results.get("distances", [[]])[0]

        for metadata, distance in zip(metadatas[0], distances, strict=True):
            # Convert L2 distance to similarity score (0-1)
            similarity = max(0.0, 1.0 - (distance / 2.0))

            # Skip very low similarity
            if similarity < 0.3:
                continue

            # Skip self
            chroma_id = (
                f"IMAGE#{metadata.get('image_id')}"
                f"#RECEIPT#{metadata.get('receipt_id', 0):05d}"
                f"#LINE#{metadata.get('line_id', 0):05d}"
                f"#WORD#{metadata.get('word_id', 0):05d}"
            )
            if chroma_id == word_chroma_id:
                continue

            merchant = metadata.get("merchant_name", "Unknown")
            is_same = merchant.lower() == target_merchant.lower()

            # Parse valid/invalid labels from comma-delimited format
            valid_labels_str = metadata.get("valid_labels", "")
            invalid_labels_str = metadata.get("invalid_labels", "")

            valid_labels = [
                lbl for lbl in valid_labels_str.split(",") if lbl.strip()
            ]
            invalid_labels = [
                lbl for lbl in invalid_labels_str.split(",") if lbl.strip()
            ]

            evidence: "SimilarWordEvidence" = {
                "word_text": metadata.get("text", ""),
                "similarity_score": round(similarity, 3),
                "chroma_id": chroma_id,
                "position_x": metadata.get("x", 0.5),
                "position_y": metadata.get("y", 0.5),
                "position_description": describe_position(
                    metadata.get("x", 0.5), metadata.get("y", 0.5)
                ),
                "left_neighbor": metadata.get("left", "<EDGE>"),
                "right_neighbor": metadata.get("right", "<EDGE>"),
                "current_label": metadata.get("label"),
                "label_status": metadata.get("label_status", "unvalidated"),
                "validated_as": [],  # Will be enriched from DynamoDB
                "invalidated_as": [],
                "merchant_name": merchant,
                "is_same_merchant": is_same,
            }

            # Pre-populate validation info from metadata
            for label in valid_labels:
                evidence["validated_as"].append(
                    {
                        "label": label,
                        "reasoning": None,  # Will be fetched from DynamoDB
                        "proposed_by": metadata.get("label_proposed_by"),
                        "timestamp": metadata.get("label_validated_at", ""),
                    }
                )

            for label in invalid_labels:
                evidence["invalidated_as"].append(
                    {
                        "label": label,
                        "reasoning": None,
                        "proposed_by": None,
                        "timestamp": "",
                    }
                )

            evidence_list.append(evidence)

        # Sort by relevance: same merchant + validated + high similarity
        evidence_list.sort(
            key=lambda e: (
                e["is_same_merchant"],
                len(e["validated_as"]) > 0,
                e["similarity_score"],
            ),
            reverse=True,
        )

        return evidence_list

    except Exception as e:
        logger.warning(f"Error querying similar words: {e}")
        return []


def enrich_evidence_with_dynamo_reasoning(
    evidence_list: list["SimilarWordEvidence"],
    dynamo_client: Any,
    limit: int = 20,
) -> list["SimilarWordEvidence"]:
    """
    Fetch reasoning from DynamoDB for top similar words.

    Only fetches for the top N most relevant words to limit DynamoDB calls.
    """
    for evidence in evidence_list[:limit]:
        try:
            # Parse IDs from chroma_id
            parts = evidence["chroma_id"].split("#")
            image_id = parts[1]
            receipt_id = int(parts[3])
            line_id = int(parts[5])
            word_id = int(parts[7])

            # Fetch labels from DynamoDB (returns tuple: labels, pagination_key)
            labels, _ = dynamo_client.list_receipt_word_labels_for_word(
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_id,
                word_id=word_id,
            )

            # Enrich validation history with reasoning
            validated_map = {v["label"]: v for v in evidence["validated_as"]}
            invalidated_map = {
                v["label"]: v for v in evidence["invalidated_as"]
            }

            for label in labels:
                if label.validation_status == "VALID":
                    if label.label in validated_map:
                        validated_map[label.label][
                            "reasoning"
                        ] = label.reasoning
                        validated_map[label.label][
                            "proposed_by"
                        ] = label.label_proposed_by
                        validated_map[label.label][
                            "timestamp"
                        ] = label.timestamp_added
                elif label.validation_status == "INVALID":
                    if label.label in invalidated_map:
                        invalidated_map[label.label][
                            "reasoning"
                        ] = label.reasoning
                        invalidated_map[label.label][
                            "proposed_by"
                        ] = label.label_proposed_by

        except Exception as e:
            logger.debug(f"Could not enrich evidence: {e}")
            continue

    return evidence_list


def compute_similarity_distribution(
    evidence_list: list["SimilarWordEvidence"],
) -> "SimilarityDistribution":
    """Compute distribution of similarity scores."""
    dist: "SimilarityDistribution" = {
        "very_high": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }

    for e in evidence_list:
        score = e["similarity_score"]
        if score >= 0.9:
            dist["very_high"] += 1
        elif score >= 0.7:
            dist["high"] += 1
        elif score >= 0.5:
            dist["medium"] += 1
        else:
            dist["low"] += 1

    return dist


def compute_label_distribution(
    evidence_list: list["SimilarWordEvidence"],
) -> dict[str, "LabelDistributionStats"]:
    """Compute label distribution across similar words."""
    label_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "example_words": [],
        }
    )

    for e in evidence_list:
        label = e["current_label"]
        if not label:
            continue

        stats = label_stats[label]
        stats["count"] += 1
        stats["valid_count"] += len(e["validated_as"])
        stats["invalid_count"] += len(e["invalidated_as"])

        if len(stats["example_words"]) < 3:
            stats["example_words"].append(e["word_text"])

    return dict(label_stats)


def compute_merchant_breakdown(
    evidence_list: list["SimilarWordEvidence"],
) -> list["MerchantBreakdown"]:
    """Compute label counts by merchant."""
    merchant_labels: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"is_same": False, "labels": Counter()}
    )

    for e in evidence_list:
        merchant = e["merchant_name"]
        label = e["current_label"]
        if label:
            merchant_labels[merchant]["labels"][label] += 1
            if e["is_same_merchant"]:
                merchant_labels[merchant]["is_same"] = True

    breakdown: list["MerchantBreakdown"] = []
    for merchant, data in sorted(
        merchant_labels.items(), key=lambda x: -sum(x[1]["labels"].values())
    ):
        breakdown.append(
            {
                "merchant_name": merchant,
                "is_same_merchant": data["is_same"],
                "labels": dict(data["labels"]),
            }
        )

    return breakdown


# =============================================================================
# Prompt Building
# =============================================================================


def format_line_item_patterns(patterns: Optional[dict]) -> str:
    """Format line item patterns for the LLM prompt."""
    if not patterns:
        return "No line item patterns available for this merchant."

    lines = []
    lines.append(f"**Merchant**: {patterns.get('merchant', 'Unknown')}")
    lines.append(
        f"**Item Structure**: {patterns.get('item_structure', 'unknown')}"
    )

    lpi = patterns.get("lines_per_item", {})
    if lpi:
        lines.append(
            f"**Lines per Item**: typical={lpi.get('typical', '?')}, "
            f"range=[{lpi.get('min', '?')}, {lpi.get('max', '?')}]"
        )

    if patterns.get("item_start_marker"):
        lines.append(f"**Item Start**: {patterns['item_start_marker']}")

    if patterns.get("item_end_marker"):
        lines.append(f"**Item End**: {patterns['item_end_marker']}")

    if patterns.get("grouping_rule"):
        lines.append(f"**Grouping Rule**: {patterns['grouping_rule']}")

    label_positions = patterns.get("label_positions", {})
    if label_positions:
        pos_lines = []
        for label, position in label_positions.items():
            pos_lines.append(f"  - {label}: {position}")
        if pos_lines:
            lines.append("**Typical Label Positions**:")
            lines.extend(pos_lines)

    return "\n".join(lines)


def build_review_prompt(
    issue: dict[str, Any],
    similar_evidence: list["SimilarWordEvidence"],
    similarity_dist: "SimilarityDistribution",
    label_dist: dict[str, "LabelDistributionStats"],
    merchant_breakdown: list["MerchantBreakdown"],
    merchant_name: str,
    merchant_receipt_count: int,
    currency_context: Optional[list[dict]] = None,
    line_item_patterns: Optional[dict] = None,
) -> str:
    """Build comprehensive LLM review prompt with full context."""

    word_text = issue.get("word_text", "")
    current_label = issue.get("current_label") or "NONE (unlabeled)"
    issue_type = issue.get("type", "unknown")
    evaluator_reasoning = issue.get("reasoning", "No reasoning provided")

    # Build similar words section
    same_merchant_examples = []
    other_merchant_examples = []

    for e in similar_evidence[:30]:  # Show top 30
        line = (
            f"- \"{e['word_text']}\" (similarity: {e['similarity_score']:.0%})"
        )
        line += f"\n  Context: `{e['left_neighbor']}` | **{e['word_text']}** "
        line += f"| `{e['right_neighbor']}`"
        line += f"\n  Position: {e['position_description']}"

        if e["validated_as"]:
            for v in e["validated_as"][:2]:
                reasoning = v.get("reasoning") or "no reasoning recorded"
                line += (
                    f"\n  VALIDATED as **{v['label']}**: \"{reasoning[:100]}\""
                )

        if e["invalidated_as"]:
            for v in e["invalidated_as"][:2]:
                reasoning = v.get("reasoning") or "no reasoning recorded"
                line += f"\n  INVALIDATED as **{v['label']}**: \"{reasoning[:100]}\""

        if e["is_same_merchant"]:
            same_merchant_examples.append(line)
        else:
            other_merchant_examples.append(line)

    # Build distribution summary
    dist_summary = (
        f"- {similarity_dist['very_high']} words with similarity >= 90% "
        f"(very similar)\n"
        f"- {similarity_dist['high']} words with similarity 70-90% (similar)\n"
        f"- {similarity_dist['medium']} words with similarity 50-70% "
        f"(somewhat similar)\n"
        f"- {similarity_dist['low']} words with similarity < 50% "
        f"(weak matches)"
    )

    # Build label distribution
    label_summary_lines = []
    for label, stats in sorted(
        label_dist.items(), key=lambda x: -x[1]["count"]
    )[:10]:
        examples = ", ".join(stats["example_words"][:3])
        label_summary_lines.append(
            f"- **{label}**: {stats['count']} occurrences "
            f"({stats['valid_count']} validated, "
            f"{stats['invalid_count']} invalidated) "
            f'e.g., "{examples}"'
        )
    label_summary = "\n".join(label_summary_lines) or "No label data available"

    # Merchant breakdown
    merchant_lines = []
    for m in merchant_breakdown[:5]:
        marker = " (SAME MERCHANT)" if m["is_same_merchant"] else ""
        labels_str = ", ".join(
            f"{lbl}: {cnt}" for lbl, cnt in list(m["labels"].items())[:3]
        )
        merchant_lines.append(f"- {m['merchant_name']}{marker}: {labels_str}")
    merchant_summary = "\n".join(merchant_lines) or "No merchant data"

    # Data sparsity note
    if merchant_receipt_count < 10:
        sparsity_note = (
            f"\n**NOTE**: Only {merchant_receipt_count} receipts available "
            f"for {merchant_name}. Cross-merchant examples are shown for "
            f"additional context.\n"
        )
    else:
        sparsity_note = ""

    prompt = f"""# Receipt Label Validation Task

You are reviewing a potential labeling issue on a receipt. Analyze the evidence
carefully and make a decision about whether the current label is correct.

## The Issue Being Reviewed

**Word**: "{word_text}"
**Current Label**: {current_label}
**Issue Type**: {issue_type}
**Evaluator's Concern**: {evaluator_reasoning}

## Label Definitions

{CORE_LABELS}

## Semantic Similarity Evidence
{sparsity_note}
The following words are semantically similar to "{word_text}" based on their
embeddings (which include surrounding context). The similarity score indicates
how close the embedding is to the target word.

### Distribution Summary

{dist_summary}

### Label Distribution Across Similar Words

{label_summary}

### By Merchant

{merchant_summary}

### From Same Merchant ({merchant_name})

{chr(10).join(same_merchant_examples[:15]) if same_merchant_examples else "No examples from same merchant"}

### From Other Merchants

{chr(10).join(other_merchant_examples[:15]) if other_merchant_examples else "No cross-merchant examples"}

## This Receipt's Currency Amounts

{format_currency_context_table(currency_context) if currency_context else "Currency context not available."}

### Mathematical Observations

{compute_currency_math_hints(currency_context) if currency_context else "No math hints available."}

**IMPORTANT**: When reviewing currency-related labels (LINE_TOTAL, SUBTOTAL, TAX,
GRAND_TOTAL, TENDER, CHANGE), use the table above to understand the relationships
between amounts on THIS receipt. The same dollar amount may correctly appear with
the same label multiple times (e.g., GRAND_TOTAL shown at top and bottom of receipt).

## Line Item Patterns for {merchant_name}

{format_line_item_patterns(line_item_patterns)}

**IMPORTANT**: When reviewing line item labels (PRODUCT_NAME, QUANTITY, UNIT_PRICE,
LINE_TOTAL, SKU), use the patterns above to understand how line items are structured
for this specific merchant. Multi-line items may have the product name, quantity, and
price on separate lines that all belong to the same item.

## Your Task

Based on all evidence above, determine:

1. **Decision**:
   - VALID: The current label IS correct (the flag was a false positive)
   - INVALID: The current label IS wrong - provide the correct label
   - NEEDS_REVIEW: Genuinely ambiguous, needs human review

2. **Reasoning**: Cite specific evidence from the similar words

3. **Suggested Label**: If INVALID, what should it be? Use a label from the
   definitions above, or null if no label applies.

4. **Confidence**: low / medium / high

Respond with ONLY a JSON object:
```json
{{
  "decision": "VALID | INVALID | NEEDS_REVIEW",
  "reasoning": "Your detailed reasoning citing evidence...",
  "suggested_label": "LABEL_NAME or null",
  "confidence": "low | medium | high"
}}
```
"""
    return prompt


def parse_llm_response(response_text: str) -> "LLMDecision":
    """Parse LLM JSON response."""
    # Handle markdown code blocks
    if "```" in response_text:
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", response_text, re.S)
        if match:
            response_text = match.group(1)

    response_text = response_text.strip()

    try:
        result = json.loads(response_text)
        decision = result.get("decision", "NEEDS_REVIEW")
        if decision not in ("VALID", "INVALID", "NEEDS_REVIEW"):
            decision = "NEEDS_REVIEW"

        confidence = result.get("confidence", "medium")
        if confidence not in ("low", "medium", "high"):
            confidence = "medium"

        # Validate suggested_label is in CORE_LABELS
        suggested_label = result.get("suggested_label")
        if suggested_label:
            suggested_upper = suggested_label.upper()
            if suggested_upper not in CORE_LABELS_SET:
                logger.warning(
                    "Rejecting invalid suggested_label '%s' (not in CORE_LABELS)",
                    suggested_label,
                )
                suggested_label = None
            else:
                suggested_label = suggested_upper  # Normalize to uppercase

        return {
            "decision": decision,
            "reasoning": result.get("reasoning", "No reasoning provided"),
            "suggested_label": suggested_label,
            "confidence": confidence,
        }
    except json.JSONDecodeError:
        return {
            "decision": "NEEDS_REVIEW",
            "reasoning": f"Failed to parse LLM response: {response_text[:200]}",
            "suggested_label": None,
            "confidence": "low",
        }


# =============================================================================
# Multi-Issue Batching
# =============================================================================

# Default number of issues to batch in a single LLM call
DEFAULT_ISSUES_PER_LLM_CALL = 10


def build_batched_review_prompt(
    issues_with_context: list[dict[str, Any]],
    merchant_name: str,
    merchant_receipt_count: int,
    line_item_patterns: Optional[dict] = None,
) -> str:
    """
    Build a batched LLM review prompt for multiple issues.

    Args:
        issues_with_context: List of dicts, each containing:
            - issue: The issue dict
            - similar_evidence: List of SimilarWordEvidence
            - similarity_dist: SimilarityDistribution
            - label_dist: Label distribution stats
            - merchant_breakdown: Merchant breakdown list
            - currency_context: Currency amounts from receipt
        merchant_name: The merchant name
        merchant_receipt_count: Number of receipts for this merchant
        line_item_patterns: Optional line item patterns

    Returns:
        Combined prompt for all issues
    """
    issues_text = []

    for idx, item in enumerate(issues_with_context):
        issue = item["issue"]
        similar_evidence = item.get("similar_evidence", [])
        similarity_dist = item.get("similarity_dist", {})
        label_dist = item.get("label_dist", {})
        currency_context = item.get("currency_context", [])

        word_text = issue.get("word_text", "")
        current_label = issue.get("current_label") or "NONE (unlabeled)"
        issue_type = issue.get("type", "unknown")
        evaluator_reasoning = issue.get("reasoning", "No reasoning provided")

        # Build condensed similar words section
        same_merchant = []
        other_merchant = []

        for e in similar_evidence[:15]:  # Reduced from 30 for batching
            line = f'"{e["word_text"]}" ({e["similarity_score"]:.0%})'
            if e.get("validated_as"):
                labels = [v["label"] for v in e["validated_as"][:2]]
                line += f" → {', '.join(labels)}"
            if e["is_same_merchant"]:
                same_merchant.append(line)
            else:
                other_merchant.append(line)

        # Condensed distribution
        dist_str = (
            f"Very high (>=90%): {similarity_dist.get('very_high', 0)}, "
            f"High (70-90%): {similarity_dist.get('high', 0)}, "
            f"Medium (50-70%): {similarity_dist.get('medium', 0)}"
        )

        # Condensed label distribution
        label_lines = []
        for label, stats in sorted(
            label_dist.items(), key=lambda x: -x[1]["count"]
        )[:5]:
            label_lines.append(
                f"{label}: {stats['count']} ({stats['valid_count']} valid)"
            )
        label_str = ", ".join(label_lines) if label_lines else "None"

        # Currency context (condensed)
        currency_str = ""
        if currency_context:
            amounts = [
                f"{c.get('label', '?')}: {c.get('text', '?')}"
                for c in currency_context[:8]
            ]
            currency_str = f"\n  Currency amounts: {', '.join(amounts)}"

        issue_block = f"""
---
## Issue {idx}

**Word**: "{word_text}"
**Current Label**: {current_label}
**Issue Type**: {issue_type}
**Evaluator's Concern**: {evaluator_reasoning}

**Similarity Distribution**: {dist_str}
**Label Distribution**: {label_str}
**Same Merchant Examples**: {', '.join(same_merchant[:5]) if same_merchant else 'None'}
**Other Merchant Examples**: {', '.join(other_merchant[:5]) if other_merchant else 'None'}{currency_str}
"""
        issues_text.append(issue_block)

    # Data sparsity note
    sparsity_note = ""
    if merchant_receipt_count < 10:
        sparsity_note = (
            f"\n**NOTE**: Only {merchant_receipt_count} receipts available "
            f"for {merchant_name}. Cross-merchant examples shown for context.\n"
        )

    prompt = f"""# Batch Receipt Label Validation

You are reviewing {len(issues_with_context)} potential labeling issues for
receipts from **{merchant_name}**. Analyze each issue and provide decisions.
{sparsity_note}
## Label Definitions

{CORE_LABELS}

## Line Item Patterns for {merchant_name}

{format_line_item_patterns(line_item_patterns)}

## Issues to Review

{"".join(issues_text)}

---

## Your Task

For EACH issue above (0 to {len(issues_with_context) - 1}), determine:

1. **Decision**: VALID (label is correct), INVALID (label is wrong), or NEEDS_REVIEW
2. **Reasoning**: Brief justification citing evidence
3. **Suggested Label**: If INVALID, the correct label (or null)
4. **Confidence**: low / medium / high

Respond with ONLY a JSON object containing a "reviews" array:
```json
{{
  "reviews": [
    {{
      "issue_index": 0,
      "decision": "VALID | INVALID | NEEDS_REVIEW",
      "reasoning": "Brief reasoning...",
      "suggested_label": "LABEL_NAME or null",
      "confidence": "low | medium | high"
    }},
    {{
      "issue_index": 1,
      "decision": "...",
      "reasoning": "...",
      "suggested_label": "...",
      "confidence": "..."
    }}
  ]
}}
```

IMPORTANT: You MUST provide exactly {len(issues_with_context)} reviews, one for each issue index from 0 to {len(issues_with_context) - 1}.
"""
    return prompt


def parse_batched_llm_response(
    response_text: str, expected_count: int
) -> list["LLMDecision"]:
    """
    Parse batched LLM JSON response.

    Args:
        response_text: Raw LLM response
        expected_count: Expected number of reviews

    Returns:
        List of LLMDecision dicts, one per issue
    """
    # Handle markdown code blocks
    if "```" in response_text:
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", response_text, re.S)
        if match:
            response_text = match.group(1)

    response_text = response_text.strip()

    # Default fallback for all issues
    fallback = {
        "decision": "NEEDS_REVIEW",
        "reasoning": "Failed to parse batched LLM response",
        "suggested_label": None,
        "confidence": "low",
    }

    try:
        result = json.loads(response_text)
        reviews = result.get("reviews", [])

        if not isinstance(reviews, list):
            logger.warning("Batched response 'reviews' is not a list")
            return [fallback.copy() for _ in range(expected_count)]

        # Build a map by issue_index
        reviews_by_index: dict[int, "LLMDecision"] = {}

        for review in reviews:
            if not isinstance(review, dict):
                continue

            idx = review.get("issue_index")
            if idx is None or not isinstance(idx, int):
                continue

            decision = review.get("decision", "NEEDS_REVIEW")
            if decision not in ("VALID", "INVALID", "NEEDS_REVIEW"):
                decision = "NEEDS_REVIEW"

            confidence = review.get("confidence", "medium")
            if confidence not in ("low", "medium", "high"):
                confidence = "medium"

            # Validate suggested_label is in CORE_LABELS
            suggested_label = review.get("suggested_label")
            if suggested_label:
                suggested_upper = suggested_label.upper()
                if suggested_upper not in CORE_LABELS_SET:
                    logger.warning(
                        "Rejecting invalid suggested_label '%s' (not in CORE_LABELS)",
                        suggested_label,
                    )
                    suggested_label = None
                else:
                    suggested_label = suggested_upper  # Normalize to uppercase

            reviews_by_index[idx] = {
                "decision": decision,
                "reasoning": review.get("reasoning", "No reasoning provided"),
                "suggested_label": suggested_label,
                "confidence": confidence,
            }

        # Build ordered list, using fallback for missing indices
        ordered_reviews = []
        for i in range(expected_count):
            if i in reviews_by_index:
                ordered_reviews.append(reviews_by_index[i])
            else:
                logger.warning(f"Missing review for issue index {i}")
                ordered_reviews.append(
                    {
                        "decision": "NEEDS_REVIEW",
                        "reasoning": f"LLM did not provide review for issue {i}",
                        "suggested_label": None,
                        "confidence": "low",
                    }
                )

        return ordered_reviews

    except json.JSONDecodeError:
        logger.exception(
            "Failed to parse batched response (preview: %s)",
            response_text[:500],
        )
        return [fallback.copy() for _ in range(expected_count)]


# =============================================================================
# Receipt-Context Prompt Builder
# =============================================================================


def build_receipt_context_prompt(
    receipt_words: list[dict],
    receipt_labels: list[dict],
    issues_with_context: list[dict[str, Any]],
    merchant_name: str,
    merchant_receipt_count: int,
    line_item_patterns: Optional[dict] = None,
) -> str:
    """
    Build a prompt with full receipt context for a single receipt's issues.

    Shows the complete receipt text with issue words highlighted, then
    details each issue with similar word evidence including reasoning.

    Args:
        receipt_words: All words from the receipt
        receipt_labels: All labels for the receipt
        issues_with_context: List of issue dicts for THIS receipt, each with:
            - issue: The issue dict (line_id, word_id, current_label, etc.)
            - similar_evidence: List of SimilarWordEvidence with reasoning
        merchant_name: The merchant name
        merchant_receipt_count: Number of receipts for this merchant
        line_item_patterns: Optional line item patterns

    Returns:
        Prompt with receipt context and issues
    """
    # Debug: Log input summary
    logger.debug(
        f"build_receipt_context_prompt: {len(receipt_words)} words, "
        f"{len(receipt_labels)} labels, {len(issues_with_context)} issues"
    )

    # Build list of issue word positions for highlighting
    highlight_words = []
    for item in issues_with_context:
        issue = item.get("issue", {})
        line_id = issue.get("line_id")
        word_id = issue.get("word_id")
        if line_id is not None and word_id is not None:
            highlight_words.append((line_id, word_id))

    # Assemble receipt text with highlighted issue words
    receipt_text = assemble_receipt_text(
        words=receipt_words,
        labels=receipt_labels,
        highlight_words=highlight_words,
        max_lines=50,
    )

    # Build issue details with similar word reasoning
    issues_text = []
    for idx, item in enumerate(issues_with_context):
        issue = item.get("issue", {})
        similar_evidence = item.get("similar_evidence")

        # Debug: Log the type and content of similar_evidence
        if similar_evidence is None:
            logger.warning(
                f"Issue {idx}: similar_evidence is None "
                f"(item keys: {list(item.keys())})"
            )
            similar_evidence = []
        elif not isinstance(similar_evidence, list):
            logger.warning(
                f"Issue {idx}: similar_evidence is {type(similar_evidence).__name__}, "
                f"not list. Value: {str(similar_evidence)[:200]}"
            )
            similar_evidence = []

        word_text = issue.get("word_text", "")
        current_label = issue.get("current_label") or "NONE (unlabeled)"
        issue_type = issue.get("type", "unknown")
        evaluator_reasoning = issue.get("reasoning", "No reasoning provided")

        # Build similar words with reasoning (top 10)
        similar_lines = []
        for e_idx, e in enumerate(similar_evidence[:10]):
            if e is None:
                logger.warning(f"Issue {idx}: evidence[{e_idx}] is None")
                continue
            if not isinstance(e, dict):
                logger.warning(
                    f"Issue {idx}: evidence[{e_idx}] is {type(e).__name__}, not dict"
                )
                continue

            sim_score = e.get("similarity_score", 0)
            sim_word = e.get("word_text", "")
            is_same = "same merchant" if e.get("is_same_merchant") else "other"

            # Get validation reasoning - with null safety
            validated_info = []
            validated_as = e.get("validated_as")
            if validated_as is None:
                logger.debug(
                    f"Issue {idx}: evidence[{e_idx}] validated_as is None"
                )
            elif not isinstance(validated_as, list):
                logger.warning(
                    f"Issue {idx}: evidence[{e_idx}] validated_as is "
                    f"{type(validated_as).__name__}, not list"
                )
            elif validated_as:
                for v in validated_as[:2]:
                    if not isinstance(v, dict):
                        continue
                    label = v.get("label", "?")
                    reasoning = (v.get("reasoning") or "no reasoning")[:80]
                    validated_info.append(f'{label}: "{reasoning}"')

            invalidated_info = []
            invalidated_as = e.get("invalidated_as")
            if invalidated_as is None:
                logger.debug(
                    f"Issue {idx}: evidence[{e_idx}] invalidated_as is None"
                )
            elif not isinstance(invalidated_as, list):
                logger.warning(
                    f"Issue {idx}: evidence[{e_idx}] invalidated_as is "
                    f"{type(invalidated_as).__name__}, not list"
                )
            elif invalidated_as:
                for v in invalidated_as[:1]:
                    if not isinstance(v, dict):
                        continue
                    label = v.get("label", "?")
                    reasoning = (v.get("reasoning") or "no reasoning")[:60]
                    invalidated_info.append(f'~~{label}~~: "{reasoning}"')

            # Format line
            line = f'- "{sim_word}" ({sim_score:.0%}, {is_same})'
            if validated_info:
                line += f" → {'; '.join(validated_info)}"
            if invalidated_info:
                line += f" | {'; '.join(invalidated_info)}"
            similar_lines.append(line)

        similar_text = (
            "\n".join(similar_lines)
            if similar_lines
            else "No similar words found"
        )

        issue_block = f"""
### Issue {idx}: "{word_text}" - {current_label}

**Issue Type**: {issue_type}
**Evaluator's Concern**: {evaluator_reasoning}

**Similar Words with Reasoning**:
{similar_text}
"""
        issues_text.append(issue_block)

    # Sparsity note
    sparsity_note = ""
    if merchant_receipt_count < 10:
        sparsity_note = (
            f"\n**NOTE**: Only {merchant_receipt_count} receipts available "
            f"for {merchant_name}. Evidence may be limited.\n"
        )

    prompt = f"""# Receipt Label Validation

You are reviewing {len(issues_with_context)} potential labeling issues on a receipt from **{merchant_name}**.
{sparsity_note}
## Label Definitions

{CORE_LABELS}

## Line Item Patterns for {merchant_name}

{format_line_item_patterns(line_item_patterns)}

## Full Receipt Text

Words marked with [brackets] are the issues to review. Labels shown in (parentheses).

```
{receipt_text}
```

## Issues to Review

{"".join(issues_text)}

---

## Your Task

For EACH issue (0 to {len(issues_with_context) - 1}), analyze the receipt context and similar word evidence to determine:

1. **Decision**: VALID (label is correct), INVALID (label is wrong), or NEEDS_REVIEW (insufficient evidence)
2. **Reasoning**: Brief justification referencing the receipt context and/or similar word patterns
3. **Suggested Label**: If INVALID, the correct label (or null if unsure)
4. **Confidence**: low / medium / high

Respond with ONLY a JSON object:
```json
{{
  "reviews": [
    {{
      "issue_index": 0,
      "decision": "VALID | INVALID | NEEDS_REVIEW",
      "reasoning": "Brief reasoning citing receipt context or similar patterns...",
      "suggested_label": "LABEL_NAME or null",
      "confidence": "low | medium | high"
    }}
  ]
}}
```

IMPORTANT: Provide exactly {len(issues_with_context)} reviews, one for each issue index.
"""
    return prompt


# =============================================================================
# Main Handler
# =============================================================================
# NOTE: apply_llm_decisions is now imported from receipt_agent.agents.label_evaluator


def handler(event: dict[str, Any], _context: Any) -> "LLMReviewBatchOutput":
    """
    Review a batch of flagged issues using LLM with semantic similarity.

    This handler processes a BATCH of issues (not all issues for a merchant).
    It supports two input formats:
    1. Batch format (from batch_issues handler): batch_s3_key points to a batch file
    2. Legacy format: issues_s3_key points to all issues

    Includes rate limiting with circuit breaker to handle Ollama API limits.
    When rate limits are hit, raises OllamaRateLimitError for Step Function retry.

    Input (batch format):
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "merchant_receipt_count": 50,
        "batch_s3_key": "batches/{exec}/{merchant_hash}_0.json",
        "batch_index": 0,
        "dry_run": false
    }

    Input (legacy format):
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "merchant_receipt_count": 50,
        "issues_s3_key": "issues/{exec}/{merchant_hash}.json",
        "dry_run": false
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

    Raises:
        OllamaRateLimitError: When rate limit is hit, triggers Step Function retry
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "Unknown")
    merchant_receipt_count = event.get("merchant_receipt_count", 0)
    batch_index = event.get("batch_index", 0)
    dry_run = event.get("dry_run", False)
    line_item_patterns_s3_key = event.get("line_item_patterns_s3_key")

    # Support both batch format (batch_s3_key) and legacy format (issues_s3_key)
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

    try:
        # 1. Load collected issues from S3
        logger.info(f"Loading issues from s3://{batch_bucket}/{data_s3_key}")
        issues_data = load_json_from_s3(batch_bucket, data_s3_key)
        collected_issues = issues_data.get("issues", [])

        total_issues = len(collected_issues)
        if total_issues == 0:
            logger.info("No issues to review")
            return {
                "status": "skipped",
                "execution_id": execution_id,
                "merchant_name": merchant_name,
                "total_issues": 0,
                "issues_reviewed": 0,
                "decisions": {},
            }

        logger.info(
            f"Reviewing {total_issues} issues for {merchant_name} "
            f"({merchant_receipt_count} receipts)"
        )

        # 2. Load line item patterns (if available)
        line_item_patterns = None
        if line_item_patterns_s3_key:
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
                    f"structure, {line_item_patterns.get('lines_per_item', {}).get('typical', '?')} "
                    f"lines per item"
                )
            except Exception as e:
                logger.warning(f"Could not load line item patterns: {e}")

        # 3. Setup ChromaDB
        chroma_client = None
        if chromadb_bucket:
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

        # 5. Setup Ollama LLM
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
            f"LLM initialized: {ollama_model} (max jitter: {max_jitter_seconds}s, "
            f"circuit breaker threshold: {circuit_breaker_threshold})"
        )

        # 5. Group issues by receipt and process with full receipt context
        # Max issues per LLM call (even within a single receipt)
        max_issues_per_call = int(
            os.environ.get("MAX_ISSUES_PER_LLM_CALL", "15")
        )

        decisions: Counter = Counter()
        reviewed_issues: list[dict[str, Any]] = []

        # Cache for similar word queries
        similar_cache: dict[str, list["SimilarWordEvidence"]] = {}

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

        # Import HumanMessage once
        from langchain_core.messages import HumanMessage

        llm_call_count = 0

        # Process each receipt
        for receipt_key, receipt_issues in issues_by_receipt.items():
            image_id, receipt_id_str = receipt_key.split(":", 1)
            receipt_id = int(receipt_id_str)

            # Load receipt data (words, labels) from S3
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
                                "reasoning": (
                                    f"Receipt data unavailable: {e}"
                                ),
                                "suggested_label": None,
                                "confidence": "low",
                            },
                            "data_load_error": str(e),
                        }
                    )
                continue

            # Process issues in chunks if receipt has too many
            for chunk_start in range(
                0, len(receipt_issues), max_issues_per_call
            ):
                chunk_end = min(
                    chunk_start + max_issues_per_call, len(receipt_issues)
                )
                chunk_issues = receipt_issues[chunk_start:chunk_end]

                logger.info(
                    f"Processing receipt {receipt_key}: issues "
                    f"{chunk_start + 1}-{chunk_end} of {len(receipt_issues)}"
                )

                # Gather context (similar evidence) for each issue
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

                # Build receipt-context prompt and make LLM call
                try:
                    prompt = build_receipt_context_prompt(
                        receipt_words=receipt_words,
                        receipt_labels=receipt_labels,
                        issues_with_context=issues_with_context,
                        merchant_name=merchant_name,
                        merchant_receipt_count=merchant_receipt_count,
                        line_item_patterns=line_item_patterns,
                    )

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

                    logger.info(
                        f"Completed receipt chunk: {len(chunk_reviews)} issues "
                        f"({len(reviewed_issues)}/{total_issues} total)"
                    )

                except OllamaRateLimitError:
                    # Save partial progress before re-raising
                    logger.warning(
                        f"Rate limit hit after {len(reviewed_issues)}/{total_issues} "
                        f"issues. Saving partial progress."
                    )
                    if reviewed_issues:
                        merchant_hash = merchant_name.lower().replace(
                            " ", "_"
                        )[:30]
                        partial_s3_key = f"partial/{execution_id}/{merchant_hash}_{batch_index}.json"
                        partial_data = {
                            "execution_id": execution_id,
                            "merchant_name": merchant_name,
                            "batch_index": batch_index,
                            "issues_processed": len(reviewed_issues),
                            "total_issues": total_issues,
                            "decisions": dict(decisions),
                            "issues": reviewed_issues,
                            "rate_limited": True,
                        }
                        upload_json_to_s3(
                            batch_bucket, partial_s3_key, partial_data
                        )
                        logger.info(
                            f"Saved partial progress to {partial_s3_key}"
                        )
                    raise  # Re-raise for Step Function retry

                except Exception as e:
                    # If LLM call fails, mark all issues in chunk as NEEDS_REVIEW
                    logger.exception(
                        "LLM call failed for receipt %s", receipt_key
                    )
                    logger.warning(
                        "Marking %d issues as NEEDS_REVIEW.",
                        len(chunk_metadata),
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
            f"Reviewed {total_issues} issues across {len(issues_by_receipt)} receipts "
            f"in {llm_call_count} LLM calls: {dict(decisions)}"
        )

        # 6. Upload reviewed results to S3
        merchant_hash = merchant_name.lower().replace(" ", "_")[:30]
        reviewed_s3_key = (
            f"reviewed/{execution_id}/{merchant_hash}_{batch_index}.json"
        )

        # Include rate limiting stats in output
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

        # 7. Apply decisions to DynamoDB (if not dry_run)
        apply_stats = None
        if not dry_run and dynamo_client and reviewed_issues:
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

        # 8. Log metrics
        from utils.emf_metrics import emf_metrics

        processing_time = time.time() - start_time
        metrics = {
            "IssuesReviewed": total_issues,
            "DecisionsValid": decisions.get("VALID", 0),
            "DecisionsInvalid": decisions.get("INVALID", 0),
            "DecisionsNeedsReview": decisions.get("NEEDS_REVIEW", 0),
            "ProcessingTimeSeconds": round(processing_time, 2),
            "SimilarWordsCached": len(similar_cache),
        }
        # Add apply_stats metrics if we wrote to DynamoDB
        if apply_stats:
            metrics["LabelsConfirmed"] = apply_stats.get("labels_confirmed", 0)
            metrics["LabelsInvalidated"] = apply_stats.get(
                "labels_invalidated", 0
            )
            metrics["LabelsCreated"] = apply_stats.get("labels_created", 0)
            metrics["ConflictsResolved"] = apply_stats.get(
                "conflicts_resolved", 0
            )
        emf_metrics.log_metrics(
            metrics=metrics,
            dimensions={"Merchant": merchant_name[:50]},
            properties={"execution_id": execution_id, "dry_run": dry_run},
            units={"ProcessingTimeSeconds": "Seconds"},
        )

        # Flush LangSmith traces
        flush_langsmith_traces()

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
        return result

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

        flush_langsmith_traces()

        return {
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
